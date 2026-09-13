"""Query language: tokenizer, recursive-descent parser, AST.

The parser keeps every v4 behaviour reachable and adds explicit operators.

Backwards compatible (v4):

* ``A B``            — joined by the AND/OR setting (AND inside an explicit clause)
* ``A&B``            — same file, both terms
* ``A,B`` / ``A;B``  — either term
* ``2of(A,B,C)``     — at least 2 of the listed terms, file level
* ``かつ`` / ``または`` — Japanese operator words
* full-width punctuation ``，＆；｜``

New in V5:

* ``A | B``          — OR
* ``!A`` / ``-A``    — NOT (``-`` stays literal for part-number-shaped tokens)
* ``(A | B) & C``    — grouping
* ``"耐久 試験"``     — exact phrase
* ``NEAR(電源,ノイズ,100)`` — both terms within 100 characters of one unit
* ``3of(A,B,C,D,E)`` — generalised N-of-M
* ``type:pdf size:<50MB modified:>=2025-01-01 name:評価`` — metadata filters

Design notes:

* An empty query parses to ``None``; the caller decides what that means.
* Metadata values may contain commas (``ext:xlsx,xls``); use ``|`` to OR two
  metadata filters.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..errors import QuerySyntaxError
from . import normalize

# --------------------------------------------------------------------------- AST


@dataclass(frozen=True)
class Term:
    """A single search word (``phrase`` set when it came from quotes)."""

    text: str
    phrase: bool = False


@dataclass(frozen=True)
class Not:
    child: Node


@dataclass(frozen=True)
class And:
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    children: tuple[Node, ...]


@dataclass(frozen=True)
class NOf:
    """At least ``count`` of ``children`` must match somewhere in the file."""

    count: int
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Near:
    """``left`` and ``right`` inside one extraction unit, within ``distance`` characters."""

    left: Node
    right: Node
    distance: int


@dataclass(frozen=True)
class Meta:
    """Metadata filter such as ``type:pdf`` or ``size:<50MB``."""

    field: str
    op: str
    value: str

    def display(self) -> str:
        symbol = {
            "contains": "",
            "eq": "",
            "lt": "<",
            "le": "<=",
            "gt": ">",
            "ge": ">=",
            "range": "",
        }.get(self.op, "")
        if self.op == "range":
            low, _, high = self.value.partition("..")
            return f"{self.field}:{_format_value(self.field, low)}..{_format_value(self.field, high)}"
        return f"{self.field}:{symbol}{_format_value(self.field, self.value)}"


def _format_value(field: str, value: str) -> str:
    if field == "size" and value.isdigit():
        return _human_size(int(value))
    return value


def _human_size(size: int) -> str:
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size and size % factor == 0:
            return f"{size // factor}{unit}"
    return f"{size}B"


Node = Term | Not | And | Or | NOf | Near | Meta


# ------------------------------------------------------------------ constants

METADATA_FIELDS = {
    "type",
    "ext",
    "name",
    "path",
    "size",
    "modified",
    "confirmed",
    "source",
    "ocr",
    "root",
}

TYPE_VALUES = {
    "pdf": "pdf",
    "excel": "excel",
    "xls": "excel",
    "xlsx": "excel",
    "word": "word",
    "doc": "word",
    "docx": "word",
    "ppt": "ppt",
    "powerpoint": "ppt",
    "pptx": "ppt",
    "text": "text",
    "txt": "text",
    "archive": "archive",
    "zip": "archive",
}

SOURCE_VALUES = {"local", "smb", "onedrive", "removable", "unknown"}

SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}

_FUNCTION_OF = re.compile(r"^(\d+)\s*of$", re.IGNORECASE)
_META_TOKEN = re.compile(r"^([A-Za-z_]+):(.*)$", re.DOTALL)
_OPERATOR_CHARS = set("()&|!,;")
_EXPLICIT_BINARY = set("&|,;")


class _TokenKind:
    WORD = "WORD"
    PHRASE = "PHRASE"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    META = "META"


@dataclass
class _Token:
    kind: str
    text: str
    start: int
    end: int
    value: object = None


def tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(_Token(_TokenKind.LPAREN, ch, i, i + 1))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token(_TokenKind.RPAREN, ch, i, i + 1))
            i += 1
            continue
        if ch == "&":
            tokens.append(_Token(_TokenKind.AND, "&", i, i + 1))
            i += 1
            continue
        if ch in ",;":
            tokens.append(_Token(_TokenKind.OR, ch, i, i + 1))
            i += 1
            continue
        if ch == "|":
            tokens.append(_Token(_TokenKind.OR, "|", i, i + 1))
            i += 1
            continue
        if ch == "!":
            tokens.append(_Token(_TokenKind.NOT, "!", i, i + 1))
            i += 1
            continue
        if ch == '"':
            end = i + 1
            buffer: list[str] = []
            while end < length:
                if text[end] == "\\" and end + 1 < length and text[end + 1] == '"':
                    buffer.append('"')
                    end += 2
                    continue
                if text[end] == '"':
                    break
                buffer.append(text[end])
                end += 1
            if end >= length:
                raise QuerySyntaxError("引用符が閉じられていません", i)
            tokens.append(_Token(_TokenKind.PHRASE, "".join(buffer), i, end + 1))
            i = end + 1
            continue

        # Bare word. Metadata values may legitimately contain commas, so a
        # token starting with "field:" swallows commas until whitespace or a
        # structural character.
        start = i
        if ch == "-" and _dash_is_operator(text, i):
            tokens.append(_Token(_TokenKind.NOT, "-", i, i + 1))
            i += 1
            continue
        while i < length:
            current = text[i]
            if current.isspace() or current in "()&|!\";":
                break
            if current == "," and not _looks_like_metadata(text, start):
                break
            i += 1
        raw = text[start:i]
        if not raw:
            raise QuerySyntaxError(f"解釈できない文字です: {text[start]!r}", start)
        meta = _META_TOKEN.match(raw)
        if meta and meta.group(1).lower() in METADATA_FIELDS:
            tokens.append(_Token(_TokenKind.META, raw, start, i, value=meta.group(1).lower()))
            continue
        tokens.append(_Token(_TokenKind.WORD, raw, start, i))
    return tokens


def _dash_is_operator(text: str, index: int) -> bool:
    """``-ABC`` is NOT, but ``-123`` and ``ABC-123`` stay literal."""
    rest = text[index + 1 :]
    if not rest or rest[0].isspace() or rest[0] in "()":
        return True
    token = rest.split()[0].rstrip(")")
    if not token:
        return True
    if token[0].isdigit():
        return False
    return not any(ch in normalize.HYPHEN_CHARS or ch.isspace() for ch in token[1:])


def _looks_like_metadata(text: str, start: int) -> bool:
    window = text[start : start + 12]
    match = _META_TOKEN.match(window)
    return bool(match and match.group(1).lower() in METADATA_FIELDS)


class _Parser:
    def __init__(self, text: str, *, legacy_operator: str = "OR") -> None:
        self.text = text
        self.tokens = tokenize(text)
        self.index = 0
        has_explicit = any(token.kind in (_TokenKind.AND, _TokenKind.OR) for token in self.tokens)
        # v4 rule: "A B" follows the AND/OR choice only when the query has no
        # explicit operator at all; inside an explicit clause, space means AND.
        self.implicit_or = (not has_explicit) and legacy_operator.upper() == "OR"

    # ------------------------------------------------------------- utilities
    def peek(self) -> _Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def next(self) -> _Token | None:
        token = self.peek()
        if token is not None:
            self.index += 1
        return token

    def error(self, message: str, token: _Token | None = None) -> QuerySyntaxError:
        position = token.start if token is not None else len(self.text)
        return QuerySyntaxError(message, position)

    def starts_atom(self, token: _Token | None) -> bool:
        if token is None:
            return False
        if token.kind in (_TokenKind.WORD, _TokenKind.PHRASE, _TokenKind.META, _TokenKind.NOT, _TokenKind.LPAREN):
            return True
        return bool(token.kind == _TokenKind.WORD and _FUNCTION_OF.match(token.text))

    # ---------------------------------------------------------------- grammar
    def parse(self) -> Node | None:
        if not self.tokens:
            return None
        node = self.parse_or()
        leftover = self.peek()
        if leftover is not None:
            if leftover.kind == _TokenKind.RPAREN:
                raise self.error("対応する '(' がない ')' があります", leftover)
            raise self.error(f"解釈できないトークンです: {leftover.text!r}", leftover)
        return node

    def parse_or(self) -> Node:
        children = [self.parse_and()]
        while True:
            token = self.peek()
            if token is not None and token.kind == _TokenKind.OR:
                self.next()
                children.append(self.parse_and())
                continue
            if self._implicit_operator_here():
                if not self.implicit_or:
                    break
                children.append(self.parse_and())
                continue
            break
        if len(children) == 1:
            return children[0]
        return Or(tuple(_flatten(children, Or)))

    def parse_and(self) -> Node:
        children = [self.parse_not()]
        while True:
            token = self.peek()
            if token is not None and token.kind == _TokenKind.AND:
                self.next()
                children.append(self.parse_not())
                continue
            if self._implicit_operator_here() and not self.implicit_or:
                children.append(self.parse_not())
                continue
            break
        if len(children) == 1:
            return children[0]
        return And(tuple(_flatten(children, And)))

    def _implicit_operator_here(self) -> bool:
        """True when the next token continues the expression without an operator."""
        token = self.peek()
        if token is None or not self.starts_atom(token):
            return False
        if self.index == 0:
            return False
        previous = self.tokens[self.index - 1]
        if previous.end >= token.start:
            return False
        return previous.kind in (
            _TokenKind.WORD,
            _TokenKind.PHRASE,
            _TokenKind.META,
            _TokenKind.RPAREN,
        )

    def parse_not(self) -> Node:
        token = self.peek()
        if token is not None and token.kind == _TokenKind.NOT:
            if token.text == "-":
                # A bare "-" in front of a word is NOT; a part-number shaped
                # remainder was already excluded by the tokenizer.
                pass
            self.next()
            child = self.parse_not()
            return Not(child)
        return self.parse_atom()

    def parse_atom(self) -> Node:
        token = self.next()
        if token is None:
            raise QuerySyntaxError("検索式が途中で終わっています", len(self.text))
        if token.kind == _TokenKind.LPAREN:
            if self.peek() is not None and self.peek().kind == _TokenKind.RPAREN:
                raise self.error("空の括弧 '()' は使用できません", self.peek())
            inner = self.parse_or()
            closing = self.next()
            if closing is None or closing.kind != _TokenKind.RPAREN:
                raise self.error("')' が足りません", token)
            return inner
        if token.kind == _TokenKind.PHRASE:
            if not token.text.strip():
                raise QuerySyntaxError("空のフレーズは使用できません", token.start)
            return Term(token.text, phrase=True)
        if token.kind == _TokenKind.META:
            return _parse_metadata(token)
        if token.kind == _TokenKind.WORD:
            function = _FUNCTION_OF.match(token.text)
            if function:
                return self.parse_threshold(int(function.group(1)), token)
            if token.text.upper() == "NEAR":
                return self.parse_near(token)
            return Term(token.text)
        raise self.error(f"解釈できないトークンです: {token.text!r}", token)

    def parse_threshold(self, count: int, token: _Token) -> Node:
        opening = self.next()
        if opening is None or opening.kind != _TokenKind.LPAREN:
            raise self.error("N of (…) の形式で指定してください", token)
        children: list[Node] = []
        depth = 0
        while True:
            current = self.peek()
            if current is None:
                raise self.error("N of (…) の ')' が足りません", token)
            if current.kind == _TokenKind.RPAREN and depth == 0:
                self.next()
                break
            if current.kind == _TokenKind.OR:
                self.next()
                continue
            if current.kind == _TokenKind.LPAREN:
                depth += 1
                self.next()
                continue
            if current.kind == _TokenKind.RPAREN:
                depth -= 1
                self.next()
                continue
            children.append(self.parse_atom())
        if not children:
            raise self.error("N of (…) の中身がありません", token)
        if count < 1 or count > len(children):
            raise self.error(
                f"{count}of(...) の数が不正です（1〜{len(children)} の範囲で指定してください）", token
            )
        return NOf(count, tuple(children))

    def parse_near(self, token: _Token) -> Node:
        opening = self.next()
        if opening is None or opening.kind != _TokenKind.LPAREN:
            return Term(token.text)
        left = self.parse_near_argument(token)
        # Inside NEAR(…) a comma is a list separator, not an OR operator.
        self._consume_list_separator(token, "NEAR(A,B,距離)")
        right = self.parse_near_argument(token)
        distance = 100
        if self.peek() is not None and self.peek().kind == _TokenKind.OR:
            self._consume_list_separator(token, "NEAR(A,B,距離)")
            number = self.next()
            if number is None or not number.text.isdigit():
                raise self.error("NEAR の距離は数字で指定してください", number or token)
            distance = int(number.text)
        closing = self.next()
        if closing is None or closing.kind != _TokenKind.RPAREN:
            raise self.error("NEAR(…) の ')' が足りません", token)
        if distance < 1:
            raise self.error("NEAR の距離は 1 以上にしてください", token)
        return Near(left, right, distance)

    def _consume_list_separator(self, token: _Token, form: str) -> None:
        current = self.peek()
        if current is None or current.kind != _TokenKind.OR:
            raise self.error(f"{form} の形式で指定してください", token)
        self.next()

    def parse_near_argument(self, token: _Token) -> Node:
        current = self.peek()
        if current is None:
            raise self.error("NEAR(…) の引数が足りません", token)
        node = self.parse_atom()
        if isinstance(node, (And, Or, NOf, Near)):
            raise self.error("NEAR の引数には語句またはフレーズを指定してください", current)
        return node


def _flatten(children: Sequence[Node], kind: type) -> list[Node]:
    flat: list[Node] = []
    for child in children:
        if isinstance(child, kind):
            flat.extend(child.children)
        else:
            flat.append(child)
    return flat


def _parse_metadata(token: _Token) -> Meta:
    field = str(token.value)
    raw = token.text.split(":", 1)[1].strip().rstrip(",")
    if not raw:
        raise QuerySyntaxError(f"{field}: の値がありません", token.start)
    op = "contains"
    value = raw
    if field == "size":
        return _parse_size(raw, token)
    if field in ("modified", "created"):
        return _parse_date(field, raw, token)
    if field in ("confirmed", "ocr"):
        lowered = raw.lower()
        if lowered in ("true", "yes", "1", "済", "はい"):
            return Meta(field, "eq", "true")
        if lowered in ("false", "no", "0", "未", "いいえ"):
            return Meta(field, "eq", "false")
        raise QuerySyntaxError(f"{field}: は true / false で指定してください", token.start)
    if field == "type":
        mapped = TYPE_VALUES.get(raw.lower())
        if mapped is None:
            raise QuerySyntaxError(
                f"type: に不明な値です: {raw}（pdf / excel / word / ppt / text / archive）", token.start
            )
        return Meta(field, "eq", mapped)
    if field == "source":
        mapped = raw.lower()
        if mapped not in SOURCE_VALUES:
            raise QuerySyntaxError(
                f"source: に不明な値です: {raw}（local / smb / onedrive）", token.start
            )
        return Meta(field, "eq", mapped)
    if field == "ext":
        values = [v.strip().lower().lstrip(".") for v in raw.split(",") if v.strip()]
        return Meta(field, "in", ",".join(values))
    for prefix, prefix_op in (("<=", "le"), (">=", "ge"), ("<", "lt"), (">", "gt")):
        if value.startswith(prefix):
            return Meta(field, prefix_op, value[len(prefix) :])
    return Meta(field, op, unicodedata.normalize("NFKC", value))


def _parse_size(raw: str, token: _Token) -> Meta:
    if ".." in raw:
        low, high = raw.split("..", 1)
        return Meta("size", "range", f"{_size_bytes(low, token)}..{_size_bytes(high, token)}")
    for prefix, op in (("<=", "le"), (">=", "ge"), ("<", "lt"), (">", "gt")):
        if raw.startswith(prefix):
            return Meta("size", op, str(_size_bytes(raw[len(prefix) :], token)))
    return Meta("size", "ge", str(_size_bytes(raw, token)))


def _size_bytes(text: str, token: _Token) -> int:
    cleaned = text.strip().replace(" ", "").replace("　", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]*)", cleaned)
    if not match:
        raise QuerySyntaxError(f"size: の値を解釈できません: {text}", token.start)
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit not in SIZE_UNITS:
        raise QuerySyntaxError(f"size: の単位が不明です: {unit}", token.start)
    return int(number * SIZE_UNITS[unit])


def _parse_date(field: str, raw: str, token: _Token) -> Meta:
    if ".." in raw:
        low, high = raw.split("..", 1)
        return Meta(field, "range", f"{_date_str(low, token)}..{_date_str(high, token)}")
    for prefix, op in (("<=", "le"), (">=", "ge"), ("<", "lt"), (">", "gt")):
        if raw.startswith(prefix):
            return Meta(field, op, _date_str(raw[len(prefix) :], token))
    return Meta(field, "ge", _date_str(raw, token))


def _date_str(text: str, token: _Token) -> str:
    cleaned = unicodedata.normalize("NFKC", text.strip()).replace("/", "-").replace(".", "-")
    if not re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", cleaned):
        raise QuerySyntaxError(f"{token.value}: の日付は YYYY-MM-DD で指定してください: {text}", token.start)
    year, month, day = (int(part) for part in cleaned.split("-"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise QuerySyntaxError(f"日付が不正です: {text}", token.start)
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_query(text: str, *, legacy_operator: str = "OR") -> Node | None:
    """Parse ``text`` into an AST (``None`` for an empty query)."""
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    normalized = normalize.fold_operators(stripped)
    return _Parser(normalized, legacy_operator=legacy_operator).parse()


# ------------------------------------------------------------------ utilities


def walk(node: Node | None) -> Iterable[Node]:
    if node is None:
        return
    yield node
    if isinstance(node, Not):
        yield from walk(node.child)
    elif isinstance(node, (And, Or, NOf)):
        for child in node.children:
            yield from walk(child)
    elif isinstance(node, Near):
        yield from walk(node.left)
        yield from walk(node.right)


def terms(node: Node | None) -> list[Term]:
    """Positive (non-negated) terms in document order, duplicates removed."""
    seen: set[str] = set()
    out: list[Term] = []
    negated = set()
    for item in walk(node):
        if isinstance(item, Not):
            negated.update(id(t) for t in walk(item.child) if isinstance(t, Term))
    for item in walk(node):
        if isinstance(item, Term) and id(item) not in negated:
            key = item.text.casefold()
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def negative_terms(node: Node | None) -> list[Term]:
    out: list[Term] = []
    for item in walk(node):
        if isinstance(item, Not):
            out.extend(t for t in walk(item.child) if isinstance(t, Term))
    return out


def metadata_nodes(node: Node | None) -> list[Meta]:
    return [item for item in walk(node) if isinstance(item, Meta)]


def is_monotone(node: Node | None) -> bool:
    """True when satisfying the node can never be undone by more content.

    A NOT is non-monotone: a term found later can flip a satisfied file back to
    a miss, so those files must be read to the end before being reported.
    """
    return not any(isinstance(item, Not) for item in walk(node))


def requires_full_read(node: Node | None) -> bool:
    """True when the file must be scanned to EOF to decide the result."""
    # NEAR needs both terms inside one unit, but a unit is decided the moment it
    # is fed, so only NOT (and the confirmed:/ocr: filters) force a full read.
    return not is_monotone(node)


def display_name(node: Node | None) -> str:
    """Short human label used in the 一致条件 column."""
    if node is None:
        return ""
    if isinstance(node, Term):
        return node.text
    if isinstance(node, Meta):
        return node.display()
    if isinstance(node, Not):
        child = display_name(node.child)
        return f"!{child}"
    if isinstance(node, And):
        return "&".join(display_name(child) for child in node.children)
    if isinstance(node, Or):
        return "|".join(display_name(child) for child in node.children)
    if isinstance(node, NOf):
        return f"{node.count}of({','.join(display_name(child) for child in node.children)})"
    if isinstance(node, Near):
        return f"NEAR({display_name(node.left)},{display_name(node.right)},{node.distance})"
    return ""


def to_query_string(node: Node | None) -> str:
    """Serialise an AST back to query text (used by the condition builder)."""
    if node is None:
        return ""
    if isinstance(node, Term):
        if node.phrase or any(ch in node.text for ch in " ()&|!,\";"):
            return '"' + node.text.replace('"', '\\"') + '"'
        return node.text
    if isinstance(node, Meta):
        return node.display()
    if isinstance(node, Not):
        return f"!{_wrap(node.child)}"
    if isinstance(node, And):
        return " & ".join(_wrap(child) for child in node.children)
    if isinstance(node, Or):
        return " | ".join(_wrap(child) for child in node.children)
    if isinstance(node, NOf):
        return f"{node.count}of({', '.join(to_query_string(child) for child in node.children)})"
    if isinstance(node, Near):
        return f"NEAR({to_query_string(node.left)}, {to_query_string(node.right)}, {node.distance})"
    return ""


def _wrap(node: Node) -> str:
    text = to_query_string(node)
    if isinstance(node, (And, Or)):
        return f"({text})"
    return text


@dataclass
class BuilderGroups:
    """Input for the "condition builder" dialog (spec section 9)."""

    all_of: list[str] = field(default_factory=list)
    any_of: list[str] = field(default_factory=list)
    n_of_count: int = 0
    n_of: list[str] = field(default_factory=list)
    none_of: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    near_a: str = ""
    near_b: str = ""
    near_distance: int = 100
    metadata: list[str] = field(default_factory=list)

    def build(self) -> Node | None:
        parts: list[Node] = []
        if self.all_of:
            parts.append(And(tuple(Term(t) for t in self.all_of if t.strip())))
        if self.any_of:
            parts.append(Or(tuple(Term(t) for t in self.any_of if t.strip())))
        terms_n = [t for t in self.n_of if t.strip()]
        if terms_n and self.n_of_count > 0:
            parts.append(NOf(min(self.n_of_count, len(terms_n)), tuple(Term(t) for t in terms_n)))
        for phrase in self.phrases:
            if phrase.strip():
                parts.append(Term(phrase.strip(), phrase=True))
        if self.near_a.strip() and self.near_b.strip():
            parts.append(
                Near(Term(self.near_a.strip()), Term(self.near_b.strip()), max(1, int(self.near_distance)))
            )
        for meta in self.metadata:
            parsed = parse_query(meta)
            if parsed is not None:
                parts.append(parsed)
        node: Node | None = None
        for part in parts:
            node = part if node is None else And((node, part))
        for term in self.none_of:
            if not term.strip():
                continue
            negated = Not(Term(term.strip()))
            node = negated if node is None else And((node, negated))
        return node
