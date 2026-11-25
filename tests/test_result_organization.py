from filescope.core.coordinator import SearchConfig, SearchSession
from filescope.ui.results import ResultModel, display_directory, match_text, values_for
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


def test_excel_overlapping_roots_index_and_export(tmp_path):
    import csv

    from openpyxl import Workbook

    from filescope.index.database import IndexDatabase
    from filescope.ui.main_window import write_results_csv

    root = tmp_path / 'documents'
    other = root / 'other'
    other.mkdir(parents=True)
    for folder in (root, other):
        book = Workbook()
        book.active['A1'] = '680S SIDE 信頼性試験'
        book.active['A2'] = '680S SIDE 温度サイクル'
        book.create_sheet('second')['A1'] = '680S SIDE 信頼性試験'
        book.save(folder / 'same.xlsx')

    db = IndexDatabase(str(tmp_path / 'index.sqlite3'))
    try:
        def run(mode, database=None):
            session = SearchSession(SearchConfig(roots=(str(root), str(other)),
                query='(680S & SIDE) & (信頼性試験 | 温度サイクル)', mode=mode), database=database)
            session.start()
            assert session.wait(60)
            return session
        direct = run('full')
        run('standard', db)
        indexed = run('standard', db)
        assert len(direct.results) == len(indexed.results) == 2
        assert {r.path for r in direct.results} == {r.path for r in indexed.results}
        assert all('680S' in match_text(r) and 'SIDE' in match_text(r) for r in indexed.results)
        model = ResultModel()
        for result in indexed.results:
            model.add(result, confirmed=False)
            model.add(result, confirmed=False)
        output = tmp_path / 'results.csv'
        assert write_results_csv(str(output), model.all_rows)
        with output.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        assert len(rows) == 3
        assert {row[-1] for row in rows[1:]} == {r.path for r in indexed.results}
        assert display_directory(str(other / 'same.xlsx'), (str(root), str(other))) == '[2] other'
    finally:
        db.close()
