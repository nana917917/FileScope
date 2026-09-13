"""Staging of remote documents into local temporary storage.

SMB/ZIP-container documents (xlsx/docx/pptx/pdf) are slow to read over the
network in small pieces, so they are copied once. Every copy is tracked so it
can be removed on success, failure, cancellation and after a crash.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

from ..logging_setup import get_logger
from ..paths import staging_dir

log = get_logger("temp")

STALE_AFTER_SECONDS = 6 * 60 * 60


@dataclass
class TempStats:
    created: int = 0
    removed: int = 0
    skipped_low_disk: int = 0
    skipped_too_large: int = 0
    bytes_staged: int = 0
    files: set[str] = field(default_factory=set)


class TempManager:
    """Owns every staged copy for one search session."""

    def __init__(self, *, reserve_bytes: int, max_stage_bytes: int) -> None:
        self.reserve_bytes = reserve_bytes
        self.max_stage_bytes = max_stage_bytes
        self.stats = TempStats()
        self._lock = threading.Lock()

    def can_stage(self, size: int, *, directory: str | None = None) -> tuple[bool, str]:
        if size > self.max_stage_bytes:
            return False, "大きいためコピーせず直接読み込みます"
        target = directory or staging_dir()
        try:
            free = shutil.disk_usage(target).free
        except OSError:
            return True, ""
        if free < size + self.reserve_bytes:
            return False, "TEMPの空き容量が不足しています"
        return True, ""

    @contextmanager
    def staged_copy(self, source: str, *, size: int, suffix: str = "", verify=None):
        """Yield a local path for ``source``; the copy is removed afterwards."""
        allowed, reason = self.can_stage(size)
        if not allowed:
            if "空き容量" in reason:
                self.stats.skipped_low_disk += 1
            else:
                self.stats.skipped_too_large += 1
            log.debug("staging skipped: %s (%s)", source, reason)
            yield source
            return

        handle, temp_path = tempfile.mkstemp(prefix="filescope-", suffix=suffix, dir=staging_dir())
        os.close(handle)
        with self._lock:
            self.stats.files.add(temp_path)
        try:
            before = stat_signature(source)
            shutil.copyfile(source, temp_path)
            self.stats.created += 1
            with suppress(OSError):
                self.stats.bytes_staged += os.path.getsize(temp_path)
            if verify is not None:
                verify(before == stat_signature(source))
            yield temp_path
        finally:
            self.discard(temp_path)

    def discard(self, path: str) -> None:
        with self._lock:
            known = path in self.stats.files
        if not known and not path.startswith(staging_dir()):
            return
        with suppress(OSError):
            os.remove(path)
            self.stats.removed += 1
        with self._lock:
            self.stats.files.discard(path)

    def cleanup(self) -> int:
        """Remove anything this session still owns; returns the file count."""
        with self._lock:
            remaining = list(self.stats.files)
            self.stats.files.clear()
        removed = 0
        for path in remaining:
            with suppress(OSError):
                os.remove(path)
                removed += 1
        return removed


def stat_signature(path: str) -> tuple[int, int]:
    try:
        info = os.stat(path)
    except OSError:
        return (-1, -1)
    return (info.st_size, info.st_mtime_ns)


def purge_stale(*, max_age_seconds: int = STALE_AFTER_SECONDS) -> int:
    """Delete leftovers from a previous crashed run."""
    directory = staging_dir()
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def staging_bytes() -> int:
    directory = staging_dir()
    total = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        try:
            total += os.path.getsize(os.path.join(directory, name))
        except OSError:
            continue
    return total
