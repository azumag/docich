"""Chapter 4 sorties kept searching for cards the panel had shown out of stock.

g1075 2026-10-29 17:40-17:50: every sortie scrolled for エンジェリン (stock 0)
and for マグネガキン/ファバード past the list's last row, then waited 5
readings for each before leaving it behind.
"""
from docich import hanjuku_policy as policy
from test_hanjuku_sortie_evidence import _c2_memory, measured_card_select

BOTTOM = ('ノリウツール', 'クースカン', 'バルムンク', 'ミックミー')


def fabado_memory():
    mem = _c2_memory()
    mem['rare_scan_month'] = 'chapter-1:unknown'    # this month's rare scan is done
    mem['card_override'] = {'1-C2': ['ファバード']}
    return mem


def test_unchanged_downs_on_the_last_row_prove_a_higher_card_out_of_stock():
    mem = fabado_memory()
    screen = measured_card_select(BOTTOM, selected=3, stocks=('2', '4', '12', '17'))
    for _ in range(3):
        assert policy.deploy_step(screen, mem) == [policy.pad('down')]
    assert policy.deploy_step(screen, mem) == []
    assert mem['card_stock']['ファバード'] == 0
    assert mem['card_scroll']['1-C2'] == 3          # not the 8-press search bound
    assert mem['_records'][-1]['decision'] == 'situation_held'


def test_a_cursor_still_moving_down_the_panel_is_not_the_bottom():
    mem = fabado_memory()
    for selected in range(3):
        screen = measured_card_select(BOTTOM, selected=selected, stocks=('2', '4', '12', '17'))
        assert policy.deploy_step(screen, mem) == [policy.pad('down')]
    assert 'ファバード' not in (mem.get('card_stock') or {})


def test_a_card_out_of_stock_before_the_attempt_is_not_planned():
    mem = _c2_memory()
    mem['card_override'] = {'1-C2': ['エンジェリン', 'ブラッキー']}
    mem['card_zero_at_start'] = {'step': '1-C2', 'cards': ['エンジェリン']}
    assert policy._deploy_cards(policy._order(mem), mem) == ['ブラッキー']
    mem['card_zero_at_start'] = {'step': '1-A1', 'cards': ['エンジェリン']}   # another order's attempt
    assert policy._deploy_cards(policy._order(mem), mem) == ['エンジェリン', 'ブラッキー']
