"""Decode a FileScope self-extracting (gzip+base64) wrapper back to plain source.

The wrapper never runs its payload here: the string literal is read with ``ast``
and decoded directly, so an unknown payload cannot execute during restoration.

Usage:
    python scripts/restore_payload.py [wrapper] [output]
"""

from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import sys
from pathlib import Path


class RestoreError(Exception):
    pass


def extract_payload_literal(wrapper_source: str) -> str:
    """Return the concatenated value of the module-level ``_PAYLOAD`` literal."""
    tree = ast.parse(wrapper_source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "_PAYLOAD" not in names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError as exc:  # pragma: no cover - malformed wrapper
            raise RestoreError(f"_PAYLOAD is not a constant literal: {exc}") from exc
        if not isinstance(value, str):
            raise RestoreError(f"_PAYLOAD must be str, got {type(value).__name__}")
        return value
    raise RestoreError("no module-level _PAYLOAD assignment found")


def decode_payload(payload: str) -> bytes:
    raw = base64.b64decode(payload, validate=False)
    return gzip.decompress(raw)


def check_only_exec_payload(wrapper_source: str) -> None:
    """Reject wrappers that do more than decode+exec their own payload."""
    tree = ast.parse(wrapper_source)
    allowed_imports = {"base64", "gzip"}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {a.name.split(".")[0] for a in node.names}
            if not names <= allowed_imports:
                raise RestoreError(f"unexpected import in wrapper: {sorted(names)}")
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_PAYLOAD" not in names:
                raise RestoreError("unexpected assignment in wrapper")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if not (isinstance(func, ast.Name) and func.id == "exec"):
                raise RestoreError("unexpected top-level call in wrapper")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring
        else:
            raise RestoreError(f"unexpected statement in wrapper: {type(node).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wrapper", nargs="?", default="baseline/FileScope_v4_1_wrapper.py.txt")
    parser.add_argument("output", nargs="?", default="baseline/FileScope_v4_1.py")
    parser.add_argument(
        "--allow-foreign-statements",
        action="store_true",
        help="skip the check that the wrapper only decodes and execs its payload",
    )
    args = parser.parse_args(argv)

    wrapper_path = Path(args.wrapper)
    source = wrapper_path.read_text(encoding="utf-8")
    if not args.allow_foreign_statements:
        check_only_exec_payload(source)
    payload = extract_payload_literal(source)
    decoded = decode_payload(payload)
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RestoreError(f"payload is not UTF-8: {exc}") from exc

    compile(text, str(wrapper_path), "exec")  # syntax check, does not execute

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrapper : {wrapper_path} ({len(source)} chars)")
    print(f"output  : {out_path}")
    print(f"lines   : {text.count(chr(10)) + 1}")
    print(f"sha256  : {hashlib.sha256(decoded).hexdigest()}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RestoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
