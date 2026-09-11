"""Shell integration: open files, reveal folders, jump to an Excel cell."""

from __future__ import annotations

import os
import subprocess
import sys

from ..logging_setup import get_logger

log = get_logger("shell")


def open_path(path: str) -> tuple[bool, str]:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True, ""
    except OSError as exc:
        log.warning("open failed path=%s error=%s", path, exc)
        return False, str(exc)


def reveal_in_explorer(path: str) -> tuple[bool, str]:
    """Select the file in Explorer (falls back to opening the folder)."""
    if os.name != "nt":
        return open_path(os.path.dirname(path))
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        return True, ""
    except OSError as exc:
        log.warning("explorer select failed: %s", exc)
        return open_path(os.path.dirname(path))


def open_excel_cell(path: str, sheet: str, cell: str) -> tuple[bool, str]:
    """Open an Excel workbook at a cell using COM; never crash the app."""
    try:
        import win32com.client as win32
    except ImportError:
        return False, "pywin32 が未導入です"
    try:
        excel = win32.gencache.EnsureDispatch("Excel.Application")
    except Exception as exc:  # COM raises a wide range of errors
        log.debug("Excel COM unavailable: %s", exc)
        return False, "Excel を起動できませんでした"
    try:
        excel.Visible = True
        book = excel.Workbooks.Open(os.path.normpath(path))
        if sheet:
            try:
                book.Worksheets(sheet).Activate()
            except Exception:
                log.debug("worksheet activate failed: %s", sheet)
        if cell and sheet:
            try:
                book.Application.Goto(book.Worksheets(sheet).Range(cell), True)
            except Exception:
                log.debug("cell goto failed: %s", cell)
        return True, ""
    except Exception as exc:
        log.warning("Excel open failed: %s", exc)
        return False, "Excel での表示に失敗しました"
