from filescope.core.coordinator import SearchConfig, SearchSession


def test_search_phase_labels_distinguish_discovery_pause_and_cancel():
    session = SearchSession(SearchConfig(roots=(), query="word"))

    assert session._phase_locked() == "フォルダを確認しています"

    session._discovery_done = True
    assert session._phase_locked() == "ファイル探索終了・本文検索中"

    session.set_paused(True)
    assert session._phase_locked() == "一時停止中"

    session.cancel_event.set()
    assert session._phase_locked() == "中断しています"


def test_progress_summary_does_not_claim_unconfirmed_files_are_complete():
    from filescope.core.models import Coverage

    coverage = Coverage(discovered=12, scanned=8, skipped_online=2, skipped_type=2)

    assert "本文確認 8" in coverage.line()
    assert "検索完了" not in coverage.line()
    assert "未取得 2" in coverage.summary_line("検索終了（一部未確認あり）")
    assert "対象外 2" in coverage.summary_line("検索完了")
