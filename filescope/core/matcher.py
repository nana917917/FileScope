"""Streaming matcher.

Semantics preserved from v4:

* every term is matched against the whole file (different sheets, pages or
  slides may hold different terms -- AND is file level),
* hyphen/whitespace folding and ``*`` wildcards in part-number mode,
* NFKC width folding and case folding per the UI options,
* excluded terms reject the file when found in the searched text.

Added in V5:

* boolean structure from the query AST (AND / OR / NOT / N-of-M / NEAR),
* incremental evaluation so a file can be accepted as soon as the condition is
  provably satisfied ("JIT"), while NOT conditions always read to the end,
* per-term evidence collection, occurrence counting and a deterministic score.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from enum import Enum

from . import normalize
from .models import Chunk, ChunkKind, Evidence, FileEntry
from .paths import classify_kind
from .query import And, Meta, Near, Node, NOf, Not, Or, Term


class Outcome(str, Enum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True)
class MatchOptions:
    case_sensitive: bool = False
    ignore_width: bool = True
    part_number_mode: bool = False
    max_evidence_per_term: int = 3
    #: When False, the file name / folder chunks are ignored so that a search
    #: with the option off behaves identically through the index (where those
    #: chunks are always stored).
    include_path_names: bool = True


@dataclass
class RuntimeFlags:
    """Search-time state used by the ``confirmed:`` and ``ocr:`` filters."""

    confirmed: bool = False
    roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermPattern:
    """One compiled search term."""

    text: str
    phrase: bool
    norm: str
    part: str
    wildcard: re.Pattern[str] | None = None

    def matches_text(self, haystack_norm: str, haystack_part: str) -> bool:
        if self.norm and self.norm in haystack_norm:
            return True
        if not self.part:
            return False
        if self.wildcard is not None:
            return self.wildcard.search(haystack_part) is not None
        return self.part in haystack_part

    def count(self, haystack_norm: str, haystack_part: str) -> int:
        if self.wildcard is not None:
            return sum(1 for _ in self.wildcard.finditer(haystack_part))
        total = 0
        if self.norm:
            total += haystack_norm.count(self.norm)
        if not total and self.part:
            total += haystack_part.count(self.part)
        return total

    def key(self) -> str:
        return self.norm or self.part


def compile_pattern(text: str, phrase: bool, options: MatchOptions) -> TermPattern:
    norm = normalize.normalize_text(
        text, ignore_width=options.ignore_width, case_sensitive=options.case_sensitive
    )
    part = ""
    wildcard = None
    if options.part_number_mode:
        part = normalize.canonical_part(text, case_sensitive=options.case_sensitive, keep_star=True)
        if "*" in part:
            wildcard = normalize.part_wildcard_regex(part)
    return TermPattern(text=text, phrase=phrase, norm=norm, part=part, wildcard=wildcard)


def compact(node: Node) -> str:
    """v4-style compact display, e.g. ``AAA&BBB`` or ``2of(A,B,C)``."""
    if isinstance(node, Term):
        return f'"{node.text}"' if node.phrase else node.text
    if isinstance(node, Meta):
        return node.display()
    if isinstance(node, Not):
        return f"!{compact(node.child)}"
    if isinstance(node, And):
        return "&".join(_maybe_wrap(child, Or) for child in node.children)
    if isinstance(node, Or):
        return "|".join(_maybe_wrap(child, And) for child in node.children)
    if isinstance(node, NOf):
        return f"{node.count}of({','.join(compact(child) for child in node.children)})"
    if isinstance(node, Near):
        return f"NEAR({compact(node.left)},{compact(node.right)},{node.distance})"
    return ""


def _maybe_wrap(node: Node, inner: type) -> str:
    text = compact(node)
    return f"({text})" if isinstance(node, inner) else text


class QueryMatcher:
    """Immutable matcher for one search run."""

    def __init__(self, root: Node | None, options: MatchOptions | None = None) -> None:
        self.root = root
        self.options = options or MatchOptions()
        self._patterns: dict[str, TermPattern] = {}
        self._near_nodes: dict[int, Near] = {}
        self._collect(root)
        self._negated_keys = {
            _term_key(term)
            for term in _terms_under_not(root)
        }
        self.requires_eof = not _is_monotone(root)
        self.positive_terms = _positive_terms(root)

    def _collect(self, node: Node | None) -> None:
        if node is None:
            return
        if isinstance(node, Term):
            self._patterns.setdefault(_term_key(node), compile_pattern(node.text, node.phrase, self.options))
        elif isinstance(node, Not):
            self._collect(node.child)
        elif isinstance(node, (And, Or, NOf)):
            for child in node.children:
                self._collect(child)
        elif isinstance(node, Near):
            self._near_nodes[id(node)] = node
            self._collect(node.left)
            self._collect(node.right)

    def pattern(self, term: Term) -> TermPattern:
        return self._patterns[_term_key(term)]

    def term_texts(self) -> list[str]:
        seen: list[str] = []
        for pattern in self._patterns.values():
            if pattern.text not in seen:
                seen.append(pattern.text)
        return seen

    def make_state(
        self,
        entry: FileEntry,
        *,
        name: str = "",
        directory: str = "",
        flags: RuntimeFlags | None = None,
    ) -> FileMatchState:
        return FileMatchState(
            matcher=self,
            entry=entry,
            name=name or os.path.basename(entry.path),
            directory=directory or os.path.dirname(entry.path),
            flags=flags or RuntimeFlags(),
        )


def _term_key(term: Term) -> str:
    return ("P:" if term.phrase else "T:") + term.text.casefold()


def _is_monotone(node: Node | None) -> bool:
    if node is None:
        return True
    if isinstance(node, Not):
        return False
    if isinstance(node, Meta):
        # confirmed:/ocr: describe the finished result, so they can flip late.
        return node.field not in ("confirmed", "ocr")
    if isinstance(node, (And, Or, NOf)):
        return all(_is_monotone(child) for child in node.children)
    return True


def _positive_terms(node: Node | None) -> list[Term]:
    found: list[Term] = []
    if node is None:
        return found
    if isinstance(node, Term):
        found.append(node)
    elif isinstance(node, Not):
        return found
    elif isinstance(node, (And, Or, NOf)):
        for child in node.children:
            found.extend(_positive_terms(child))
    elif isinstance(node, Near):
        found.extend(_positive_terms(node.left))
        found.extend(_positive_terms(node.right))
    return found


def _terms_under_not(node: Node | None) -> list[Term]:
    found: list[Term] = []
    if node is None:
        return found
    if isinstance(node, Not):
        found.extend(term for term in _all_terms(node.child))
    elif isinstance(node, (And, Or, NOf)):
        for child in node.children:
            found.extend(_terms_under_not(child))
    elif isinstance(node, Near):
        found.extend(_terms_under_not(node.left))
        found.extend(_terms_under_not(node.right))
    return found


def _all_terms(node: Node | None) -> list[Term]:
    return [item for item in _iter_nodes(node) if isinstance(item, Term)]


def _iter_nodes(node: Node | None):
    if node is None:
        return
    yield node
    if isinstance(node, Not):
        yield from _iter_nodes(node.child)
    elif isinstance(node, (And, Or, NOf)):
        for child in node.children:
            yield from _iter_nodes(child)
    elif isinstance(node, Near):
        yield from _iter_nodes(node.left)
        yield from _iter_nodes(node.right)


@dataclass
class FileMatchState:
    """Incremental evaluation state for one file."""

    matcher: QueryMatcher
    entry: FileEntry
    name: str = ""
    directory: str = ""
    flags: RuntimeFlags = field(default_factory=RuntimeFlags)

    _present: set[str] = field(default_factory=set, init=False)
    _satisfied_near: set[int] = field(default_factory=set, init=False)
    _evidence: dict[str, list[Evidence]] = field(default_factory=dict, init=False)
    _ocr_hit: bool = field(default=False, init=False)
    _name_hit: bool = field(default=False, init=False)
    _path_hit: bool = field(default=False, init=False)
    hit_count: int = field(default=0, init=False)
    hit_count_exact: bool = field(default=True, init=False)
    units_scanned: int = field(default=0, init=False)

    # ------------------------------------------------------------- feeding
    def feed(self, chunk: Chunk) -> Outcome:
        options = self.matcher.options
        text = chunk.text
        if not text:
            return Outcome.PENDING
        if chunk.kind in (ChunkKind.NAME, ChunkKind.PATH) and not options.include_path_names:
            return Outcome.PENDING
        self.units_scanned += 1
        hay_norm = normalize.normalize_text(
            text, ignore_width=options.ignore_width, case_sensitive=options.case_sensitive
        )
        hay_part = (
            normalize.canonical_part(text, case_sensitive=options.case_sensitive)
            if options.part_number_mode
            else ""
        )

        matched_here = False
        for term, pattern in self._iter_terms():
            if not pattern.matches_text(hay_norm, hay_part):
                continue
            self._present.add(pattern.key())
            if pattern.key() in self.matcher._negated_keys:
                # Negated terms decide acceptance; they are not part of the
                # hit count or the evidence list.
                continue
            matched_here = True
            occurrences = pattern.count(hay_norm, hay_part) or 1
            self.hit_count += occurrences
            if chunk.kind is ChunkKind.NAME:
                self._name_hit = True
            elif chunk.kind is ChunkKind.PATH:
                self._path_hit = True
            self._record_evidence(term, pattern, chunk, text)

        if chunk.kind is ChunkKind.OCR and (matched_here or not self.matcher.positive_terms):
            # OCR provenance is tracked even for pure-metadata queries such as
            # ``ocr:true``.
            self._ocr_hit = True

        for near in self.matcher._near_nodes.values():
            if id(near) in self._satisfied_near:
                continue
            if self._near_matches(near, hay_norm, hay_part):
                self._satisfied_near.add(id(near))
                terms = [t.text for t in (near.left, near.right) if isinstance(t, Term)]
                self._record_label_evidence(compact(near), chunk, text, terms)

        if self.matcher.requires_eof:
            return Outcome.PENDING
        if self._evaluate(self.matcher.root):
            return Outcome.ACCEPT
        if not self._can_still_match(self.matcher.root):
            return Outcome.REJECT
        return Outcome.PENDING

    def finish(self) -> Outcome:
        if self.matcher.root is None:
            return Outcome.ACCEPT
        return Outcome.ACCEPT if self._evaluate(self.matcher.root) else Outcome.REJECT

    def _iter_terms(self):
        for node in _iter_nodes(self.matcher.root):
            if isinstance(node, Term):
                yield node, self.matcher.pattern(node)

    def _record_evidence(self, term: Term, pattern: TermPattern, chunk: Chunk, text: str) -> None:
        bucket = self._evidence.setdefault(pattern.key(), [])
        if len(bucket) >= self.matcher.options.max_evidence_per_term:
            return
        bucket.append(
            Evidence(
                term=term.text,
                location=location_label(chunk),
                kind=chunk.kind,
                snippet=normalize.snippet(
                    text,
                    [term.text],
                    ignore_width=self.matcher.options.ignore_width,
                    case_sensitive=self.matcher.options.case_sensitive,
                ),
            )
        )

    def _record_label_evidence(self, label: str, chunk: Chunk, text: str, terms: list[str]) -> None:
        bucket = self._evidence.setdefault("@" + label, [])
        if bucket:
            return
        bucket.append(
            Evidence(
                term=label,
                location=location_label(chunk),
                kind=chunk.kind,
                snippet=normalize.snippet(text, terms, max_len=200),
            )
        )

    # ---------------------------------------------------------- evaluation
    def _near_matches(self, near: Near, hay_norm: str, hay_part: str) -> bool:
        if not isinstance(near.left, Term) or not isinstance(near.right, Term):
            return False
        left = self.matcher.pattern(near.left)
        right = self.matcher.pattern(near.right)
        if left.key() == right.key():
            positions = _find_all(hay_norm, left.norm) or _find_all(hay_part, left.part)
            if len(positions) < 2:
                return False
            return (positions[-1] - positions[0]) <= near.distance
        left_positions = _find_all(hay_norm, left.norm) or _find_all(hay_part, left.part)
        right_positions = _find_all(hay_norm, right.norm) or _find_all(hay_part, right.part)
        for left_pos in left_positions:
            for right_pos in right_positions:
                if abs(left_pos - right_pos) <= near.distance:
                    return True
        return False

    def _evaluate(self, node: Node | None) -> bool:
        if node is None:
            return True
        if isinstance(node, Term):
            return self.matcher.pattern(node).key() in self._present
        if isinstance(node, Meta):
            return self._meta_ok(node)
        if isinstance(node, Not):
            return not self._evaluate(node.child)
        if isinstance(node, And):
            return all(self._evaluate(child) for child in node.children)
        if isinstance(node, Or):
            return any(self._evaluate(child) for child in node.children)
        if isinstance(node, NOf):
            return sum(1 for child in node.children if self._evaluate(child)) >= node.count
        if isinstance(node, Near):
            return id(node) in self._satisfied_near
        return False

    def _can_still_match(self, node: Node | None) -> bool:
        """Conservative: False only when the node can never become true."""
        if node is None:
            return True
        if isinstance(node, Term):
            return True
        if isinstance(node, Meta):
            return node.field not in ("confirmed", "ocr") or self._meta_ok(node)
        if isinstance(node, Not):
            return True
        if isinstance(node, And):
            return all(self._can_still_match(child) for child in node.children)
        if isinstance(node, Or):
            return any(self._can_still_match(child) for child in node.children)
        if isinstance(node, NOf):
            return sum(1 for child in node.children if self._can_still_match(child)) >= node.count
        return True

    def _meta_ok(self, meta: Meta) -> bool:
        entry = self.entry
        if meta.field == "type":
            value = classify_kind(entry.extension)
            return getattr(value, "value", str(value)).lower() == meta.value
        if meta.field == "ext":
            wanted = {value.lstrip(".") for value in meta.value.split(",") if value}
            return entry.extension.lstrip(".").lower() in wanted
        if meta.field == "size":
            return _size_ok(meta, entry.size)
        if meta.field == "modified":
            return _date_ok(meta, entry.mtime_ns)
        if meta.field == "name":
            return _contains(meta, self.name)
        if meta.field == "path":
            return _contains(meta, entry.path)
        if meta.field == "source":
            return entry.source_type.value == meta.value
        if meta.field == "root":
            return any(_contains(meta, root) for root in (self.flags.roots or ()))
        if meta.field == "confirmed":
            return (meta.value == "true") == bool(self.flags.confirmed)
        if meta.field == "ocr":
            return (meta.value == "true") == bool(self._ocr_hit)
        return True

    # ------------------------------------------------------------ reporting
    @property
    def ocr_hit(self) -> bool:
        return self._ocr_hit

    def evidence(self) -> list[Evidence]:
        out: list[Evidence] = []
        for bucket in self._evidence.values():
            out.extend(bucket)
        return out

    def matched_terms(self) -> tuple[str, ...]:
        texts: list[str] = []
        for term in _positive_terms(self.matcher.root):
            pattern = self.matcher.pattern(term)
            if pattern.key() in self._present and term.text not in texts:
                texts.append(term.text)
        return tuple(texts)

    def displays(self) -> tuple[str, ...]:
        return _display_labels(self.matcher.root, self)

    def score(self) -> float:
        positives = _positive_terms(self.matcher.root)
        if not positives:
            return 0.0
        keys = {self.matcher.pattern(term).key() for term in positives}
        score = len(keys & self._present) / max(1, len(keys)) * 0.60
        if any(
            term.phrase for term in positives if self.matcher.pattern(term).key() in self._present
        ):
            score += 0.10
        if self._name_hit:
            score += 0.15
        if self._path_hit:
            score += 0.10
        score += min(self.hit_count, 20) / 20 * 0.05
        if self._ocr_hit:
            score -= 0.02
        return round(max(0.0, min(1.0, score)), 4)


def location_label(chunk: Chunk) -> str:
    base = chunk.location or chunk.kind.value
    if chunk.kind is ChunkKind.OCR and "[OCR]" not in base:
        return f"{base} [OCR]"
    if chunk.kind is ChunkKind.PAGE and "[PDF" not in base:
        return f"{base} [PDF文字]"
    return base


def _display_labels(node: Node | None, state: FileMatchState) -> tuple[str, ...]:
    if node is None:
        return ()
    if isinstance(node, Or):
        labels: list[str] = []
        for child in node.children:
            if state._evaluate(child):
                label = compact(child)
                if label and label not in labels:
                    labels.append(label)
        return tuple(labels)
    if state._evaluate(node):
        label = compact(node)
        return (label,) if label else ()
    return ()


def _find_all(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    out: list[int] = []
    start = haystack.find(needle)
    while start >= 0:
        out.append(start)
        start = haystack.find(needle, start + 1)
        if len(out) > 64:  # bounded: only proximity matters
            break
    return out


def _contains(meta: Meta, value: str) -> bool:
    haystack = normalize.normalize_text(value, ignore_width=True, case_sensitive=False)
    needle = normalize.normalize_text(meta.value, ignore_width=True, case_sensitive=False)
    if meta.op == "eq":
        return haystack == needle
    return needle in haystack


def _size_ok(meta: Meta, size: int) -> bool:
    if meta.op == "range":
        low, _, high = meta.value.partition("..")
        ok_low = size >= int(low) if low else True
        ok_high = size <= int(high) if high else True
        return ok_low and ok_high
    try:
        target = int(meta.value)
    except ValueError:
        return True
    return {
        "lt": size < target,
        "le": size <= target,
        "gt": size > target,
        "ge": size >= target,
        "eq": size == target,
    }.get(meta.op, True)


def _date_ok(meta: Meta, mtime_ns: int) -> bool:
    moment = dt.datetime.fromtimestamp(mtime_ns / 1_000_000_000).date()
    if meta.op == "range":
        low, _, high = meta.value.partition("..")
        ok_low = moment >= dt.date.fromisoformat(low) if low else True
        ok_high = moment <= dt.date.fromisoformat(high) if high else True
        return ok_low and ok_high
    target = dt.date.fromisoformat(meta.value)
    return {
        "lt": moment < target,
        "le": moment <= target,
        "gt": moment > target,
        "ge": moment >= target,
        "eq": moment == target,
    }.get(meta.op, True)


_ = re
