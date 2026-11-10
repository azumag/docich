"""敗北後の再攻撃で開幕に使う札は、実在庫にある枚数だけを計画する。

g1153 2026-11-08 13:21 ココット: イッテツーン在庫0なのに ``card_override`` へ2枚を
固定で入れていた。次回の出撃はそれを携行できず、``planned_cards`` だけが残って
「まだ使える資源がある」と扱われ、白兵9.8倍差でも退却できなかった。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as p
from docich.hanjuku.state.regions import plan as _plan_region

STEP = '1-A3'


def loss(stock):
    mem = {'chapter': 1, 'tick': 100, 'orders': {STEP: 'launched'},
           'retries': {STEP: 0},
           'launched': {'ゴーメン': {'general': 'ゼウス', 'step': STEP}},
           'card_stock': dict(stock), '_records': [], 'captured': [],
           'stats': {'wins': 0, 'losses': 0, 'unclassified': 0},
           # 以前の再攻撃で残った計画。在庫が無ければ次の判断で消える。
           'card_override': {STEP: ['イッテツーン', 'イッテツーン']}}
    cur = {'enemy': 'ガルバンゾー', 'ally': 'ゼウス', 'enemy_hp': 30, 'ally_hp': 0,
           'start_enemy_hp': 30, 'start_ally_hp': 85, 'castle': 'ゴーメン',
           'side': 'attack', 'step': STEP, 'cards_used': [], 'cards_selected': [],
           'cards_missing': [], 'cards_unclassified': [],
           'card_consumption_complete': True, 'card_evidence_version': 1,
           'planned_cards': [], 'away': 0}
    mem['battle'] = cur
    p.battle_end(mem, 'map')
    p.battle_end(mem, 'map')
    return mem


def test_two_copies_in_stock_keep_the_two_opening_cards():
    mem = loss({'イッテツーン': 2})
    assert _plan_region(mem)['orders'][STEP] == 'pending' and mem['retries'][STEP] == 1
    assert mem['card_override'][STEP] == ['イッテツーン', 'イッテツーン']
    assert mem['retry_context'][STEP]['expected_metric'] == {'enemy_hp_after_open': 10}


def test_one_copy_in_stock_plans_one_card():
    mem = loss({'イッテツーン': 1})
    assert mem['card_override'][STEP] == ['イッテツーン']
    assert mem['retry_context'][STEP]['expected_metric'] == {'enemy_hp_after_open': 20}
    assert '1枚' in mem['retry_context'][STEP]['deviation_reason']


def test_no_stock_clears_the_plan_and_keeps_the_chart_kit():
    mem = loss({'イッテツーン': 0})
    assert STEP not in (mem.get('card_override') or {})
    assert mem['retry_context'][STEP]['expected_metric'] == {'enemy_hp_after_open': None}
    assert '実在庫の切り札なし' in mem['retry_context'][STEP]['deviation_reason']
    assert _plan_region(mem)['orders'][STEP] == 'pending'          # 再攻撃自体は続ける
    retry = [r for r in mem['_records'] if r['decision'] == 'order_retry']
    assert retry and retry[-1]['strategy_variant'] == 'retry_with_opening_cards'


def test_an_unread_stock_does_not_invent_cards():
    mem = loss({})
    assert STEP not in (mem.get('card_override') or {})
    assert mem['retry_context'][STEP]['expected_metric'] == {'enemy_hp_after_open': None}
