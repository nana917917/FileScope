"""Excel extraction: xlsx/xlsm/xltx/xltm (openpyxl), xls (xlrd), xlsb (pyxlsb).

Cell values, formulas, comments, sheet names, defined names, hyperlinks and
chart/textbox text are all reported with a location so a hit can be traced.
"""

from __future__ import annotations

import datetime as dt
import os
from contextlib import suppress

from ..core.models import Chunk, ChunkKind, FileEntry
from ..core.paths import EXCEL_EXTS
from ..errors import Issue, Severity
from .base import ExtractContext, ExtractOptions, ExtractResult, Sink
from .ooxml import iter_parts, open_zip, xml_texts

MAX_CELLS = 2_000_000
MAX_FORMULA_PASS_BYTES = 64 * 1024 * 1024


def decimal_places_from_format(fmt: str) -> int | None:
    """v4 helper: how many decimals a number format displays."""
    if not fmt or "." not in str(fmt):
        return None
    after = str(fmt).split(".", 1)[1]
    count = 0
    for ch in after:
        if ch in ("0", "#"):
            count += 1
        else:
            break
    return min(count, 10) if count else None


def excel_cell_candidates(value: object, number_format: str = "") -> list[str]:
    """v4 behaviour: searchable representations of one cell value."""
    if value is None:
        return []
    candidates = [str(value)]
    if isinstance(value, bool):
        candidates.extend(["TRUE" if value else "FALSE", "true" if value else "false"])
    elif isinstance(value, int):
        candidates.extend([f"{value}.0", f"{value}.00", f"{value:03d}", f"{value:04d}", f"{value:05d}"])
    elif isinstance(value, float):
        if value.is_integer():
            integer = int(value)
            candidates.extend(
                [str(integer), f"{integer}.0", f"{integer}.00", f"{integer:03d}", f"{integer:04d}", f"{integer:05d}"]
            )
        decimals = decimal_places_from_format(number_format)
        if decimals is not None:
            candidates.append(f"{value:.{decimals}f}")
    elif isinstance(value, (dt.datetime, dt.date)):
        candidates.extend(
            [
                value.strftime("%Y/%m/%d"),
                value.strftime("%Y-%m-%d"),
                f"{value.year}/{value.month}/{value.day}",
            ]
        )
        if isinstance(value, dt.datetime):
            candidates.extend(
                [value.strftime("%Y/%m/%d %H:%M"), value.strftime("%Y/%m/%d %H:%M:%S")]
            )
    return list(dict.fromkeys(candidates))


class ExcelExtractor:
    name = "excel"
    version = "5.0"

    def supports(self, entry: FileEntry, options: ExtractOptions) -> bool:
        return entry.extension in EXCEL_EXTS

    def extract(self, context: ExtractContext, sink: Sink) -> ExtractResult:
        result = ExtractResult()
        limit = context.options.limits.office_max_bytes
        if context.entry.size > limit:
            result.skipped = True
            result.reason = f"Office上限（{limit // (1024 * 1024)}MB）を超えています"
            return result

        ext = context.entry.extension
        try:
            if ext == ".xls":
                self._extract_xls(context, sink, result)
            elif ext == ".xlsb":
                self._extract_xlsb(context, sink, result)
            else:
                self._extract_openpyxl(context, sink, result)
        except ImportError as exc:
            result.warnings.append(
                Issue(
                    path=context.entry.path,
                    code="excel-dependency",
                    message=f"{ext} の読み込みライブラリがありません（{exc.name}）",
                    severity=Severity.WARN,
                )
            )
            result.skipped = True
            result.reason = "ライブラリ未導入"
        if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
            self._extract_ooxml_extras(context, sink, result)
        return result

    # -------------------------------------------------------------- openpyxl
    def _extract_openpyxl(self, context: ExtractContext, sink: Sink, result: ExtractResult) -> None:
        from openpyxl import load_workbook

        path = context.path
        cells = 0
        formulas = 0
        workbook = load_workbook(path, read_only=True, data_only=not context.options.search_formula)
        try:
            for sheet in workbook.worksheets:
                sink.text(sheet.title, ChunkKind.SHEET_NAME, "シート名")
                for row in sheet.iter_rows():
                    for cell in row:
                        value = cell.value
                        if value is None:
                            continue
                        cells += 1
                        if cells > MAX_CELLS:
                            result.truncated = True
                            result.warnings.append(
                                Issue(
                                    path=context.entry.path,
                                    code="excel-too-large",
                                    message=f"セル数が{MAX_CELLS:,}を超えたため以降を省略しました",
                                    severity=Severity.WARN,
                                )
                            )
                            return
                        location = f"{sheet.title}!{cell.coordinate}"
                        text = str(value)
                        if text.startswith("="):
                            formulas += 1
                            sink.add(Chunk(text=text, kind=ChunkKind.FORMULA, location=location))
                            continue
                        sink.add(Chunk(text=text, kind=ChunkKind.CELL, location=location))
            if formulas and context.options.search_formula:
                self._emit_cached_values(path, sink, result)
        finally:
            close = getattr(workbook, "close", None)
            if callable(close):
                close()

    def _emit_cached_values(self, path: str, sink: Sink, result: ExtractResult) -> None:
        """Second pass: values Excel cached next to formulas.

        Without this, ``=A1*2`` would hide the number a user actually sees.
        """
        try:
            if os.path.getsize(path) > MAX_FORMULA_PASS_BYTES:
                return
            from openpyxl import load_workbook

            workbook = load_workbook(path, read_only=True, data_only=True)
        except Exception:
            return
        try:
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        value = cell.value
                        if value is None or (isinstance(value, str) and value.startswith("=")):
                            continue
                        sink.add(
                            Chunk(
                                text=str(value),
                                kind=ChunkKind.CELL,
                                location=f"{sheet.title}!{cell.coordinate}",
                            )
                        )
        finally:
            close = getattr(workbook, "close", None)
            if callable(close):
                close()

    # ------------------------------------------------------------------- xls
    def _extract_xls(self, context: ExtractContext, sink: Sink, result: ExtractResult) -> None:
        import xlrd

        book = xlrd.open_workbook(context.path, on_demand=True)
        try:
            for sheet in book.sheets():
                sink.text(sheet.name, ChunkKind.SHEET_NAME, "シート名")
                for row_index in range(sheet.nrows):
                    for column_index in range(sheet.ncols):
                        value = sheet.cell_value(row_index, column_index)
                        if value in (None, ""):
                            continue
                        ctype = sheet.cell_type(row_index, column_index)
                        coord = f"{_column_letter(column_index)}{row_index + 1}"
                        location = f"{sheet.name}!{coord}"
                        kind = ChunkKind.CELL
                        if ctype == xlrd.XL_CELL_DATE:
                            with suppress(ValueError, OverflowError):
                                value = dt.datetime(*xlrd.xldate_as_tuple(value, book.datemode))
                        elif ctype == xlrd.XL_CELL_BOOLEAN:
                            value = bool(value)
                        elif ctype == xlrd.XL_CELL_TEXT and str(value).startswith("="):
                            kind = ChunkKind.FORMULA
                        for candidate in excel_cell_candidates(value):
                            sink.add(Chunk(text=candidate, kind=kind, location=location))
        finally:
            release = getattr(book, "release_resources", None)
            if callable(release):
                release()

    # ------------------------------------------------------------------ xlsb
    def _extract_xlsb(self, context: ExtractContext, sink: Sink, result: ExtractResult) -> None:
        from pyxlsb import open_workbook

        with open_workbook(context.path) as book:
            for sheet_name in book.sheets:
                sink.text(sheet_name, ChunkKind.SHEET_NAME, "シート名")
                with book.get_sheet(sheet_name) as sheet:
                    for row in sheet.rows():
                        for cell in row:
                            if cell.v is None or cell.v == "":
                                continue
                            location = f"{sheet_name}!{cell.r}{cell.c + 1}"
                            for candidate in excel_cell_candidates(cell.v):
                                sink.add(Chunk(text=candidate, kind=ChunkKind.CELL, location=location))

    # --------------------------------------------------------------- extras
    def _extract_ooxml_extras(self, context: ExtractContext, sink: Sink, result: ExtractResult) -> None:
        archive = open_zip(context.path)
        if archive is None:
            return
        with archive:
            for name, data in iter_parts(
                archive,
                lambda member: (
                    (member.startswith("xl/") and "comments" in member and member.endswith(".xml"))
                    or "/threadedComments/" in member
                    or member.endswith("workbook.xml")
                ),
            ):
                if name.endswith("workbook.xml"):
                    for value in xml_texts(data, {"definedName"}):
                        sink.text(value, ChunkKind.DEFINED_NAME, "定義された名前")
                else:
                    for value in xml_texts(data, {"t"}):
                        sink.text(value, ChunkKind.CELL_COMMENT, "コメント")

            for _drawing_name, data in iter_parts(
                archive,
                lambda member: member.startswith("xl/drawings/") and member.endswith(".xml"),
                max_parts=120,
            ):
                for value in xml_texts(data, {"t"}):
                    sink.text(value, ChunkKind.TEXTBOX, "テキストボックス")

            for _name, data in iter_parts(
                archive,
                lambda member: member.startswith("xl/charts/") and member.endswith(".xml"),
                max_parts=120,
            ):
                for value in xml_texts(data, {"t", "v"}):
                    sink.text(value, ChunkKind.CHART, "グラフ")


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
