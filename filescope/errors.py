"""Error taxonomy.

V4 code mostly swallowed exceptions (``except Exception: pass``), which made
"no hits" and "could not be read" look identical. V5 classifies every failure
so the UI can show coverage honestly and logs stay useful.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    SKIP = "skip"      # expected: unsupported type, online-only file, too large
    WARN = "warn"      # recoverable: OCR timeout, locked file, staged copy reused
    ERROR = "error"    # the file should have been searchable but was not


class FileScopeError(Exception):
    """Base class for all FileScope-specific failures."""


class QuerySyntaxError(FileScopeError):
    def __init__(self, message: str, position: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.position = position

    def pretty(self, query: str = "") -> str:
        if not query:
            return self.message
        caret = " " * max(0, min(self.position, len(query))) + "^"
        return f"{self.message}\n{query}\n{caret}"


class SearchCancelled(FileScopeError):
    """Raised inside workers when the user cancels or closes the search."""


class StopExtraction(FileScopeError):
    """Raised by a chunk sink when the coordinator no longer needs the file."""


class IndexUnavailable(FileScopeError):
    """Raised when the cache database is missing, disabled or corrupt."""


@dataclass(frozen=True)
class Issue:
    path: str
    code: str
    message: str
    severity: Severity = Severity.WARN
    detail: str = ""

    def as_row(self) -> tuple[str, str, str, str]:
        return (self.severity.value.upper(), self.code, self.path, self.message)
