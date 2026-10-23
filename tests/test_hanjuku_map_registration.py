"""Map registration boundaries; planted coordinates below are synthetic only."""
import pytest

from docich import hanjuku_chart as chart, hanjuku_policy as policy, hanjuku_map_sources as sources
from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import navigation, readers, roster as receipts, world
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_house import feed, roster, status
from test_hanjuku_recruit_roster import _survey_main


def monthly_memory(chapter):
    return {'chapter': chapter, 'month': '1-7', 'tick': 500, 'gold': 250,
            'shop': {'key': '1-7', 'egg': 'not_needed', 'recruit': 'deferred_roster'},
            '_records': []}


def test_map_calibrations_share_data_but_keep_distinct_pixel_conventions():
    assert readers.WORLD_MAP_OFFSET is chart.WORLD_MAP_OFFSET
    assert navigation.Y_JUMP_OFFSET is chart.Y_JUMP_OFFSET
    assert world.HOUSE_VIEW is chart.HOUSE_VIEW
    assert {c: chart.WORLD_MAP_OFFSET[c] for c in (1, 2)} == {1: (64.5, 44.4), 2: (64.2, 45.0)}
    assert {c: chart.Y_JUMP_OFFSET[c] for c in (1, 2)} == {1: (63.0, 47.5), 2: (63.0, 47.5)}
    assert {c: chart.HOUSE_VIEW[c] for c in (1, 2)} == {1: (118.5, 126.5), 2: (128.5, 94.5)}


def test_source_catalog_keeps_all_132_locations_and_distinguishes_boss_models():
    assert set(sources.SOURCE_CASTLES) == set(sources.SOURCE_BOSS_SITES) == set(range(1, 13))
    assert sum(map(len, sources.SOURCE_CASTLES.values())) == 117
    assert sum(map(len, sources.SOURCE_BOSS_SITES.values())) == 15
    assert len(sources.SOURCE_BOSS_SITES[4]) == 4
    assert sources.RANDOM_BOSS_OCCUPANTS == {4}
    assert sources.MOVING_BOSS_CHAPTERS == {9}


@pytest.mark.parametrize('chapter', range(1, 13))
def test_source_ordinary_names_match_the_full_chapter_list(chapter):
    expected = set(chart.CASTLE_NAMES[chapter]) - {chart.boss_castle(chapter)}
    assert set(sources.SOURCE_CASTLES[chapter]) == expected
    assert all(type(x) is int and type(y) is int and 0 <= x < 130 and 0 <= y < 130
               for x, y in sources.SOURCE_CASTLES[chapter].values())
    assert sources.source_page(chapter).endswith(f'/{chapter:02d}.html')
    assert sources.source_image(chapter).endswith(f'/{chapter:02d}.png')


def test_eightfold_source_model_is_not_an_exact_native_calibration():
    residuals = []
    for chapter in (1, 2):
        for name, (left, top) in sources.SOURCE_CASTLES[chapter].items():
            x, y = chart.CASTLES[chapter][name]
            residuals.append((abs(x - 8 * (left + 1)), abs(y - 8 * (top + 3))))
    assert len(residuals) == 15
    assert max(x for x, _ in residuals) == 6
    assert max(y for _, y in residuals) == 8
    # Source pixels are never published as native movement facts by import.
    import importlib
    from copy import deepcopy
    before = deepcopy(chart.CASTLES)
    importlib.reload(sources)
    assert chart.CASTLES == before


@pytest.mark.parametrize('chapter', range(3, 13))
def test_monthly_roster_reading_does_not_require_a_house_coordinate(chapter, monkeypatch):
    monkeypatch.delitem(house.HOUSE_VIEW, chapter, raising=False)
    mem = monthly_memory(chapter)
    frame = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(frame), mem, frame) == [policy.pad('a')]
    assert mem['house']['month_scan'] is True
    assert mem['house']['chapter'] == chapter
    assert not mem.get('sorties') and not mem.get('y_jump')


@pytest.mark.parametrize('chapter', range(3, 13))
def test_field_house_scan_stays_closed_without_a_house_coordinate(chapter, monkeypatch):
    monkeypatch.delitem(house.HOUSE_VIEW, chapter, raising=False)
    mem = monthly_memory(chapter)
    assert house.step(Screen([], None, '', kind='map'), mem, None) is None
    assert not mem.get('house') and not mem.get('sorties')


def test_native_month_survey_finishes_without_a_status_read_or_a_trip(monkeypatch):
    """The monthly list read needs no house coordinate and dispatches nothing.

    Main's list-only month scan (#2367) merges visible names and ends on a
    short page. This keeps the #2545 boundary: chapters without a measured
    house still finish a complete monthly roster, open no per-general status
    panel, and never start a repair trip. Only the list counts as evidence.
    """
    monkeypatch.delitem(house.HOUSE_VIEW, 3, raising=False)
    monkeypatch.setattr(house, 'general_status',
                        lambda screen: pytest.fail('status panel read'))
    mem = monthly_memory(3)
    assert feed(mem, month_canvas(250, on='メインメニュー', month=7)) == [policy.pad('a')]
    assert feed(mem, _survey_main(7)) == [policy.pad('a')]
    names = ['どうし', 'ゼウス']
    # A page shorter than the native eight rows proves the list end on screen.
    assert feed(mem, roster(names, 0)) == [policy.pad('b')]
    assert feed(mem, _survey_main(7)) == [policy.pad('b')]
    assert feed(mem, month_canvas(250, on='メインメニュー', month=7)) == []
    assert not mem.get('house')
    receipt = receipts.fresh(mem)
    assert receipt['complete'] is True
    assert receipt['names'] == names
    assert not mem.get('house_eggs')
    assert not mem.get('sorties') and not mem.get('y_jump')
    # No repair trip may start in a chapter whose house is not measured.
    assert not any(r['decision'] in {'house_departed', 'house_repair_verified'}
                   for r in mem['_records'])


def test_partial_chapter_three_registration_only_releases_three_initial_orders(monkeypatch):
    names = [chart.home_castle(3), 'グリン', 'メガパタ', 'コラダイン']
    # Artificial coordinates exercise registration gates, not geographic facts.
    monkeypatch.setitem(chart.CASTLES, 3, {name: (i * 32, 0) for i, name in enumerate(names)})
    assert [o['step'] for o in chart.orders(3)] == ['3-Z1', '3-C1', '3-V1']
    assert not policy._all_normal_captured({'chapter': 3, 'captured': names})


def test_measured_juaam_remains_an_interim_target_without_a_chart_order(monkeypatch):
    home = chart.home_castle(10)
    monkeypatch.setitem(chart.CASTLES, 10, {home: (0, 0), 'ジュアム': (64, 0)})
    mem = {'chapter': 10, 'orders': {}, 'garrison': {home: ['どうし', 'ゼウス']}}
    assert not any(o['target'] == 'ジュアム' for o in chart.orders(10))
    assert any(o['target'] == 'ジュアム' for o in policy.interim_candidates(mem).values())
    monkeypatch.setitem(chart.CASTLES, 10, {home: (0, 0)})
    assert not any(o['target'] == 'ジュアム' for o in policy.interim_candidates(mem).values())


def test_chapter_six_requires_toutou_before_the_normal_castle_gate_opens():
    normal = set(chart.CASTLE_NAMES[6]) - {chart.home_castle(6), chart.boss_castle(6)}
    assert 'トウトウ' in normal and len(normal) == 11
    assert not policy._all_normal_captured({'chapter': 6, 'captured': normal - {'トウトウ'}})
    assert policy._all_normal_captured({'chapter': 6, 'captured': normal})


@pytest.mark.parametrize('missing', ['house', 'y_cursor', 'castle'])
def test_repair_trip_requires_all_independent_calibrations(monkeypatch, missing):
    chapter = 3
    names = chart.CASTLE_NAMES[chapter]
    monkeypatch.setitem(chart.CASTLES, chapter, {n: (i * 32, 0) for i, n in enumerate(names)})
    monkeypatch.setitem(house.HOUSE_TARGET, chapter, (140, 140))
    monkeypatch.setitem(navigation.Y_JUMP_OFFSET, chapter, (0, 0))
    if missing == 'house':
        monkeypatch.delitem(house.HOUSE_TARGET, chapter)
    elif missing == 'y_cursor':
        monkeypatch.delitem(navigation.Y_JUMP_OFFSET, chapter)
    else:
        monkeypatch.setitem(chart.CASTLES, chapter, {chart.home_castle(chapter): (0, 0)})
    mem = {'chapter': chapter, 'house': {'source': chart.home_castle(chapter)}}
    reason, blocked = house._house_route_problem(mem)
    assert '城配置が未確認' in reason and blocked == []
