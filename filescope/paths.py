"""Filesystem locations FileScope owns.

Everything lives under ``%LOCALAPPDATA%\\FileScope`` (or ``~/.config/FileScope``
off Windows). Nothing is ever written next to the user's documents.
"""

from __future__ import annotations

import os
import sys
import tempfile

APP_NAME = "FileScope"


def app_dir() -> str:
    """Directory holding the application (source checkout or frozen bundle)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


def legacy_settings_path() -> str:
    """V4 stored settings next to the script; still read once for migration."""
    return os.path.join(app_dir(), "search_tool_settings.json")


def index_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "index"))


def index_path() -> str:
    return os.path.join(index_dir(), "filescope-index.sqlite3")


def logs_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "logs"))


def preview_cache_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "preview"))


def crash_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "crashes"))


def staging_dir() -> str:
    """Per-user staging folder used for remote-file copies.

    A dedicated folder (instead of the shared TEMP root) makes crash-time
    cleanup possible without touching other applications' files.
    """
    return ensure_dir(os.path.join(tempfile.gettempdir(), f"{APP_NAME}-staging"))


def session_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "sessions"))
