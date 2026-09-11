"""Environment/self diagnostics (spec section 67)."""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field

from . import paths
from .platform import tesseract as tesseract_module


@dataclass
class Diagnostics:
    rows: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, label: str, value: object) -> None:
        self.rows.append((label, str(value)))

    def as_text(self) -> str:
        width = max((len(label) for label, _ in self.rows), default=10)
        body = "\n".join(f"{label.ljust(width)} : {value}" for label, value in self.rows)
        if self.notes:
            body += "\n\n" + "\n".join(self.notes)
        return body


def _module_status(name: str) -> str:
    try:
        module = __import__(name)
    except ImportError:
        return "未導入"
    version = getattr(module, "__version__", "")
    return f"OK {version}".strip()


def collect(*, index_path: str = "", index_size: int = 0, index_files: int = 0) -> Diagnostics:
    report = Diagnostics()
    report.add("FileScope", _version())
    report.add("Python", sys.version.split()[0])
    report.add("実行ファイル", sys.executable)
    report.add("OS", f"{platform.system()} {platform.release()} ({platform.version()})")
    report.add("SQLite", sqlite3.sqlite_version)
    report.add("FTS5", "OK" if _fts5() else "利用不可")
    report.add("FTS5 trigram", "OK" if _fts5("trigram") else "利用不可")
    for name in ("openpyxl", "xlrd", "pyxlsb", "pypdf", "docx", "pptx", "PIL", "win32com"):
        report.add(name, _module_status(name))

    status = tesseract_module.probe()
    report.add("pytesseract", _module_status("pytesseract"))
    report.add("pypdfium2", _module_status("pypdfium2"))
    report.add("Tesseract", status.executable or "見つかりません")
    report.add("Tesseract version", status.version or "-")
    report.add("OCR言語", ", ".join(status.languages) if status.languages else "-")
    report.add("OCR状態", status.message)

    temp_dir = paths.staging_dir()
    report.add("TEMP", temp_dir)
    report.add("TEMP 空き", _human(shutil.disk_usage(temp_dir).free if os.path.isdir(temp_dir) else 0))
    report.add("データフォルダ", paths.data_dir())
    report.add("ログ", paths.logs_dir())
    report.add("インデックス", index_path or paths.index_path())
    report.add("インデックス サイズ", _human(index_size))
    report.add("インデックス ファイル数", f"{index_files:,}")

    if not status.ready:
        report.notes.append("OCRは利用できませんが、通常PDF・Office・テキストの検索は利用できます。")
    return report


def _version() -> str:
    from .version import __version__

    return __version__


def _fts5(tokenizer: str = "") -> bool:
    try:
        connection = sqlite3.connect(":memory:")
    except sqlite3.Error:
        return False
    try:
        clause = f", tokenize='{tokenizer}'" if tokenizer else ""
        connection.execute(f"CREATE VIRTUAL TABLE probe USING fts5(x{clause})")
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"
