"""Extractor registry.

A plain ordered list of extractors: first ``supports()`` wins. No factories,
no plugin framework -- adding a format means adding one module and one entry.
"""

from __future__ import annotations

from ..core.models import FileEntry
from .base import ExtractContext, ExtractOptions, ExtractResult, Sink
from .excel import ExcelExtractor
from .pdf import PdfExtractor
from .powerpoint import PowerPointExtractor
from .text import TextExtractor
from .unknown import UnknownTextExtractor
from .word import WordExtractor

_REGISTRY: list[object] = []


def _build_registry() -> list[object]:
    from .archive import ArchiveExtractor

    return [
        ExcelExtractor(),
        WordExtractor(),
        PowerPointExtractor(),
        PdfExtractor(),
        TextExtractor(),
        ArchiveExtractor(),
        UnknownTextExtractor(),
    ]


def registry() -> list[object]:
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    return _REGISTRY


def extractor_for(entry: FileEntry, options: ExtractOptions):
    for extractor in registry():
        if extractor.supports(entry, options):
            return extractor
    return None


def extract(entry: FileEntry, path: str, sink: Sink, options: ExtractOptions) -> ExtractResult:
    """Run the matching extractor; unknown formats are reported, not crashed on."""
    extractor = extractor_for(entry, options)
    if extractor is None:
        return ExtractResult(skipped=True, reason="対応していない形式です")
    context = ExtractContext(entry=entry, path=path, options=options)
    return extractor.extract(context, sink)


__all__ = [
    "ExtractContext",
    "ExtractOptions",
    "ExtractResult",
    "Sink",
    "extract",
    "extractor_for",
    "registry",
]
