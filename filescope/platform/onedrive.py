"""Policy for online-only (Files On-Demand) files.

FileScope never changes OneDrive settings: it only decides whether reading a
placeholder's content is acceptable for the current search mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import CloudState, FileEntry


@dataclass(frozen=True)
class CloudDecision:
    read_content: bool
    reason: str
    counted_as_skipped: bool = False


def decide(entry: FileEntry, *, mode: str, policy: str, indexed: bool = False) -> CloudDecision:
    """Decide whether the body of ``entry`` may be read.

    mode:    fast / standard / full      (spec section 7)
    policy:  auto / skip / fetch         (spec section 20)
    indexed: the file already has index rows, so no download is needed.
    """
    if entry.cloud_state is not CloudState.ONLINE_ONLY:
        return CloudDecision(True, "")

    if policy == "skip":
        return CloudDecision(False, "オンライン専用（取得しない設定）", True)
    if policy == "fetch":
        return CloudDecision(True, "オンライン専用（取得する設定）")

    # auto: follow the search mode.
    if mode == "fast":
        return CloudDecision(False, "オンライン専用（高速モードでは取得しない）", True)
    if indexed:
        return CloudDecision(False, "オンライン専用（索引済みのため取得不要）")
    if mode == "full":
        return CloudDecision(True, "オンライン専用（完全モードで取得）")
    return CloudDecision(True, "オンライン専用（標準モードで取得）")
