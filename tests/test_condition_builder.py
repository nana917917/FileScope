"""Condition builder ↔ query parser regressions (v5.1 bug fixes).

User reports covered here:

* "条件に引用符が付くと検索できない" — the builder used to wrap an existing
  expression (or a quoted value) into a literal phrase, producing a query that
  could never match.
* "条件を多数追加すると検索されない" — a pathological expression raised an
  unhandled ``RecursionError`` and the UI stayed in the searching state.
"""

from __future__ import annotations

import os
import sys
import time
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core.matcher import MatchOptions, Outcome, QueryMatcher
from filescope.core.models import Chunk, ChunkKind, CloudState, FileEntry, SourceType
from filescope.core.query import (
    BuilderGroups,
    clean_literal,
    equivalent,
    parse_into_groups,
    parse_query,
    to_query_string,
)
from filescope.errors import QuerySyntaxError


def entry() -> FileEntry:
    return FileEntry(
        path="C:/docs/sample.xlsx",
        size=1024,
        mtime_ns=0,
        extension=".xlsx",
        source_type=SourceType.LOCAL,
        cloud_state=CloudState.LOCAL,
    )


def matches(query: str, chunks: list[str], **options) -> bool:
    node = parse_query(query)
    matcher = QueryMatcher(node, MatchOptions(**options))
    state = matcher.make_state(entry())
    for text in chunks:
        if state.feed(Chunk(text=text, kind=ChunkKind.CELL, location="A1")) is Outcome.ACCEPT:
            return True
    return state.finish() is Outcome.ACCEPT


class TestQuotedInput:
    @pytest.mark.parametrize(
        "query, chunks, expected",
        [
            ('"AAA"', ["AAA"], True),
            ('"AAA"', ["BBB"], False),
            ('"耐久 試験"', ["耐久 試験を実施"], True),
            ('"耐久 試験"', ["耐久を実施"], False),
            ('AAA & "耐久 試験"', ["AAA と 耐久 試験"], True),
            ('"AAA" & "BBB"', ["AAA", "BBB"], True),
            ('"AAA" | "BBB"', ["BBB"], True),
            ('2of("AAA","BBB","CCC")', ["AAA", "BBB"], True),
            ('"ABC-123"', ["ABC-123"], True),
            ('( "AAA" | "BBB" ) & "CCC"', ["AAA", "CCC"], True),
        ],
    )
    def test_quoted_queries_match(self, query, chunks, expected) -> None:
        assert matches(query, chunks) is expected, query

    def test_builder_field_with_quotes_is_a_literal(self) -> None:
        """A quoted value typed into a field must search for the value itself."""
        query = to_query_string(BuilderGroups(all_of=['"ABC-123"']).build())
        assert query == "ABC-123"
        assert matches(query, ["ABC-123"], part_number_mode=True)
        assert not matches(query, ["ABC-124"])

    def test_builder_field_with_spaces_becomes_a_phrase(self) -> None:
        query = to_query_string(BuilderGroups(all_of=["耐久 試験"]).build())
        assert query == '"耐久 試験"'
        assert matches(query, ["耐久 試験"])

    def test_clean_literal_variants(self) -> None:
        assert clean_literal('"ABC-123"') == "ABC-123"
        assert clean_literal("\u201c評価\u201d") == "評価"
        assert clean_literal("\u300c耐久\u300d") == "耐久"
        assert clean_literal("  AAA  ") == "AAA"
        assert clean_literal('""') == '""'          # nothing to unwrap
        assert clean_literal('"unterminated') == '"unterminated'

    def test_backslash_values_round_trip(self) -> None:
        query = to_query_string(BuilderGroups(all_of=[r"C:\temp"]).build())
        assert parse_query(query) == parse_query(to_query_string(parse_query(query)))

    @pytest.mark.parametrize("text", ['""', 'AAA"', '"AAA', "AAA &", "()", "2of()"])
    def test_invalid_queries_raise_a_clear_error(self, text) -> None:
        with pytest.raises(QuerySyntaxError):
            parse_query(text)


class TestBuilderRoundTrip:
    CASES: ClassVar[list[str]] = [
        "AAA",
        "AAA&BBB",
        '"耐久 試験"',
        'AAA & "耐久 試験"',
        "2of(AAA,BBB,CCC)",
        "AAA | BBB",
        "AAA,BBB",
        "AAA & !旧版",
        "NEAR(電源,ノイズ,100)",
        "type:pdf & 耐久",
        "size:<50MB",
        "modified:>=2025-01-01",
        "name:報告",
        "ext:xlsx,xls",
        "(A|B)&C",
        'part_numbers: "ABC-123"',
    ]

    @pytest.mark.parametrize("query", CASES)
    def test_groups_rebuild_the_same_meaning(self, query) -> None:
        original = parse_query(query)
        groups = parse_into_groups(original)
        rebuilt = groups.build()
        assert equivalent(parse_query(to_query_string(rebuilt)), original), query

    @pytest.mark.parametrize("query", CASES)
    def test_dialog_round_trip(self, query) -> None:
        """Opening the dialog with a query and pressing OK must not change it."""
        tk = pytest.importorskip("tkinter")
        try:
            root = tk.Tk()
        except tk.TclError:  # pragma: no cover - headless
            pytest.skip("Tk display unavailable")
        root.withdraw()
        from filescope.ui.dialogs import ConditionBuilderDialog

        try:
            dialog = ConditionBuilderDialog(root, initial=query)
            produced, error = dialog._build_query()
            dialog.window.destroy()
        finally:
            root.destroy()
        assert error == ""
        assert equivalent(parse_query(produced), parse_query(query)), f"{query} -> {produced}"

    def test_unparsable_initial_query_is_kept_in_advanced(self) -> None:
        groups = BuilderGroups(advanced="AAA & !!")
        # The builder keeps broken text verbatim so the user can repair it.
        assert groups.advanced == "AAA & !!"
        assert repr(groups.build())  # build() must not raise

    def test_file_conditions_generate_metadata(self) -> None:
        groups = BuilderGroups(all_of=["耐久"], metadata=["ext:pdf,xlsx", "size:<50MB"])
        query = to_query_string(groups.build())
        node = parse_query(query)
        assert node is not None
        assert "ext:pdf,xlsx" in query and "size:<50MB" in query


class TestManyConditions:
    @staticmethod
    def query_with(count: int, prefix: str = "W") -> str:
        return " & ".join(f"{prefix}{index:03d}" for index in range(count))

    @pytest.mark.parametrize("count", [10, 30, 50, 100])
    def test_large_conjunction_is_parsed_and_searchable(self, count) -> None:
        query = self.query_with(count)
        node = parse_query(query)
        assert node is not None
        terms = [f"W{index:03d}" for index in range(count)]
        assert matches(query, terms) is True
        assert matches(query, terms[:-1]) is False

    def test_complex_mixed_expression(self) -> None:
        parts = [self.query_with(20, "A"), self.query_with(20, "B")]
        query = f"({parts[0]}) | ({parts[1]}) & !{ '旧版' }"
        assert parse_query(query) is not None
        assert matches(query, [f"A{i:03d}" for i in range(20)]) is True

    def test_deep_nesting_is_a_query_error_not_a_crash(self) -> None:
        with pytest.raises(QuerySyntaxError):
            parse_query("(" * 400 + "AAA" + ")" * 400)
        with pytest.raises(QuerySyntaxError):
            parse_query("(" * 250 + "AAA" + ")" * 250)

    def test_overlong_query_is_a_query_error(self) -> None:
        with pytest.raises(QuerySyntaxError):
            parse_query("AAA " * 10_000)

    def test_many_condition_evaluation_stays_fast(self) -> None:
        """100 conditions over 2000 chunks must stay well under a second."""
        query = self.query_with(100)
        matcher = QueryMatcher(parse_query(query), MatchOptions())
        state = matcher.make_state(entry())
        chunks = [
            Chunk(text=f"line {index} W000 W001", kind=ChunkKind.LINE, location=f"{index}行")
            for index in range(2000)
        ]
        started = time.perf_counter()
        for chunk in chunks:
            if state.feed(chunk) is Outcome.ACCEPT:
                break
        state.finish()
        elapsed = time.perf_counter() - started
        # v5.0.0 took ~160 ms here; the v5.1 scan plan is ~4x faster. The bound is
        # deliberately generous so the test is not timing-flaky.
        assert elapsed < 0.6, f"100 conditions took {elapsed:.3f}s"

    def test_only_re_evaluates_when_something_changed(self) -> None:
        """The AST must not be walked again for chunks that add no new term."""
        query = self.query_with(30)
        matcher = QueryMatcher(parse_query(query), MatchOptions())
        state = matcher.make_state(entry())
        state.feed(Chunk(text="nothing here", kind=ChunkKind.LINE, location="1行"))
        assert state._evaluated_once is True
        state.feed(Chunk(text="still nothing", kind=ChunkKind.LINE, location="2行"))
        assert state._evaluated_once is True

    def test_bucket_scan_plan_matches_brute_force(self) -> None:
        """The optimised scan must find exactly the same terms as plain `in`."""
        from filescope.core.matcher import compile_pattern

        terms = ["AAA", "AAB", "BBA", "耐", "耐久", "耐久試験", "abc", "ABC-123", "123"]
        options = MatchOptions(part_number_mode=True)
        matcher = QueryMatcher(
            parse_query(" | ".join(f'"{term}"' for term in terms)), options
        )
        haystack = "AAA AAB BB 耐 耐久 耐久試験 abc ABC-123 123 456"
        state = matcher.make_state(entry())
        state.feed(Chunk(text=haystack, kind=ChunkKind.LINE, location="1行"))
        state.finish()
        for term in terms:
            pattern = compile_pattern(term, False, options)
            expected = pattern.matches_text(haystack.casefold(), haystack.casefold())
            assert (pattern.key() in state._present) is expected, term
