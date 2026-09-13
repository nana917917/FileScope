"""Preview race tests (spec sections 26-27).

Selecting A then B then C must end with C's content: a slow A must never
overwrite the pane, and the phase-2 callback must report the file it analysed.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import suppress

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.config import Settings
from filescope.core.models import Chunk, ChunkKind, CloudState, FileKind, FileResult, SourceType
from filescope.ui.preview import PreviewContent, PreviewPane
from filescope.ui.state import UiState

tk = pytest.importorskip("tkinter")
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


def make_result(tmp_path, name: str) -> FileResult:
    path = tmp_path / name
    path.write_text(f"{name} body\n", encoding="utf-8")
    return FileResult(
        path=str(path),
        file_kind=FileKind.TEXT,
        size=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
        source_type=SourceType.LOCAL,
        cloud_state=CloudState.LOCAL,
        matched_terms=(name,),
        displays=(name,),
        hit_count=1,
        hit_count_exact=False,
    )


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError:  # pragma: no cover - headless
        pytest.skip("Tk display unavailable")
    window.withdraw()
    yield window
    with suppress(tk.TclError):
        window.destroy()


def test_stale_preview_result_is_dropped(root, tmp_path) -> None:
    pane = PreviewPane(root)
    config = UiState(root=str(tmp_path)).to_config(Settings())
    slow = make_result(tmp_path, "slow.txt")
    fast = make_result(tmp_path, "fast.txt")

    def build_slow(_entry, _options, _chunks):
        time.sleep(0.5)
        return PreviewContent(title="slow.txt", lines=["slow content"], locations=["1行"])

    def build_fast(_entry, _options, _chunks):
        return PreviewContent(title="fast.txt", lines=["fast content"], locations=["1行"])

    pane._build = build_slow  # type: ignore[method-assign]
    pane.show(slow, config)
    deadline = time.time() + 5
    while time.time() < deadline:
        root.update()
        if "slow content" in pane.text.get("1.0", "end"):
            break
        time.sleep(0.05)
    assert "slow content" in pane.text.get("1.0", "end")

    pane._build = build_fast  # type: ignore[method-assign]
    pane.show(fast, config)
    pane._build = build_slow  # type: ignore[method-assign]
    deadline = time.time() + 5
    while time.time() < deadline:
        root.update()
        if "fast content" in pane.text.get("1.0", "end"):
            break
        time.sleep(0.05)
    assert "fast content" in pane.text.get("1.0", "end")
    assert pane.title_var.get() == "fast.txt"
    pane.destroy()


def test_phase2_callback_carries_its_own_file(root, tmp_path) -> None:
    """_on_preview_chunks must use the analysed file, not the current selection."""
    from filescope.ui.main_window import MainWindow

    class Fake:
        _on_preview_chunks = MainWindow._on_preview_chunks
        _current_matcher = MainWindow._current_matcher

        def __init__(self) -> None:
            import queue

            self._full_scan_queue = queue.Queue()
            self.state = UiState(query="AAA")

    fake = Fake()
    analysed = make_result(tmp_path, "analysed.txt")
    selected_now = make_result(tmp_path, "selected.txt")
    fake._on_preview_chunks(  # type: ignore[attr-defined]
        analysed, [Chunk(text="AAA AAA", kind=ChunkKind.LINE, location="1行")]
    )
    path, hits, _evidence, _terms, _displays = fake._full_scan_queue.get_nowait()
    assert path == analysed.path
    assert hits == 2
    assert selected_now.path != path
