"""Query parser regression tests.

Every legacy v4 form is pinned here (spec section 5) together with the new V5
operators and the error cases from spec section 82.
"""

from __future__ import annotations

import pytest

from filescope.core.query import (
    And,
    Meta,
    Near,
    NOf,
    Not,
    Or,
    Term,
    display_name,
    metadata_nodes,
    parse_query,
    terms,
    to_query_string,
)
from filescope.errors import QuerySyntaxError


def parse(text: str, mode: str = "OR"):
    node = parse_query(text, legacy_operator=mode)
    assert node is not None
    return node


class TestLegacyCompatibility:
    def test_single_term(self) -> None:
        assert parse("AAA") == Term("AAA")

    def test_space_uses_and_or_setting(self) -> None:
        assert parse("AAA BBB", "OR") == Or((Term("AAA"), Term("BBB")))
        assert parse("AAA BBB", "AND") == And((Term("AAA"), Term("BBB")))

    def test_ampersand_is_file_level_and(self) -> None:
        assert parse("AAA&BBB") == And((Term("AAA"), Term("BBB")))

    def test_comma_and_semicolon_are_or(self) -> None:
        assert parse("AAA,BBB") == Or((Term("AAA"), Term("BBB")))
        assert parse("AAA;BBB") == Or((Term("AAA"), Term("BBB")))

    def test_space_binds_tighter_than_comma(self) -> None:
        assert parse("AAA BBB,CCC DDD") == Or(
            (And((Term("AAA"), Term("BBB"))), And((Term("CCC"), Term("DDD"))))
        )
        assert parse("A,B C") == Or((Term("A"), And((Term("B"), Term("C")))))

    def test_n_of_m(self) -> None:
        node = parse("2of(AAA,BBB,CCC)")
        assert isinstance(node, NOf)
        assert node.count == 2
        assert node.children == (Term("AAA"), Term("BBB"), Term("CCC"))

    def test_n_of_m_generalised(self) -> None:
        node = parse("3of(A,B,C,D,E)")
        assert isinstance(node, NOf) and node.count == 3 and len(node.children) == 5

    def test_n_of_is_case_insensitive(self) -> None:
        assert isinstance(parse("2OF(A,B,C)"), NOf)

    def test_full_width_operators(self) -> None:
        assert parse("AAA，BBB") == Or((Term("AAA"), Term("BBB")))
        assert parse("ＡＢＣ－１２３") == Term("ＡＢＣ－１２３")
        assert parse("AAA＆BBB") == And((Term("AAA"), Term("BBB")))

    def test_japanese_operator_words(self) -> None:
        assert parse("AAA かつ BBB") == And((Term("AAA"), Term("BBB")))
        assert parse("AAA または BBB") == Or((Term("AAA"), Term("BBB")))

    def test_ideographic_space_is_a_separator(self) -> None:
        assert parse("AAA　BBB", "AND") == And((Term("AAA"), Term("BBB")))


class TestNewOperators:
    def test_pipe_or(self) -> None:
        assert parse("A | B") == Or((Term("A"), Term("B")))

    def test_negation_forms(self) -> None:
        assert parse("!A") == Not(Term("A"))
        assert parse("-A") == Not(Term("A"))

    def test_dash_stays_literal_for_part_numbers(self) -> None:
        assert parse("-123") == Term("-123")
        assert parse("ABC-123") == Term("ABC-123")
        assert parse("-ABC-123") == Term("-ABC-123")

    def test_parentheses(self) -> None:
        assert parse("(A | B) & C") == And((Or((Term("A"), Term("B"))), Term("C")))

    def test_phrase(self) -> None:
        assert parse('"耐久 試験"') == Term("耐久 試験", phrase=True)

    def test_escaped_quote_in_phrase(self) -> None:
        assert parse('"say \\"hi\\""') == Term('say "hi"', phrase=True)

    def test_near_default_distance(self) -> None:
        assert parse("NEAR(A,B)") == Near(Term("A"), Term("B"), 100)

    def test_near_explicit_distance(self) -> None:
        assert parse("NEAR(電源,ノイズ,100)") == Near(Term("電源"), Term("ノイズ"), 100)

    def test_negation_inside_group(self) -> None:
        assert parse("A & !B") == And((Term("A"), Not(Term("B"))))


class TestMetadata:
    def test_type_filter(self) -> None:
        node = parse("type:pdf")
        assert isinstance(node, Meta) and node.field == "type" and node.value == "pdf"

    def test_type_alias(self) -> None:
        assert parse("type:powerpoint").value == "ppt"  # type: ignore[union-attr]

    def test_ext_list_keeps_commas(self) -> None:
        node = parse("ext:xlsx,xls")
        assert isinstance(node, Meta) and node.value == "xlsx,xls"

    def test_size_less_than(self) -> None:
        node = parse("size:<50MB")
        assert isinstance(node, Meta) and node.op == "lt" and node.value == str(50 * 1024 * 1024)

    def test_size_range(self) -> None:
        node = parse("size:1MB..100MB")
        assert isinstance(node, Meta)
        low, _, high = node.value.partition("..")
        assert int(low) == 1024 * 1024 and int(high) == 100 * 1024 * 1024

    def test_modified_filter(self) -> None:
        node = parse("modified:>=2025-01-01")
        assert isinstance(node, Meta) and node.op == "ge" and node.value == "2025-01-01"

    def test_boolean_filters(self) -> None:
        assert parse("confirmed:false").value == "false"
        assert parse("ocr:true").value == "true"

    def test_metadata_combined_with_terms(self) -> None:
        node = parse("type:pdf & 耐久")
        assert node == And((Meta("type", "eq", "pdf"), Term("耐久")))
        assert len(metadata_nodes(node)) == 1

    def test_window_path_is_not_a_filter(self) -> None:
        assert parse(r"C:\temp") == Term(r"C:\temp")


class TestErrors:
    @pytest.mark.parametrize(
        "query",
        [
            "()",
            "(",
            ")",
            '"abc',
            "0of(A,B)",
            "2of(A)",
            "3of(A,B)",
            "NEAR(A,B,0)",
            "A !",
            "size:5XB",
            "modified:2025/13/01",
            "type:unknown",
            "source:dropbox",
            "confirmed:maybe",
            "A &",
        ],
    )
    def test_invalid_queries_raise(self, query: str) -> None:
        with pytest.raises(QuerySyntaxError):
            parse(query)

    def test_error_reports_position(self) -> None:
        with pytest.raises(QuerySyntaxError) as info:
            parse("AAA & ()")
        assert info.value.position > 0
        assert "^" in info.value.pretty("AAA & ()")

    def test_empty_query_is_none(self) -> None:
        assert parse_query("   ") is None
        assert parse_query("") is None


class TestSerialisation:
    @pytest.mark.parametrize(
        "query",
        ["AAA", "AAA & BBB", "AAA | BBB", "2of(A,B,C)", "NEAR(A,B,50)", 'type:pdf & "耐久 試験"'],
    )
    def test_roundtrip(self, query: str) -> None:
        node = parse(query)
        assert parse(to_query_string(node)) == node

    def test_display_name(self) -> None:
        assert display_name(parse("2of(A,B,C)")) == "2of(A,B,C)"
        assert display_name(parse("A & B | C")) in {"A&B|C", "(A&B)|C"}

    def test_terms_helper(self) -> None:
        node = parse("A & !B & C")
        assert [t.text for t in terms(node)] == ["A", "C"]
