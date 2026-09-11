"""Windows file attributes and source classification.

OneDrive/SharePoint "Files On-Demand" placeholders are identified from file
attributes only, so classifying a file never triggers a download.
"""

from __future__ import annotations

import ctypes
import os
import shutil

from ..core.models import CloudState, SourceType

# Attribute bits from winnt.h.
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_PINNED = 0x00080000
FILE_ATTRIBUTE_UNPINNED = 0x00100000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

_CLOUD_HINT_DIRS = ("onedrive", "sharepoint", "dropbox", "box", "google drive")


def long_path(path: str) -> str:
    """Prefix with ``\\\\?\\`` so paths past MAX_PATH still work."""
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + os.path.abspath(path)


def drive_type(path: str) -> int:
    if os.name != "nt":
        return DRIVE_FIXED
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return DRIVE_FIXED
        return int(ctypes.windll.kernel32.GetDriveTypeW(drive + "\\"))
    except (OSError, ValueError, AttributeError):
        return DRIVE_FIXED


def is_remote_path(path: str) -> bool:
    """True for UNC paths and mapped network drives (v4 behaviour)."""
    if str(path).startswith("\\\\"):
        return True
    if os.name != "nt":
        return False
    return drive_type(path) == DRIVE_REMOTE


def is_onedrive_path(path: str) -> bool:
    """Heuristic used only as a hint; attributes decide the cloud state."""
    lowered = os.path.normcase(path)
    return any(hint in lowered for hint in _CLOUD_HINT_DIRS)


def read_attributes(path: str) -> int:
    """Return the Windows file attribute bitmask (0 when unavailable)."""
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return 0
    return int(getattr(info, "st_file_attributes", 0) or 0)


def classify_source(path: str) -> SourceType:
    if is_remote_path(path):
        return SourceType.SMB
    if os.name == "nt" and drive_type(path) in (DRIVE_REMOVABLE, DRIVE_CDROM):
        return SourceType.REMOVABLE
    if is_onedrive_path(path):
        return SourceType.ONEDRIVE
    return SourceType.LOCAL


def classify_cloud(path: str, attributes: int | None = None) -> CloudState:
    """Cloud state from attributes alone (never opens the file)."""
    if is_remote_path(path):
        return CloudState.SMB
    attrs = read_attributes(path) if attributes is None else attributes
    if not attrs:
        return CloudState.LOCAL
    # Recall flags mark a placeholder whose data still lives in the cloud.
    if attrs & (FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS):
        return CloudState.ONLINE_ONLY
    if attrs & FILE_ATTRIBUTE_OFFLINE:
        return CloudState.ONLINE_ONLY
    if attrs & FILE_ATTRIBUTE_PINNED and not attrs & FILE_ATTRIBUTE_UNPINNED:
        return CloudState.ALWAYS_AVAILABLE
    return CloudState.LOCAL


def is_placeholder(path: str, attributes: int | None = None) -> bool:
    return classify_cloud(path, attributes) is CloudState.ONLINE_ONLY


def free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0
