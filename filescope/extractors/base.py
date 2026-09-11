"""Extractor contract and shared plumbing."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..config import ResourceLimits
from ..core.models import Chunk, FileEntry
from ..errors import Issue, StopExtraction


@dataclass(frozen=True)
class ExtractOptions:
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    ocr_mode: str = "auto"          # off / auto / all
    ocr_languages: str = "jpn+eng"
    search_formula: bool = True
    include_archives: bool = False
    text_probe_bytes: int = 64 * 1024
    ocr_cache: object | None = None
    ocr_available: bool = False
    archive_depth: int = 0


@dataclass
class ExtractResult:
    chunks: int = 0
    ocr_pages: int = 0
    skipped: bool = False
    reason: str = ""
    warnings: list[Issue] = field(default_factory=list)
    truncated: bool = False


class Sink:
    """Receives chunks; the pipeline decides when reading can stop.

    ``add`` may raise :class:`StopExtraction` -- extractors must let it
    propagate so a JIT-accepted file stops being read immediately.
    """

    def __init__(self, emit: Callable[[Chunk], None], cancel: threading.Event | None = None) -> None:
        self._emit = emit
        self._cancel = cancel or threading.Event()
        self.count = 0

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise StopExtraction("cancelled")

    def add(self, chunk: Chunk) -> None:
        self.check()
        self._emit(chunk)
        self.count += 1

    def text(self, text: str, kind, location: str = "") -> None:
        if text is None:
            return
        value = str(text)
        if value.strip():
            self.add(Chunk(text=value, kind=kind, location=location))


@dataclass(frozen=True)
class ExtractContext:
    entry: FileEntry
    path: str
    options: ExtractOptions


class Extractor(Protocol):
    name: str
    version: str

    def supports(self, entry: FileEntry, options: ExtractOptions) -> bool: ...

    def extract(self, context: ExtractContext, sink: Sink) -> ExtractResult: ...
