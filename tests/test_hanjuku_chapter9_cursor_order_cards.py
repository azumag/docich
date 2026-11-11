"""Chapter 9 takes cards from the top of the list instead of the plan's.

Owner 2026-11-11: 9話限定で切り札も上から順に適当に選ぶようにして良い.
g1163 12:15 scrolled four times for クースカン on every sortie.
"""
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy

from test_hanjuku_chapter9_cursor_order_sortie import _memory
from test_hanjuku_sortie_evidence import measured_card_select


def _card_memory(chapter=9):
    mem = _memory('attack', chapter=chapter, general='ゼウス')
    mem['chart_adjust']['interim_order']['cards'] = ['クースカン']
    mem.setdefault('sortie_general', {})[mem['_test_step']] = 'ゼウス'
    mem['picked'] = []
    return mem


def _step(mem, screen):
    with chart.map_context(mem):
        return policy.deploy_step(screen, mem)


def test_the_highlighted_card_is_taken_without_scrolling_for_the_plan():
    mem = _card_memory()
    assert _step(mem, measured_card_select(selected=0)) == [policy.pad('a')]
    assert mem['picked'] == ['イッテツーン']
    assert mem['card_override'][mem['_test_step']] == ['イッテツーン']
    assert not [r for r in mem['_records'] if r.get('decision') == 'card_scroll']


def test_an_empty_row_steps_down_and_a_full_kit_closes_the_list():
    mem = _card_memory()
    screen = measured_card_select(selected=0, stocks=['0', '2', '2', '2'])
    assert _step(mem, screen) == [policy.pad('down')]
    mem = _card_memory()
    mem['picked'] = ['イッテツーン', 'ダイチスイム', 'ブラッキー']
    assert _step(mem, measured_card_select(selected=0, remaining='0')) == [policy.pad('b')]
    assert mem['card_override'][mem['_test_step']] == ['イッテツーン', 'ダイチスイム', 'ブラッキー']


def test_the_enemy_egg_threshold_still_caps_the_kit():
    mem = _card_memory()
    mem['picked'] = ['クースカン', 'クースカン', 'クースカン']     # 39
    screen = measured_card_select(('グリンボー', 'ブラッキー'), selected=0, remaining='1')
    # 39 + 5 = 44 < 48: taken. Then the cap is checked against the new total.
    assert _step(mem, screen) == [policy.pad('a')]
    mem['picked'] = ['クースカン', 'クースカン', 'クースカン', 'クースカン']  # 52 is already past; nothing more
    assert _step(mem, measured_card_select(('グリンボー',), selected=0, remaining='1')) == [policy.pad('down')]


def test_other_chapters_keep_the_planned_cards():
    mem = _card_memory(chapter=8)
    _step(mem, measured_card_select(selected=0))
    assert mem['_test_step'] not in (mem.get('card_override') or {})
