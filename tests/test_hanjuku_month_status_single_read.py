"""v511: one monthly country-status visit reads both pages and the counts.

The measured page-1 rows are 「しょうぐん Nめい」/「へいし Nめい」; the long
labels kept for older wordings never matched the live screen, so the totals
stayed unknown and a second visit was opened in the same month.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku.flows import house
from docich.hanjuku.flows import month as month_flow
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, month_canvas
from test_hanjuku_level_scan import _country_page1, _country_page2, _main_on, _month_memory


IDENTITY = {'game': 'hanjuku-hero', 'generation': 7,
            'runtime_id': 'g7-synthetic', 'lease_id': 'synthetic-lease'}


def _measured_page1(generals=12, soldiers=100, month=7):
    """The live page-1 wording measured 2026-11-07 (short headcount rows)."""
    c = Canvas()
    c.text(56, 7, f'1わ1ねん{month}のつき250G')
    c.text(24, 39, 'わがくにの じょうたい')
    c.text(24, 71, 'しゅうにゅう 300G')
    c.text(24, 87, 'そうちんぎん 150G')
    c.text(24, 103, f'しょうぐん {generals}めい')
    c.text(24, 119, f'へいし {soldiers}めい')
    return c.frame()


def test_short_headcount_rows_are_read_as_totals():
    screen = parse(_measured_page1(12, 100))
    assert house._country_status_counts(screen) == (12, 100)


def test_long_headcount_labels_still_read():
    screen = parse(_country_page1(generals=6, soldiers=0))
    assert house._country_status_counts(screen) == (6, 0)


def test_level_page_and_menu_help_are_not_count_sources():
    assert house._country_status_counts(parse(_country_page2())) == (None, None)
    c = Canvas()
    c.text(24, 39, 'ステータスしょうぐんしろきりふだシステム')
    c.text(24, 55, 'わがくにのじょうたいをみることができますぞ!')
    assert house._country_status_counts(parse(c.frame())) == (None, None)


def test_generic_headcount_requires_a_counter():
    # A 商人 price (50G) or a bare menu number is not a headcount.
    c = Canvas()
    c.text(56, 7, '1わ1ねん7のつき250G')
    c.text(24, 71, 'しゅうにゅう 300G')
    c.text(24, 87, 'そうちんぎん 150G')
    c.text(24, 103, 'しょうぐんぼしゅう 50G')
    c.text(24, 119, 'へいし 100')
    assert house._country_status_counts(parse(c.frame())) == (None, None)


def test_month_visit_updates_counts_from_the_short_rows():
    mem = _month_memory()
    mem.update(_run_identity=dict(IDENTITY))
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    main = _main_on('ステータス')
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    page = _measured_page1(12, 100)
    assert house.step(parse(page), mem, page) == [p.pad('a')]
    assert mem['country_general_count']['count'] == 12
    assert mem['country_general_count']['scope'] == [1, '1-7']
    assert mem['country_soldier_count']['count'] == 100
    assert mem['soldiers_seen'] == 100
    assert 'house_country_status_counts_unread' not in {r.get('decision') for r in mem['_records']}


def test_unread_headcount_is_logged_once_with_the_page_rows():
    mem = _month_memory()
    mem.update(_run_identity=dict(IDENTITY))
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    main = _main_on('ステータス')
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    c = Canvas()
    c.text(56, 7, '1わ1ねん7のつき250G')
    c.text(24, 39, 'わがくにの じょうたい')
    c.text(24, 71, 'しゅうにゅう 300G')
    c.text(24, 87, 'そうちんぎん 150G')
    page = c.frame()
    house.step(parse(page), mem, page)
    house.step(parse(page), mem, page)
    logs = [r for r in mem['_records'] if r.get('decision') == 'house_country_status_counts_unread']
    assert len(logs) == 1
    rows = logs[0]['observed_metric']['rows']
    assert any('しゅうにゅう' in row for row in rows)
    assert not mem.get('country_general_count')


def test_missing_page1_still_allows_the_bounded_retry():
    mem = _month_memory()
    mem.update(hanjuku_level_seen={'scope': [1, '1-7'], 'tick': 500},
               recruit_month_scan_attempts={'scope': [1, '1-7'], 'count': 1})
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    assert mem['recruit_month_scan_attempts']['count'] == 2


def test_cards_are_held_while_the_recruit_question_is_unresolved():
    assert not month_flow._recruit_precedes_cards({'recruit': 'deferred_roster'})
    assert month_flow._recruit_precedes_cards({'recruit': 'deferred_egg'})
    assert month_flow._recruit_precedes_cards({'recruit': 'pending'})
    assert not month_flow._recruit_precedes_cards({'recruit': 'not_needed'})
    assert not month_flow._recruit_precedes_cards({'recruit': 'done'})


def test_hold_month_cards_is_once_and_keeps_the_plan_for_diagnostics():
    mem = {'_records': [], 'tick': 10}
    shop = {'key': '1-7', 'recruit': 'deferred_egg', 'chikujou': 'check',
            'items': [['ノリウツール', 3, 18]]}
    month_flow._hold_month_cards(mem, shop)
    month_flow._hold_month_cards(mem, shop)
    assert shop['cards_held'] == 'recruit_pending'
    assert shop['chikujou'] == 'skipped'
    logs = [r for r in mem['_records'] if r.get('decision') == 'month_cards_held_for_recruit']
    assert len(logs) == 1
    assert logs[0]['observed_metric']['planned'] == [['ノリウツール', 3, 18]]


def test_chapter_change_keeps_the_monthly_status_visit():
    from docich.hanjuku.strategy import campaign
    mem = _month_memory()
    mem.update(_run_identity=dict(IDENTITY))
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    campaign._enter_chapter(mem, 2, reason='chapter header')
    assert mem['chapter'] == 2
    assert mem['house']['chapter'] == 2
    assert mem['house']['level_only'] is True
    main = _main_on('ステータス')
    assert house.step(parse(main), mem, main) == [p.pad('a')]


def test_chapter_change_drops_a_field_survey():
    from docich.hanjuku.strategy import campaign
    mem = _month_memory()
    mem['house'] = {'phase': 'roster', 'chapter': 1, 'month_scan': False,
                    'level_only': False, 'seen': [], 'pending': [], 'age': 0, 'total': 0}
    campaign._enter_chapter(mem, 2, reason='chapter header')
    assert 'house' not in mem


def test_deferred_egg_holds_merchant_and_castle_until_recruit():
    from test_hanjuku_chart_bot import _short_recruit_memory
    mem = _short_recruit_memory()
    for key, count in (('country_general_count', 2), ('country_soldier_count', 70)):
        mem[key] = {'scope': [1, '1-7'], 'identity': None,
                    'tick': mem['tick'], 'count': count}
    mem['shop'] = {'key': '1-7', 'items': [['ノリウツール', 1, 18]],
                   'merchant_done': False, 'soldiers': 0, 'soldiers_done': True,
                   'egg': 'check', 'recruit': 'deferred_egg', 'recruit_priority': True,
                   'chikujou': 'check', 'reserve': 0, 'recruit_reserve': 0,
                   'gold_start': 200, 'chikujou_tried': []}
    frame = month_canvas(200, on='しょうにん', month=7)
    month_flow.month_step(parse(frame), mem)
    assert mem['shop']['cards_held'] == 'recruit_pending'
    assert mem['shop']['chikujou'] == 'skipped'
    assert mem['shop']['merchant_done'] is False
    assert any(r.get('decision') == 'month_cards_held_for_recruit'
               for r in mem.get('_records') or [])


def _castle_status_page():
    """城ステータス: しゅうにゅう と しょうぐん Nめい はあるが そうちんぎん は無い."""
    c = Canvas()
    c.text(56, 7, '2わ3ねん1のつき57G')
    c.text(24, 39, 'アルマムーン じょう')
    c.text(24, 71, 'しゅうにゅう 30G')
    c.text(24, 87, 'レベル 1')
    c.text(24, 103, 'しょうぐん 0めい')
    return c.frame()


def test_castle_status_page_is_not_a_country_page1_source():
    screen = parse(_castle_status_page())
    assert house._country_status_counts(screen) == (None, None)
    mem = _month_memory()
    mem.update(_run_identity=dict(IDENTITY))
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    main = _main_on('ステータス')
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    page = _castle_status_page()
    house.step(parse(page), mem, page)
    assert not mem.get('country_general_count')
    assert not mem.get('country_soldier_count')
    assert not mem.get('country_income')
