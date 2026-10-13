from __future__ import annotations

from filescope.config import Settings
from filescope.core.query import equivalent, parse_query
from filescope.ui.state import UiState


def test_search_two_is_an_and_search_over_the_same_scope() -> None:
    state = UiState(query="(耐久 | 温度)", refine_text="type:pdf | ext:xlsx", exclude_query="旧版")

    actual = parse_query(state.to_config(Settings()).query)
    expected = parse_query("((耐久 | 温度) & (type:pdf | ext:xlsx)) & !旧版")

    assert equivalent(actual, expected)


def test_search_two_without_search_one_becomes_the_primary_search() -> None:
    state = UiState(query="", refine_text='"耐久 試験"')

    assert state.to_config(Settings()).query == '"耐久 試験"'


def test_search_one_without_search_two_preserves_legacy_query() -> None:
    state = UiState(query="AAA BBB", legacy_operator="OR")

    assert equivalent(
        parse_query(state.to_config(Settings()).query, legacy_operator=state.legacy_operator),
        parse_query("AAA BBB", legacy_operator="OR"),
    )


def test_refinement_is_kept_in_saved_search_preset() -> None:
    from filescope.ui.state import preset_from_state

    state = UiState(query="AAA", refine_text="BBB")

    assert preset_from_state(state, "sample")["refine_text"] == "BBB"
