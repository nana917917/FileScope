"""Tesseract discovery and capability reporting (spec sections 28-33)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from ..logging_setup import get_logger
from ..paths import app_dir

log = get_logger("ocr")


@dataclass(frozen=True)
class OcrStatus:
    ready: bool
    executable: str = ""
    languages: tuple[str, ...] = ()
    version: str = ""
    message: str = ""

    @property
    def language_expression(self) -> str:
        if "jpn" in self.languages and "eng" in self.languages:
            return "jpn+eng"
        if "jpn" in self.languages:
            return "jpn"
        if "eng" in self.languages:
            return "eng"
        return ""


def candidate_paths() -> list[str]:
    candidates: list[str] = []
    env_cmd = _env("TESSERACT_CMD").strip()
    if env_cmd:
        candidates.append(env_cmd)
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    base = app_dir()
    program_files = _env("ProgramFiles", "PROGRAMFILES") or r"C:\Program Files"
    program_files_x86 = _env("ProgramFiles(x86)", "PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    local_app_data = _env("LOCALAPPDATA", "LocalAppData")
    candidates.extend(
        [
            os.path.join(base, "Tesseract-OCR", "tesseract.exe"),
            os.path.join(base, "tesseract", "tesseract.exe"),
            os.path.join(program_files, "Tesseract-OCR", "tesseract.exe"),
            os.path.join(program_files_x86, "Tesseract-OCR", "tesseract.exe"),
            os.path.join(local_app_data, "Programs", "Tesseract-OCR", "tesseract.exe"),
            os.path.join(local_app_data, "Tesseract-OCR", "tesseract.exe"),
        ]
    )
    return [path for path in candidates if path]


def _env(*names: str) -> str:
    """First present environment variable (Windows names are case-insensitive)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def find_executable() -> str:
    for candidate in candidate_paths():
        if os.path.isfile(candidate):
            return candidate
    return ""


def configure(executable: str) -> None:
    """Point pytesseract at ``executable`` (no-op when pytesseract is absent)."""
    if not executable:
        return
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = executable
    except ImportError:
        return


def probe() -> OcrStatus:
    """Full capability check; never raises and never blocks the UI."""
    try:
        import pytesseract
    except ImportError:
        return OcrStatus(False, message="OCR未導入: pytesseract がありません（通常PDF検索は利用できます）")

    executable = find_executable()
    if not executable:
        return OcrStatus(False, message="OCR未導入: Tesseract本体が見つかりません（通常PDF検索は利用できます）")

    pytesseract.pytesseract.tesseract_cmd = executable

    version = ""
    try:
        completed = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        version = completed.stdout.splitlines()[0].strip() if completed.stdout else ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("tesseract --version failed: %s", exc)

    try:
        languages = tuple(sorted(pytesseract.get_languages(config="")))
    except Exception as exc:  # pytesseract raises its own TesseractError
        return OcrStatus(False, executable, (), version, f"OCR確認エラー: {type(exc).__name__}")

    if "jpn" in languages and "eng" in languages:
        message = "OCR利用可: 日本語+英語"
    elif "jpn" in languages:
        message = "OCR利用可: 日本語（eng未導入）"
    elif "eng" in languages:
        message = "OCR利用可: 英語のみ（jpn未導入）"
    else:
        return OcrStatus(
            False, executable, languages, version, "OCR未導入: jpn/eng 言語データがありません"
        )
    return OcrStatus(True, executable, languages, version, message)
