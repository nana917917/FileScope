"""UI smoke tests: the window builds, searches, previews and exports.

Skipped automatically when Tk cannot open a display (for example a headless CI
container); on a normal Windows session these run against a real Tk root.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tk = pytest.importorskip("tkinter")

# Tk variables destroyed after the test root can raise during GC on Windows;
# that is a test-harness artefact, not an application error.
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")

from filescope import paths  # noqa: E402
from filescope.config import Settings  # noqa: E402
from filescope.ui import results as results_module  # noqa: E402
from filescope.ui.main_window import MainWindow, write_results_csv  # noqa: E402
from tools.make_corpus import build_corpus  # noqa: E402


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Keep settings/index/logs inside the test's temp directory."""
    data = tmp_path / "appdata"
    data.mkdir()
    monkeypatch.setattr(paths, "data_dir", lambda: str(data))
    for name in ("index_dir", "logs_dir", "session_dir", "crash_dir", "staging_dir", "preview_cache_dir"):
        monkeypatch.setattr(paths, name, lambda name=name: str(_ensure(data, name)))
    return data


def _ensure(base: Path, name: str) -> Path:
    target = base / name
    target.mkdir(parents=True, exist_ok=True)
    return target



def shutdown(window) -> None:
    """Close a test window without destroying the session-shared Tk root."""
    from contextlib import suppress

    if window.session is not None and not window.session.finished:
        window.session.cancel()
        window.session.wait(5)
    with suppress(Exception):
        window.ocr_cache.close()
    if window.database is not None:
        with suppress(Exception):
            window.database.close()
    with suppress(tk.TclError):
        window.destroy()


def pump(root, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)


class TestMainWindow:
    def test_window_builds_and_searches(self, tk_root, tmp_path, isolated_paths) -> None:
        corpus = build_corpus(tmp_path / "corpus", with_ocr_image=False)
        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(str(corpus.root))
        window.query_var.set("AAA&BBB")
        window.start_search()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 1.0)
        assert window.model.all_rows, "no results reached the UI model"
        rows = window.tree.get_children()
        assert rows, "results were not rendered"
        assert window._coverage_text.get()

    def test_preview_shows_evidence(self, tk_root, tmp_path, isolated_paths) -> None:
        corpus = build_corpus(tmp_path / "corpus2", with_ocr_image=False)
        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(str(corpus.root))
        window.query_var.set("AAA")
        window.start_search()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
        first = window.tree.get_children()[0]
        window.tree.selection_set(first)
        pump(tk_root, 2.0)
        assert window.preview.text.get("1.0", "end").strip()

    def test_refine_filters_without_new_search(self, tk_root, tmp_path, isolated_paths) -> None:
        corpus = build_corpus(tmp_path / "corpus3", with_ocr_image=False)
        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(str(corpus.root))
        window.query_var.set("AAA")
        window.start_search()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
        before = len(window.model.visible)
        window._refine_var.set("電源")
        window.apply_refine()
        pump(tk_root, 0.3)
        assert len(window.model.visible) <= before
        window.clear_refine()
        assert len(window.model.visible) == before

    def test_condition_builder_produces_query(self) -> None:
        from filescope.core.query import BuilderGroups, to_query_string

        groups = BuilderGroups(
            all_of=["AAA", "BBB"],
            any_of=["CCC"],
            n_of_count=2,
            n_of=["X", "Y", "Z"],
            none_of=["旧版"],
            phrases=["耐久 試験"],
            near_a="電源",
            near_b="ノイズ",
            near_distance=100,
        )
        query = to_query_string(groups.build())
        assert "AAA & BBB" in query
        assert "CCC" in query
        assert "2of(X, Y, Z)" in query
        assert "!旧版" in query
        assert "NEAR(電源, ノイズ, 100)" in query


class TestExports:
    def test_csv_export_contains_evidence(self, tmp_path) -> None:
        from filescope.core.models import ChunkKind, CloudState, Evidence, FileKind, FileResult, SourceType

        result = FileResult(
            path=str(tmp_path / "report.xlsx"),
            file_kind=FileKind.EXCEL,
            size=1024,
            mtime_ns=0,
            source_type=SourceType.LOCAL,
            cloud_state=CloudState.LOCAL,
            matched_terms=("AAA",),
            displays=("AAA",),
            hit_count=3,
            evidence=[Evidence(term="AAA", location="Sheet1!A1", kind=ChunkKind.CELL, snippet="AAA 評価")],
        )
        target = tmp_path / "out.csv"
        assert write_results_csv(str(target), [results_module.Row(result=result, confirmed=True)])
        text = target.read_text(encoding="utf-8-sig")
        assert "Sheet1!A1" in text
        assert "AAA" in text


class TestAcceptance:
    def test_multiple_search_folders(self, tk_root, tmp_path, isolated_paths) -> None:
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        (first / "a.txt").write_text("AAA\n", encoding="utf-8")
        (second / "b.txt").write_text("AAA\n", encoding="utf-8")
        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(f"{first}; {second}")
        window._sync_state()
        assert window.state.roots() == (str(first), str(second))
        window.query_var.set("AAA")
        window.start_search()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
        assert {row.result.name for row in window.model.all_rows} == {"a.txt", "b.txt"}

    def test_folders_dialog_deduplicates(self, tk_root, tmp_path) -> None:
        from filescope.ui.dialogs import FoldersDialog

        first = tmp_path / "x"
        first.mkdir()
        dialog = FoldersDialog(tk_root, roots=[str(first)])
        dialog.roots.append(str(first))
        dialog._refresh()
        assert dialog.listbox.size() == 2
        dialog._remove()
        dialog.window.destroy()

    def test_progress_bar_modes(self, tk_root, tmp_path, isolated_paths) -> None:
        """Indeterminate while enumerating, determinate once the total is known."""
        from filescope.core.models import Progress

        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window._start_progress()
        assert str(window.progress_bar.cget("mode")) == "indeterminate"
        window._apply_progress(Progress(phase="検索しています", discovered=10, scanned=5, discovery_done=True, total_known=True))
        assert str(window.progress_bar.cget("mode")) == "determinate"
        assert float(str(window.progress_bar.cget("value"))) == 50.0
        window._apply_progress(Progress(phase="フォルダを確認しています", discovered=3, scanned=1))
        assert str(window.progress_bar.cget("mode")) == "indeterminate"
        shutdown(window)
        _ = isolated_paths

    def test_search_button_state_during_and_after_search(self, tk_root, tmp_path, isolated_paths) -> None:
        corpus = build_corpus(tmp_path / "state-corpus", with_ocr_image=False)
        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(str(corpus.root))
        window.query_var.set("AAA")
        assert str(window.search_button.cget("state")) == "normal"
        window.start_search()
        assert str(window.search_button.cget("state")) == "disabled"
        assert str(window.cancel_button.cget("state")) == "normal"
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
        assert str(window.search_button.cget("state")) == "normal"
        assert str(window.cancel_button.cget("state")) == "disabled"

    def test_query_error_restores_the_ui(self, tk_root, tmp_path, isolated_paths) -> None:
        corpus = build_corpus(tmp_path / "error-corpus", with_ocr_image=False)
        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(str(corpus.root))
        window.query_var.set("AAA & (")
        window.start_search()
        for _ in range(40):
            tk_root.update()
            time.sleep(0.03)
        assert str(window.search_button.cget("state")) == "normal"
        assert str(window.cancel_button.cget("state")) == "disabled"
        assert "検索式エラー" in window._status_text.get()

    def test_corrupt_index_falls_back_to_direct_search(self, tk_root, tmp_path, isolated_paths) -> None:
        """A damaged index database must not stop FileScope from starting."""
        index_file = Path(paths.index_path())
        index_file.parent.mkdir(parents=True, exist_ok=True)
        index_file.write_bytes(b"definitely not sqlite" * 100)
        corpus = build_corpus(tmp_path / "corrupt-corpus", with_ocr_image=False)

        settings = Settings()
        settings.index_enabled = True
        settings.index_max_bytes = 64 * 1024 * 1024
        window = MainWindow(tk_root, settings=settings)
        assert window.database is None
        assert settings.index_disabled_reason
        window.root_var.set(str(corpus.root))
        window.query_var.set("AAA")
        window.start_search()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
        assert window.model.all_rows, "direct search should still work"

    def test_large_result_set_is_virtualised(self, tk_root, tmp_path, isolated_paths) -> None:
        root = tmp_path / "many"
        for index in range(3000):
            folder = root / f"d{index // 500}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"f{index:05d}.txt").write_text("AAA 評価\n", encoding="utf-8")

        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.root_var.set(str(root))
        window.query_var.set("AAA")
        window.start_search()
        deadline = time.time() + 120
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 1.5)
        assert len(window.model.visible) == 3000
        # Only a window of rows may exist in the widget at any time.
        assert len(window.tree.get_children()) <= results_module.WINDOW_ROWS
