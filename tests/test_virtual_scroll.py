import pytest

from filescope.ui.results import WINDOW_ROWS, scrollbar_fractions, visible_window


@pytest.mark.parametrize("total", [0, 1, 10, 50, 299, 300, 301, 1_000, 10_000])
def test_virtual_scroll_bounds_cover_result_counts(total):
    visible_rows = 18
    start, end = visible_window(total, 10**9, visible_rows)

    assert start == max(0, total - visible_rows)
    assert end == total
    assert end - start <= visible_rows + 60
    assert end - start <= WINDOW_ROWS

    first, last = scrollbar_fractions(total, start, visible_rows)
    if total <= visible_rows:
        assert (first, last) == (0.0, 1.0)
    else:
        assert first == pytest.approx((total - visible_rows) / total)
        assert last == 1.0


def test_virtual_scroll_thumb_tracks_viewport_not_cache_size():
    total = 299
    visible_rows = 18
    start, end = visible_window(total, 0, visible_rows)
    first, last = scrollbar_fractions(total, start, visible_rows)

    assert start == 0
    assert end == visible_rows + 60
    assert first == 0.0
    assert last == pytest.approx(visible_rows / total)


def test_virtual_scroll_preserves_requested_position_when_appending_rows():
    start, end = visible_window(301, 120, 18)
    next_start, _ = visible_window(302, start, 18)

    assert start == next_start == 120
    assert end == 198
