"""Events sent from the pipeline to the UI thread.

The UI never blocks on file IO: workers only push these small objects into a
bounded queue, and the UI coalesces them on a timer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import Issue
from .models import Coverage, FileResult, Progress, Summary


@dataclass
class Started:
    query: str
    mode: str
    roots: tuple[str, ...]


@dataclass
class Discovered:
    count: int
    root: str = ""


@dataclass
class Progressed:
    progress: Progress


@dataclass
class ResultAdded:
    result: FileResult
    replace: bool = False


@dataclass
class IssueAdded:
    issue: Issue


@dataclass
class CoverageChanged:
    coverage: Coverage
    index_coverage: tuple[int, int] = (0, 0)


@dataclass
class Finished:
    summary: Summary


@dataclass
class QueryFailed:
    message: str
    position: int = 0
    query: str = ""


@dataclass
class Batch:
    """Optional grouping so the UI can refresh once per batch."""

    results: list[FileResult] = field(default_factory=list)
