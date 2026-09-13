"""Unknown extensions: probe 64 KiB and treat text-like files as text.

v4 behaviour kept: the probe happens in the worker (never during discovery) so
OneDrive placeholders are not hydrated just to list them.
"""

from __future__ import annotations

from ..core.models import FileEntry
from ..core.paths import OBVIOUS_BINARY_EXTS
from .base import ExtractContext, ExtractOptions, ExtractResult, Sink
from .text import TextExtractor, looks_binary


class UnknownTextExtractor:
    name = "unknown-text"
    version = "5.0"

    def supports(self, entry: FileEntry, options: ExtractOptions) -> bool:
        if not entry.extension:
            return entry.size <= options.limits.unknown_text_max_bytes
        if entry.extension in OBVIOUS_BINARY_EXTS:
            return False
        return entry.size <= options.limits.unknown_text_max_bytes

    def extract(self, context: ExtractContext, sink: Sink) -> ExtractResult:
        probe_bytes = context.options.text_probe_bytes
        try:
            with open(context.path, "rb") as handle:
                probe = handle.read(probe_bytes)
        except OSError as exc:
            return ExtractResult(skipped=True, reason=f"読み込みエラー: {type(exc).__name__}")
        if not probe:
            return ExtractResult(skipped=True, reason="空ファイル")
        if looks_binary(probe):
            return ExtractResult(skipped=True, reason="テキストとして判定できません")
        return TextExtractor().extract(context, sink)
