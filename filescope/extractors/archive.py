"""ZIP archives: search inside members with Extractor reuse (spec section 34).

Bomb defences (spec section 35) are enforced before anything is unpacked:
entry count, per-entry size, total size, compression ratio and nesting depth.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from contextlib import suppress
from dataclasses import replace

from ..core.models import Chunk, CloudState, FileEntry, SourceType
from ..core.paths import ARCHIVE_EXTS, OBVIOUS_BINARY_EXTS, OPTIONAL_ARCHIVE_EXTS
from ..errors import Issue, Severity
from ..paths import staging_dir
from .base import ExtractContext, ExtractOptions, ExtractResult, Sink


class ArchiveExtractor:
    name = "archive"
    version = "5.0"

    def supports(self, entry: FileEntry, options: ExtractOptions) -> bool:
        return options.include_archives and entry.extension in ARCHIVE_EXTS | OPTIONAL_ARCHIVE_EXTS

    def extract(self, context: ExtractContext, sink: Sink) -> ExtractResult:
        result = ExtractResult()
        options = context.options
        if context.entry.extension in OPTIONAL_ARCHIVE_EXTS:
            result.skipped = True
            result.reason = f"{context.entry.extension} は任意アダプタ未導入のため検索しません"
            return result
        limits = options.limits
        if context.entry.size > limits.archive_max_bytes:
            result.skipped = True
            result.reason = "アーカイブサイズ上限を超えています"
            return result
        if getattr(options, "archive_depth", 0) >= limits.archive_max_depth:
            result.skipped = True
            result.reason = "アーカイブの入れ子が深すぎます"
            return result

        try:
            archive = zipfile.ZipFile(context.path)
        except (OSError, zipfile.BadZipFile) as exc:
            result.skipped = True
            result.reason = f"アーカイブを開けません: {type(exc).__name__}"
            return result

        from . import extract as extract_member  # local import: registry owns the list

        total_bytes = 0
        with archive:
            infos = archive.infolist()
            if len(infos) > limits.archive_max_entries:
                result.truncated = True
                result.warnings.append(
                    Issue(
                        path=context.entry.path,
                        code="zip-entries",
                        message=f"エントリ数が上限（{limits.archive_max_entries}）を超えたため一部のみ検索しました",
                        severity=Severity.WARN,
                    )
                )
                infos = infos[: limits.archive_max_entries]
            for info in infos:
                sink.check()
                if info.is_dir():
                    continue
                name = _safe_name(info.filename)
                if name is None:
                    result.warnings.append(
                        Issue(
                            path=context.entry.path,
                            code="zip-traversal",
                            message=f"安全でないパスをスキップしました: {info.filename}",
                            severity=Severity.WARN,
                        )
                    )
                    continue
                if info.file_size > limits.archive_max_bytes:
                    continue
                compression = info.compress_size or 1
                if info.file_size / compression > limits.archive_max_ratio:
                    result.warnings.append(
                        Issue(
                            path=context.entry.path,
                            code="zip-ratio",
                            message=f"圧縮率が異常なためスキップしました: {name}",
                            severity=Severity.WARN,
                        )
                    )
                    continue
                total_bytes += info.file_size
                if total_bytes > limits.archive_max_total_bytes:
                    result.truncated = True
                    result.warnings.append(
                        Issue(
                            path=context.entry.path,
                            code="zip-total",
                            message="展開後の合計サイズが上限を超えたため以降を省略しました",
                            severity=Severity.WARN,
                        )
                    )
                    break

                extension = os.path.splitext(name)[1].lower()
                if extension in OBVIOUS_BINARY_EXTS:
                    continue
                member_result = self._extract_member(
                    archive, info, name, extension, context, sink, extract_member, options
                )
                result.chunks += member_result.chunks
                result.warnings.extend(member_result.warnings)
        return result

    def _extract_member(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        name: str,
        extension: str,
        context: ExtractContext,
        sink: Sink,
        extract_member,
        options: ExtractOptions,
    ) -> ExtractResult:
        handle, temp_path = tempfile.mkstemp(prefix="filescope-zip-", suffix=extension, dir=staging_dir())
        os.close(handle)
        try:
            with archive.open(info) as source, open(temp_path, "wb") as target:
                while True:
                    block = source.read(1024 * 256)
                    if not block:
                        break
                    target.write(block)
            display = f"{os.path.basename(context.entry.path)} > {name}"
            member_entry = FileEntry(
                path=display,
                size=info.file_size,
                mtime_ns=context.entry.mtime_ns,
                extension=extension,
                source_type=SourceType.LOCAL if context.entry.source_type is SourceType.LOCAL else context.entry.source_type,
                cloud_state=CloudState.LOCAL,
            )
            member_options = replace(options, archive_depth=getattr(options, "archive_depth", 0) + 1)
            return extract_member(member_entry, temp_path, _PrefixSink(sink, display), member_options)
        finally:
            with suppress(OSError):
                os.remove(temp_path)


class _PrefixSink(Sink):
    """Prefixes archive locations so a hit shows where it came from."""

    def __init__(self, inner: Sink, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix
        self.count = 0

    @property
    def cancelled(self) -> bool:
        return self._inner.cancelled

    def check(self) -> None:
        self._inner.check()

    def add(self, chunk: Chunk) -> None:
        location = f"{self._prefix} / {chunk.location}" if chunk.location else self._prefix
        self._inner.add(Chunk(text=chunk.text, kind=chunk.kind, location=location, sequence=chunk.sequence))
        self.count += 1


def _safe_name(name: str) -> str | None:
    """Reject absolute paths and ``..`` traversal inside archives."""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return None
    if ":" in normalized.split("/")[0]:
        return None
    if not normalized.strip():
        return None
    return normalized
