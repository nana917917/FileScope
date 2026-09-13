"""Shared data models for the search pipeline."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum


class SourceType(str, Enum):
    LOCAL = "local"
    SMB = "smb"
    ONEDRIVE = "onedrive"
    REMOVABLE = "removable"
    UNKNOWN = "unknown"


SOURCE_LABELS = {
    SourceType.LOCAL: "Local",
    SourceType.SMB: "SMB",
    SourceType.ONEDRIVE: "OneDrive",
    SourceType.REMOVABLE: "Removable",
    SourceType.UNKNOWN: "?",
}


class CloudState(str, Enum):
    LOCAL = "local"                      # fully present on disk
    ONLINE_ONLY = "online"               # placeholder: reading it triggers a download
    ALWAYS_AVAILABLE = "pinned"          # pinned / "Always keep on this device"
    SMB = "smb"                          # network share, not Files On-Demand
    UNKNOWN = "unknown"


CLOUD_LABELS = {
    CloudState.LOCAL: "Local",
    CloudState.ONLINE_ONLY: "☁ Online",
    CloudState.ALWAYS_AVAILABLE: "📌 Always",
    CloudState.SMB: "SMB",
    CloudState.UNKNOWN: "?",
}


class FileKind(str, Enum):
    EXCEL = "Excel"
    PDF = "PDF"
    WORD = "Word"
    POWERPOINT = "PPT"
    TEXT = "Text"
    ARCHIVE = "Archive"
    UNKNOWN = "?"


class ChunkKind(str, Enum):
    """Where a piece of extracted text came from (spec sections 23-26, 32)."""

    LINE = "line"
    CELL = "cell"
    FORMULA = "formula"
    CELL_COMMENT = "cell-comment"
    SHEET_NAME = "sheet-name"
    DEFINED_NAME = "defined-name"
    HYPERLINK = "hyperlink"
    CHART = "chart"
    TEXTBOX = "textbox"
    PAGE = "page"
    OCR = "ocr"
    PARAGRAPH = "paragraph"
    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    ENDNOTE = "endnote"
    COMMENT = "comment"
    SLIDE = "slide"
    NOTES = "notes"
    NAME = "name"          # file name
    PATH = "path"          # folder path
    ARCHIVE = "archive"    # text inside an archive member
    INTERNAL = "internal"  # OOXML text with no reliable location
    META = "meta"          # document properties (title, author, ...)


# Values that mean "this hit came from OCR" for the `ocr:` filter and the
# `[OCR]` marker in the result list.
OCR_KINDS = frozenset({ChunkKind.OCR})


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    kind: ChunkKind = ChunkKind.LINE
    location: str = ""
    sequence: int = 0

    def label(self) -> str:
        if self.location:
            return self.location
        return self.kind.value


@dataclass(frozen=True)
class FileEntry:
    """Cheap metadata gathered during discovery; no file content is touched."""

    path: str
    size: int
    mtime_ns: int
    extension: str
    source_type: SourceType = SourceType.LOCAL
    cloud_state: CloudState = CloudState.LOCAL
    attributes: int = 0

    @property
    def directory(self) -> str:
        return os.path.dirname(self.path)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass(frozen=True, slots=True)
class Evidence:
    """One place a term matched. Kept small: the UI shows a snippet, not a file."""

    term: str
    location: str
    kind: ChunkKind
    snippet: str


@dataclass
class FileResult:
    path: str
    file_kind: FileKind
    size: int
    mtime_ns: int
    source_type: SourceType
    cloud_state: CloudState
    matched_terms: tuple[str, ...] = ()
    displays: tuple[str, ...] = ()
    hit_count: int = 0
    #: False when JIT stopped reading early, so the count is a lower bound.
    hit_count_exact: bool = True
    evidence: list[Evidence] = field(default_factory=list)
    ocr_pages: int = 0
    from_index: bool = False
    confirmed: bool = False
    bookmark: str = ""
    score: float = 0.0
    name: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = os.path.basename(self.path)

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    @property
    def directory(self) -> str:
        return os.path.dirname(self.path)

    def matches_display(self, display: str) -> bool:
        return display in self.displays


@dataclass
class Coverage:
    """Answers "did we really look at everything?" (spec section 49)."""

    discovered: int = 0
    scanned: int = 0
    hits: int = 0
    indexed: int = 0
    read: int = 0
    ocr_pages: int = 0
    skipped_online: int = 0
    skipped_size: int = 0
    skipped_type: int = 0
    warnings: int = 0
    errors: int = 0
    cancelled: bool = False
    elapsed_seconds: float = 0.0

    @property
    def unsearched(self) -> int:
        return max(0, self.discovered - self.scanned)

    def line(self) -> str:
        return (
            f"発見 {self.discovered:,} / 検索完了 {self.scanned:,} / Hit {self.hits:,}資料"
            f" / 索引 {self.indexed:,} / 実読込 {self.read:,}"
            f" / OCR {self.ocr_pages:,}ページ"
            f" / Online-only skip {self.skipped_online:,}"
            + (f" / 警告 {self.warnings:,}" if self.warnings else "")
            + (f" / エラー {self.errors:,}" if self.errors else "")
        )


@dataclass
class Progress:
    phase: str = "待機中"
    discovered: int = 0
    scanned: int = 0
    hit_files: int = 0
    active: tuple[str, ...] = ()
    detail: str = ""


@dataclass
class Summary:
    """Final result of one search run."""

    query: str
    mode: str
    started_at: float
    finished_at: float
    coverage: Coverage
    results: list[FileResult] = field(default_factory=list)
    issues: list[object] = field(default_factory=list)
    total_hits: int = 0
    truncated: bool = False
    index_used: bool = False
    index_coverage: tuple[int, int] = (0, 0)   # indexed files / known files

    @property
    def elapsed(self) -> float:
        return max(0.0, self.finished_at - self.started_at)


def total_hits(results: Iterable[FileResult]) -> int:
    return sum(r.hit_count for r in results)
