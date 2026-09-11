"""Plain-text extraction with v4 encoding detection."""

from __future__ import annotations

import codecs
import os

from ..core.models import ChunkKind, FileEntry
from ..core.paths import TEXT_EXTS
from ..errors import Issue, Severity
from .base import ExtractContext, ExtractOptions, ExtractResult, Sink

# Order matters: strict UTF-8 first, then Japanese legacy code pages.
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp932", "shift_jis", "utf-16", "utf-16-le", "utf-16-be")

CHUNK_SIZE = 256 * 1024
MAX_LINE_LENGTH = 8000


def sniff_encoding(path: str, *, probe_bytes: int) -> str:
    """Return the first encoding that decodes the probe without errors."""
    try:
        with open(path, "rb") as handle:
            probe = handle.read(probe_bytes)
    except OSError:
        return "utf-8"
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if probe.startswith(bom):
            return encoding
    if b"\x00" in probe[:4096]:
        # UTF-16 without BOM is detected by the NUL pattern.
        if probe[1:2] == b"\x00":
            return "utf-16-le"
        if probe[0:1] == b"\x00":
            return "utf-16-be"
    for encoding in ENCODINGS:
        try:
            probe.decode(encoding, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
        return encoding
    return "cp932"


def looks_binary(probe: bytes) -> bool:
    if not probe:
        return False
    if probe[:4096].count(0) / max(1, len(probe[:4096])) > 0.02:
        return True
    control = sum(1 for byte in probe if byte < 9 or 13 < byte < 32)
    return control / len(probe) > 0.10


class TextExtractor:
    name = "text"
    version = "5.0"

    def supports(self, entry: FileEntry, options: ExtractOptions) -> bool:
        return entry.extension in TEXT_EXTS

    def extract(self, context: ExtractContext, sink: Sink) -> ExtractResult:
        result = ExtractResult()
        limit = context.options.limits.text_max_bytes
        size = context.entry.size
        if size > limit:
            result.skipped = True
            result.reason = f"テキスト上限（{limit // (1024 * 1024)}MB）を超えています"
            return result

        encoding = sniff_encoding(context.path, probe_bytes=context.options.text_probe_bytes)
        try:
            with open(context.path, encoding=encoding, errors="replace", newline="") as handle:
                for index, line in enumerate(handle, 1):
                    if len(line) > MAX_LINE_LENGTH:
                        # A single enormous line (minified JSON, CSV) is cut into
                        # windows so the UI stays responsive.
                        for offset in range(0, len(line), MAX_LINE_LENGTH):
                            sink.text(line[offset : offset + MAX_LINE_LENGTH], ChunkKind.LINE, f"{index}行")
                    else:
                        sink.text(line, ChunkKind.LINE, f"{index}行")
        except (OSError, UnicodeError) as exc:
            result.skipped = True
            result.reason = f"読み込みエラー: {type(exc).__name__}"
            result.warnings.append(
                Issue(path=context.entry.path, code="text-read", message=result.reason, severity=Severity.WARN)
            )
        return result


def describe_encoding(path: str) -> str:
    return sniff_encoding(path, probe_bytes=64 * 1024) if os.path.isfile(path) else ""
