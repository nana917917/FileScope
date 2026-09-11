"""Index-assisted search.

The index is used to *narrow the candidate set*; the verdict always comes from
the same :class:`QueryMatcher` that direct search uses, running over the stored
chunk text. Two consequences that matter:

* direct and indexed searches cannot disagree (spec section 14/81), because the
  matching code and the matched text are identical;
* an index that cannot express a term (Japanese 1-2 character queries, wildcard
  part numbers) never silently drops results -- it falls back to a scan of the
  cached text instead.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from ..core.matcher import MatchOptions, Outcome, QueryMatcher, RuntimeFlags
from ..core.models import CloudState, FileEntry, SourceType
from ..core.query import And, Meta, Near, Node, NOf, Not, Or, Term
from ..logging_setup import get_logger
from .database import IndexDatabase
from .schema import quote_for_fts

log = get_logger("index-search")

SHORT_TERM_LENGTH = 3          # trigram indexes cannot answer 1-2 char queries
TYPE_EXTENSIONS = {
    "pdf": [".pdf"],
    "excel": [".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlsb"],
    "word": [".docx", ".docm"],
    "ppt": [".pptx", ".pptm"],
    "text": [".txt", ".csv", ".tsv", ".log", ".md", ".ini", ".json", ".xml", ".html"],
    "archive": [".zip"],
}


class IndexSearcher:
    def __init__(self, database: IndexDatabase, matcher: QueryMatcher) -> None:
        self.db = database
        self.matcher = matcher
        self.options = matcher.options

    # ------------------------------------------------------- candidate set
    def candidate_files(self, *, limit: int = 0) -> list[FileEntry]:
        """Indexed files that could satisfy the query (a superset)."""
        try:
            file_ids = self._candidate_ids()
            return self._load_entries(file_ids, limit=limit)
        except sqlite3.DatabaseError as exc:
            log.warning("index candidate query failed: %s", exc)
            return []

    def _candidate_ids(self) -> set[int]:
        terms = list(_positive_terms(self.matcher.root))
        sql_filters, params = _conjunctive_sql(self.matcher.root)
        if not terms:
            query = "SELECT id FROM files WHERE status NOT IN ('skipped','too_large')"
            if sql_filters:
                query += " AND " + " AND ".join(sql_filters)
            rows = self.db._connection_or_raise().execute(query, params).fetchall()
            return {int(row[0]) for row in rows}

        ids: set[int] = set()
        for term in terms:
            ids |= self._ids_for_term(term)
        return ids

    def _ids_for_term(self, term: Term) -> set[int]:
        pattern = self.matcher.pattern(term)
        connection = self.db._connection_or_raise()
        ids: set[int] = set()
        wildcard = pattern.wildcard
        if wildcard is not None:
            like = "%" + pattern.part.replace("%", r"\%").replace("_", r"\_").replace("*", "%") + "%"
            rows = connection.execute(
                "SELECT DISTINCT file_id FROM chunks WHERE part_text LIKE ? ESCAPE '\\'", (like,)
            ).fetchall()
            return {int(row[0]) for row in rows}
        if pattern.part and len(pattern.part) >= SHORT_TERM_LENGTH:
            ids |= self._fts_ids(connection, "chunks_fts_part", "part_text", pattern.part)
        if pattern.norm and len(pattern.norm) >= SHORT_TERM_LENGTH:
            ids |= self._fts_ids(connection, "chunks_fts", "norm_text", pattern.norm)
        if ids:
            return ids
        # Short (1-2 character) or non-trigram-safe terms: scan cached text.
        needle = pattern.norm or pattern.part
        if not needle:
            return set()
        column = "norm_text" if pattern.norm else "part_text"
        like = "%" + needle.replace("%", r"\%").replace("_", r"\_") + "%"
        rows = connection.execute(
            f"SELECT DISTINCT file_id FROM chunks WHERE {column} LIKE ? ESCAPE '\\'", (like,)
        ).fetchall()
        return {int(row[0]) for row in rows}

    def _fts_ids(self, connection, table: str, column: str, needle: str) -> set[int]:
        try:
            rows = connection.execute(
                f"SELECT chunks.file_id FROM {table} JOIN chunks ON chunks.id = {table}.rowid"
                f" WHERE {table}.{column} MATCH ?",
                (quote_for_fts(needle),),
            ).fetchall()
        except sqlite3.Error as exc:
            log.debug("fts query failed (%s): %s", table, exc)
            return set()
        return {int(row[0]) for row in rows}

    def _load_entries(self, file_ids: set[int], *, limit: int = 0) -> list[FileEntry]:
        if not file_ids:
            return []
        connection = self.db._connection_or_raise()
        entries: list[FileEntry] = []
        ids = sorted(file_ids)
        chunk = 900  # stay well under SQLite's parameter limit
        for start in range(0, len(ids), chunk):
            batch = ids[start : start + chunk]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                "SELECT display_path, size, mtime_ns, extension, source_type, cloud_state"
                f" FROM files WHERE id IN ({placeholders}) AND status NOT IN ('skipped','too_large')",
                batch,
            ).fetchall()
            for path, size, mtime_ns, extension, source_type, cloud_state in rows:
                entries.append(
                    FileEntry(
                        path=str(path),
                        size=int(size),
                        mtime_ns=int(mtime_ns),
                        extension=str(extension),
                        source_type=_source_type(str(source_type)),
                        cloud_state=_cloud_state(str(cloud_state)),
                    )
                )
            if limit and len(entries) >= limit:
                break
        return entries

    # ------------------------------------------------------------ per file
    def evaluate(
        self, path: str, entry: FileEntry, flags: RuntimeFlags | None = None
    ) -> tuple[Outcome, object] | None:
        row = self.db.file_row(path)
        if row is None or row[1] in ("skipped", "too_large", "empty"):
            return None
        file_id = row[0]
        state = self.matcher.make_state(entry, flags=flags)
        # Stream the stored chunks so an early decision stops the read, exactly
        # like the direct path (JIT phase 1).
        for chunk in self.db.iter_chunks(file_id):
            outcome = state.feed(chunk)
            if outcome is Outcome.ACCEPT:
                return outcome, state
            if outcome is Outcome.REJECT:
                return outcome, state
        return state.finish(), state


def _positive_terms(node: Node | None) -> list[Term]:
    if node is None:
        return []
    if isinstance(node, Term):
        return [node]
    if isinstance(node, Not):
        return []
    if isinstance(node, (And, Or, NOf)):
        found: list[Term] = []
        for child in node.children:
            found.extend(_positive_terms(child))
        return found
    if isinstance(node, Near):
        return _positive_terms(node.left) + _positive_terms(node.right)
    return []


def _conjunctive_metas(node: Node | None):
    """Metadata filters that are ANDed at the top level (safe to apply in SQL)."""
    if node is None:
        return
    if isinstance(node, And):
        for child in node.children:
            yield from _conjunctive_metas(child)
    elif isinstance(node, Meta):
        yield node


def _conjunctive_sql(node: Node | None) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for meta in _conjunctive_metas(node):
        if meta.field == "type" and meta.op == "eq":
            extensions = TYPE_EXTENSIONS.get(meta.value, [])
            if extensions:
                clauses.append("extension IN (" + ",".join("?" for _ in extensions) + ")")
                params.extend(extensions)
        elif meta.field == "ext" and meta.op == "in":
            extensions = ["." + value.lstrip(".") for value in meta.value.split(",") if value]
            if extensions:
                clauses.append("extension IN (" + ",".join("?" for _ in extensions) + ")")
                params.extend(extensions)
        elif meta.field == "size":
            clause, values = _size_clause(meta)
            if clause:
                clauses.append(clause)
                params.extend(values)
        elif meta.field == "modified":
            clause, values = _modified_clause(meta)
            if clause:
                clauses.append(clause)
                params.extend(values)
        elif meta.field == "source" and meta.op == "eq":
            clauses.append("source_type = ?")
            params.append(meta.value)
        elif (meta.field == "name" and meta.op == "contains") or (meta.field == "path" and meta.op == "contains"):
            clauses.append("display_path LIKE ? ESCAPE '\\'")
            params.append("%" + meta.value.replace("%", r"\%").replace("_", r"\_") + "%")
    return clauses, params


def _size_clause(meta: Meta) -> tuple[str, list[object]]:
    if meta.op == "range":
        low, _, high = meta.value.partition("..")
        clauses, params = [], []
        if low:
            clauses.append("size >= ?")
            params.append(int(low))
        if high:
            clauses.append("size <= ?")
            params.append(int(high))
        return " AND ".join(clauses), params
    operator = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "="}.get(meta.op)
    if operator is None:
        return "", []
    try:
        return f"size {operator} ?", [int(meta.value)]
    except ValueError:
        return "", []


def _modified_clause(meta: Meta) -> tuple[str, list[object]]:
    def to_ns(value: str) -> int:
        moment = dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC)
        return int(moment.timestamp() * 1_000_000_000)

    if meta.op == "range":
        low, _, high = meta.value.partition("..")
        clauses, params = [], []
        if low:
            clauses.append("mtime_ns >= ?")
            params.append(to_ns(low))
        if high:
            clauses.append("mtime_ns <= ?")
            params.append(to_ns(high))
        return " AND ".join(clauses), params
    operator = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "="}.get(meta.op)
    if operator is None:
        return "", []
    return f"mtime_ns {operator} ?", [to_ns(meta.value)]


def _source_type(value: str) -> SourceType:
    try:
        return SourceType(value)
    except ValueError:
        return SourceType.UNKNOWN


def _cloud_state(value: str) -> CloudState:
    try:
        return CloudState(value)
    except ValueError:
        return CloudState.UNKNOWN


_ = MatchOptions
