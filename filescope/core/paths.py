"""Extension tables, file-kind classification and path keys.

The tables mirror v4 so the same files stay searchable.
"""

from __future__ import annotations

import os

from .models import FileKind

EXCEL_EXTS = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlsb"}
PDF_EXTS = {".pdf"}
WORD_EXTS = {".docx", ".docm"}
POWERPOINT_EXTS = {".pptx", ".pptm"}
TEXT_EXTS = {
    ".txt", ".csv", ".tsv", ".log", ".md", ".ini", ".json", ".xml",
    ".html", ".htm", ".py", ".bat", ".cmd", ".ps1", ".sql", ".yaml", ".yml",
    ".cfg", ".conf", ".properties", ".rtf",
}
ARCHIVE_EXTS = {".zip"}
OPTIONAL_ARCHIVE_EXTS = {".7z", ".rar"}

# v4 kept these out of the "unknown text" probe so binaries are never parsed.
OBVIOUS_BINARY_EXTS = {
    ".exe", ".dll", ".bin", ".iso", ".zip", ".7z", ".rar", ".tar", ".gz", ".png", ".jpg",
    ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".mp3", ".wav", ".mp4", ".mov",
    ".avi", ".db", ".sqlite", ".doc", ".ppt", ".xls.old",
}

ALL_SUPPORTED_EXTS = EXCEL_EXTS | PDF_EXTS | WORD_EXTS | POWERPOINT_EXTS | TEXT_EXTS

TEXT_PROBE_BYTES = 64 * 1024

DEFAULT_WORKERS = 4


def classify_kind(extension: str) -> FileKind:
    ext = extension.lower()
    if not ext.startswith("."):
        ext = "." + ext if ext else ""
    if ext in EXCEL_EXTS:
        return FileKind.EXCEL
    if ext in PDF_EXTS:
        return FileKind.PDF
    if ext in WORD_EXTS:
        return FileKind.WORD
    if ext in POWERPOINT_EXTS:
        return FileKind.POWERPOINT
    if ext in TEXT_EXTS:
        return FileKind.TEXT
    if ext in ARCHIVE_EXTS or ext in OPTIONAL_ARCHIVE_EXTS:
        return FileKind.ARCHIVE
    return FileKind.UNKNOWN


def file_kind_for_meta_value(extension: str) -> str:
    return classify_kind(extension).value.lower()


def normalized_key(path: str) -> str:
    """Stable cache key: absolute, case-folded on Windows, no trailing slash."""
    try:
        absolute = os.path.abspath(path)
    except (OSError, ValueError):
        absolute = path
    return os.path.normcase(os.path.normpath(absolute))


def display_path(path: str) -> str:
    try:
        return os.path.normpath(os.path.abspath(path))
    except (OSError, ValueError):
        return path


def extension_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def is_under(path: str, root: str) -> bool:
    path_key = normalized_key(path)
    root_key = normalized_key(root).rstrip("\\/")
    if not root_key:
        return False
    return path_key == root_key or path_key.startswith(root_key + os.sep)
