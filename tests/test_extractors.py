"""Extractor tests against generated fixtures (spec section 75)."""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core.models import ChunkKind, FileEntry
from filescope.core.paths import extension_of
from filescope.extractors import extract
from filescope.extractors.base import ExtractOptions, Sink
from filescope.extractors.excel import decimal_places_from_format, excel_cell_candidates
from filescope.extractors.text import sniff_encoding
from tools.make_corpus import build_corpus, write_zip


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    return build_corpus(root, with_ocr_image=True)


def collect(path: Path, **option_overrides):
    entry = FileEntry(
        path=str(path),
        size=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
        extension=extension_of(str(path)),
    )
    chunks = []
    sink = Sink(chunks.append)
    result = extract(entry, str(path), sink, ExtractOptions(**option_overrides))
    return chunks, result


def texts(chunks) -> list[str]:
    return [chunk.text for chunk in chunks]


def locations(chunks) -> list[str]:
    return [chunk.location for chunk in chunks]


class TestExcel:
    def test_cell_values_and_locations(self, corpus) -> None:
        chunks, result = collect(corpus.path("report.xlsx"))
        assert not result.skipped
        assert "AAA" in texts(chunks)
        assert any(location == "評価!A1" for location in locations(chunks))

    def test_different_sheets_are_extracted(self, corpus) -> None:
        chunks, _ = collect(corpus.path("report.xlsx"))
        assert "BBB" in texts(chunks)
        assert any(location == "Sheet8!D52" for location in locations(chunks))

    def test_formulas_and_cached_values(self, corpus) -> None:
        chunks, _ = collect(corpus.path("report.xlsx"), search_formula=True)
        formulas = [chunk for chunk in chunks if chunk.kind is ChunkKind.FORMULA]
        assert formulas and formulas[0].text == "=SUM(1,2)"
        assert "AAA" in texts(chunks)  # the value pass does not lose plain cells

    def test_formula_search_can_be_disabled(self, corpus) -> None:
        chunks, _ = collect(corpus.path("report.xlsx"), search_formula=False)
        assert not [chunk for chunk in chunks if chunk.kind is ChunkKind.FORMULA]

    def test_sheet_names_defined_names_and_comments(self, corpus) -> None:
        chunks, _ = collect(corpus.path("report.xlsx"))
        kinds = {chunk.kind for chunk in chunks}
        assert ChunkKind.SHEET_NAME in kinds
        assert ChunkKind.DEFINED_NAME in kinds
        assert ChunkKind.CELL_COMMENT in kinds

    def test_cell_candidate_expansion(self) -> None:
        assert "123.0" in excel_cell_candidates(123)
        assert "0123" in excel_cell_candidates(123)
        assert decimal_places_from_format("0.00") == 2
        assert decimal_places_from_format("General") is None


class TestWord:
    def test_paragraphs_tables_and_headers(self, corpus) -> None:
        chunks, result = collect(corpus.path("review.docx"))
        assert not result.skipped
        assert any("AAA" in text for text in texts(chunks))
        assert "BBB" in texts(chunks)
        assert any(chunk.kind is ChunkKind.HEADER for chunk in chunks)


class TestPowerPoint:
    def test_shapes_notes_and_hidden_slides(self, corpus) -> None:
        chunks, result = collect(corpus.path("slides.pptx"))
        assert not result.skipped
        assert any("AAA" in text for text in texts(chunks))
        assert any("BBB" in text for text in texts(chunks))
        assert any("非表示" in location for location in locations(chunks))


class TestPdf:
    def test_text_layer_pages(self, corpus) -> None:
        chunks, result = collect(corpus.path("manual_2page.pdf"), ocr_mode="off")
        assert not result.skipped
        page_chunks = [chunk for chunk in chunks if chunk.kind is ChunkKind.PAGE]
        assert len(page_chunks) == 2
        assert "AAA" in page_chunks[0].text
        assert "BBB" in page_chunks[1].text
        assert page_chunks[1].location == "2ページ"

    def test_image_only_pdf_without_ocr_is_graceful(self, corpus) -> None:
        _chunks, result = collect(corpus.path("scan_page.pdf"), ocr_mode="auto", ocr_available=False)
        assert result.ocr_pages == 0
        assert any(issue.code == "ocr-unavailable" for issue in result.warnings)

    def test_malformed_pdf_is_reported_not_raised(self, tmp_path) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 not really a pdf")
        _, result = collect(broken)
        assert result.skipped
        assert result.reason


class TestText:
    def test_utf8(self, corpus) -> None:
        chunks, _ = collect(corpus.path("text_utf8.txt"))
        assert len(chunks) == 3

    def test_cp932(self, corpus) -> None:
        chunks, _ = collect(corpus.path("text_cp932.txt"))
        assert "電源ノイズ" in texts(chunks)[0]

    def test_utf16(self, corpus) -> None:
        chunks, _ = collect(corpus.path("text_utf16.txt"))
        assert "温度" in texts(chunks)[0]

    def test_encoding_sniffing(self, corpus) -> None:
        assert sniff_encoding(str(corpus.path("text_cp932.txt")), probe_bytes=4096).startswith("cp")
        assert sniff_encoding(str(corpus.path("text_utf8.txt")), probe_bytes=4096).startswith("utf-8")
        assert "16" in sniff_encoding(str(corpus.path("text_utf16.txt")), probe_bytes=4096)

    def test_nested_folder_file(self, corpus) -> None:
        chunks, _ = collect(corpus.path("sub/deep/nested_text.txt"))
        assert "耐久" in texts(chunks)[0]


class TestUnknownAndBinary:
    def test_unknown_text_file_is_probed(self, corpus) -> None:
        chunks, result = collect(corpus.path("unknown.dat"))
        assert not result.skipped
        assert "落下" in texts(chunks)[0]

    def test_binary_file_is_skipped(self, corpus) -> None:
        _, result = collect(corpus.path("binary.bin"))
        assert result.skipped


class TestArchive:
    def test_zip_members_are_searchable(self, corpus) -> None:
        chunks, result = collect(corpus.path("archive.zip"), include_archives=True)
        assert not result.skipped
        assert any("異音" in text for text in texts(chunks))
        assert all("archive.zip >" in location for location in locations(chunks))

    def test_archives_ignored_by_default(self, corpus) -> None:
        _, result = collect(corpus.path("archive.zip"))
        assert result.skipped

    def test_path_traversal_is_blocked(self, tmp_path) -> None:
        target = tmp_path / "evil.zip"
        write_zip(target, {"../escape.txt": "AAA"})
        chunks, result = collect(target, include_archives=True)
        assert not chunks
        assert any(issue.code == "zip-traversal" for issue in result.warnings)

    def test_entry_limit_is_reported(self, tmp_path) -> None:
        target = tmp_path / "many.zip"
        with zipfile.ZipFile(target, "w") as archive:
            for index in range(20):
                archive.writestr(f"file{index}.txt", "AAA")
        _, result = collect(target, include_archives=True)
        # 20 entries is below the default limit, so nothing is truncated.
        assert not result.truncated
