"""Result list: one row per file, virtualised so 100k rows stay responsive.

v4 inserted one row per hit, which made large result sets unusable. V5 keeps a
file-level model and only materialises the rows the user can actually see
(spec sections 39, 64).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ..core.models import CLOUD_LABELS, SOURCE_LABELS, FileResult

COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("confirmed", "済", 34),
    ("name", "ファイル", 260),
    ("displays", "一致条件", 150),
    ("hits", "Hit数", 60),
    ("kind", "種類", 60),
    ("modified", "更新日時", 130),
    ("size", "サイズ", 90),
    ("source", "Source", 90),
    ("ocr", "OCR", 50),
    ("path", "Path", 320),
)

WINDOW_ROWS = 300       # rows kept in the widget at once
OVERSCAN = 60


@dataclass
class Row:
    result: FileResult
    confirmed: bool
    refine_unknown: bool = False


class ResultModel:
    """Owns the ordered rows and the virtual window shown by the Treeview."""

    def __init__(self) -> None:
        self.all_rows: list[Row] = []
        self.visible: list[Row] = []
        self.offset = 0
        self.sort_column = "relevance"
        self.sort_descending = True

    # ------------------------------------------------------------- content
    def add(self, result: FileResult, *, confirmed: bool) -> None:
        self.all_rows.append(Row(result=result, confirmed=confirmed))

    def update_confirmed(self, path: str, confirmed: bool) -> None:
        for row in self.all_rows:
            if row.result.path == path:
                row.confirmed = confirmed

    def clear(self) -> None:
        self.all_rows.clear()
        self.visible.clear()
        self.offset = 0

    @property
    def count(self) -> int:
        return len(self.visible)

    # ------------------------------------------------------------ ordering
    def resort(self, column: str, descending: bool) -> None:
        self.sort_column = column
        self.sort_descending = descending
        self.apply_view(self.all_rows)

    def apply_view(self, rows: list[Row]) -> None:
        self.visible = sorted(rows, key=self._sort_key, reverse=self.sort_descending)
        self.offset = 0

    def _sort_key(self, row: Row):
        result = row.result
        column = self.sort_column
        if column == "name":
            return result.name.casefold()
        if column == "path":
            return result.path.casefold()
        if column == "hits":
            return result.hit_count
        if column == "kind":
            return result.file_kind.value
        if column == "modified":
            return result.mtime_ns
        if column == "size":
            return result.size
        if column == "source":
            return result.source_type.value
        if column == "confirmed":
            return int(row.confirmed)
        return result.score

    # -------------------------------------------------------------- window
    def window(self, first: int, last: int) -> list[tuple[int, Row]]:
        first = max(0, min(first, max(0, len(self.visible) - 1)))
        last = min(len(self.visible), last)
        self.offset = first
        return list(enumerate(self.visible[first:last], start=first))

    def row_at(self, index: int) -> Row | None:
        if 0 <= index < len(self.visible):
            return self.visible[index]
        return None


def values_for(row: Row, ocr_available: bool = True) -> tuple[str, ...]:
    result = row.result
    source = SOURCE_LABELS.get(result.source_type, "?")
    if result.cloud_state.value in ("online", "pinned"):
        source = f"{source} {CLOUD_LABELS[result.cloud_state]}"
    kind = result.file_kind.value
    if result.from_index:
        kind = f"{kind}*"
    ocr = ""
    if result.ocr_pages:
        ocr = f"OCR {result.ocr_pages}"
    elif not ocr_available:
        ocr = "-"
    return (
        "✓" if row.confirmed else "",
        result.name,
        " / ".join(result.displays) if result.displays else "",
        str(result.hit_count),
        kind,
        format_time(result.mtime_ns),
        format_size(result.size),
        source,
        ocr,
        result.path,
    )


TAGS = {
    "confirmed": {"foreground": "#6b6b6b"},
    "ocr": {"foreground": "#1f5f8b"},
    "odd": {"background": "#f7f7f7"},
    "issue_skip": {"foreground": "#8a6d3b"},
    "issue_warn": {"foreground": "#8a6d3b"},
    "issue_error": {"foreground": "#a94442"},
}


def row_tags(row: Row, index: int) -> tuple[str, ...]:
    tags: list[str] = []
    if row.confirmed:
        tags.append("confirmed")
    if row.result.ocr_pages:
        tags.append("ocr")
    if index % 2:
        tags.append("odd")
    return tuple(tags)


def format_time(mtime_ns: int) -> str:
    if not mtime_ns:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime_ns / 1_000_000_000))
    except (OverflowError, OSError, ValueError):
        return ""


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def copy_text_for(result: FileResult) -> str:
    """Clipboard payload for "Copy Content" (spec section 84)."""
    lines = [f"{result.path}"]
    for evidence in result.evidence:
        location = evidence.location or evidence.kind.value
        lines.append(f"[{location}] {evidence.snippet}")
    return "\n".join(lines)


def folder_of(path: str) -> str:
    return os.path.dirname(path)
