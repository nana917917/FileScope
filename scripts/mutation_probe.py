"""Mutation probe: would the tests notice if critical logic broke?

Applies small, deliberate defects to the search core / index at runtime and
checks that the corresponding invariant from the test suite fails. A mutation
that nothing notices means the tests are too weak there.

    python scripts/mutation_probe.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filescope.core import matcher as matcher_module  # noqa: E402
from filescope.core.coordinator import SearchConfig, SearchSession  # noqa: E402
from filescope.core.matcher import MatchOptions, Outcome, QueryMatcher  # noqa: E402
from filescope.core.models import Chunk, ChunkKind, CloudState, FileEntry, SourceType  # noqa: E402
from filescope.core.query import parse_query  # noqa: E402
from filescope.index.database import IndexDatabase  # noqa: E402
from filescope.index.search import IndexSearcher  # noqa: E402
from tools.make_corpus import build_corpus  # noqa: E402


def entry(size: int = 1024, extension: str = ".txt") -> FileEntry:
    return FileEntry(
        path=f"C:/x/file{extension}",
        size=size,
        mtime_ns=0,
        extension=extension,
        source_type=SourceType.LOCAL,
        cloud_state=CloudState.LOCAL,
    )


def mutation_size_filter() -> bool:
    """`size:<1MB` must reject a 5MB file; make the comparison always true."""
    node = parse_query("size:<1MB")
    result = QueryMatcher(node).make_state(entry(size=5 * 1024 * 1024)).finish()
    return result is Outcome.REJECT


def mutation_not_requires_eof() -> bool:
    """`A & !B` must not accept before EOF when B appears later."""
    node = parse_query("A & !B")
    matcher = QueryMatcher(node)
    state = matcher.make_state(entry())
    first = state.feed(Chunk(text="A", kind=ChunkKind.LINE, location="1"))
    return first is not Outcome.ACCEPT


def mutation_early_accept() -> bool:
    """A file-level AND must not report a match with only one term present."""
    node = parse_query("AAA&BBB")
    state = QueryMatcher(node).make_state(entry())
    state.feed(Chunk(text="AAA only", kind=ChunkKind.LINE, location="1"))
    return state.finish() is Outcome.REJECT


def mutation_part_number_fuzzy() -> bool:
    """Part numbers must not fuzzy-match a different final character."""
    node = parse_query("2SC4117")
    state = QueryMatcher(node, MatchOptions(part_number_mode=True)).make_state(entry())
    state.feed(Chunk(text="2SC411T", kind=ChunkKind.CELL, location="A1"))
    return state.finish() is Outcome.REJECT


def mutation_index_candidates() -> bool:
    """An index search must not lose files when candidates are computed wrongly."""
    root = Path(tempfile.mkdtemp(prefix="filescope-mutation-"))
    build_corpus(root, with_ocr_image=False)
    db = IndexDatabase(str(root / "index.sqlite3"))

    def search(query: str, database=None) -> set[str]:
        session = SearchSession(
            SearchConfig(roots=(str(root),), query=query, mode="standard" if database else "full",
                         workers=2, index_enabled=database is not None),
            database=database,
        )
        import threading

        def drain() -> None:
            while not session.finished or not session.events.empty():
                try:
                    session.events.get(timeout=0.05)
                except Exception:
                    continue

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        session.start()
        session.wait(120)
        thread.join(2)
        return {os.path.basename(result.path) for result in session.summary.results}

    try:
        direct = search("AAA&BBB")
        search("AAA", db)
        indexed = search("AAA&BBB", db)
        return direct == indexed and bool(direct)
    finally:
        db.close()


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    # 1. A wrong size comparison must be caught by the size filter test.
    original_size_ok = matcher_module._size_ok
    matcher_module._size_ok = lambda meta, size: True  # mutation
    try:
        detected = not mutation_size_filter()
    finally:
        matcher_module._size_ok = original_size_ok
    checks.append(("size filter (size:<1MB must reject 5MB)", detected, "mutated comparison"))

    # 2. Removing the NOT/EOF rule must be caught.
    original_requires_eof = QueryMatcher.__init__

    def broken_init(self, root, options=None):
        original_requires_eof(self, root, options)
        self.requires_eof = False  # mutation: allow early accept for NOT queries

    QueryMatcher.__init__ = broken_init
    try:
        invariant_held = mutation_not_requires_eof()
    finally:
        QueryMatcher.__init__ = original_requires_eof
    checks.append(("NOT forces end-of-file", not invariant_held, "early accept allowed"))

    # 3. A file-level AND false positive must be caught.
    from filescope.core.matcher import And, FileMatchState

    original_evaluate = FileMatchState._evaluate

    def broken_evaluate(self, node):
        if isinstance(node, And):
            return any(self._evaluate(child) for child in node.children)  # mutation
        return original_evaluate(self, node)

    FileMatchState._evaluate = broken_evaluate
    try:
        invariant_held = mutation_early_accept()
    finally:
        FileMatchState._evaluate = original_evaluate
    checks.append(("file-level AND needs both terms", not invariant_held, "AND evaluated as OR"))

    # 4. Part-number fuzzy matching must be caught.
    from filescope.core.matcher import TermPattern

    original_matches = TermPattern.matches_text

    def broken_matches(self, hay_norm, hay_part):
        if self.part and len(self.part) >= 5 and self.part[:5] in hay_part:
            return True  # mutation: prefix "fuzzy" match
        return original_matches(self, hay_norm, hay_part)

    TermPattern.matches_text = broken_matches
    try:
        invariant_held = mutation_part_number_fuzzy()
    finally:
        TermPattern.matches_text = original_matches
    checks.append(("part number does not fuzzy match", not invariant_held, "prefix fuzzy match allowed"))

    # 5. An index that loses candidates must be caught by the equivalence test.
    original_candidates = IndexSearcher._candidate_ids
    IndexSearcher._candidate_ids = lambda self: set()  # mutation
    try:
        detected = mutation_index_candidates()
    finally:
        IndexSearcher._candidate_ids = original_candidates
    checks.append(("index/direct equivalence", not detected, "index returned no candidates"))

    failed = 0
    for name, ok, detail in checks:
        status = "DETECTED" if ok else "NOT DETECTED"
        if not ok:
            failed += 1
        print(f"{status:12}  {name}  ({detail})")
    print()
    print(f"mutations probed: {len(checks)}, undetected: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
