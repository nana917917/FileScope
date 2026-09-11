"""Grammar fuzz / edge tests (spec section 82).

The parser must never raise anything other than ``QuerySyntaxError`` for user
input, must not hang, and the matcher must stay bounded on hostile-ish patterns.
"""

from __future__ import annotations

import os
import random
import string
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core.matcher import MatchOptions, QueryMatcher
from filescope.core.models import Chunk, ChunkKind
from filescope.core.query import parse_query, to_query_string
from filescope.errors import QuerySyntaxError

HAND_WRITTEN = [
    "",
    "   ",
    "()",
    "(())",
    "((()))",
    "(((A)))",
    "(A",
    "A)",
    "&",
    "|",
    ",",
    "!",
    "-",
    '"',
    '""',
    '"unclosed',
    'a"b',
    "0of(A,B)",
    "99of(A)",
    "2of()",
    "2of(A,)",
    "2of((A),B)",
    "-",
    "!-",
    "A !",
    "A &",
    "& A",
    "NEAR()",
    "NEAR(A)",
    "NEAR(A,B,)",
    "NEAR(A,B,-1)",
    "NEAR(A,B,abc)",
    "neAr(A,B,3)",
    "type:",
    "type:",
    "size:..",
    "size:1MB..",
    "size:..100MB",
    "size:１MB",
    "modified:2025-13-99",
    "modified:>=0000-00-00",
    "ext:",
    "name:",
    "ocr:yes",
    "confimed:true",
    "2OF(A,B)",
    "2of（A,B）",
    "ＡＢＣ＆ＤＥＦ",
    "Ａ，Ｂ",
    "評価　電源",
    "！重要",
    "ＡＢＣ－１２３",
    "*",
    "***",
    "\\",
    "'quoted'",
    "a::b",
    "type:pdf::xlsx",
    "(((A | B) & C) | D) & !E & 2of(F,G,H)",
]


@pytest.mark.parametrize("query", HAND_WRITTEN)
def test_hand_written_edges_do_not_hang_or_crash(query: str) -> None:
    start = time.perf_counter()
    try:
        node = parse_query(query)
    except QuerySyntaxError:
        node = None
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    if node is not None:
        # Serialising back must not crash either.
        assert isinstance(to_query_string(node), str)


def test_random_fuzz_never_raises_other_errors() -> None:
    alphabet = [
        *"()&|!,;\"-*",
        *string.ascii_letters,
        *string.digits,
        *" 　（）＆，；｜！",
        "type:",
        "size:",
        "of(",
        "NEAR(",
        "modified:",
        "評価",
        "耐久",
    ]
    rng = random.Random(20260912)
    for _ in range(600):
        query = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24)))
        try:
            node = parse_query(query)
        except QuerySyntaxError:
            continue
        if node is not None:
            to_query_string(node)


def test_wildcard_bomb_stays_bounded() -> None:
    """A part query full of wildcards must not backtrack forever."""
    query = "*".join(["A"] * 40)  # far beyond the wildcard cap
    node = parse_query(query)
    matcher = QueryMatcher(node, MatchOptions(part_number_mode=True))
    state = matcher.make_state(_entry())
    payload = "A" * 20000
    start = time.perf_counter()
    state.feed(Chunk(text=payload, kind=ChunkKind.CELL, location="A1"))
    state.finish()
    assert time.perf_counter() - start < 2.0


def test_long_token_is_still_searchable() -> None:
    query = "A" * 5000
    node = parse_query(query)
    matcher = QueryMatcher(node)
    state = matcher.make_state(_entry())
    state.feed(Chunk(text="A" * 5000, kind=ChunkKind.LINE, location="1行"))
    assert state.finish().value == "accept"


def _entry():
    from filescope.core.models import FileEntry

    return FileEntry(path="C:/a.txt", size=1, mtime_ns=0, extension=".txt")
