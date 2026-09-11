"""OCR text cache (spec section 31).

Keyed by file identity plus render/engine parameters, so the same scanned PDF
is never OCR'd twice. Images are never stored -- only recognised text. The
cache is a small standalone SQLite file so it keeps working when the main index
is disabled, and it can be trimmed least-recently-used (section 17).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import suppress

from .logging_setup import get_logger
from .paths import index_dir
from .version import OCR_VERSION

log = get_logger("ocr-cache")

DEFAULT_MAX_BYTES = 256 * 1024 * 1024


class OcrCache:
    def __init__(self, path: str | None = None, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = path or os.path.join(index_dir(), "ocr-cache.sqlite3")
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ocr (
                key TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                page INTEGER NOT NULL,
                text TEXT NOT NULL,
                chars INTEGER NOT NULL,
                created_at REAL NOT NULL,
                used_at REAL NOT NULL
            )
            """
        )
        connection.commit()
        self._connection = connection
        return connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                with suppress(sqlite3.Error):
                    self._connection.close()
                self._connection = None

    @staticmethod
    def make_key(normalized_path: str, size: int, mtime_ns: int, page: int, languages: str, scale: float) -> str:
        return f"{normalized_path}|{size}|{mtime_ns}|{page}|{languages}|{OCR_VERSION}|{scale:g}"

    # --------------------------------------------------------------- usage
    def get(self, key: str) -> str | None:
        try:
            with self._lock:
                connection = self._connect()
                row = connection.execute("SELECT text FROM ocr WHERE key = ?", (key,)).fetchone()
                if row is None:
                    return None
                connection.execute("UPDATE ocr SET used_at = ? WHERE key = ?", (time.time(), key))
                connection.commit()
                return str(row[0])
        except sqlite3.Error as exc:
            log.debug("ocr cache read failed: %s", exc)
            return None

    def put(self, key: str, path: str, page: int, text: str) -> None:
        if not text:
            return
        try:
            with self._lock:
                connection = self._connect()
                now = time.time()
                connection.execute(
                    "INSERT OR REPLACE INTO ocr (key, path, page, text, chars, created_at, used_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, path, page, text, len(text), now, now),
                )
                connection.commit()
        except sqlite3.Error as exc:
            log.debug("ocr cache write failed: %s", exc)

    def drop_file(self, normalized_path: str) -> int:
        try:
            with self._lock:
                connection = self._connect()
                cursor = connection.execute("DELETE FROM ocr WHERE path = ?", (normalized_path,))
                connection.commit()
                return int(cursor.rowcount or 0)
        except sqlite3.Error:
            return 0

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                continue
        return total

    def trim(self, max_bytes: int | None = None) -> int:
        """Delete least recently used pages until the cache fits the budget."""
        budget = self.max_bytes if max_bytes is None else max_bytes
        if budget <= 0:
            return 0
        removed = 0
        try:
            with self._lock:
                connection = self._connect()
                while self.size_bytes() > budget:
                    row = connection.execute(
                        "SELECT key, chars FROM ocr ORDER BY used_at ASC LIMIT 200"
                    ).fetchall()
                    if not row:
                        break
                    connection.executemany("DELETE FROM ocr WHERE key = ?", [(r[0],) for r in row])
                    connection.commit()
                    removed += len(row)
                    if len(row) < 200:
                        break
                connection.execute("VACUUM")
        except sqlite3.Error as exc:
            log.debug("ocr cache trim failed: %s", exc)
        return removed

    def clear(self) -> None:
        try:
            with self._lock:
                connection = self._connect()
                connection.execute("DELETE FROM ocr")
                connection.commit()
                connection.execute("VACUUM")
        except sqlite3.Error:
            pass
