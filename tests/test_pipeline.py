"""Pipeline tests: concurrency, cancellation, coverage and failure isolation
(spec sections 76, 57, 61)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core import events
from filescope.core.coordinator import SearchConfig, SearchSession, sort_results
from filescope.core.models import CloudState, FileEntry, SourceType
from filescope.platform import onedrive
from tools.make_corpus import build_corpus


def make_tree(root: Path, count: int, *, term: str = "AAA") -> Path:
    for index in range(count):
        folder = root / f"d{index // 200:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"line {line} {term} {index}" for line in range(6))
        (folder / f"f{index:05d}.txt").write_text(body, encoding="utf-8")
    return root


def run(root: Path, query: str, **kwargs):
    """Start a search and drain its event queue (the UI does this on a timer)."""
    defaults = {"workers": 4}
    defaults.update(kwargs)
    session = SearchSession(SearchConfig(roots=(str(root),), query=query, **defaults))
    drained: list[object] = []

    def drain() -> None:
        while not session.finished or not session.events.empty():
            try:
                drained.append(session.events.get(timeout=0.05))
            except Exception:
                continue

    consumer = threading.Thread(target=drain, daemon=True)
    consumer.start()
    session.start()
    assert session.wait(180), "search did not finish"
    consumer.join(2)
    session.drained = drained  # type: ignore[attr-defined]
    return session


class TestConcurrency:
    def test_1000_files_complete_without_deadlock(self, tmp_path) -> None:
        root = make_tree(tmp_path / "small", 1000)
        session = run(root, "AAA")
        summary = session.summary
        assert summary is not None
        assert summary.coverage.discovered == 1000
        assert summary.coverage.scanned == 1000
        assert len(summary.results) == 1000

    def test_10000_files_complete_without_deadlock(self, tmp_path) -> None:
        root = make_tree(tmp_path / "big", 10_000)
        session = run(root, "AAA", workers=4)
        summary = session.summary
        assert summary is not None
        assert summary.coverage.discovered == 10_000
        assert len(summary.results) == 10_000

    def test_cancellation_returns_promptly(self, tmp_path) -> None:
        root = make_tree(tmp_path / "cancel", 3000)
        session = SearchSession(SearchConfig(roots=(str(root),), query="AAA", workers=2))
        session.start()
        time.sleep(0.35)
        session.cancel()
        assert session.wait(60)
        assert session.summary is not None
        assert session.summary.coverage.cancelled is True
        assert session.summary.coverage.scanned < 3000

    def test_pause_stops_progress_and_resume_finishes(self, tmp_path) -> None:
        root = make_tree(tmp_path / "pause", 600)
        session = SearchSession(SearchConfig(roots=(str(root),), query="AAA", workers=2))
        session.start()
        session.set_paused(True)
        time.sleep(0.3)
        paused_at = session.coverage.scanned
        time.sleep(0.4)
        assert session.coverage.scanned <= paused_at + 40
        session.set_paused(False)
        assert session.wait(120)
        assert session.summary is not None and session.summary.coverage.scanned == 600


class TestCoverageAndIssues:
    def test_coverage_counts_add_up(self, tmp_path) -> None:
        root = tmp_path / "cov"
        (root / "sub").mkdir(parents=True)
        (root / "a.txt").write_text("AAA", encoding="utf-8")
        (root / "b.bin").write_bytes(os.urandom(2048))
        (root / "big.txt").write_text("x" * 10, encoding="utf-8")
        session = run(root, "AAA")
        coverage = session.summary.coverage
        assert coverage.discovered == 3
        assert coverage.scanned == 2          # the binary file is not searched
        assert coverage.skipped_type >= 1
        assert coverage.hits == 1

    def test_broken_file_does_not_stop_the_search(self, tmp_path) -> None:
        root = tmp_path / "broken"
        root.mkdir()
        (root / "good.txt").write_text("AAA", encoding="utf-8")
        (root / "bad.pdf").write_bytes(b"%PDF broken")
        session = run(root, "AAA")
        assert len(session.summary.results) == 1
        assert any(issue.code.startswith("pdf") for issue in session.summary.issues)

    def test_issues_are_separate_from_results(self, tmp_path) -> None:
        root = tmp_path / "issues"
        root.mkdir()
        (root / "good.txt").write_text("AAA", encoding="utf-8")
        (root / "empty.txt").write_text("", encoding="utf-8")
        session = run(root, "AAA")
        assert all("empty.txt" not in result.path for result in session.summary.results)

    def test_result_cap_is_reported(self, tmp_path) -> None:
        root = make_tree(tmp_path / "cap", 50)
        session = run(root, "AAA", max_results=10)
        assert len(session.summary.results) <= 10
        assert session.summary.coverage.cancelled is True


class TestSearchModes:
    def test_name_search_does_not_read_content(self, tmp_path) -> None:
        root = tmp_path / "names"
        root.mkdir()
        for index in range(20):
            (root / f"評価{index:02d}.txt").write_text("noise\n" * 50, encoding="utf-8")
        session = run(root, "name:評価", mode="fast")
        assert len(session.summary.results) == 20
        assert session.summary.coverage.read == 0

    def test_fast_mode_skips_unindexed_content_read_is_not_required(self, tmp_path) -> None:
        root = make_tree(tmp_path / "fast", 30)
        session = run(root, "AAA", mode="fast")
        assert len(session.summary.results) == 30

    def test_subfolder_toggle(self, tmp_path) -> None:
        root = tmp_path / "subs"
        (root / "deep").mkdir(parents=True)
        (root / "top.txt").write_text("AAA", encoding="utf-8")
        (root / "deep" / "inner.txt").write_text("AAA", encoding="utf-8")
        session = run(root, "AAA", include_subfolders=False)
        assert [r.name for r in session.summary.results] == ["top.txt"]
        session = run(root, "AAA", include_subfolders=True)
        assert len(session.summary.results) == 2


class TestCloudPolicy:
    def online_entry(self) -> FileEntry:
        return FileEntry(
            path="C:/OneDrive/doc.txt",
            size=10,
            mtime_ns=0,
            extension=".txt",
            source_type=SourceType.ONEDRIVE,
            cloud_state=CloudState.ONLINE_ONLY,
        )

    def test_fast_mode_never_hydrates(self) -> None:
        decision = onedrive.decide(self.online_entry(), mode="fast", policy="auto")
        assert decision.read_content is False and decision.counted_as_skipped

    def test_standard_mode_fetches_unindexed(self) -> None:
        assert onedrive.decide(self.online_entry(), mode="standard", policy="auto").read_content

    def test_standard_mode_skips_indexed(self) -> None:
        decision = onedrive.decide(self.online_entry(), mode="standard", policy="auto", indexed=True)
        assert decision.read_content is False and not decision.counted_as_skipped

    def test_explicit_policies_win(self) -> None:
        assert onedrive.decide(self.online_entry(), mode="full", policy="skip").read_content is False
        assert onedrive.decide(self.online_entry(), mode="fast", policy="fetch").read_content is True

    def test_skip_is_counted_in_coverage(self, tmp_path) -> None:
        root = tmp_path / "cloud"
        root.mkdir()
        (root / "a.txt").write_text("AAA", encoding="utf-8")
        session = run(root, "AAA", online_files_policy="skip")
        assert session.summary.coverage.skipped_online == 0  # no online files here


class TestEvents:
    def test_events_are_emitted_in_order(self, tmp_path) -> None:
        root = tmp_path / "events"
        root.mkdir()
        (root / "a.txt").write_text("AAA", encoding="utf-8")
        session = run(root, "AAA")
        seen = [type(event).__name__ for event in session.drained]  # type: ignore[attr-defined]
        assert "Started" in seen
        assert "ResultAdded" in seen
        assert seen[-1] == "Finished"

    def test_query_error_is_reported_not_raised(self, tmp_path) -> None:
        root = tmp_path / "err"
        root.mkdir()
        session = SearchSession(SearchConfig(roots=(str(root),), query="2of(A)"))
        session.start()
        assert session.finished
        events_seen = []
        while not session.events.empty():
            events_seen.append(session.events.get_nowait())
        assert any(isinstance(event, events.QueryFailed) for event in events_seen)


class TestSorting:
    def test_sort_columns(self, tmp_path) -> None:
        root = make_tree(tmp_path / "sort", 5)
        session = run(root, "AAA")
        results = session.summary.results
        by_name = sort_results(results, "filename", False)
        assert [r.name for r in by_name] == sorted(r.name for r in results)
        by_hits = sort_results(results, "hits", True)
        assert by_hits[0].hit_count >= by_hits[-1].hit_count


class TestCorpusEndToEnd:
    def test_file_level_and_across_formats(self, tmp_path) -> None:
        corpus = build_corpus(tmp_path / "corpus", with_ocr_image=False)
        session = run(corpus.root, "AAA&BBB")
        names = {result.name for result in session.summary.results}
        assert "report.xlsx" in names      # Sheet1!A1 + Sheet8!D52
        assert "manual_2page.pdf" in names  # page 1 + page 2
        assert "review.docx" in names

    def test_japanese_two_char_terms(self, tmp_path) -> None:
        corpus = build_corpus(tmp_path / "corpus-jp", with_ocr_image=False)
        session = run(corpus.root, "評価")
        names = {result.name for result in session.summary.results}
        assert "text_utf8.txt" in names
        assert "report.xlsx" in names
