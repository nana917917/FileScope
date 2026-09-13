"""Matcher regression tests, including the file-level AND behaviour that v4
guaranteed across sheets and pages (spec section 5)."""

from __future__ import annotations

from filescope.core.matcher import MatchOptions, Outcome, QueryMatcher, RuntimeFlags
from filescope.core.models import Chunk, ChunkKind, CloudState, FileEntry, SourceType
from filescope.core.query import parse_query


def entry(extension: str = ".xlsx", size: int = 1024, mtime_ns: int = 0) -> FileEntry:
    return FileEntry(
        path=f"C:/docs/sample{extension}",
        size=size,
        mtime_ns=mtime_ns,
        extension=extension,
        source_type=SourceType.LOCAL,
        cloud_state=CloudState.LOCAL,
    )


def evaluate(
    query: str,
    chunks: list[tuple[str, ChunkKind, str]],
    *,
    mode: str = "OR",
    part_number: bool = False,
    file_entry: FileEntry | None = None,
    flags: RuntimeFlags | None = None,
):
    node = parse_query(query, legacy_operator=mode)
    matcher = QueryMatcher(node, MatchOptions(part_number_mode=part_number))
    state = matcher.make_state(file_entry or entry(), flags=flags)
    for text, kind, location in chunks:
        outcome = state.feed(Chunk(text=text, kind=kind, location=location))
        if outcome is Outcome.ACCEPT:
            return outcome, state
        if outcome is Outcome.REJECT:
            return outcome, state
    return state.finish(), state


class TestFileLevelAnd:
    def test_sheets_are_combined(self) -> None:
        outcome, _ = evaluate(
            "AAA&BBB",
            [("AAA", ChunkKind.CELL, "Sheet1!A1"), ("BBB", ChunkKind.CELL, "Sheet8!D52")],
        )
        assert outcome is Outcome.ACCEPT

    def test_pages_are_combined(self) -> None:
        outcome, _ = evaluate(
            "AAA&BBB",
            [("AAA", ChunkKind.PAGE, "2ページ"), ("BBB", ChunkKind.PAGE, "85ページ")],
        )
        assert outcome is Outcome.ACCEPT

    def test_slides_and_paragraphs_are_combined(self) -> None:
        outcome, _ = evaluate(
            "AAA&BBB",
            [("AAA", ChunkKind.SLIDE, "スライド1"), ("BBB", ChunkKind.PARAGRAPH, "段落12")],
        )
        assert outcome is Outcome.ACCEPT

    def test_missing_term_rejects(self) -> None:
        outcome, _ = evaluate("AAA&BBB", [("AAA", ChunkKind.CELL, "Sheet1!A1")])
        assert outcome is Outcome.REJECT

    def test_early_accept_stops_reading(self) -> None:
        outcome, state = evaluate(
            "AAA&BBB",
            [("AAA", ChunkKind.CELL, "S1!A1"), ("BBB", ChunkKind.CELL, "S1!B2")],
        )
        assert outcome is Outcome.ACCEPT
        assert state.units_scanned == 2


class TestOperatorSemantics:
    def test_or(self) -> None:
        assert evaluate("AAA,BBB", [("BBB", ChunkKind.LINE, "1")])[0] is Outcome.ACCEPT

    def test_and_requires_both(self) -> None:
        assert evaluate("AAA&BBB", [("BBB", ChunkKind.LINE, "1")])[0] is Outcome.REJECT

    def test_n_of_m(self) -> None:
        assert evaluate("2of(A,B,C)", [("A", ChunkKind.LINE, "1"), ("C", ChunkKind.LINE, "2")])[0] is Outcome.ACCEPT
        assert evaluate("2of(A,B,C)", [("A", ChunkKind.LINE, "1")])[0] is Outcome.REJECT

    def test_not_requires_end_of_file(self) -> None:
        outcome, _ = evaluate("A & !B", [("A", ChunkKind.LINE, "1"), ("B", ChunkKind.LINE, "2")])
        assert outcome is Outcome.REJECT
        outcome, _ = evaluate("A & !B", [("A", ChunkKind.LINE, "1")])
        assert outcome is Outcome.ACCEPT

    def test_not_never_reports_early(self) -> None:
        outcome, state = evaluate("A & !B", [("A", ChunkKind.LINE, "1")])
        assert outcome is Outcome.ACCEPT
        assert state.units_scanned == 1

    def test_phrase_requires_exact_text(self) -> None:
        assert evaluate('"耐久 試験"', [("耐久 試験", ChunkKind.PARAGRAPH, "段落1")])[0] is Outcome.ACCEPT
        assert evaluate('"耐久 試験"', [("耐久を実施", ChunkKind.PARAGRAPH, "段落1")])[0] is Outcome.REJECT

    def test_near_within_distance(self) -> None:
        assert evaluate("NEAR(電源,ノイズ,10)", [("電源とノイズ", ChunkKind.LINE, "1")])[0] is Outcome.ACCEPT
        assert evaluate("NEAR(電源,ノイズ,2)", [("電源とノイズ", ChunkKind.LINE, "1")])[0] is Outcome.REJECT

    def test_near_needs_same_unit(self) -> None:
        outcome, _ = evaluate(
            "NEAR(電源,ノイズ,100)",
            [("電源", ChunkKind.LINE, "1"), ("ノイズ", ChunkKind.LINE, "2")],
        )
        assert outcome is Outcome.REJECT


class TestPartNumbers:
    def test_hyphen_and_width_are_absorbed(self) -> None:
        for text in ("ABC-123", "ABC123", "ＡＢＣ－１２３", "ABC 123"):
            assert evaluate("ABC-123", [(text, ChunkKind.CELL, "A1")], part_number=True)[0] is Outcome.ACCEPT

    def test_no_fuzzy_matching(self) -> None:
        assert evaluate("2SC4117", [("2SC411T", ChunkKind.CELL, "A1")], part_number=True)[0] is Outcome.REJECT

    def test_wildcard(self) -> None:
        assert evaluate("ABC*123", [("ABC-999-123", ChunkKind.CELL, "A1")], part_number=True)[0] is Outcome.ACCEPT
        assert evaluate("ABC*123", [("ABC-999", ChunkKind.CELL, "A1")], part_number=True)[0] is Outcome.REJECT


class TestMetadataFilters:
    def test_type_filter(self) -> None:
        assert evaluate("type:pdf", [("x", ChunkKind.PAGE, "1")], file_entry=entry(".pdf"))[0] is Outcome.ACCEPT
        assert evaluate("type:pdf", [("x", ChunkKind.PAGE, "1")], file_entry=entry(".xlsx"))[0] is Outcome.REJECT

    def test_size_filter(self) -> None:
        node = parse_query("size:<1MB")
        matcher = QueryMatcher(node)
        small = matcher.make_state(entry(size=1024))
        assert small.finish() is Outcome.ACCEPT
        big = matcher.make_state(entry(size=5 * 1024 * 1024))
        assert big.finish() is Outcome.REJECT

    def test_confirmed_filter(self) -> None:
        assert evaluate("confirmed:true", [("x", ChunkKind.LINE, "1")], flags=RuntimeFlags(confirmed=True))[0] is Outcome.ACCEPT
        assert evaluate("confirmed:true", [("x", ChunkKind.LINE, "1")], flags=RuntimeFlags(confirmed=False))[0] is Outcome.REJECT

    def test_ocr_filter(self) -> None:
        assert evaluate("ocr:true", [("x", ChunkKind.OCR, "1ページ")])[0] is Outcome.ACCEPT
        assert evaluate("ocr:true", [("x", ChunkKind.PAGE, "1ページ")])[0] is Outcome.REJECT

    def test_name_and_path_filters(self) -> None:
        file_entry = FileEntry(
            path="C:/docs/耐久試験/評価シート.xlsx",
            size=10,
            mtime_ns=0,
            extension=".xlsx",
        )
        assert evaluate("name:評価", [("x", ChunkKind.NAME, "評価シート.xlsx")], file_entry=file_entry)[0] is Outcome.ACCEPT
        assert evaluate("path:耐久", [("x", ChunkKind.PATH, "耐久試験")], file_entry=file_entry)[0] is Outcome.ACCEPT


class TestEvidenceAndScoring:
    def test_evidence_is_limited_per_term(self) -> None:
        chunks = [("AAA", ChunkKind.LINE, f"行{i}") for i in range(10)]
        # ``!ZZZ`` forces a full read, so evidence collection is not cut short
        # by the JIT early-accept.
        _, state = evaluate("AAA & !ZZZ", chunks)
        assert len(state.evidence()) == 3

    def test_hit_count_sums_occurrences(self) -> None:
        _, state = evaluate("AAA", [("AAA AAA", ChunkKind.LINE, "行1")])
        assert state.hit_count == 2

    def test_ocr_hits_are_marked(self) -> None:
        _, state = evaluate("AAA", [("AAA", ChunkKind.OCR, "12ページ")])
        assert state.ocr_hit
        assert state.evidence()[0].location.endswith("[OCR]")

    def test_score_is_deterministic_and_bounded(self) -> None:
        _, state = evaluate("AAA,BBB", [("AAA", ChunkKind.LINE, "1")])
        first = state.score()
        assert 0.0 <= first <= 1.0
        assert state.score() == first

    def test_displays_use_v4_labels(self) -> None:
        _, state = evaluate("AAA&BBB", [("AAA", ChunkKind.LINE, "1"), ("BBB", ChunkKind.LINE, "2")])
        assert state.displays() == ("AAA&BBB",)


def test_default_mode_is_or() -> None:
    assert evaluate("A B", [("A", ChunkKind.LINE, "1")])[0] is Outcome.ACCEPT
    assert evaluate("A B", [("A", ChunkKind.LINE, "1")], mode="AND")[0] is Outcome.REJECT
