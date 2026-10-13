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
from filescope.ui.main_window import MainWindow, _bounded_layout_value, write_results_csv  # noqa: E402
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
    def test_invalid_saved_layout_values_fall_back_to_defaults(self, tk_root, isolated_paths) -> None:
        settings = Settings()
        settings.index_enabled = False
        settings.window = {"column_name": "bad-width", "preview_ratio_per_mille": None}

        window = MainWindow(tk_root, settings=settings)
        assert int(window.tree.column("name", "width")) == 180
        assert _bounded_layout_value(settings, "preview_ratio_per_mille", 360, 200, 700) == 360
        settings.window["preview_ratio_per_mille"] = 900
        assert _bounded_layout_value(settings, "preview_ratio_per_mille", 360, 200, 700) == 700

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

    def test_search_two_reruns_search_one_and_second_condition(self, tk_root, tmp_path, isolated_paths) -> None:
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
        first_session = window.session
        window._refine_var.set("電源")
        window.apply_refine()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
        assert window.session is not first_session
        assert window.session.config.query == "(AAA) & (電源)"
        assert len(window.model.visible) <= before
        window.clear_refine()
        deadline = time.time() + 60
        while time.time() < deadline:
            tk_root.update()
            time.sleep(0.05)
            if window.session is not None and window.session.finished:
                break
        pump(tk_root, 0.5)
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

    def test_rerender_keeps_the_previewed_row_selected(self, tk_root, tmp_path, isolated_paths) -> None:
        """Rebuilding the list mid-search must not throw the preview away."""
        corpus = build_corpus(tmp_path / "anchor-corpus", with_ocr_image=False)
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
        rows = window.tree.get_children()
        assert rows
        window.tree.selection_set(rows[0])
        pump(tk_root, 1.0)
        anchor = window.preview.current_path
        assert anchor, "the preview should be showing the selected file"

        window._render_results()
        pump(tk_root, 0.2)
        selection = window.tree.selection()
        assert len(selection) == 1
        row = window.model.row_at(int(selection[0]))
        assert row is not None and row.result.path == anchor
        assert window.preview.current_path == anchor
        shutdown(window)

    def test_lost_selection_does_not_clear_the_preview(self, tk_root, tmp_path, isolated_paths) -> None:
        corpus = build_corpus(tmp_path / "anchor2-corpus", with_ocr_image=False)
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
        pump(tk_root, 1.0)
        rows = window.tree.get_children()
        window.tree.selection_set(rows[0])
        pump(tk_root, 1.0)
        anchor = window.preview.current_path
        assert anchor
        # Tk reports the selection a redraw drops as a select event; the pane
        # must keep showing the file the user picked.
        for item in window.tree.get_children():
            window.tree.delete(item)
        pump(tk_root, 0.3)
        window._on_select()
        assert window.preview.current_path == anchor
        shutdown(window)

    def test_reselecting_the_same_file_does_not_reload_the_preview(
        self, tk_root, tmp_path, isolated_paths
    ) -> None:
        corpus = build_corpus(tmp_path / "anchor3-corpus", with_ocr_image=False)
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
        pump(tk_root, 1.0)
        rows = window.tree.get_children()
        window.tree.selection_set(rows[0])
        pump(tk_root, 1.0)
        loads: list[str] = []
        original = window.preview.show

        def record(result, config, **kwargs):
            loads.append(result.path)
            return original(result, config, **kwargs)

        window.preview.show = record  # type: ignore[method-assign]
        window._on_select()
        window._on_select()
        assert loads == []
        other = window.tree.get_children()[1]
        window.tree.selection_set(other)
        window._on_select()
        assert len(loads) == 1
        shutdown(window)

    def test_list_refresh_is_throttled_while_results_stream(self, tk_root, isolated_paths) -> None:
        class _Running:
            finished = False

        settings = Settings()
        settings.index_enabled = False
        window = MainWindow(tk_root, settings=settings)
        window.session = _Running()  # type: ignore[assignment]
        window._last_render_at = time.perf_counter()
        assert window._list_refresh_due() is False
        window._last_render_at -= 10.0
        assert window._list_refresh_due() is True
        window.session = None
        assert window._list_refresh_due() is True
        shutdown(window)

    def test_status_line_reports_interruption_and_short_autosave_name(
        self, tk_root, tmp_path, isolated_paths
    ) -> None:
        """The two states stay short enough for a 1024-wide window."""
        from filescope.core.models import (
            CloudState,
            Coverage,
            FileKind,
            FileResult,
            SourceType,
            Summary,
        )

        settings = Settings()
        settings.index_enabled = False
        settings.autosave_results = True
        window = MainWindow(tk_root, settings=settings)
        window._autosave = lambda summary: str(tmp_path / "deep" / "folder" / "results.csv")  # type: ignore[method-assign]

        hit = FileResult(
            path=str(tmp_path / "a.txt"),
            file_kind=FileKind.TEXT,
            size=10,
            mtime_ns=0,
            source_type=SourceType.LOCAL,
            cloud_state=CloudState.LOCAL,
            matched_terms=("AAA",),
            displays=("AAA",),
            hit_count=2,
        )

        def summary(cancelled: bool) -> Summary:
            return Summary(
                query="AAA",
                mode="standard",
                started_at=0.0,
                finished_at=1.5,
                coverage=Coverage(cancelled=cancelled, discovered=5, scanned=5, hits=1),
                results=[hit],
                total_hits=2,
            )

        window._on_finished(summary(cancelled=True))
        assert window._status_text.get().startswith("中断:")
        window._on_finished(summary(cancelled=False))
        status = window._status_text.get()
        assert status.startswith("完了:")
        assert "results.csv" in status
        assert str(tmp_path) not in status, "the full session path must not be shown"
        shutdown(window)
