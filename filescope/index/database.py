"""Index database: connection, differential updates, capacity and recovery."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from ..core.models import Chunk, FileEntry
from ..core.paths import normalized_key
from ..errors import IndexUnavailable
from ..logging_setup import get_logger
from ..version import EXTRACTOR_VERSION, OCR_VERSION
from . import schema

log = get_logger("index")

MAX_CHUNK_CHARS = 64 * 1024          # one cell/page/paragraph
MAX_FILE_CHARS = 8 * 1024 * 1024     # total stored text per file


@dataclass
class IndexStatus:
    enabled: bool = True
    path: str = ""
    files: int = 0
    chunks: int = 0
    size_bytes: int = 0
    truncated_files: int = 0
    last_scan: float = 0.0
    fts5: bool = False
    trigram: bool = False
    message: str = ""

    def coverage_line(self, discovered: int) -> str:
        return f"索引済み {self.files:,} / {discovered:,}ファイル"


@dataclass
class UpdateOutcome:
    action: str = "unchanged"      # added / updated / unchanged / skipped / removed
    chunks: int = 0
    truncated: bool = False
    stored_chars: int = 0


@dataclass
class StoredFile:
    id: int
    path: str
    status: str
    chunks: list[Chunk] = field(default_factory=list)


class IndexDatabase:
    """Owns one SQLite connection (WAL) and every index mutation."""

    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = 0,
        extractor_version: str = EXTRACTOR_VERSION,
        ocr_version: str = OCR_VERSION,
    ) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.extractor_version = extractor_version
        self.ocr_version = ocr_version
        self.capacity_reached = False
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._fts5 = False
        self._trigram = False
        try:
            self._connect()
        except IndexUnavailable:
            raise
        except sqlite3.Error as exc:
            raise IndexUnavailable(str(exc)) from exc

    # ------------------------------------------------------------- plumbing
    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._fts5, self._trigram = schema.fts_available(connection)
            if not self._fts5:
                raise IndexUnavailable("このSQLiteにはFTS5がありません")
            schema.create_schema(connection, trigram=self._trigram)
            self._check_integrity(connection)
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(schema.SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('extractor_version', ?)",
                (self.extractor_version,),
            )
            connection.commit()
        except sqlite3.DatabaseError as exc:
            raise IndexUnavailable(f"インデックスを読み込めません: {exc}") from exc
        self._connection = connection
        return connection

    @staticmethod
    def _check_integrity(connection: sqlite3.Connection) -> None:
        try:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
        except sqlite3.DatabaseError as exc:
            raise IndexUnavailable(f"インデックスが破損しています: {exc}") from exc
        if row and str(row[0]).lower() != "ok":
            raise IndexUnavailable(f"インデックスが破損しています: {row[0]}")

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                except sqlite3.Error:
                    pass
                self._connection = None

    def meta(self, key: str, default: str = "") -> str:
        try:
            row = self._connection_or_raise().execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.Error:
            return default
        return str(row[0]) if row else default

    def _connection_or_raise(self) -> sqlite3.Connection:
        if self._connection is None:
            raise IndexUnavailable("インデックスが開かれていません")
        return self._connection

    # ------------------------------------------------------------- updates
    def needs_update(self, entry: FileEntry, *, ocr_language: str = "") -> bool:
        row = self._connection_or_raise().execute(
            "SELECT size, mtime_ns, extractor_version, ocr_version, ocr_language, status"
            " FROM files WHERE path_key = ?",
            (normalized_key(entry.path),),
        ).fetchone()
        if row is None:
            return True
        size, mtime_ns, extractor_version, ocr_version, stored_language, status = row
        if str(status) in ("skipped", "too_large", "empty"):
            # Unsupported/oversized files are never served from the index.
            return True
        return (
            int(size) != int(entry.size)
            or int(mtime_ns) != int(entry.mtime_ns)
            or str(extractor_version) != self.extractor_version
            or str(ocr_version) != self.ocr_version
            or str(stored_language) != (ocr_language or "")
        )

    def store_file(
        self,
        entry: FileEntry,
        chunks: list[Chunk],
        *,
        ocr_language: str = "",
        status: str = "ok",
        truncated: bool = False,
    ) -> UpdateOutcome:
        """Insert or replace one file and its chunks (differential friendly)."""
        if self.capacity_reached:
            return UpdateOutcome(action="skipped")
        if truncated and status == "ok":
            status = "partial"
        stored: list[tuple] = []
        total_chars = 0
        limit_hit = False
        for index, chunk in enumerate(chunks):
            text = chunk.text[:MAX_CHUNK_CHARS]
            if total_chars + len(text) > MAX_FILE_CHARS:
                limit_hit = True
                status = "too_large"
                break
            total_chars += len(text)
            stored.append(
                (
                    index,
                    chunk.kind.value,
                    chunk.location,
                    text,
                    _norm(text),
                    _part(text),
                )
            )
        if status == "too_large":
            stored = []
            total_chars = 0

        connection = self._connection_or_raise()
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT id FROM files WHERE path_key = ?", (normalized_key(entry.path),)
                ).fetchone()
                now = time.time()
                if row is None:
                    cursor = connection.execute(
                        "INSERT INTO files (path_key, display_path, source_type, cloud_state, extension,"
                        " size, mtime_ns, extractor_version, ocr_version, ocr_language, indexed_at, status,"
                        " chunk_count, text_chars) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            normalized_key(entry.path),
                            entry.path,
                            entry.source_type.value,
                            entry.cloud_state.value,
                            entry.extension,
                            entry.size,
                            entry.mtime_ns,
                            self.extractor_version,
                            self.ocr_version,
                            ocr_language or "",
                            now,
                            status,
                            len(stored),
                            total_chars,
                        ),
                    )
                    file_id = int(cursor.lastrowid or 0)
                    action = "added"
                else:
                    file_id = int(row[0])
                    connection.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                    connection.execute(
                        "UPDATE files SET display_path=?, source_type=?, cloud_state=?, extension=?,"
                        " size=?, mtime_ns=?, extractor_version=?, ocr_version=?, ocr_language=?,"
                        " indexed_at=?, status=?, chunk_count=?, text_chars=? WHERE id=?",
                        (
                            entry.path,
                            entry.source_type.value,
                            entry.cloud_state.value,
                            entry.extension,
                            entry.size,
                            entry.mtime_ns,
                            self.extractor_version,
                            self.ocr_version,
                            ocr_language or "",
                            now,
                            status,
                            len(stored),
                            total_chars,
                            file_id,
                        ),
                    )
                    action = "updated"
                if stored:
                    connection.executemany(
                        "INSERT INTO chunks (file_id, chunk_index, kind, location, text, norm_text, part_text)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(file_id, *item) for item in stored],
                    )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                if _is_corruption(exc):
                    raise IndexUnavailable(str(exc)) from exc
                log.warning("index write failed for %s: %s", entry.path, exc)
                return UpdateOutcome(action="skipped")
        self._enforce_capacity()
        return UpdateOutcome(
            action=action, chunks=len(stored), truncated=limit_hit, stored_chars=total_chars
        )

    def remove_paths(self, paths: list[str]) -> int:
        if not paths:
            return 0
        connection = self._connection_or_raise()
        removed = 0
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for path in paths:
                    row = connection.execute(
                        "SELECT id FROM files WHERE path_key = ?", (normalized_key(path),)
                    ).fetchone()
                    if row is None:
                        continue
                    connection.execute("DELETE FROM chunks WHERE file_id = ?", (int(row[0]),))
                    connection.execute("DELETE FROM files WHERE id = ?", (int(row[0]),))
                    removed += 1
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                log.warning("index delete failed: %s", exc)
        return removed

    def remove_root(self, root: str) -> int:
        """Drop every indexed file under ``root`` (used by 再構築/削除)."""
        connection = self._connection_or_raise()
        prefix = _root_prefix(root)
        with self._lock:
            rows = connection.execute(
                "SELECT path_key FROM files WHERE substr(path_key, 1, ?) = ?",
                (len(prefix), prefix),
            ).fetchall()
        return self.remove_paths([str(row[0]) for row in rows])

    def clear(self) -> None:
        connection = self._connection_or_raise()
        with self._lock:
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM files")
            connection.execute("DELETE FROM chunks_fts")
            connection.execute("DELETE FROM chunks_fts_part")
            connection.commit()
            connection.execute("VACUUM")

    def rebuild_fts(self) -> None:
        connection = self._connection_or_raise()
        with self._lock:
            connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
            connection.execute("INSERT INTO chunks_fts_part(chunks_fts_part) VALUES ('rebuild')")
            connection.commit()

    # ------------------------------------------------------------ capacity
    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                continue
        return total

    def _enforce_capacity(self) -> None:
        if self.max_bytes and self.size_bytes() > self.max_bytes:
            if not self.capacity_reached:
                log.info(
                    "index capacity reached (%s > %s); new files fall back to direct search",
                    self.size_bytes(),
                    self.max_bytes,
                )
            # Never delete indexed data: dropping rows would make searches
            # silently incomplete. Stop adding instead.
            self.capacity_reached = True

    # -------------------------------------------------------------- reading
    def status(self) -> IndexStatus:
        connection = self._connection_or_raise()
        files = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        truncated = int(
            connection.execute(
                "SELECT COUNT(*) FROM files WHERE status IN ('partial', 'too_large')"
            ).fetchone()[0]
        )
        last = connection.execute("SELECT MAX(indexed_at) FROM files").fetchone()[0] or 0.0
        message = ""
        if self.capacity_reached:
            message = "容量上限に達したため新規の索引作成を停止しました（検索は継続します）"
        return IndexStatus(
            enabled=True,
            path=self.path,
            files=files,
            chunks=chunks,
            size_bytes=self.size_bytes(),
            truncated_files=truncated,
            last_scan=float(last),
            fts5=self._fts5,
            trigram=self._trigram,
            message=message,
        )

    def load_file(self, path: str) -> StoredFile | None:
        connection = self._connection_or_raise()
        row = connection.execute(
            "SELECT id, display_path, status FROM files WHERE path_key = ?", (normalized_key(path),)
        ).fetchone()
        if row is None:
            return None
        file_id, display_path, status = int(row[0]), str(row[1]), str(row[2])
        chunks = self.load_chunks(file_id)
        return StoredFile(id=file_id, path=display_path, status=status, chunks=chunks)

    def load_chunks(self, file_id: int) -> list[Chunk]:

        connection = self._connection_or_raise()
        rows = connection.execute(
            "SELECT kind, location, text, chunk_index FROM chunks WHERE file_id = ? ORDER BY chunk_index",
            (file_id,),
        ).fetchall()
        return [_to_chunk(kind, location, text, index) for kind, location, text, index in rows]

    def file_row(self, path: str) -> tuple[int, str] | None:
        """Return ``(file_id, status)`` for a stored path."""
        connection = self._connection_or_raise()
        row = connection.execute(
            "SELECT id, status FROM files WHERE path_key = ?", (normalized_key(path),)
        ).fetchone()
        return (int(row[0]), str(row[1])) if row else None

    def iter_chunks(self, file_id: int):
        """Stream a file's chunks so indexed search can stop as soon as it can."""
        connection = self._connection_or_raise()
        cursor = connection.execute(
            "SELECT kind, location, text, chunk_index FROM chunks WHERE file_id = ? ORDER BY chunk_index",
            (file_id,),
        )
        while True:
            rows = cursor.fetchmany(64)
            if not rows:
                return
            for kind, location, text, index in rows:
                yield _to_chunk(kind, location, text, index)

    def indexed_paths_under(self, root: str) -> set[str]:
        connection = self._connection_or_raise()
        prefix = _root_prefix(root)
        rows = connection.execute(
            "SELECT path_key FROM files WHERE substr(path_key, 1, ?) = ?",
            (len(prefix), prefix),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def known_count(self) -> int:
        return int(self._connection_or_raise().execute("SELECT COUNT(*) FROM files").fetchone()[0])


def _to_chunk(kind, location, text, index) -> Chunk:
    from ..core.models import ChunkKind

    try:
        chunk_kind = ChunkKind(str(kind))
    except ValueError:
        chunk_kind = ChunkKind.LINE
    return Chunk(text=str(text), kind=chunk_kind, location=str(location), sequence=int(index))


def _norm(text: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKC", text).casefold()


def _root_prefix(root: str) -> str:
    """Normalised prefix that matches every indexed path under ``root``."""
    return normalized_key(root).rstrip("\\/") + os.sep


def extraction_fingerprint(
    *, search_formula: bool, include_archives: bool, ocr_mode: str, ocr_languages: str
) -> str:
    """Extraction settings that change stored text, folded into the version.

    Anything that changes extracted content must invalidate index rows, or an
    indexed search could disagree with a direct one (for example formulas
    excluded during a first pass and requested later).
    """
    return (
        f"{EXTRACTOR_VERSION}|f{int(bool(search_formula))}"
        f"|a{int(bool(include_archives))}|o{ocr_mode}|{ocr_languages}"
    )


def _part(text: str) -> str:
    from ..core.normalize import canonical_part

    return canonical_part(text, case_sensitive=False)


def _is_corruption(exc: sqlite3.Error) -> bool:
    message = str(exc).lower()
    return "malformed" in message or "not a database" in message or "corrupt" in message
