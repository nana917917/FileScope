from filescope.core.coordinator import SearchConfig, SearchSession
from filescope.ui.results import ResultModel, values_for
from filescope.ui.state import UiState, apply_filters


def test_overlapping_roots_and_csv_facet(tmp_path):
    sub = tmp_path / 'sub'
    sub.mkdir()
    (tmp_path / 'same.txt').write_text('needle needle', encoding='utf-8')
    (sub / 'same.txt').write_text('needle', encoding='utf-8')
    (sub / 'table.csv').write_text('needle', encoding='utf-8')
    (sub / 'table.tsv').write_text('needle', encoding='utf-8')
    session = SearchSession(SearchConfig(roots=(str(tmp_path), str(sub)), query='needle'))
    session.start()
    assert session.wait(30)
    assert len(session.results) == 4
    assert session.coverage.discovered == 4
    model = ResultModel()
    for result in session.results:
        model.add(result, confirmed=False)
        model.add(result, confirmed=False)
    assert len(model.all_rows) == 4
    csv_rows = apply_filters(model.all_rows, UiState(facet_kinds={'CSV'}), set())
    assert len(csv_rows) == 1
    assert values_for(csv_rows[0])[4] == 'CSV'
    text_rows = apply_filters(model.all_rows, UiState(facet_kinds={'Text'}), set())
    assert len(text_rows) == 3
