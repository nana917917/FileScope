"""OCR pipeline tests.

The real Tesseract binary is not required: the OCR engine and page renderer are
stubbed so the surrounding logic (page selection, chunk labelling, caching,
timeouts, graceful degradation) is verified on any machine. When Tesseract is
installed the same tests run against it through the extra test at the bottom.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core.models import ChunkKind, FileEntry, SourceType
from filescope.extractors import extract
from filescope.extractors import pdf as pdf_module
from filescope.extractors.base import ExtractOptions, Sink
from filescope.ocr_cache import OcrCache
from filescope.platform import tesseract as tesseract_module


class FakeImage:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_entry(path) -> FileEntry:
    stat = path.stat()
    return FileEntry(
        path=str(path),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        extension=".pdf",
        source_type=SourceType.LOCAL,
    )


def install_fake_tesseract(monkeypatch, text: str = "AAA SCAN 1234", *, raises=None):
    module = types.ModuleType("pytesseract")
    calls = {"count": 0}

    def image_to_string(_image, lang="", timeout=0):
        calls["count"] += 1
        if raises is not None:
            raise raises
        return text

    module.image_to_string = image_to_string
    module.pytesseract = types.SimpleNamespace(tesseract_cmd="")
    module.get_languages = lambda config="": ["eng", "jpn"]
    monkeypatch.setitem(sys.modules, "pytesseract", module)
    return calls


def run_pdf(entry: FileEntry, path, options: ExtractOptions):
    """Run the registered PDF extractor and return (result, chunks)."""
    chunks = []
    result = extract(entry, str(path), Sink(chunks.append), options)
    return result, chunks


@pytest.fixture
def scan_pdf(tmp_path):
    from tools.make_corpus import write_image_pdf

    target = tmp_path / "scan.pdf"
    write_image_pdf(target, "AAA")
    return target


class TestOcrPipeline:
    def test_all_mode_ocrs_every_page(self, monkeypatch, scan_pdf, tmp_path) -> None:
        install_fake_tesseract(monkeypatch)
        rendered: list[int] = []

        def fake_render(path, index, scale):
            rendered.append(index)
            return FakeImage()

        monkeypatch.setattr(pdf_module, "render_page", fake_render)
        cache = OcrCache(str(tmp_path / "ocr.sqlite3"))
        options = ExtractOptions(ocr_mode="all", ocr_available=True, ocr_cache=cache)
        result, chunks = run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert result.ocr_pages == 1
        assert chunks and chunks[0].kind is ChunkKind.OCR
        assert chunks[0].location.endswith("ページ")
        cache.close()

    def test_cache_prevents_second_ocr(self, monkeypatch, scan_pdf, tmp_path) -> None:
        install_fake_tesseract(monkeypatch)
        renders = {"count": 0}

        def fake_render(path, index, scale):
            renders["count"] += 1
            return FakeImage()

        monkeypatch.setattr(pdf_module, "render_page", fake_render)
        cache = OcrCache(str(tmp_path / "ocr.sqlite3"))
        options = ExtractOptions(ocr_mode="all", ocr_available=True, ocr_cache=cache)
        run_pdf(make_entry(scan_pdf), scan_pdf, options)
        run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert renders["count"] == 1
        cache.close()

    def test_off_mode_skips_ocr(self, monkeypatch, scan_pdf) -> None:
        calls = install_fake_tesseract(monkeypatch)
        options = ExtractOptions(ocr_mode="off", ocr_available=True)
        result, _chunks = run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert result.ocr_pages == 0 and calls["count"] == 0

    def test_missing_ocr_is_a_skip_not_an_error(self, scan_pdf) -> None:
        options = ExtractOptions(ocr_mode="auto", ocr_available=False)
        result, _chunks = run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert result.ocr_pages == 0
        assert any(issue.code == "ocr-unavailable" for issue in result.warnings)
        assert all(issue.severity.value in ("skip", "warn") for issue in result.warnings)

    def test_timeout_is_reported_and_survived(self, monkeypatch, scan_pdf) -> None:
        install_fake_tesseract(monkeypatch, raises=RuntimeError("timeout"))
        monkeypatch.setattr(pdf_module, "render_page", lambda path, index, scale: FakeImage())
        options = ExtractOptions(ocr_mode="all", ocr_available=True)
        result, _chunks = run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert any(issue.code == "ocr-timeout" for issue in result.warnings)
        assert result.ocr_pages == 0

    def test_engine_failure_is_reported(self, monkeypatch, scan_pdf) -> None:
        install_fake_tesseract(monkeypatch, raises=OSError("boom"))
        monkeypatch.setattr(pdf_module, "render_page", lambda path, index, scale: FakeImage())
        options = ExtractOptions(ocr_mode="all", ocr_available=True)
        result, _chunks = run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert any(issue.code == "ocr-error" for issue in result.warnings)

    def test_render_failure_is_reported(self, monkeypatch, scan_pdf) -> None:
        install_fake_tesseract(monkeypatch)

        def boom(path, index, scale):
            raise RuntimeError("render")

        monkeypatch.setattr(pdf_module, "render_page", boom)
        options = ExtractOptions(ocr_mode="all", ocr_available=True)
        result, _chunks = run_pdf(make_entry(scan_pdf), scan_pdf, options)
        assert any(issue.code == "pdf-render" for issue in result.warnings)


class TestOcrCache:
    def test_roundtrip_and_trim(self, tmp_path) -> None:
        cache = OcrCache(str(tmp_path / "ocr.sqlite3"))
        key = cache.make_key("c:/a.pdf", 10, 20, 0, "jpn+eng", 2.0)
        assert cache.get(key) is None
        cache.put(key, "c:/a.pdf", 0, "AAA")
        assert cache.get(key) == "AAA"
        cache.trim(max_bytes=1)
        assert cache.get(key) is None
        cache.close()

    def test_drop_file(self, tmp_path) -> None:
        cache = OcrCache(str(tmp_path / "ocr.sqlite3"))
        key = cache.make_key("c:/a.pdf", 10, 20, 0, "eng", 2.0)
        cache.put(key, "c:/a.pdf", 0, "AAA")
        assert cache.drop_file("c:/a.pdf") == 1
        assert cache.get(key) is None
        cache.close()


@pytest.mark.skipif(not tesseract_module.probe().ready, reason="Tesseract not installed")
def test_real_tesseract_recognises_english(tmp_path) -> None:  # pragma: no cover - environment dependent
    import pytesseract
    from PIL import Image, ImageDraw

    from tools.make_corpus import write_image_pdf

    target = tmp_path / "scan.pdf"
    write_image_pdf(target, "AAA 1234")
    image = pdf_module.render_page(str(target), 0, 2.0)
    text = pytesseract.image_to_string(image, lang="eng", timeout=30)
    assert "AAA" in text.upper().replace(" ", "")
    _ = ImageDraw
    _ = Image
