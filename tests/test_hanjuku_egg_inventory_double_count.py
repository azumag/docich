"""携行札の在庫計算で、同じ1回の使用を二重に差し引かない (#2364 二重控除)。

選択時に ``cards_selected`` へ入った札が、使用の告知を確認できず
``cards_unclassified`` にも入ると、同じ1回の使用が両方に現れる。
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku.judgment.battle import _egg_inventory


def row(rows, name):
    return next(r for r in rows if r['name'] == name)


def test_one_use_selected_then_unclassified_consumes_one_carried_copy():
    # 携行2枚・1回使用 → 残りは1枠であるべき。選択と未分類の両方に載っても1回。
    mem = {'card_stock': {'グルミー': 2}}
    cur = {'cards_selected': ['グルミー'], 'cards_unclassified': ['グルミー'],
           'cards_used': []}
    r = row(_egg_inventory(mem, cur, ['グルミー', 'グルミー']), 'グルミー')
    assert r['carried'] == 2
    assert r['available'] == 1
    assert r['usable'] is True


def test_confirmed_use_recorded_in_both_lists_is_still_one_use():
    mem = {'card_stock': {'グルミー': 2}}
    cur = {'cards_selected': ['グルミー'], 'cards_used': ['グルミー'],
           'cards_unclassified': []}
    r = row(_egg_inventory(mem, cur, ['グルミー', 'グルミー']), 'グルミー')
    assert r['available'] == 1


def test_two_separate_uses_still_consume_two_copies():
    mem = {'card_stock': {'グルミー': 2}}
    cur = {'cards_selected': ['グルミー', 'グルミー'], 'cards_unclassified': ['グルミー'],
           'cards_used': []}
    r = row(_egg_inventory(mem, cur, ['グルミー', 'グルミー']), 'グルミー')
    assert r['available'] == 0
    assert r['unavailable'] == 'used'


def test_real_plan_counts_pending_and_settled_uses_across_battles_once():
    from docich import hanjuku_policy as policy
    from docich.hanjuku_screen import Battle, Screen
    from docich.hanjuku.judgment.battle import _egg_plan, _card_use_unclassified
    card = 'ブラッキー'
    mem = {'chapter': 1, 'month': '1-10', 'active': '1-C2', 'variant': 'chart',
           'orders': {'1-C2': 'pending'}, 'picked': [], '_records': [],
           'attack': {'general': 'ココット', 'castle': 'キカンドン',
                      'side': 'attack', 'step': '1-C2'},
           'card_override': {'1-C2': [card, card]}, 'card_stock': {card: 2}}
    view = Screen(lines=[], hand=None, text='', kind='battle',
                  battle=Battle('キッシュ', 26, 'ココット', 24))
    policy.battle_step(view, mem); policy.battle_step(view, mem)
    cur = mem['battle']
    def count():
        plan = _egg_plan(mem, cur)
        return row(plan['cards'], card), plan
    assert count()[0]['available'] == 2
    cur['cards_selected'] = [card]
    cur['card_flow'] = {'card': card, 'stage': 'announce', 'selection_planned': True}
    assert count()[0]['available'] == 1
    _card_use_unclassified(mem, cur, 'test transition')
    remaining, plan = count()
    assert plan['carried'] == [card]
    assert remaining['available'] == 1 and remaining['usable'] is True
    assert card in plan['droppers']
    # A new battle retains the sortie expenditure, but resets local receipts.
    next_cur = {**cur, 'cards_selected': [], 'cards_unclassified': [], 'cards_used': []}
    assert row(_egg_plan(mem, next_cur)['cards'], card)['available'] == 1
    next_cur['cards_selected'] = [card]
    assert row(_egg_plan(mem, next_cur)['cards'], card)['available'] == 0
    # Second use in the original battle: pending and settled both leave zero.
    cur['cards_selected'].append(card)
    cur['card_flow'] = {'card': card, 'stage': 'announce', 'selection_planned': True}
    assert count()[0]['available'] == 0
    _card_use_unclassified(mem, cur, 'second transition')
    assert count()[0]['available'] == 0
    assert count()[1]['droppers'] == []


@pytest.mark.parametrize('copies', [2, 3])
def test_battle_step_settles_each_carried_copy_once(copies):
    from docich import hanjuku_policy as policy
    from docich.hanjuku_screen import Battle, Screen
    from docich.hanjuku.judgment.battle import _egg_plan
    card = 'ブラッキー'
    mem = {'chapter': 1, 'month': '1-10', 'active': '1-C2', 'variant': 'chart',
           'orders': {'1-C2': 'pending'}, 'picked': [], '_records': [],
           'attack': {'general': 'ココット', 'castle': 'キカンドン',
                      'side': 'attack', 'step': '1-C2'},
           'card_override': {'1-C2': [card] * copies}, 'card_stock': {card: copies}}
    view = Screen(lines=[], hand=None, text='', kind='battle',
                  battle=Battle('キッシュ', 26, 'ココット', 24))
    policy.battle_step(view, mem)
    policy.battle_step(view, mem)
    cur = mem['battle']
    for used in range(1, copies + 1):
        cur['cards_selected'].append(card)
        cur['card_flow'] = {'card': card, 'stage': 'announce', 'selection_planned': True}
        assert row(_egg_plan(mem, cur)['cards'], card)['available'] == copies - used
        policy.battle_step(view, mem)
        policy.battle_step(view, mem)
        assert cur['cards_unclassified'].count(card) == used
        assert row(_egg_plan(mem, cur)['cards'], card)['available'] == copies - used
    assert not _egg_plan(mem, cur)['droppers']
