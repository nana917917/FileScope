"""Reproduce the two user-reported v5.0.0 defects (quotes, many conditions)."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["LOCALAPPDATA"] = str(Path(tempfile.mkdtemp(prefix="fs-repro-appdata-")))

from filescope.core.query import BuilderGroups, parse_query, to_query_string  # noqa: E402
from filescope.errors import QuerySyntaxError  # noqa: E402


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def try_parse(text: str) -> str:
    try:
        node = parse_query(text)
    except QuerySyntaxError as exc:
        return f"QuerySyntaxError: {exc.message}"
    except RecursionError:
        return "RecursionError"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return f"OK -> {to_query_string(node)!r}"


def main() -> int:
    section("A1: parser behaviour for quoted input")
    for text in [
        "AAA",
        '"AAA"',
        '"耐久 試験"',
        'AAA & "耐久 試験"',
        '"AAA" & "BBB"',
        '"AAA" | "BBB"',
        '2of("AAA","BBB","CCC")',
        '"ABC-123"',
        '""',
        'AAA"',
        '"AAA',
        "“AAA”",          # full-width quotes
        '( "AAA" | "BBB" ) & "CCC"',
    ]:
        print(f"  {text!r:34} {try_parse(text)}")

    section("A2: condition builder round-trip (open with an existing query, press OK)")
    from filescope.ui.dialogs import ConditionBuilderDialog

    root = tk.Tk()
    root.withdraw()
    for original in ['AAA', 'AAA&BBB', '"耐久 試験"', 'AAA & "耐久 試験"', '2of(AAA,BBB,CCC)']:
        dialog = ConditionBuilderDialog(root, initial=original)
        dialog.all_of.delete("1.0", "end")
        dialog.all_of.insert("1.0", original)
        produced, error = dialog._build_query()
        dialog.window.destroy()
        parsed = try_parse(produced) if produced else f"(empty) {error}"
        print(f"  input {original!r:26} -> query {produced!r:44} -> {parsed}")

    section("A3: builder field containing quotes (user types a quoted part number)")
    for field_value in ['"ABC-123"', '耐久 試験', 'AAA']:
        groups = BuilderGroups(all_of=[field_value])
        query = to_query_string(groups.build())
        print(f"  field {field_value!r:14} -> query {query!r:26} -> {try_parse(query)}")

    root.destroy()

    section("B1: many conditions - parse and evaluation cost")
    for count in (10, 30, 50, 100):
        query = " & ".join(f"W{i:03d}" for i in range(count))
        start = time.perf_counter()
        node = parse_query(query)
        parse_ms = (time.perf_counter() - start) * 1000
        from filescope.core.matcher import MatchOptions, QueryMatcher
        from filescope.core.models import Chunk, ChunkKind, FileEntry

        matcher = QueryMatcher(node, MatchOptions())
        entry = FileEntry(path="C:/x.txt", size=1, mtime_ns=0, extension=".txt")
        state = matcher.make_state(entry)
        start = time.perf_counter()
        for index in range(2000):  # simulate 2000 chunks of one file
            state.feed(Chunk(text=f"line {index} W000 W001", kind=ChunkKind.LINE, location=f"{index}行"))
        state.finish()
        eval_ms = (time.perf_counter() - start) * 1000
        print(f"  {count:3} conditions: parse {parse_ms:7.2f} ms, 2000 chunks {eval_ms:8.2f} ms")

    section("B2: deeply nested parentheses")
    for depth in (10, 50, 100):
        query = "(" * depth + "AAA" + ")" * depth
        print(f"  depth {depth:3}: {try_parse(query)}")
    for depth in (200, 400):
        query = "(" * depth + "AAA" + ")" * depth
        print(f"  depth {depth:3}: {try_parse(query)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
