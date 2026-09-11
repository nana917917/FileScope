"""Search pipeline (spec sections 11, 12, 27, 49, 61).

    discovery -> metadata pre-filter -> cloud state -> index/cache
              -> extraction queue -> matcher -> results -> UI events

Design rules that the code below enforces:

* bounded queues and a fixed worker set (no thread per file/future explosion),
* a dedicated lane for PDF/OCR so one huge scan cannot block everything,
* no file IO on the UI thread,
* JIT: stop reading as soon as the condition is provably satisfied, unless the
  file must be read to the end (NOT / confirmed: / ocr:) or is being indexed,
* honest coverage counters and a separate issue stream.
"""

from __future__ import annotations

import queue
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field, replace

from ..config import ResourceLimits
from ..errors import Issue, QuerySyntaxError, Severity, StopExtraction
from ..extractors import extract, extractor_for
from ..extractors.base import ExtractOptions, Sink
from ..index.database import IndexDatabase
from ..index.search import IndexSearcher
from ..logging_setup import get_logger
from ..ocr_cache import OcrCache
from ..platform import onedrive, tempfiles
from . import events
from .matcher import MatchOptions, Outcome, QueryMatcher, RuntimeFlags
from .models import (
    Chunk,
    ChunkKind,
    Coverage,
    FileEntry,
    FileKind,
    FileResult,
    Progress,
    SourceType,
    Summary,
)
from .paths import classify_kind, normalized_key
from .query import parse_query
from .scanner import iter_entries

log = get_logger("pipeline")

QUEUE_SIZE = 2048
EVENT_QUEUE_SIZE = 8192
MAX_ISSUES = 2000
MAX_RESULTS = 200_000
MAX_INDEX_TRACKED_KEYS = 400_000


@dataclass
class SearchConfig:
    roots: tuple[str, ...] = ()
    query: str = ""
    exclude_query: str = ""
    legacy_operator: str = "OR"
    mode: str = "standard"
    case_sensitive: bool = False
    ignore_width: bool = True
    part_number_mode: bool = False
    include_subfolders: bool = True
    include_excel: bool = True
    include_pdf: bool = True
    include_word: bool = True
    include_powerpoint: bool = True
    include_text: bool = True
    include_unknown_text: bool = True
    include_archives: bool = False
    search_path_names: bool = True
    search_formula: bool = True
    pdf_ocr_mode: str = "auto"
    ocr_languages: str = "jpn+eng"
    online_files_policy: str = "auto"
    stage_remote_files: bool = True
    workers: int = 4
    confirmed: frozenset[str] = frozenset()
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    index_enabled: bool = True
    index_max_bytes: int = 0
    max_results: int = MAX_RESULTS
    max_issues: int = MAX_ISSUES

    def included_kinds(self) -> frozenset[FileKind]:
        kinds = set()
        if self.include_excel:
            kinds.add(FileKind.EXCEL)
        if self.include_pdf:
            kinds.add(FileKind.PDF)
        if self.include_word:
            kinds.add(FileKind.WORD)
        if self.include_powerpoint:
            kinds.add(FileKind.POWERPOINT)
        if self.include_text:
            kinds.add(FileKind.TEXT)
        if self.include_archives:
            kinds.add(FileKind.ARCHIVE)
        if self.include_unknown_text:
            kinds.add(FileKind.UNKNOWN)
        return frozenset(kinds)


class SearchSession:
    """One search run. Safe to construct on the UI thread; work happens elsewhere."""

    def __init__(
        self,
        config: SearchConfig,
        *,
        events_queue: queue.Queue[object] | None = None,
        database: IndexDatabase | None = None,
        ocr_cache: OcrCache | None = None,
        ocr_available: bool = False,
    ) -> None:
        self.config = config
        self.events: queue.Queue = events_queue or queue.Queue(maxsize=EVENT_QUEUE_SIZE)
        self.database = database if config.index_enabled else None
        self.ocr_cache = ocr_cache
        self.ocr_available = ocr_available

        self.cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._temp = tempfiles.TempManager(
            reserve_bytes=config.limits.temp_free_reserve_bytes,
            max_stage_bytes=config.limits.remote_stage_max_bytes,
        )
        self.coverage = Coverage()
        self._lock = threading.Lock()
        self._results: list[FileResult] = []
        self._issues: list[Issue] = []
        self._threads: list[threading.Thread] = []
        self._general_queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._pdf_queue: queue.Queue = queue.Queue(maxsize=256)
        self._index_candidates: set[str] = set()
        self._session_started = 0.0
        self._last_progress = 0.0
        self._finished = threading.Event()
        self.summary: Summary | None = None
        self.matcher: QueryMatcher | None = None
        self._query_error: QuerySyntaxError | None = None
        self._removed_index_rows = 0

    # ------------------------------------------------------------- control
    def start(self) -> None:
        try:
            node = parse_query(self.config.query, legacy_operator=self.config.legacy_operator)
        except QuerySyntaxError as exc:
            self._query_error = exc
            self.events.put(events.QueryFailed(message=exc.message, position=exc.position, query=self.config.query))
            self._finished.set()
            return

        self.matcher = QueryMatcher(
            node,
            MatchOptions(
                case_sensitive=self.config.case_sensitive,
                ignore_width=self.config.ignore_width,
                part_number_mode=self.config.part_number_mode,
            ),
        )
        self._session_started = time.time()
        self.events.put(events.Started(query=self.config.query, mode=self.config.mode, roots=self.config.roots))
        self._prepare_index_candidates()

        general_workers = max(1, self.config.workers)
        self._threads.append(threading.Thread(target=self._enumerate, name="filescope-discovery", daemon=True))
        for index in range(general_workers):
            self._threads.append(
                threading.Thread(target=self._worker, name=f"filescope-worker-{index}", daemon=True)
            )
        self._threads.append(threading.Thread(target=self._pdf_worker, name="filescope-pdf", daemon=True))
        monitor = threading.Thread(target=self._monitor, name="filescope-monitor", daemon=True)
        for thread in self._threads:
            thread.start()
        monitor.start()
        self._monitor_thread = monitor

    def cancel(self) -> None:
        self.cancel_event.set()
        self._pause_event.set()
        # Wake idle workers so cancellation is prompt.
        for _ in range(max(1, self.config.workers)):
            with suppress(queue.Full):
                self._general_queue.put_nowait(_SENTINEL)
        with suppress(queue.Full):
            self._pdf_queue.put_nowait(_SENTINEL)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_event.clear()
        else:
            self._pause_event.set()

    @property
    def paused(self) -> bool:
        return not self._pause_event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    @property
    def results(self) -> list[FileResult]:
        return self._results

    @property
    def issues(self) -> list[Issue]:
        return self._issues

    # ------------------------------------------------------------ internals
    def _prepare_index_candidates(self) -> None:
        if self.database is None or self.matcher is None:
            return
        if self.config.mode == "full":
            return
        try:
            searcher = IndexSearcher(self.database, self.matcher)
            entries = searcher.candidate_files()
        except Exception as exc:  # index problems must never stop a search
            log.warning("index candidate generation failed: %s", exc)
            return
        self._index_candidates = {normalized_key(entry.path) for entry in entries}

    def _enumerate(self) -> None:
        try:
            per_root_keys: dict[str, set[str]] = {}
            for root in self.config.roots:
                if self.cancel_event.is_set():
                    break
                keys = per_root_keys.setdefault(root, set())
                complete = True
                for item in iter_entries(
                    root,
                    include_subfolders=self.config.include_subfolders,
                    cancel=self.cancel_event,
                ):
                    if self.cancel_event.is_set():
                        complete = False
                        break
                    if isinstance(item, Issue):
                        self._add_issue(item)
                        continue
                    with self._lock:
                        self.coverage.discovered += 1
                        discovered = self.coverage.discovered
                    if len(keys) < MAX_INDEX_TRACKED_KEYS:
                        keys.add(normalized_key(item.path))
                    if discovered % 500 == 0:
                        self.events.put(events.Discovered(count=discovered, root=root))
                    self._dispatch(item)
                self._cleanup_index(root, keys, complete=complete)
        finally:
            for _ in range(max(1, self.config.workers)):
                self._general_queue.put(_SENTINEL)
            self._pdf_queue.put(_SENTINEL)
            self._pdf_queue.put(_SENTINEL)

    def _dispatch(self, entry: FileEntry) -> None:
        kind = classify_kind(entry.extension)
        if kind is FileKind.PDF:
            self._put(self._pdf_queue, entry)
        else:
            self._put(self._general_queue, entry)

    def _put(self, target: queue.Queue, entry: FileEntry) -> None:
        while not self.cancel_event.is_set():
            try:
                target.put(entry, timeout=0.2)
                return
            except queue.Full:
                continue

    def _worker(self) -> None:
        while True:
            try:
                item = self._general_queue.get(timeout=0.2)
            except queue.Empty:
                if self.cancel_event.is_set():
                    return
                continue
            if item is _SENTINEL:
                self._general_queue.task_done()
                return
            try:
                self._pause_event.wait(timeout=0.5)
                if self.cancel_event.is_set():
                    continue
                self._process(item)
            except Exception as exc:  # a broken file must never kill the worker
                log.exception("worker error for %s: %s", getattr(item, "path", "?"), exc)
                self._add_issue(
                    Issue(
                        path=getattr(item, "path", ""),
                        code="worker",
                        message=f"処理中に予期しないエラー: {type(exc).__name__}",
                        severity=Severity.ERROR,
                    )
                )
            finally:
                self._general_queue.task_done()

    def _pdf_worker(self) -> None:
        while True:
            try:
                item = self._pdf_queue.get(timeout=0.2)
            except queue.Empty:
                if self.cancel_event.is_set():
                    return
                continue
            if item is _SENTINEL:
                self._pdf_queue.task_done()
                return
            try:
                self._pause_event.wait(timeout=0.5)
                if self.cancel_event.is_set():
                    continue
                self._process(item)
            except Exception as exc:
                log.exception("pdf worker error for %s: %s", getattr(item, "path", "?"), exc)
                self._add_issue(
                    Issue(
                        path=getattr(item, "path", ""),
                        code="worker",
                        message=f"PDF処理中にエラー: {type(exc).__name__}",
                        severity=Severity.ERROR,
                    )
                )
            finally:
                self._pdf_queue.task_done()

    # ------------------------------------------------------------ per file
    def _process(self, entry: FileEntry) -> None:
        kind = classify_kind(entry.extension)
        if kind not in self.config.included_kinds():
            with self._lock:
                self.coverage.skipped_type += 1
            return
        if entry.size > self._size_limit(kind):
            with self._lock:
                self.coverage.skipped_size += 1
            self._add_issue(
                Issue(
                    path=entry.path,
                    code="size-limit",
                    message=f"サイズ上限を超えているため検索しません（{entry.size:,} bytes）",
                    severity=Severity.SKIP,
                )
            )
            return
        if extractor_for(entry, self._extract_options()) is None:
            # Nothing in FileScope can read this format (binaries, archives when
            # disabled, unsupported Office variants): skip without touching it.
            with self._lock:
                self.coverage.skipped_type += 1
            return

        key = normalized_key(entry.path)
        indexed = self._indexed_and_current(entry)
        decision = onedrive.decide(
            entry,
            mode=self.config.mode,
            policy=self.config.online_files_policy,
            indexed=indexed and key in self._index_candidates,
        )
        if not decision.read_content:
            with self._lock:
                self.coverage.skipped_online += 1
                self.coverage.scanned += 1
            if decision.reason:
                self._add_issue(
                    Issue(path=entry.path, code="online-only", message=decision.reason, severity=Severity.SKIP)
                )
            return

        if indexed and self.config.mode != "full":
            if not self._index_candidates or key not in self._index_candidates:
                # The index already proves this file cannot match this query.
                with self._lock:
                    self.coverage.indexed += 1
                    self.coverage.scanned += 1
                self._touch_progress()
                return
            if self._evaluate_from_index(entry, kind):
                return

        self._read_and_match(entry, kind)

    def _size_limit(self, kind: FileKind) -> int:
        limits = self.config.limits
        if kind is FileKind.PDF:
            return limits.pdf_max_bytes
        if kind is FileKind.TEXT:
            return limits.text_max_bytes
        if kind is FileKind.UNKNOWN:
            return limits.unknown_text_max_bytes
        if kind is FileKind.ARCHIVE:
            return limits.archive_max_bytes
        return limits.office_max_bytes

    def _extract_options(self) -> ExtractOptions:
        return ExtractOptions(
            limits=self.config.limits,
            ocr_mode="off" if self.config.mode == "fast" else self.config.pdf_ocr_mode,
            ocr_languages=self.config.ocr_languages,
            search_formula=self.config.search_formula,
            include_archives=self.config.include_archives,
            ocr_cache=self.ocr_cache,
            ocr_available=self.ocr_available,
        )

    def _indexed_and_current(self, entry: FileEntry) -> bool:
        if self.database is None:
            return False
        try:
            return not self.database.needs_update(entry, ocr_language=self.config.ocr_languages)
        except Exception as exc:
            log.warning("index lookup failed for %s: %s", entry.path, exc)
            return False

    def _evaluate_from_index(self, entry: FileEntry, kind: FileKind) -> bool:
        """Return True when the index answered this file (no file IO)."""
        if self.database is None or self.matcher is None:
            return False
        try:
            searcher = IndexSearcher(self.database, self.matcher)
            evaluated = searcher.evaluate(entry.path, entry, self._flags(entry))
        except Exception as exc:
            log.warning("index evaluation failed for %s: %s", entry.path, exc)
            return False
        with self._lock:
            self.coverage.indexed += 1
            self.coverage.scanned += 1
        if evaluated is None:
            return True
        outcome, state = evaluated
        if outcome is Outcome.ACCEPT:
            self._emit_result(entry, kind, state, from_index=True, ocr_pages=(state.ocr_hit and 1) or 0)
        self._touch_progress()
        return True

    def _flags(self, entry: FileEntry) -> RuntimeFlags:
        return RuntimeFlags(
            confirmed=normalized_key(entry.path) in self.config.confirmed,
            roots=self.config.roots,
        )

    def _read_and_match(self, entry: FileEntry, kind: FileKind) -> None:
        assert self.matcher is not None
        indexing = self.database is not None and self.config.mode != "fast"
        state = self.matcher.make_state(entry, flags=self._flags(entry))
        buffered: list[Chunk] | None = [] if indexing else None
        ocr_pages = 0
        warnings: list[Issue] = []
        truncated = False
        stopped_early = False

        def emit(chunk: Chunk) -> None:
            outcome = state.feed(chunk)
            if buffered is not None:
                buffered.append(chunk)
                return
            if outcome in (Outcome.ACCEPT, Outcome.REJECT):
                raise StopExtraction("decided")

        sink = Sink(emit, self.cancel_event)
        if self.config.search_path_names:
            try:
                self._feed_name_and_path(sink, entry)
                if buffered is None and state.finish() is Outcome.ACCEPT:
                    stopped_early = True
            except StopExtraction:
                stopped_early = True

        if not stopped_early:
            options = self._extract_options()
            attempts = 0
            while True:
                attempts += 1
                before = tempfiles.stat_signature(entry.path)
                try:
                    result = self._extract(entry, options, sink)
                except StopExtraction:
                    stopped_early = True
                    break
                ocr_pages += getattr(result, "ocr_pages", 0)
                warnings.extend(getattr(result, "warnings", []))
                truncated = bool(getattr(result, "truncated", False))
                if getattr(result, "skipped", False) and not sink.count:
                    # Nothing usable (unsupported/undecodable): report and stop.
                    if result.reason:
                        warnings.append(
                            Issue(
                                path=entry.path,
                                code="extract-skip",
                                message=result.reason,
                                severity=Severity.SKIP,
                            )
                        )
                    if buffered is not None and self.database is not None:
                        # Remember the file exists so metadata-only queries and
                        # deletion cleanup stay accurate. ``empty`` rows are
                        # re-extracted on every search, so the index can never
                        # claim "no content" for a file that was not readable.
                        self._store_index(entry, buffered, truncated, status="empty")
                    self._store_issue_list(warnings)
                    with self._lock:
                        self.coverage.scanned += 1
                    return
                after = tempfiles.stat_signature(entry.path)
                if before == after or attempts >= 2:
                    if before != after:
                        warnings.append(
                            Issue(
                                path=entry.path,
                                code="file-changed",
                                message="検索中にファイルが更新されました（結果は最後の読み込み時点）",
                                severity=Severity.WARN,
                            )
                        )
                    break
                warnings.append(
                    Issue(
                        path=entry.path,
                        code="file-changed-retry",
                        message="読み込み中にファイルが更新されたため1回だけ再試行します",
                        severity=Severity.WARN,
                    )
                )

        outcome = state.finish()
        with self._lock:
            self.coverage.scanned += 1
            if not stopped_early:
                self.coverage.read += 1
                self.coverage.ocr_pages += ocr_pages
        if buffered and self.database is not None:
            self._store_index(entry, buffered, truncated)
        self._store_issue_list(warnings)
        if outcome is Outcome.ACCEPT:
            self._emit_result(entry, kind, state, from_index=False, ocr_pages=ocr_pages)
        self._touch_progress()

    def _feed_name_and_path(self, sink: Sink, entry: FileEntry) -> None:
        sink.add(Chunk(text=entry.name, kind=ChunkKind.NAME, location="ファイル名"))
        sink.add(Chunk(text=entry.directory, kind=ChunkKind.PATH, location="フォルダ"))

    def _extract(self, entry: FileEntry, options: ExtractOptions, sink: Sink):
        ext = entry.extension
        remote = entry.source_type is SourceType.SMB
        stage = (
            self.config.stage_remote_files
            and remote
            and ext not in (".txt", ".csv", ".tsv", ".log", ".md")
        )
        if not stage:
            return extract(entry, entry.path, sink, options)
        with self._temp.staged_copy(entry.path, size=entry.size, suffix=ext) as local_path:
            return extract(entry, local_path, sink, options)

    def _store_index(
        self, entry: FileEntry, chunks: list[Chunk], truncated: bool, *, status: str = "ok"
    ) -> None:
        if self.database is None:
            return
        try:
            self.database.store_file(
                entry,
                chunks,
                ocr_language=self.config.ocr_languages,
                status=status,
                truncated=truncated,
            )
        except Exception as exc:
            log.warning("index update failed for %s: %s", entry.path, exc)

    def _cleanup_index(self, root: str, discovered: set[str], *, complete: bool) -> None:
        """Delete index rows for files that no longer exist (spec section 15)."""
        if self.database is None or not complete or self.cancel_event.is_set():
            return
        if not discovered:
            return
        try:
            known = self.database.indexed_paths_under(root)
        except Exception as exc:
            log.warning("index cleanup lookup failed: %s", exc)
            return
        missing = [path for path in known if path not in discovered]
        if missing:
            self._removed_index_rows += self.database.remove_paths(missing)

    def _emit_result(
        self,
        entry: FileEntry,
        kind: FileKind,
        state,
        *,
        from_index: bool,
        ocr_pages: int,
    ) -> None:
        result = FileResult(
            path=entry.path,
            file_kind=kind,
            size=entry.size,
            mtime_ns=entry.mtime_ns,
            source_type=entry.source_type,
            cloud_state=entry.cloud_state,
            matched_terms=state.matched_terms(),
            displays=state.displays(),
            hit_count=state.hit_count,
            evidence=state.evidence(),
            ocr_pages=ocr_pages,
            from_index=from_index,
            confirmed=normalized_key(entry.path) in self.config.confirmed,
            score=state.score(),
        )
        too_many = False
        with self._lock:
            if len(self._results) >= self.config.max_results:
                too_many = True
            else:
                self._results.append(result)
                self.coverage.hits += 1
        if too_many:
            self._add_issue(
                Issue(
                    path=entry.path,
                    code="result-cap",
                    message=f"結果が上限（{self.config.max_results:,}件）に達したため以降を表示しません",
                    severity=Severity.WARN,
                )
            )
            self.cancel_event.set()
            return
        self._push(events.ResultAdded(result=result))

    def _add_issue(self, issue: Issue) -> None:
        with self._lock:
            if issue.severity is Severity.ERROR:
                self.coverage.errors += 1
            elif issue.severity is Severity.WARN:
                self.coverage.warnings += 1
            if len(self._issues) >= self.config.max_issues:
                return
            self._issues.append(issue)
        self._push(events.IssueAdded(issue=issue))

    def _store_issue_list(self, issues: list[Issue]) -> None:
        for issue in issues:
            self._add_issue(issue)

    def _touch_progress(self, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if not force and now - self._last_progress < 0.2:
                return
            self._last_progress = now
            progress = Progress(
                phase="検索中" if not self.cancel_event.is_set() else "中断",
                discovered=self.coverage.discovered,
                scanned=self.coverage.scanned,
                hit_files=self.coverage.hits,
            )
            coverage = replace(self.coverage)
        self._push(events.Progressed(progress=progress), droppable=True)
        self._push(events.CoverageChanged(coverage=coverage), droppable=True)

    def _push(self, event: object, *, droppable: bool = False) -> None:
        """Push an event; progress events are dropped rather than blocking.

        Result/issue/finish events are back-pressured: the UI drains the queue
        on a timer, so a full queue means the consumer is behind, not gone.
        """
        if droppable:
            with suppress(queue.Full):
                self.events.put_nowait(event)
            return
        while True:
            try:
                self.events.put(event, timeout=0.5)
                return
            except queue.Full:
                if self.cancel_event.is_set():
                    with suppress(queue.Full):
                        self.events.put_nowait(event)
                    return

    def _finalize(self) -> None:
        self._temp.cleanup()
        if self.ocr_cache is not None:
            self.ocr_cache.trim()
        with self._lock:
            self.coverage.cancelled = self.cancel_event.is_set()
            self.coverage.elapsed_seconds = time.time() - (self._session_started or time.time())
            coverage = replace(self.coverage)
            results = list(self._results)
            issues = list(self._issues)
        index_coverage = (0, 0)
        if self.database is not None:
            try:
                index_coverage = (self.database.known_count(), coverage.discovered)
            except Exception:
                index_coverage = (0, 0)
        summary = Summary(
            query=self.config.query,
            mode=self.config.mode,
            started_at=self._session_started,
            finished_at=time.time(),
            coverage=coverage,
            results=results,
            issues=issues,
            total_hits=sum(result.hit_count for result in results),
            truncated=coverage.hits >= self.config.max_results,
            index_used=bool(self.database is not None and coverage.indexed),
            index_coverage=index_coverage,
        )
        self.summary = summary
        self._push(events.CoverageChanged(coverage=coverage, index_coverage=index_coverage))
        self._push(events.Finished(summary=summary))
        self._finished.set()

    def _monitor(self) -> None:
        for thread in self._threads:
            thread.join()
        self._finalize()


class _Sentinel:
    __slots__ = ()


_SENTINEL = _Sentinel()


def sort_results(results: list[FileResult], column: str, descending: bool) -> list[FileResult]:
    """Deterministic sorting for the result list (spec section 54)."""
    keymap = {
        "relevance": lambda r: r.score,
        "filename": lambda r: r.name.casefold(),
        "path": lambda r: r.path.casefold(),
        "type": lambda r: r.file_kind.value,
        "hits": lambda r: r.hit_count,
        "modified": lambda r: r.mtime_ns,
        "size": lambda r: r.size,
        "confirmed": lambda r: r.confirmed,
        "source": lambda r: r.source_type.value,
    }
    key = keymap.get(column, keymap["relevance"])
    return sorted(results, key=key, reverse=descending)
