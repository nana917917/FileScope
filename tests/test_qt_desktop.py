"""Qt widget and pipeline tests with synthetic documents only."""

import os
import time

import pytest

from filescope.config import Settings
from filescope.core.models import CloudState, FileKind, FileResult, SourceType
from filescope.core.query import NOf, parse_query
from filescope.ui.qt_conditions import build_condition, editable_nof
from filescope.ui.results import Row


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def pump(app, condition, timeout=12):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


def test_literal_conditions():
    parsed = parse_query(build_condition(2, ["680S", "DEV", "温度サイクル"], 2))
    assert isinstance(parsed, NOf)
    assert parsed.count == 2
    assert [term.text for term in parsed.children] == ["680S", "DEV", "温度サイクル"]
    assert parse_query(build_condition(0, ["a & b"])).text == "a & b"
    assert parse_query(build_condition(5, ["a", "b"], distance=50)).distance == 50
    with pytest.raises(ValueError, match="一致数"):
        build_condition(2, ["only one"], 2)


def test_nof_edit_only_accepts_a_simple_standalone_threshold():
    expression = '2of(680S, DEV, "温度サイクル")'
    node = editable_nof(expression, "OR")
    assert node == parse_query(expression)
    assert editable_nof(f"({expression}) & !test", "OR") is None
    assert editable_nof("2of(AAA, BBB", "OR") is None


def test_qt_large_model_keeps_selection_and_sorts(qt_app, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt

    from filescope.ui.qt_window import Window

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = Window(Settings())
    window.show()
    qt_app.processEvents()
    rows = [
        Row(FileResult(
            path=f"C:/synthetic/{n:05}.txt", size=10, mtime_ns=0, file_kind=FileKind.TEXT,
            cloud_state=CloudState.LOCAL, source_type=SourceType.LOCAL, hit_count=n,
        ), False)
        for n in range(10_050)
    ]
    window.model.extend(rows[:10000])
    window.table.scrollToBottom()
    window.table.setCurrentIndex(window.proxy.index(9980, 1))
    window.model.extend(rows[10000:])
    qt_app.processEvents()
    assert window._result(window.table.currentIndex()).hit_count == 9980
    assert window.proxy.rowCount() == 10050
    window.table.sortByColumn(3, Qt.DescendingOrder)
    assert window._result(window.proxy.index(0, 3)).hit_count == 10049
    window.filename_filter.setText("09980.txt")
    assert window.proxy.rowCount() == 1
    for theme in ("dark", "classic", "light"):
        window._theme(theme)
        assert window.settings.theme == theme
    window.close()


def test_qt_search_index_and_preview(qt_app, monkeypatch, tmp_path):
    from filescope.ui.qt_window import Window

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    root = tmp_path / "corpus"
    child = root / "nested"
    child.mkdir(parents=True)
    (child / "match.csv").write_text("680S,DEV,温度サイクル\n", encoding="utf-8")
    (root / "miss.txt").write_text("temperature\n", encoding="utf-8")
    (root / "match.txt").write_text("DEV 680S\n", encoding="utf-8")
    settings = Settings()
    settings.defaults.pdf_ocr_mode = "off"
    settings.autosave_results = False
    window = Window(settings)
    window.show()
    window.root_input.setEditText(f"{root};{child}")
    window.query.setText(build_condition(2, ["680S", "DEV", "温度サイクル"], 2))
    window.start_search()
    pump(qt_app, lambda: window.summary is not None)
    assert window.proxy.rowCount() == 2
    assert window.summary.coverage.discovered == 3
    assert "検索完了" in window.status.text()
    window.kind_actions["CSV"].setChecked(True)
    assert window.proxy.rowCount() == 1
    window.table.setCurrentIndex(window.proxy.index(0, 1))
    pump(qt_app, lambda: bool(window.preview_cache))
    assert "温度サイクル" in window.preview.toPlainText()
    assert window.matches
    window.start_search()
    pump(qt_app, lambda: window.summary is not None)
    assert window.summary.coverage.indexed > 0
    window.close()


def test_pdf_page_render_is_on_demand(qt_app, tmp_path):
    pytest.importorskip("pypdfium2")
    from PIL import Image

    from filescope.ui.qt_preview import pdf_preview
    from filescope.ui.state import UiState

    path = tmp_path / "test.pdf"
    with Image.new("RGB", (100, 150), "white") as page:
        page.save(path)
    result = FileResult(
        path=str(path), size=path.stat().st_size, mtime_ns=0, file_kind=FileKind.PDF,
        cloud_state=CloudState.LOCAL, source_type=SourceType.LOCAL,
    )
    config = UiState().to_config(Settings())
    png, page_number, count = pdf_preview(result, config, 1)
    assert png.startswith(b"\x89PNG")
    assert page_number == count == 1



def test_qt_pause_cancel_and_settings(qt_app, tmp_path, monkeypatch):
    from filescope.core import coordinator
    from filescope.ui.qt_window import Window

    data = tmp_path / "data"
    monkeypatch.setenv("LOCALAPPDATA", str(data))
    corpus = tmp_path / "docs"
    corpus.mkdir()
    for i in range(20):
        (corpus / f"{i}.txt").write_text("sample\n", encoding="utf-8")
    original = coordinator.extract

    def slow(entry, path, sink, options):
        for _ in range(25):
            sink.check()
            time.sleep(0.01)
        return original(entry, path, sink, options)

    monkeypatch.setattr(coordinator, "extract", slow)
    settings = Settings()
    settings.index_enabled = False
    settings.defaults.pdf_ocr_mode = "off"
    settings.defaults.root = str(corpus)
    settings.defaults.query = "sample"
    settings.window["column_name"] = 321
    window = Window(settings)
    window.show()
    assert window.table.columnWidth(1) == 321
    window.start_search()
    pump(qt_app, lambda: window.session is not None)
    window._pause()
    assert window.session.paused and window.status.text() == "一時停止中"
    window._cancel()
    pump(qt_app, lambda: window.summary is not None)
    assert window.summary.coverage.cancelled
    assert "検索中断" in window.status.text()
    window._theme("dark")
    window.close()
    restored = Settings.load()
    assert restored.theme == "dark" and restored.defaults.query == "sample"
    assert restored.window["qt_header"]
