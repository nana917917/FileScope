"""Measure where a headless FileScope search spends its time.

Separates the stages the v5.1 brief asks about (enumeration, index lookup,
extraction, matching, result production) instead of only reporting the wall
clock of a whole search. ``--root`` selects which checkout to import, so the
same harness can be pointed at an older commit. Run with the project venv:

    .venv\\Scripts\\python.exe scripts\\perf_stage.py --corpus <dir> --appdata <dir> --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(os.path.dirname(HERE))


def _redirect(appdata: str, temp: str) -> None:
    os.makedirs(appdata, exist_ok=True)
    os.makedirs(temp, exist_ok=True)
    os.environ["LOCALAPPDATA"] = appdata
    os.environ["TEMP"] = temp
    os.environ["TMP"] = temp


class Counters:
    """Per-stage elapsed time and call counts, filled by monkeypatched wrappers."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    def add(self, name: str, seconds: float) -> None:
        with self._lock:
            slot = self.data.setdefault(name, {"seconds": 0.0, "calls": 0})
            slot["seconds"] += seconds
            slot["calls"] += 1

    def wrap(self, module, attr: str, name: str) -> None:
        original = getattr(module, attr)

        def timed(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.add(name, time.perf_counter() - start)

        setattr(module, attr, timed)


def _install(counters: Counters) -> None:
    from filescope.core import coordinator
    from filescope.index import database as database_module
    from filescope.index import search as search_module

    counters.wrap(coordinator, "extract", "extract")
    counters.wrap(coordinator, "extractor_for", "extractor_for")
    counters.wrap(coordinator, "classify_kind", "classify_kind")
    counters.wrap(database_module.IndexDatabase, "needs_update", "index.needs_update")
    counters.wrap(database_module.IndexDatabase, "store_file", "index.store_file")
    counters.wrap(database_module.IndexDatabase, "file_row", "index.file_row")
    counters.wrap(database_module.IndexDatabase, "indexed_paths_under", "index.indexed_paths_under")
    counters.wrap(database_module.IndexDatabase, "known_count", "index.known_count")
    counters.wrap(search_module.IndexSearcher, "candidate_files", "index.candidate_files")
    counters.wrap(search_module.IndexSearcher, "evaluate", "index.evaluate")
    counters.wrap(coordinator.SearchSession, "_prepare_index_candidates", "session.prepare_candidates")
    counters.wrap(coordinator.SearchSession, "_read_and_match_inner", "session.read_and_match")
    counters.wrap(coordinator.SearchSession, "_emit_result", "session.emit_result")
    counters.wrap(coordinator.SearchSession, "_store_index", "session.store_index")
    counters.wrap(coordinator.SearchSession, "_touch_progress", "session.touch_progress")


class ProcessMemory:
    """Peak working set for this process (Windows; 0 elsewhere)."""

    @staticmethod
    def peak_bytes() -> int:
        if os.name != "nt":
            return 0
        try:
            import ctypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counts = Counters()
            counts.cb = ctypes.sizeof(counts)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            function = ctypes.windll.psapi.GetProcessMemoryInfo
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
            function.restype = ctypes.c_int
            if function(process, ctypes.byref(counts), counts.cb):
                return int(counts.PeakWorkingSetSize)
        except (AttributeError, OSError):
            return 0
        return 0


def enumerate_only(root: str, repeats: int = 3) -> dict:
    from filescope.core.scanner import iter_entries

    best = None
    count = 0
    for _ in range(repeats):
        start = time.perf_counter()
        count = 0
        for item in iter_entries(root):
            if hasattr(item, "path"):
                count += 1
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return {"files": count, "seconds": round(best or 0.0, 4)}


def run_search(
    root: str,
    query: str,
    *,
    mode: str,
    database,
    ocr_cache,
    workers: int = 4,
    cancel_after: float = 0.0,
) -> dict:
    from filescope.core.coordinator import SearchConfig, SearchSession

    config = SearchConfig(
        roots=(root,),
        query=query,
        mode=mode,
        workers=workers,
        index_enabled=database is not None,
        include_unknown_text=True,
    )
    session = SearchSession(config, database=database, ocr_cache=ocr_cache, ocr_available=False)
    started = time.perf_counter()
    marks: dict[str, float] = {}
    cancel_at = started + cancel_after if cancel_after else 0.0
    cancelled_at = 0.0
    cancel_issued = 0.0

    def drain() -> None:
        nonlocal cancelled_at, cancel_issued
        while not session.finished or not session.events.empty():
            try:
                event = session.events.get(timeout=0.02)
            except Exception:
                continue
            kind = type(event).__name__
            if kind == "ResultAdded" and "first_result" not in marks:
                marks["first_result"] = time.perf_counter() - started
            if cancel_at and cancel_issued == 0.0 and time.perf_counter() >= cancel_at:
                cancel_issued = time.perf_counter()
                session.cancel()
            if kind == "Finished":
                marks["finished_event"] = time.perf_counter() - started
                cancelled_at = time.perf_counter()

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    session.start()
    if cancel_after:
        finished = session.wait(30)
        if not finished:
            session.cancel()
            finished = session.wait(30)
        total = (cancelled_at - started) if cancelled_at else 0.0
        cancel_latency = (cancelled_at - cancel_issued) if cancelled_at and cancel_issued else None
    else:
        finished = session.wait(1800)
        total = time.perf_counter() - started
        if finished:
            thread.join(30)
        cancel_latency = None
    if not finished:
        session.cancel()
        session.wait(30)
    result = {
        "query": query,
        "mode": mode,
        "index": database is not None,
        "seconds": round(marks.get("finished_event", total), 4),
        "first_result_seconds": round(marks.get("first_result", -1), 4),
        "hits": len(session.results),
        "coverage": session.coverage.line() if session.coverage else "",
        "files_discovered": session.coverage.discovered,
        "files_scanned": session.coverage.scanned,
        "indexed_files": session.coverage.indexed,
        "read_files": session.coverage.read,
        "peak_rss_bytes": ProcessMemory.peak_bytes(),
    }
    if cancel_after:
        result["cancel_latency_seconds"] = (
            round(cancel_latency, 4) if cancel_latency is not None else None
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--appdata", required=True)
    parser.add_argument("--temp", default="")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", default="")
    parser.add_argument("--scenarios", default="direct,first,repeat,combined,fast,cancel")
    args = parser.parse_args()
    if args.root not in sys.path:
        sys.path.insert(0, args.root)
    temp = args.temp or os.path.join(os.path.dirname(args.appdata), "temp")
    _redirect(args.appdata, temp)

    counters = Counters()
    _install(counters)

    from filescope.index.database import IndexDatabase
    from filescope.ocr_cache import OcrCache

    wanted = {name.strip() for name in args.scenarios.split(",") if name.strip()}
    report: dict[str, object] = {
        "corpus": args.corpus,
        "enumeration": enumerate_only(args.corpus),
        "runs": {},
    }
    runs: dict[str, object] = report["runs"]  # type: ignore[assignment]
    report["root"] = args.root
    database = IndexDatabase(
        os.path.join(args.appdata, "FileScope", "index", "filescope-index.sqlite3")
    )
    ocr_cache = OcrCache(os.path.join(args.appdata, "FileScope", "index", "ocr-cache.sqlite3"))
    query = "評価"
    combined = "((評価)) & ((BBB 電源))"
    workers = args.workers

    def run(label: str, target: str, **kwargs) -> None:
        samples = []
        for _ in range(args.repeat):
            samples.append(
                run_search(args.corpus, target, workers=workers, **kwargs)
            )
        entry: dict[str, object] = samples[-1]
        if args.repeat > 1:
            entry["samples"] = [sample["seconds"] for sample in samples]
            entry["seconds"] = round(statistics.median([s["seconds"] for s in samples]), 4)
        runs[label] = entry

    try:
        if "direct" in wanted:
            run("direct", query, mode="standard", database=None, ocr_cache=None)
        if "fast_noidx" in wanted:
            run("fast_noidx", query, mode="fast", database=None, ocr_cache=None)
        if "first" in wanted:
            run("standard_first", query, mode="standard", database=database, ocr_cache=ocr_cache)
        if "repeat" in wanted:
            run("standard_repeat", query, mode="standard", database=database, ocr_cache=ocr_cache)
        if "minimal" in wanted:
            run("standard_minimal", "BBB", mode="standard", database=database, ocr_cache=ocr_cache)
        if "combined" in wanted:
            run("combined", combined, mode="standard", database=database, ocr_cache=ocr_cache)
        if "fast" in wanted:
            run("fast", query, mode="fast", database=database, ocr_cache=ocr_cache)
        if "cancel" in wanted:
            run(
                "cancel",
                query,
                mode="standard",
                database=None,
                ocr_cache=None,
                cancel_after=0.05,
            )
        if "cancel_index" in wanted:
            run(
                "cancel_index",
                query,
                mode="standard",
                database=database,
                ocr_cache=ocr_cache,
                cancel_after=0.05,
            )
    finally:
        report["stages"] = counters.data
        database.close()
        ocr_cache.close()

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
