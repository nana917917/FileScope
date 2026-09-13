"""Privacy regression test (spec section 12 of the RC brief).

FileScope must never send query terms, document text, OCR output or index
contents anywhere. This is enforced statically: the shipping package may not
import a network client at all, and the only subprocess use is the local
Tesseract probe / shell integration.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PACKAGE = os.path.join(ROOT, "filescope")
FORBIDDEN = re.compile(
    r"\b(requests|httpx|urllib|socket|webbrowser|urlopen|telemetry|analytics|sentry)\b"
)
ALLOWED_SUBPROCESS_FILES = (
    "filescope/platform/shell.py",      # explorer /select, open, xdg-open
    "filescope/platform/tesseract.py",  # tesseract --version
)


def shipping_files() -> list[str]:
    found: list[str] = []
    for directory, _dirs, files in os.walk(PACKAGE):
        for name in files:
            if name.endswith(".py"):
                found.append(os.path.join(directory, name))
    return found


def test_no_network_client_is_imported() -> None:
    offenders: list[str] = []
    for path in shipping_files():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                code = line.split("#", 1)[0]  # comments may mention the word
                if FORBIDDEN.search(code):
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "network-capable code in the shipping package:\n" + "\n".join(offenders)


def test_no_url_literals() -> None:
    offenders: list[str] = []
    for path in shipping_files():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if "http://" in line or "https://" in line:
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "URL literals in the shipping package:\n" + "\n".join(offenders)


def test_subprocess_use_is_local_only() -> None:
    """Only the reviewed platform modules may spawn processes, and never a shell."""
    users: list[str] = []
    offenders: list[str] = []
    for path in shipping_files():
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        relative = os.path.relpath(path, ROOT).replace("\\", "/")
        if "subprocess." in text:
            users.append(relative)
        if "shell=True" in text:
            offenders.append(f"{relative}: shell=True")
    unexpected = sorted(set(users) - set(ALLOWED_SUBPROCESS_FILES))
    assert unexpected == [], f"subprocess used outside the reviewed modules: {unexpected}"
    assert offenders == [], "shell execution is not allowed:\n" + "\n".join(offenders)


def test_baseline_is_never_imported_by_shipping_code() -> None:
    offenders: list[str] = []
    for path in shipping_files():
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if "baseline" in text:
            offenders.append(os.path.relpath(path, ROOT))
    assert offenders == [], f"shipping code references the V4 baseline: {offenders}"


def test_log_statements_do_not_include_document_text() -> None:
    """Log calls must not interpolate a chunk/cell body variable."""
    suspicious = re.compile(r"logger?\.\w+\([^)]*\b(text|body|value|snippet|content)\b")
    offenders: list[str] = []
    for path in shipping_files():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if suspicious.search(line):
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "logging may leak document text:\n" + "\n".join(offenders)


_ = pytest
