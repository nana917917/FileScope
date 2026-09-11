"""Application settings.

Writes are atomic (temp file + fsync + ``os.replace``) so a crash mid-save can
never leave a half-written ``settings.json`` that stops the app from starting.
A corrupt or unreadable file falls back to defaults and is preserved as
``settings.corrupt-<timestamp>.json`` for inspection.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from . import paths
from .logging_setup import get_logger

log = get_logger("config")

SCHEMA_VERSION = 5

# Search modes (spec section 7).
SEARCH_MODES: tuple[str, ...] = ("fast", "standard", "full")
MODE_LABELS = {
    "fast": "高速",
    "standard": "標準",
    "full": "完全",
}
MODE_DESCRIPTIONS = {
    "fast": "索引・キャッシュ・ファイル情報を中心に検索します。オンライン専用ファイルは取得しません。",
    "standard": "通常はこれ。索引済みは索引を使い、新規・変更・未索引のファイルだけ本文を読みます。",
    "full": "索引を信用せず本文を確認します。必要ならオンライン専用ファイルの取得や全ページOCRも行います。",
}

ONLINE_FILE_POLICIES = ("auto", "skip", "fetch")
ONLINE_POLICY_LABELS = {
    "auto": "自動（モードに従う）",
    "skip": "取得しない",
    "fetch": "取得して検索する",
}

OCR_MODES = ("off", "auto", "all")
OCR_MODE_LABELS = {"off": "OFF", "auto": "自動", "all": "全ページ"}

# Index size choices offered in the UI (bytes); 0 disables the index.
INDEX_SIZE_CHOICES: tuple[tuple[str, int], ...] = (
    ("OFF", 0),
    ("512MB", 512 * 1024 * 1024),
    ("1GB", 1024 * 1024 * 1024),
    ("2GB", 2 * 1024 * 1024 * 1024),
    ("5GB", 5 * 1024 * 1024 * 1024),
)


@dataclass
class ResourceLimits:
    """Bytes beyond which a file is skipped (with a visible reason)."""

    office_max_bytes: int = 500 * 1024 * 1024
    pdf_max_bytes: int = 1024 * 1024 * 1024
    text_max_bytes: int = 50 * 1024 * 1024
    unknown_text_max_bytes: int = 50 * 1024 * 1024
    archive_max_bytes: int = 512 * 1024 * 1024
    remote_stage_max_bytes: int = 256 * 1024 * 1024
    temp_free_reserve_bytes: int = 4 * 1024 * 1024 * 1024
    # Archive (zip) guards.
    archive_max_depth: int = 2
    archive_max_entries: int = 2000
    archive_max_total_bytes: int = 512 * 1024 * 1024
    archive_max_ratio: int = 200


@dataclass
class SearchDefaults:
    root: str = ""
    query: str = ""
    exclude_query: str = ""
    search_mode: str = "standard"
    legacy_operator: str = "OR"
    include_subfolders: bool = True
    include_excel: bool = True
    include_pdf: bool = True
    include_word: bool = True
    include_powerpoint: bool = True
    include_text: bool = True
    include_unknown_text: bool = True
    include_archives: bool = False
    search_path_names: bool = True
    search_formula: bool = True
    pdf_ocr_mode: str = "auto"
    case_sensitive: bool = False
    ignore_width: bool = True
    part_number_mode: bool = False
    stage_remote_files: bool = True
    online_files_policy: str = "auto"
    workers: int = 4
    sort_column: str = "relevance"
    sort_descending: bool = True


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    defaults: SearchDefaults = field(default_factory=SearchDefaults)
    recent_roots: list[str] = field(default_factory=list)

    index_enabled: bool = True
    index_max_bytes: int = 1024 * 1024 * 1024
    index_roots: list[str] = field(default_factory=list)

    history_enabled: bool = True
    history: list[dict[str, Any]] = field(default_factory=list)
    presets: list[dict[str, Any]] = field(default_factory=list)
    confirmed_paths: list[str] = field(default_factory=list)
    bookmarks: dict[str, str] = field(default_factory=dict)

    preview_enabled: bool = True
    autosave_results: bool = True
    restore_last_session: bool = True
    coalesce_ms: int = 200
    log_level: str = "INFO"
    theme: str = "system"
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    window: dict[str, int] = field(default_factory=dict)

    # Set when SQLite reports corruption; the index stays off until rebuilt.
    index_disabled_reason: str = ""

    # ------------------------------------------------------------------ IO
    @classmethod
    def load(cls, path: str | None = None) -> Settings:
        path = path or paths.settings_path()
        raw: dict[str, Any] = {}
        if os.path.isfile(path):
            try:
                loaded = _read_json(path)
                if isinstance(loaded, dict):
                    raw = loaded
                else:
                    raise ValueError("settings root is not an object")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.warning("settings unreadable, using defaults: %s", exc)
                _quarantine(path)
        elif os.path.isfile(paths.legacy_settings_path()):
            raw = _load_legacy()
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Settings:
        defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
        limits = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
        settings = cls()

        for descriptor in fields(SearchDefaults):
            if descriptor.name in defaults:
                setattr(settings.defaults, descriptor.name, defaults[descriptor.name])
        for descriptor in fields(ResourceLimits):
            if descriptor.name in limits:
                with suppress(TypeError, ValueError):
                    setattr(settings.limits, descriptor.name, int(limits[descriptor.name]))
        for descriptor in fields(cls):
            name = descriptor.name
            if name in ("defaults", "limits", "schema_version"):
                continue
            if name in raw:
                setattr(settings, name, raw[name])

        settings.schema_version = SCHEMA_VERSION
        settings.sanitize()
        return settings

    def sanitize(self) -> None:
        """Clamp values that come from disk so bad data cannot break startup."""
        if self.defaults.search_mode not in SEARCH_MODES:
            self.defaults.search_mode = "standard"
        if self.defaults.pdf_ocr_mode not in OCR_MODES:
            self.defaults.pdf_ocr_mode = "auto"
        if self.defaults.online_files_policy not in ONLINE_FILE_POLICIES:
            self.defaults.online_files_policy = "auto"
        self.defaults.legacy_operator = "AND" if str(self.defaults.legacy_operator).upper() == "AND" else "OR"
        try:
            self.defaults.workers = max(1, min(16, int(self.defaults.workers)))
        except (TypeError, ValueError):
            self.defaults.workers = 4
        try:
            self.index_max_bytes = max(0, int(self.index_max_bytes))
        except (TypeError, ValueError):
            self.index_max_bytes = 1024 * 1024 * 1024
        if not isinstance(self.history, list):
            self.history = []
        if not isinstance(self.presets, list):
            self.presets = []
        if not isinstance(self.confirmed_paths, list):
            self.confirmed_paths = []
        if not isinstance(self.bookmarks, dict):
            self.bookmarks = {}
        if not isinstance(self.recent_roots, list):
            self.recent_roots = []

    def save(self, path: str | None = None) -> bool:
        path = path or paths.settings_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            atomic_write_json(path, asdict(self))
            return True
        except OSError as exc:
            log.warning("could not save settings: %s", exc)
            return False

    # ------------------------------------------------------------- helpers
    def remember_root(self, root: str, limit: int = 12) -> None:
        if not root:
            return
        root = os.path.abspath(root)
        self.recent_roots = [r for r in self.recent_roots if os.path.normcase(r) != os.path.normcase(root)]
        self.recent_roots.insert(0, root)
        del self.recent_roots[limit:]

    def add_history(self, entry: dict[str, Any], limit: int = 200) -> None:
        if not self.history_enabled or not entry.get("query"):
            return
        self.history.insert(0, entry)
        del self.history[limit:]

    def confirmed_set(self) -> set[str]:
        return {os.path.normcase(p) for p in self.confirmed_paths}


def atomic_write_json(path: str, payload: Any) -> None:
    """Write JSON so that an interrupted save cannot destroy the old file."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp_path = os.path.join(directory, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            with suppress(OSError):
                os.remove(temp_path)


def _quarantine(path: str) -> None:
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.replace(path, f"{path}.corrupt-{stamp}")
    except OSError:
        pass


def _read_json(path: str) -> Any:
    """Read JSON written by this or an older FileScope (UTF-8, then cp932)."""
    with open(path, "rb") as handle:
        data = handle.read()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("settings file is not readable JSON")


def _load_legacy() -> dict[str, Any]:
    """Best-effort migration from the v4 settings file."""
    path = paths.legacy_settings_path()
    try:
        old = _read_json(path)
    except (OSError, ValueError) as exc:
        log.warning("legacy settings unreadable: %s", exc)
        return {}
    if not isinstance(old, dict):
        return {}

    defaults: dict[str, Any] = {}
    mapping = {
        "mode": "legacy_operator",
        "ignore_width": "ignore_width",
        "case_sensitive": "case_sensitive",
        "part_number_mode": "part_number_mode",
        "ocr_mode": "pdf_ocr_mode",
        "include_unknown": "include_unknown_text",
    }
    for old_key, new_key in mapping.items():
        if old_key in old:
            defaults[new_key] = old[old_key]
    if "roots" in old and isinstance(old["roots"], list) and old["roots"]:
        defaults["root"] = str(old["roots"][0])
    return {
        "defaults": defaults,
        "recent_roots": old.get("roots", []) if isinstance(old.get("roots"), list) else [],
        "confirmed_paths": old.get("confirmed", []) if isinstance(old.get("confirmed"), list) else [],
    }
