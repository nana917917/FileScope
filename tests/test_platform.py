"""Windows integration helpers (spec sections 19, 20, 21)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filescope.core.models import CloudState, FileEntry, SourceType
from filescope.platform import onedrive, tempfiles, windows
from filescope.platform.tesseract import candidate_paths


class TestCloudState:
    def test_online_only_from_recall_on_open(self, tmp_path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        state = windows.classify_cloud(str(target), attributes=windows.FILE_ATTRIBUTE_RECALL_ON_OPEN)
        assert state is CloudState.ONLINE_ONLY

    def test_online_only_from_recall_on_data_access(self, tmp_path) -> None:
        state = windows.classify_cloud(
            str(tmp_path / "a.txt"), attributes=windows.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        )
        assert state is CloudState.ONLINE_ONLY

    def test_pinned_is_always_available(self, tmp_path) -> None:
        state = windows.classify_cloud(str(tmp_path / "a.txt"), attributes=windows.FILE_ATTRIBUTE_PINNED)
        assert state is CloudState.ALWAYS_AVAILABLE

    def test_plain_file_is_local(self, tmp_path) -> None:
        state = windows.classify_cloud(str(tmp_path / "a.txt"), attributes=0)
        assert state is CloudState.LOCAL

    def test_unc_path_is_smb(self) -> None:
        assert windows.classify_cloud(r"\\server\share\a.txt") is CloudState.SMB
        assert windows.is_remote_path(r"\\server\share\a.txt") is True

    def test_placeholder_check_does_not_open_the_file(self, tmp_path) -> None:
        target = tmp_path / "cloud.txt"
        target.write_text("x", encoding="utf-8")
        assert windows.is_placeholder(str(target), attributes=windows.FILE_ATTRIBUTE_RECALL_ON_OPEN) is True
        assert windows.is_placeholder(str(target), attributes=0) is False


class TestTesseractDiscovery:
    def test_candidate_list_covers_common_locations(self) -> None:
        paths = [path.lower() for path in candidate_paths()]
        assert any("program files\\tesseract-ocr" in path for path in paths)
        assert any("localappdata" in path or "tesseract-ocr" in path for path in paths)
        assert any(path.endswith("tesseract.exe") for path in paths)


class TestTempStaging:
    def test_staged_copy_is_removed(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(tempfiles, "staging_dir", lambda: str(tmp_path))
        source = tmp_path / "source.txt"
        source.write_text("AAA", encoding="utf-8")
        manager = tempfiles.TempManager(reserve_bytes=0, max_stage_bytes=1024 * 1024)
        with manager.staged_copy(str(source), size=source.stat().st_size, suffix=".txt") as staged:
            assert staged != str(source)
            assert Path(staged).read_text(encoding="utf-8") == "AAA"
            created = staged
        assert not os.path.exists(created)
        assert manager.stats.created == 1 and manager.stats.removed == 1

    def test_large_file_is_not_copied(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(tempfiles, "staging_dir", lambda: str(tmp_path))
        source = tmp_path / "big.bin"
        source.write_bytes(b"x" * 100)
        manager = tempfiles.TempManager(reserve_bytes=0, max_stage_bytes=10)
        with manager.staged_copy(str(source), size=100) as staged:
            assert staged == str(source)
        assert manager.stats.skipped_too_large == 1

    def test_cleanup_removes_leftovers(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(tempfiles, "staging_dir", lambda: str(tmp_path))
        leftover = tmp_path / "filescope-x"
        leftover.write_text("x", encoding="utf-8")
        manager = tempfiles.TempManager(reserve_bytes=0, max_stage_bytes=1024)
        manager.stats.files.add(str(leftover))
        assert manager.cleanup() == 1
        assert not leftover.exists()


class TestCloudPolicy:
    def entry(self, state: CloudState) -> FileEntry:
        return FileEntry(
            path="C:/OneDrive/a.pdf",
            size=1,
            mtime_ns=0,
            extension=".pdf",
            source_type=SourceType.ONEDRIVE,
            cloud_state=state,
        )

    def test_local_files_always_read(self) -> None:
        decision = onedrive.decide(self.entry(CloudState.LOCAL), mode="fast", policy="skip")
        assert decision.read_content is True

    def test_full_mode_with_auto_policy_fetches(self) -> None:
        assert onedrive.decide(self.entry(CloudState.ONLINE_ONLY), mode="full", policy="auto").read_content

    def test_skip_policy_is_counted(self) -> None:
        decision = onedrive.decide(self.entry(CloudState.ONLINE_ONLY), mode="standard", policy="skip")
        assert decision.read_content is False and decision.counted_as_skipped
