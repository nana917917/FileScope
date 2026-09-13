"""Discovery: enumerate candidates without opening a single file.

Opening a file during discovery would hydrate OneDrive placeholders and stall
on SMB, so enumeration only stats entries and records Windows attributes.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

from ..errors import Issue, Severity
from ..logging_setup import get_logger
from ..platform import windows
from .models import CloudState, FileEntry, SourceType
from .paths import extension_of

log = get_logger("scan")

FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


def iter_entries(
    root: str,
    *,
    include_subfolders: bool = True,
    cancel: threading.Event | None = None,
    follow_reparse_points: bool = False,
    max_files: int = 0,
) -> Iterator[FileEntry | Issue]:
    """Yield entries under ``root``; issues are yielded instead of raised."""
    root = os.path.abspath(root)
    source_type = windows.classify_source(root)
    remote = source_type is SourceType.SMB
    stack = [root]
    seen_dirs: set[str] = set()
    produced = 0

    while stack:
        if cancel is not None and cancel.is_set():
            return
        directory = stack.pop()
        key = os.path.normcase(directory)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            with os.scandir(directory) as iterator:
                for item in iterator:
                    if cancel is not None and cancel.is_set():
                        return
                    try:
                        is_directory = item.is_dir(follow_symlinks=follow_reparse_points)
                    except OSError as exc:
                        yield Issue(path=item.path, code="scan-stat", message=str(exc), severity=Severity.WARN)
                        continue
                    if is_directory:
                        if not include_subfolders:
                            continue
                        if not follow_reparse_points and _is_reparse(item, directory):
                            continue
                        stack.append(item.path)
                        continue
                    entry = _make_entry(item, source_type, remote)
                    if entry is None:
                        continue
                    produced += 1
                    if max_files and produced > max_files:
                        return
                    yield entry
        except OSError as exc:
            yield Issue(path=directory, code="scan-dir", message=str(exc), severity=Severity.WARN)
    return


def _is_reparse(item: os.DirEntry, parent: str) -> bool:
    try:
        info = item.stat(follow_symlinks=False)
    except OSError:
        return True
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        # OneDrive folders are reparse points too, but they are real folders;
        # only skip junctions/symlinks that could loop.
        target = os.path.realpath(item.path)
        return not os.path.normcase(target).startswith(os.path.normcase(parent))
    return False


def _make_entry(item: os.DirEntry, source_type: SourceType, remote: bool) -> FileEntry | None:
    try:
        info = item.stat(follow_symlinks=False)
    except OSError:
        return None
    if not os.path.isfile(item.path):
        return None
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    cloud = (
        CloudState.SMB
        if remote
        else windows.classify_cloud(item.path, attributes=attributes)
    )
    return FileEntry(
        path=item.path,
        size=int(info.st_size),
        mtime_ns=int(info.st_mtime_ns),
        extension=extension_of(item.name),
        source_type=source_type,
        cloud_state=cloud,
        attributes=attributes,
    )
