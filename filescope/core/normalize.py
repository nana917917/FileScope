"""Text normalisation shared by the matcher, the index and the query parser.

V4 rules that must not regress:

* full-width / half-width folding (NFKC) when the option is enabled,
* case folding unless case sensitivity is requested,
* part numbers ignore hyphen variants and whitespace, and support ``*``
  wildcards, but never fuzzy-match digits or letters that differ.
"""

from __future__ import annotations

import re
import unicodedata

# Hyphen-like characters used by CAD/BOM exports (v4 behaviour, unchanged).
HYPHEN_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u30fc\uff70\uff0d"

_FULLWIDTH_OPERATOR_MAP = {
    "\uff06": "&",   # ＆
    "\uff0c": ",",   # ，
    "\u3001": ",",   # 、
    "\uff1b": ";",   # ；
    "\uff5c": "|",   # ｜
    "\uff0f": "/",   # ／
    "\u3000": " ",   # ideographic space
}

_AND_WORDS = re.compile(r"(?<![^\W\d_])(かつ|且つ|and)(?![^\W\d_])", re.IGNORECASE)
_OR_WORDS = re.compile(r"(?<![^\W\d_])(または|又は|もしくは|若しくは|or)(?![^\W\d_])", re.IGNORECASE)


def fold_operators(text: str) -> str:
    """Map full-width punctuation to ASCII equivalents (v4 did this for queries)."""
    for old, new in _FULLWIDTH_OPERATOR_MAP.items():
        text = text.replace(old, new)
    text = _AND_WORDS.sub("&", text)
    text = _OR_WORDS.sub(",", text)
    return text


def normalize_text(text: object, *, ignore_width: bool, case_sensitive: bool) -> str:
    if text is None:
        return ""
    value = str(text)
    if ignore_width:
        value = unicodedata.normalize("NFKC", value)
    if not case_sensitive:
        value = value.casefold()
    return value


def canonical_part(text: object, *, case_sensitive: bool = False, keep_star: bool = False) -> str:
    """Strip separators that are meaningless in part numbers."""
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    if not case_sensitive:
        value = value.casefold()
    out: list[str] = []
    for ch in value:
        if ch in HYPHEN_CHARS or ch.isspace():
            continue
        if keep_star and ch == "*":
            out.append(ch)
            continue
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def part_wildcard_regex(part: str) -> re.Pattern[str]:
    return re.compile(re.escape(part).replace(r"\*", ".*"))


def collapse_whitespace(text: str) -> str:
    return " ".join(str(text or "").replace("\r", "\n").split())


def snippet(text: object, terms: list[str], *, max_len: int = 260, ignore_width: bool = True,
            case_sensitive: bool = False) -> str:
    """Return a window around the first matching term (v4 ``SearchMatcher.snippet``)."""
    value = collapse_whitespace(text if text is not None else "")
    if len(value) <= max_len:
        return value
    haystack = normalize_text(value, ignore_width=ignore_width, case_sensitive=case_sensitive)
    positions: list[int] = []
    for term in terms:
        needle = normalize_text(term, ignore_width=ignore_width, case_sensitive=case_sensitive)
        if not needle:
            continue
        found = haystack.find(needle)
        if found >= 0:
            positions.append(found)
    pos = min(positions) if positions else 0
    half = max_len // 2
    start = max(0, pos - half)
    end = min(len(value), start + max_len)
    prefix = "..." if start else ""
    suffix = "..." if end < len(value) else ""
    return prefix + value[start:end] + suffix
