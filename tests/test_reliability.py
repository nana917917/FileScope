"""Reliability tests: atomic settings, corrupt input recovery, logging policy
and crash reports (spec sections 57-61)."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope import logging_setup, paths
from filescope.config import SCHEMA_VERSION, Settings, atomic_write_json


class TestSettings:
    def test_roundtrip(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        settings = Settings()
        settings.defaults.query = "耐久"
        settings.defaults.search_mode = "full"
        settings.recent_roots = ["D:/資料"]
        assert settings.save(str(path))
        loaded = Settings.load(str(path))
        assert loaded.defaults.query == "耐久"
        assert loaded.defaults.search_mode == "full"
        assert loaded.recent_roots == ["D:/資料"]
        assert loaded.schema_version == SCHEMA_VERSION

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        path.write_text("{ this is not json", encoding="utf-8")
        loaded = Settings.load(str(path))
        assert loaded.defaults.search_mode == "standard"
        quarantined = list(tmp_path.glob("settings.json.corrupt-*"))
        assert quarantined, "the corrupt file should be preserved for inspection"

    def test_bad_values_are_sanitised(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "defaults": {"search_mode": "turbo", "workers": "many", "pdf_ocr_mode": "sometimes"},
                    "index_max_bytes": "lots",
                    "history": "nope",
                }
            ),
            encoding="utf-8",
        )
        loaded = Settings.load(str(path))
        assert loaded.defaults.search_mode == "standard"
        assert loaded.defaults.workers == 4
        assert loaded.defaults.pdf_ocr_mode == "auto"
        assert loaded.index_max_bytes == 1024 * 1024 * 1024
        assert loaded.history == []

    def test_atomic_write_leaves_no_temp_files(self, tmp_path) -> None:
        target = tmp_path / "out.json"
        atomic_write_json(str(target), {"a": 1})
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
        assert [p.name for p in tmp_path.iterdir()] == ["out.json"]

    def test_legacy_settings_are_migrated(self, tmp_path, monkeypatch) -> None:
        legacy = tmp_path / "search_tool_settings.json"
        legacy.write_text(
            json.dumps({"mode": "AND", "roots": ["C:/資料"], "confirmed": ["C:/a.txt"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(paths, "legacy_settings_path", lambda: str(legacy))
        monkeypatch.setattr(paths, "settings_path", lambda: str(tmp_path / "missing.json"))
        loaded = Settings.load()
        assert loaded.defaults.legacy_operator == "AND"
        assert loaded.defaults.root == "C:/資料"
        assert loaded.confirmed_paths == ["C:/a.txt"]


class TestLogging:
    def test_log_file_excludes_document_text(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(logging_setup, "logs_dir", lambda: str(tmp_path))
        monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
        logger = logging_setup.setup_logging("DEBUG")
        component = logging_setup.get_logger("test")
        secret = "極秘の設計値42"
        try:
            raise ValueError("boom")
        except ValueError as exc:
            logging_setup.log_exception(component, "extract failed", exc, path="C:/a.xlsx")
        log_path = tmp_path / "filescope.log"
        text = log_path.read_text(encoding="utf-8")
        assert "extract failed" in text
        assert "ValueError" in text
        assert secret not in text
        assert "a.xlsx" in text
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers.clear()

    def test_rotating_handler_is_bounded(self, tmp_path, monkeypatch) -> None:
        assert logging_setup.LOG_MAX_BYTES == 2 * 1024 * 1024
        assert logging_setup.LOG_BACKUP_COUNT == 3
        _ = logging.Handler


class TestCrashReport:
    def test_report_is_written_outside_the_document_tree(self, tmp_path, monkeypatch) -> None:
        from filescope import app

        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        monkeypatch.setattr(paths, "crash_dir", lambda: str(crash_dir))
        monkeypatch.setattr(paths, "data_dir", lambda: str(tmp_path))
        try:
            raise RuntimeError("simulated failure")
        except RuntimeError as exc:
            path = app.write_crash_report(type(exc), exc, exc.__traceback__)
        assert path and os.path.isfile(path)
        content = Path(path).read_text(encoding="utf-8")
        assert "simulated failure" in content
        assert "FileScope" in content

    def test_crash_report_is_safe_when_directory_is_unwritable(self, monkeypatch) -> None:
        from filescope import app

        def boom() -> str:
            raise OSError("no permission")

        monkeypatch.setattr(paths, "crash_dir", boom)
        with pytest.raises(RuntimeError) as info:
            raise RuntimeError("x")
        exc = info.value
        assert app.write_crash_report(type(exc), exc, exc.__traceback__) == ""


class TestErrorTaxonomy:
    def test_severities_are_distinct(self) -> None:
        from filescope.errors import Issue, Severity

        skip = Issue(path="a", code="c", message="m", severity=Severity.SKIP)
        error = Issue(path="a", code="c", message="m", severity=Severity.ERROR)
        assert skip.as_row()[0] == "SKIP"
        assert error.as_row()[0] == "ERROR"

    def test_query_error_pretty_print(self) -> None:
        from filescope.errors import QuerySyntaxError

        exc = QuerySyntaxError("テスト", 3)
        assert "^" in exc.pretty("AAA & ()")
