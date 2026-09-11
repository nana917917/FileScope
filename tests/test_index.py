"""Index tests: differential updates, capacity, corruption and the
direct/index equivalence acceptance test (spec sections 14, 15, 16, 81)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core.coordinator import SearchConfig, SearchSession
from filescope.core.models import Chunk, ChunkKind, CloudState, FileEntry, SourceType
from filescope.errors import IndexUnavailable
from filescope.index.database import IndexDatabase
from tools.make_corpus import build_corpus


def entry(path: Path, extension: str = ".txt") -> FileEntry:
    stat = path.stat()
    return FileEntry(
        path=str(path),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        extension=extension,
        source_type=SourceType.LOCAL,
        cloud_state=CloudState.LOCAL,
    )


@pytest.fixture
def database(tmp_path):
    db = IndexDatabase(str(tmp_path / "index.sqlite3"), max_bytes=10 * 1024 * 1024)
    yield db
    db.close()


class TestStorage:
    def test_store_and_load(self, database, tmp_path) -> None:
        target = tmp_path / "doc.txt"
        target.write_text("AAA BBB", encoding="utf-8")
        item = entry(target)
        outcome = database.store_file(
            item,
            [
                Chunk(text="AAA", kind=ChunkKind.LINE, location="1行"),
                Chunk(text="BBB", kind=ChunkKind.LINE, location="2行"),
            ],
        )
        assert outcome.action == "added"
        stored = database.load_file(item.path)
        assert stored is not None and len(stored.chunks) == 2
        assert stored.chunks[1].location == "2行"

    def test_unchanged_file_needs_no_update(self, database, tmp_path) -> None:
        target = tmp_path / "doc.txt"
        target.write_text("AAA", encoding="utf-8")
        item = entry(target)
        database.store_file(item, [Chunk(text="AAA")])
        assert database.needs_update(item) is False

    def test_modified_file_is_detected(self, database, tmp_path) -> None:
        target = tmp_path / "doc.txt"
        target.write_text("AAA", encoding="utf-8")
        item = entry(target)
        database.store_file(item, [Chunk(text="AAA")])
        time.sleep(0.01)
        target.write_text("AAA BBB", encoding="utf-8")
        assert database.needs_update(entry(target)) is True

    def test_extractor_version_change_invalidates(self, tmp_path) -> None:
        target = tmp_path / "doc.txt"
        target.write_text("AAA", encoding="utf-8")
        item = entry(target)
        first = IndexDatabase(str(tmp_path / "a.sqlite3"), extractor_version="4.1")
        first.store_file(item, [Chunk(text="AAA")])
        assert first.needs_update(item) is False
        first.close()
        second = IndexDatabase(str(tmp_path / "a.sqlite3"), extractor_version="5.0")
        assert second.needs_update(item) is True
        second.close()

    def test_skipped_files_are_not_served_from_index(self, database, tmp_path) -> None:
        target = tmp_path / "doc.exe"
        target.write_bytes(b"\x00\x01")
        item = entry(target, ".exe")
        database.store_file(item, [], status="skipped")
        assert database.needs_update(item) is True

    def test_oversized_text_is_marked_not_truncated(self, database, tmp_path) -> None:
        target = tmp_path / "big.txt"
        target.write_text("x" * 1024, encoding="utf-8")
        item = entry(target)
        huge = [Chunk(text="y" * 60_000) for _ in range(200)]
        outcome = database.store_file(item, huge)
        assert outcome.action in ("added", "updated")
        stored = database.load_file(item.path)
        assert stored is not None
        assert stored.status == "too_large"
        assert stored.chunks == []

    def test_remove_paths(self, database, tmp_path) -> None:
        target = tmp_path / "doc.txt"
        target.write_text("AAA", encoding="utf-8")
        item = entry(target)
        database.store_file(item, [Chunk(text="AAA")])
        assert database.remove_paths([item.path]) == 1
        assert database.load_file(item.path) is None

    def test_clear(self, database, tmp_path) -> None:
        target = tmp_path / "doc.txt"
        target.write_text("AAA", encoding="utf-8")
        database.store_file(entry(target), [Chunk(text="AAA")])
        database.clear()
        assert database.known_count() == 0


class TestCapacityAndRecovery:
    def test_capacity_stops_new_rows_without_deleting(self, tmp_path) -> None:
        db = IndexDatabase(str(tmp_path / "index.sqlite3"), max_bytes=1)
        target = tmp_path / "doc.txt"
        target.write_text("AAA", encoding="utf-8")
        item = entry(target)
        db.store_file(item, [Chunk(text="AAA")])
        assert db.capacity_reached is True
        second = tmp_path / "b.txt"
        second.write_text("BBB", encoding="utf-8")
        outcome = db.store_file(entry(second), [Chunk(text="BBB")])
        assert outcome.action == "skipped"
        assert db.status().files == 1  # nothing was deleted
        db.close()

    def test_corrupt_database_raises_index_unavailable(self, tmp_path) -> None:
        path = tmp_path / "broken.sqlite3"
        path.write_bytes(b"this is not a sqlite database" * 40)
        with pytest.raises(IndexUnavailable):
            IndexDatabase(str(path))

    def test_schema_version_is_recorded(self, database) -> None:
        assert database.meta("schema_version") == "1"


def run_search(root: Path, query: str, *, database=None, mode: str = "standard", **kwargs):
    config = SearchConfig(roots=(str(root),), query=query, mode=mode, workers=3, **kwargs)
    session = SearchSession(config, database=database)
    session.start()
    assert session.wait(120), "search did not finish in time"
    return session.summary


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("equivalence-corpus")
    return build_corpus(root, with_ocr_image=False)


EQUIVALENCE_QUERIES = [
    "AAA",
    "評価",
    "耐久 試験",
    "AAA&BBB",
    "AAA,BBB",
    "2of(AAA、BBB、CCC)",
    "AAA & !電源",
    "NEAR(AAA,耐久,200)",
    '"AAA 評価"',
    "type:pdf",
    "type:excel & 耐久",
    "ext:txt,docx",
    "size:>100B",
    "name:report",
    "落下",
    "温度",
    "AAA*",
]


class TestDirectIndexEquivalence:
    """The V5 acceptance test: the same corpus must give the same file set."""

    @pytest.mark.parametrize("query", EQUIVALENCE_QUERIES)
    def test_same_results(self, corpus, tmp_path, query) -> None:
        direct = run_search(corpus.root, query, mode="full")
        direct_paths = sorted(result.path for result in direct.results)

        db = IndexDatabase(str(tmp_path / "eq-index.sqlite3"))
        try:
            # First pass builds the index, the second answers from it.
            run_search(corpus.root, "AAA", database=db, mode="standard")
            indexed = run_search(corpus.root, query, database=db, mode="standard")
        finally:
            db.close()
        indexed_paths = sorted(result.path for result in indexed.results)
        assert indexed_paths == direct_paths, f"index/direct mismatch for {query!r}"

    def test_part_number_mode_matches(self, corpus, tmp_path) -> None:
        direct = run_search(corpus.root, "AAA-BBB", mode="full", part_number_mode=True)
        db = IndexDatabase(str(tmp_path / "part.sqlite3"))
        try:
            run_search(corpus.root, "AAA", database=db, mode="standard", part_number_mode=True)
            indexed = run_search(corpus.root, "AAA-BBB", database=db, mode="standard", part_number_mode=True)
        finally:
            db.close()
        assert sorted(r.path for r in indexed.results) == sorted(r.path for r in direct.results)


class TestIndexUsage:
    def test_extraction_setting_change_reindexes(self, corpus, tmp_path) -> None:
        """Formulas/archives/OCR settings change stored text, so rows must refresh."""
        db = IndexDatabase(str(tmp_path / "settings.sqlite3"))
        try:
            run_search(corpus.root, "AAA", database=db, mode="standard", search_formula=True)
            indexed = run_search(
                corpus.root, "=SUM", database=db, mode="standard", search_formula=False
            )
            direct = run_search(corpus.root, "=SUM", mode="full", search_formula=False)
        finally:
            db.close()
        assert sorted(r.path for r in indexed.results) == sorted(r.path for r in direct.results)

    def test_path_name_option_is_consistent(self, corpus, tmp_path) -> None:
        """Turning file-name search off must behave the same through the index."""
        db = IndexDatabase(str(tmp_path / "names.sqlite3"))
        try:
            run_search(corpus.root, "AAA", database=db, mode="standard", search_path_names=True)
            indexed = run_search(
                corpus.root, "name:report", database=db, mode="standard", search_path_names=False
            )
            direct = run_search(corpus.root, "name:report", mode="full", search_path_names=False)
        finally:
            db.close()
        assert sorted(r.path for r in indexed.results) == sorted(r.path for r in direct.results)

    def test_second_search_reads_no_files(self, corpus, tmp_path) -> None:
        db = IndexDatabase(str(tmp_path / "reuse.sqlite3"))
        try:
            first = run_search(corpus.root, "AAA", database=db, mode="standard")
            second = run_search(corpus.root, "AAA", database=db, mode="standard")
        finally:
            db.close()
        assert first.coverage.read > 0
        assert second.coverage.indexed > 0
        assert sorted(r.path for r in second.results) == sorted(r.path for r in first.results)

    def test_deleted_files_leave_the_index(self, corpus, tmp_path) -> None:
        victim = corpus.root / "text_utf8.txt"
        backup = victim.read_bytes()
        db = IndexDatabase(str(tmp_path / "delete.sqlite3"))
        try:
            run_search(corpus.root, "AAA", database=db, mode="standard")
            assert db.load_file(str(victim)) is not None
            victim.unlink()
            run_search(corpus.root, "AAA", database=db, mode="standard")
            assert db.load_file(str(victim)) is None
        finally:
            victim.write_bytes(backup)
            db.close()
