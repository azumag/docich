"""A base boss card the readable panel proves out of stock is left behind."""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows.deploy import _card_proven_absent
from docich.hanjuku.strategy.kit import _deploy_cards
from test_hanjuku_sortie_evidence import actor_panel, measured_card_select, menu

# g825 2-9 (2026-10-17 09:3x): 1-B1 wanted クースカン(13) after ノリウツール was
# picked; the panel showed these rows and the strict boss kit held for good.
G825 = ('ブラッキー', 'フットバース', 'ノリウツール', 'バルムンク')


def memory(picked=('ノリウツール',)):
    return {'rare_scan_month': 'chapter-1:unknown', 'chapter': 1, 'active': '1-B1',
            'captured': list(set(policy.chart.CASTLE_NAMES[1]) - {'アルマムーン', 'けっかい'}),
            'variant': 'chart', 'orders': {'1-B1': 'pending'}, 'picked': list(picked),
            'sortie_general': {'1-B1': 'アンディーブ'}}


def panel(names=G825, stocks=None, remaining='2', selected=3):
    screen = measured_card_select(names, stocks=stocks or [1] * len(names),
                                  remaining=remaining, selected=selected)
    return actor_panel(screen, 'アンディーブ')


def test_g825_missing_boss_card_is_dropped_and_the_sortie_continues():
    mem = memory()
    for _ in range(policy.CARD_MISS_LIMIT - 1):
        assert policy.deploy_step(panel(), mem) == []  # bounded readings, no scroll
    assert not mem.get('card_scroll')
    assert policy.deploy_step(panel(), mem) == []
    assert mem['card_drop'] == {'1-B1': ['クースカン']}
    dropped = mem['_records'][-1]
    assert dropped['decision'] == 'card_dropped' and dropped['card'] == 'クースカン'
    assert _deploy_cards({'step': '1-B1', 'target': 'けっかい', 'general': 'どうし',
                          'cards': ('クースカン', 'ノリウツール')}, mem) == ['ノリウツール']
    # The next observation leaves the list for the carried-card confirmation.
    assert policy.deploy_step(panel(), mem) == [policy.pad('b')]
    assert mem['orders']['1-B1'] == 'pending' and mem['active'] == '1-B1'


def test_exhausted_scroll_budget_from_the_live_stall_also_drops():
    mem = memory()
    mem['card_scroll'] = {'1-B1': policy.CARD_SCROLL_LIMIT}
    for _ in range(policy.CARD_MISS_LIMIT):
        policy.deploy_step(panel(), mem)
    assert mem['card_drop'] == {'1-B1': ['クースカン']}


@pytest.mark.parametrize('names,stocks,remaining,expected', [
    (G825, None, '2', True),                                          # 11 < 13 < 16
    (('ブラッキー', 'ノリウツール'), None, '2', True),                     # whole short list
    (('ブラッキー', 'ノリウツール', 'クースカン'), [1, 1, 0], '2', True),   # shown at zero
    (('イッテツーン', 'ブラッキー', 'フットバース', 'ノリウツール'), None, '2', False),  # may be below
    (('クースカン', 'ノリウツール'), None, '2', False),                  # in stock
    (G825, None, '0', False),                                         # no carry slot left
    (('ノリウツール', 'ブラッキー', 'フットバース', 'バルムンク'), None, '2', False),   # not ID order
])
def test_absence_needs_a_readable_id_ordered_proof(names, stocks, remaining, expected):
    inventory = policy._measured_card_select(panel(names, stocks, remaining, selected=0))
    assert _card_proven_absent('クースカン', inventory) is expected


def test_card_that_may_be_scrolled_off_cancels_without_weakening_the_boss_kit():
    mem = memory()
    names = ('イッテツーン', 'ブラッキー', 'フットバース', 'ノリウツール')
    mem['card_scroll'] = {'1-B1': policy.CARD_SCROLL_LIMIT}
    for _ in range(policy.CARD_UNREADABLE_LIMIT - 1):
        assert policy.deploy_step(panel(names), mem) == []
    assert policy.deploy_step(panel(names), mem) == [policy.pad('b'), policy.pad('b')]
    assert not mem.get('card_drop')
    assert mem['_records'][-1]['decision'] == 'boss_card_selection_unconfirmed'


def test_unreadable_boss_panel_cancels_without_dropping_any_card():
    mem = memory()
    mem['sortie_general']['1-B1'] = 'ゼウス'  # the actor menu() draws
    screen = menu('card_select', ['クースカン1'], True)
    for _ in range(policy.CARD_UNREADABLE_LIMIT - 1):
        assert policy.deploy_step(screen, mem) == []
    assert policy.deploy_step(screen, mem) == [policy.pad('b'), policy.pad('b')]
    assert not mem.get('card_drop') and mem['orders']['1-B1'] == 'pending'
