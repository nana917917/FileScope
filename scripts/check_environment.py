"""Print (and optionally verify) everything FileScope needs at runtime."""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filescope import diagnostics  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-ocr", action="store_true", help="exit non-zero when OCR is unavailable")
    parser.add_argument("--require-index", action="store_true", help="exit non-zero when FTS5 is unavailable")
    args = parser.parse_args(argv)

    report = diagnostics.collect()
    print(report.as_text())
    data = dict(report.rows)
    if args.require_ocr and not data.get("OCR状態", "").startswith("OCR利用可"):
        print("\nOCR is not available.", file=sys.stderr)
        return 1
    if args.require_index and data.get("FTS5") != "OK":
        print("\nSQLite FTS5 is not available.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
