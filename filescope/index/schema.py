"""SQLite schema and FTS5 capability detection.

Two FTS tables are maintained:

* ``chunks_fts`` over the NFKC + case-folded text. Used for terms of three or
  more characters, where the trigram tokenizer can answer substring queries.
* ``chunks_fts_part`` over the part-number canonical form (separators removed),
  so ``ABC123`` still finds ``ABC-123`` when part-number mode is on.

Short terms (1-2 characters, extremely common in Japanese) cannot use a trigram
index at all; they fall back to a bounded ``LIKE`` scan, which is correct even
if it is slower. That is deliberate: a search that silently misses 「評価」
because the index cannot express it is worse than a slower search.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

FILES_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    display_path TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'local',
    cloud_state TEXT NOT NULL DEFAULT 'local',
    extension TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    extractor_version TEXT NOT NULL DEFAULT '',
    ocr_version TEXT NOT NULL DEFAULT '',
    ocr_language TEXT NOT NULL DEFAULT '',
    indexed_at REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    text_chars INTEGER NOT NULL DEFAULT 0
)
"""

CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    kind TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    norm_text TEXT NOT NULL,
    part_text TEXT NOT NULL
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id)",
    "CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension)",
    "CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)",
)

TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, norm_text) VALUES (new.id, new.norm_text);
        INSERT INTO chunks_fts_part(rowid, part_text) VALUES (new.id, new.part_text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, norm_text) VALUES ('delete', old.id, old.norm_text);
        INSERT INTO chunks_fts_part(chunks_fts_part, rowid, part_text) VALUES ('delete', old.id, old.part_text);
    END
    """,
)


def fts_available(connection: sqlite3.Connection) -> tuple[bool, bool]:
    """Return ``(fts5, trigram)`` availability for this SQLite build."""
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.__probe USING fts5(x)")
        connection.execute("DROP TABLE IF EXISTS temp.__probe")
        fts5 = True
    except sqlite3.Error:
        return False, False
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.__probe2 USING fts5(x, tokenize='trigram')"
        )
        connection.execute("DROP TABLE IF EXISTS temp.__probe2")
        trigram = True
    except sqlite3.Error:
        trigram = False
    return fts5, trigram


def create_schema(connection: sqlite3.Connection, *, trigram: bool) -> None:
    connection.execute(META_TABLE)
    connection.execute(FILES_TABLE)
    connection.execute(CHUNKS_TABLE)
    for statement in INDEXES:
        connection.execute(statement)
    tokenizer = "trigram" if trigram else "unicode61"
    connection.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            norm_text, content='chunks', content_rowid='id', tokenize='{tokenizer}'
        )
        """
    )
    connection.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_part USING fts5(
            part_text, content='chunks', content_rowid='id', tokenize='{tokenizer}'
        )
        """
    )
    for statement in TRIGGERS:
        connection.execute(statement)


def quote_for_fts(text: str) -> str:
    """Quote a string for an FTS5 MATCH expression (no user text is concatenated raw)."""
    return '"' + text.replace('"', '""') + '"'
