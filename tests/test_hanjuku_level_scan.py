"""Issue #3011: the monthly visit reads the country-status 半熟レベル page."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from docich.hanjuku.flows import house
from docich.hanjuku.flows import month as month_flow
from test_hanjuku_chart_bot import Canvas, month_canvas


def _main_on(label, month=7):
    """Main menu rows as _survey_main draws them, hand on the named row."""
    c = Canvas()
    c.text(56, 7, f'1わ1ねん{month}のつき250G')
    c.text(48, 47, 'しょうぐん')
    c.text(48, 63, 'ステータス')
    c.text(144, 63, 'システム')
    y = {'しょうぐん': 41, 'ステータス': 57, 'システム': 57}[label]
    x = {'しょうぐん': 26, 'ステータス': 26, 'システム': 122}[label]
    c.hand(x, y)
    return c.frame()


def _country_page1(month=7):
    c = Canvas()
    c.text(56, 7, f'1わ1ねん{month}のつき250G')
    c.text(24, 39, 'わがくにの じょうたい')
    c.text(24, 71, 'しゅうにゅう 300G')
    c.text(24, 87, 'そうちんぎん 150G')
    c.text(24, 103, 'しょうぐんにんすう 10にん')
    return c.frame()


def _country_page2(level=13, value=2350, needed=2800, month=7):
    c = Canvas()
    c.text(56, 7, f'1わ1ねん{month}のつき250G')
    c.text(24, 39, 'わがくにの じょうたい')
    c.text(24, 71, 'たまごのかず 8こ')
    c.text(24, 87, f'はんじゅくレベル {level}')
    c.text(24, 103, f'はんじゅくち {value}')
    c.text(24, 119, f'つぎのレベル {needed}')
    return c.frame()


def _month_memory():
    return {'chapter': 1, 'month': '1-7', 'tick': 500, '_records': []}


def test_country_status_reader_finds_level_value_and_next():
    from docich.hanjuku.flows.house import _country_status_level
    read = _country_status_level(parse(_country_page2()))
    assert read == {'level': 13, 'value': 2350, 'needed': 2800}
    # Page 1 and the あ・た・し♥ wording never pass the level-page marker.
    assert _country_status_level(parse(_country_page1())) is None
    c = Canvas()
    c.text(24, 151, 'はんじゅくちも こころもち すってきに')
    assert _country_status_level(parse(c.frame())) is None


def test_await_level_scan_bounds():
    mem = _month_memory()
    shop = {'key': '1-7'}
    for expected in (1, 2, 3, 4):
        assert month_flow._await_level_scan(mem, shop) is True
        assert shop['level_scan_wait'] == expected
    assert month_flow._await_level_scan(mem, shop) is False
    shop['level_scan_wait'] = 0
    mem['hanjuku_level_seen'] = {'scope': [1, '1-7']}
    assert month_flow._await_level_scan(mem, shop) is False
    mem.pop('hanjuku_level_seen')
    mem['recruit_month_scan_attempts'] = {'scope': [1, '1-7'], 'count': 1}
    assert month_flow._await_level_scan(mem, shop) is True   # budget remains
    mem['recruit_month_scan_attempts']['count'] = 2
    assert month_flow._await_level_scan(mem, shop) is False


def test_level_trigger_visits_status_and_skips_the_roster():
    mem = _month_memory()
    mem.update(shop={'key': '1-7', 'level_scan_wait': 1},
               roster_survey_scope=[1, '1-7'],
               recruit_roster={'chapter': 1, 'month': '1-7', 'tick': 1,
                               'names': ['どうし'], 'wages': {}, 'complete': True})
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    assert mem['house']['month_scan'] is True
    assert mem['house']['level_only'] is True
    main = _main_on('しょうぐん')
    assert house.step(parse(main), mem, main) == [p.pad('down')]
    main = _main_on('ステータス')
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    assert mem['house']['phase'] == 'level_status'
    page1 = _country_page1()
    assert house.step(parse(page1), mem, page1) == [p.pad('a')]
    page2 = _country_page2(level=14, value=2800, needed=3400)
    assert house.step(parse(page2), mem, page2) == [p.pad('b')]
    assert mem['hanjuku_level'] == 14
    assert mem['hanjuku_value'] == 2800
    assert mem['hanjuku_level_seen']['scope'] == [1, '1-7']
    record = mem['_records'][-1]
    assert record['decision'] == 'house_hanjuku_level_observed'
    assert record['observed_metric']['level'] == 14
    main = _main_on('しょうぐん')
    assert house.step(parse(main), mem, main) == []   # leave_status → open_roster
    assert mem['house']['phase'] == 'open_roster'
    assert house.step(parse(main), mem, main) == [p.pad('b')]
    assert mem['house']['phase'] == 'leave_roster'
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == []
    assert 'house' not in mem
    # The level-only visit never touched the existing roster receipt.
    assert mem['recruit_roster']['names'] == ['どうし']
    assert mem['recruit_roster']['complete'] is True


def test_roster_scan_does_not_detour_without_the_level_trigger():
    # Ordinary roster visits keep their measured order: the status page is
    # only read on the end-of-month visit the level trigger creates.
    mem = _month_memory()
    mem['shop'] = {'key': '1-7'}
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    assert not mem['house'].get('level_only')
    main = _main_on('しょうぐん')
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    assert mem['house']['phase'] == 'roster'


def test_unreadable_level_page_gives_up_bounded():
    mem = _month_memory()
    state = {'phase': 'level_status', 'chapter': 1, 'roster_month': '1-7',
             'month_scan': True, 'age': 0, 'total': 0, 'seen': [], 'pending': [],
             'level_only': True, 'level_checked': True}
    mem['house'] = state
    blank = Canvas().frame()
    for _ in range(house.LEVEL_STATUS_WAIT - 1):
        assert house.step(parse(blank), mem, blank) == []
    assert house.step(parse(blank), mem, blank) == [p.pad('b')]
    assert mem['house']['phase'] == 'leave_status'
    assert any(r['decision'] == 'house_hanjuku_level_unread' for r in mem['_records'])
