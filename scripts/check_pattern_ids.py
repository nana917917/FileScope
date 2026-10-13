"""Diagnostic: can the scan plan tell every compiled pattern apart?

The per-chunk scan plan dedups patterns by ``TermPattern.pattern_id``, so two
patterns sharing an id make one of them invisible in every chunk. v5.1.0 built
the id from object addresses and collided often (144 of 400 patterns in this
very check); v5.1.1 uses a counter.

    .venv\\Scripts\\python.exe scripts\\check_pattern_ids.py [checkout-root]

Prints the duplicate count and, when a collision exists, a query whose term is
dropped. Exit code is 1 when duplicates are found, so it can gate a check.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else ROOT)

from filescope.core.matcher import MatchOptions, QueryMatcher  # noqa: E402
from filescope.core.models import Chunk, ChunkKind, FileEntry  # noqa: E402
from filescope.core.query import parse_query  # noqa: E402

entry = FileEntry(path=r"C:\x.txt", size=1, mtime_ns=0, extension=".txt")
seen: dict[int, str] = {}
total = 0
duplicates: list[tuple[str, str]] = []

for index in range(200):
    query = f"2of(_A{index:04d}B, _A{index + 1:04d}B)"
    matcher = QueryMatcher(parse_query(query), MatchOptions(part_number_mode=True))
    for pattern in matcher._patterns.values():
        total += 1
        if pattern.pattern_id in seen:
            duplicates.append((pattern.text, seen[pattern.pattern_id]))
        else:
            seen[pattern.pattern_id] = pattern.text

print(f"compiled patterns : {total}")
print(f"duplicate ids     : {len(duplicates)}")
if duplicates:
    print(f"example           : {duplicates[0][0]} == {duplicates[0][1]}")

demonstrated = False
for index in range(4000):
    query = f"2of(_A{index:04d}B, _A{index + 1:04d}B)"
    matcher = QueryMatcher(parse_query(query), MatchOptions(part_number_mode=True))
    patterns = list(matcher._patterns.values())
    ids = [pattern.pattern_id for pattern in patterns]
    if len(set(ids)) == len(ids):
        continue
    state = matcher.make_state(entry)  # builds the scan plan
    for left in range(len(patterns)):
        for right in range(left + 1, len(patterns)):
            first, second = patterns[left], patterns[right]
            if first.pattern_id != second.pattern_id:
                continue
            if first.pattern_id not in matcher._multi_bucket_ids:
                continue
            text = f"{first.text} {second.text}"
            state = matcher.make_state(entry)
            state.feed(Chunk(text=text, kind=ChunkKind.LINE, location="1行"))
            print(f"collision in {query}: text '{text}'")
            print(f"  expected terms  : ({first.text}, {second.text})")
            print(f"  matched terms   : {state.matched_terms()}")
            demonstrated = True
            break
        if demonstrated:
            break
    if demonstrated:
        break

if not demonstrated:
    print("no term-dropping collision found in this run")

raise SystemExit(1 if duplicates else 0)
