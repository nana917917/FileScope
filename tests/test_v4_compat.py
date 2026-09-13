"""Continuous V4.1 compatibility check.

Runs the real V4.1 engine and the V5 pipeline over the same fixture corpus and
asserts that V5 never loses a file V4.1 found. Skipped when the raw baseline is
not present (it is kept in baseline/ for exactly this purpose).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.make_corpus import build_corpus
from tools.v4_v5_compare import CLASSIFICATIONS, DEFAULT_CASES, V4_PATH, compare

pytestmark = pytest.mark.skipif(
    not os.path.isfile(V4_PATH), reason="raw V4.1 baseline not available"
)


@pytest.fixture(scope="module")
def comparisons(tmp_path_factory):
    root = tmp_path_factory.mktemp("v4-compat")
    build_corpus(root, with_ocr_image=False)
    return {comparison.name: comparison for comparison in compare(str(root), DEFAULT_CASES)}


def test_no_file_is_lost_compared_to_v4(comparisons) -> None:
    """The hard requirement: V5 must not miss anything V4.1 found.

    A case may only "lose" files when the difference is recorded as an
    intentional, documented behaviour change (see CLASSIFICATIONS).
    """
    unexplained: dict[str, list[str]] = {}
    for name, comparison in comparisons.items():
        lost = comparison.v4 - comparison.v5
        if not lost:
            continue
        kind, _reason = CLASSIFICATIONS.get(name, ("UNCLASSIFIED", ""))
        if kind != "intentional change":
            unexplained[name] = comparison.only("v4")
    assert unexplained == {}, f"V5 lost files that V4.1 matched: {unexplained}"


def test_exclusion_difference_is_correct_not_lost(comparisons, tmp_path) -> None:
    """The only case where V5 matches fewer files: it excludes them by content.

    V4 applied 除外 only to extracted evidence units and did not extract sheet
    names / defined names, so those files stayed. V5 excludes at file level, so
    the files it drops must actually contain the excluded term.
    """
    comparison = comparisons["exclude"]
    dropped = comparison.only("v4")
    assert dropped, "expected the documented exclusion difference"
    from pathlib import Path

    from filescope.core.models import FileEntry
    from filescope.extractors import extract
    from filescope.extractors.base import ExtractOptions, Sink

    corpus = Path(next(iter(comparison.v4))).parent
    for name in dropped:
        path = corpus / name
        entry = FileEntry(
            path=str(path),
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            extension=path.suffix.lower(),
        )
        chunks: list = []
        extract(entry, str(path), Sink(chunks.append), ExtractOptions(ocr_mode="off"))
        assert any("評価" in chunk.text for chunk in chunks), (
            f"{name} was excluded by V5 but does not contain the excluded term"
        )


def test_intentional_differences_are_the_documented_ones(comparisons) -> None:
    differing = {name for name, comparison in comparisons.items() if not comparison.same}
    assert differing == {"exclude", "japanese two chars"}, (
        "unexpected behavioural differences vs V4.1; classify them in "
        "tools/v4_v5_compare.py CLASSIFICATIONS and docs/V4_V5_REGRESSION.md"
    )


def test_file_level_and_across_pages_and_sheets(comparisons) -> None:
    """V4 and V5 must agree that separate sheets/pages still satisfy AND."""
    comparison = comparisons["file-level AND (AAA&BBB)"]
    assert comparison.same
    assert any(name.endswith("report.xlsx") for name in comparison.v5)
    assert any(name.endswith("manual_2page.pdf") for name in comparison.v5)


def test_part_number_and_case_behaviour_unchanged(comparisons) -> None:
    for name in (
        "part number hyphen",
        "part number no hyphen",
        "part number space",
        "part number no fuzzy",
        "wildcard",
        "case insensitive",
        "case sensitive",
        "NFKC width",
    ):
        assert comparisons[name].same, name


def test_encodings_and_unknown_extension(comparisons) -> None:
    for name in ("cp932 text", "utf16 text", "unknown extension", "excel formula"):
        assert comparisons[name].same, name
