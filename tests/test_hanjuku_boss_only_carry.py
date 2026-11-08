"""ボス専用札 (バルムンク) を通常の出撃・戦闘計画に持ち込まない。

owner 2026-11-08（g1153 13:21 ココット）: 通常城ドンダムへ卵落とし用にバルムンクを
携行したが、戦闘側はボス突入の実測前は全バルムンク戦術を除外するため使えず、敗北で
そのまま失った。携行側（出撃の追加札・調整チャート既定戦術）と予定札
（``planned_cards``）を、戦闘側が実際に使える札だけに揃える。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from docich import hanjuku_policy as p
from docich import hanjuku_reference as reference
from docich.hanjuku.state.regions import flow as _flow, world as _world
from docich.hanjuku.strategy.execution import confirm_departure_order
from test_hanjuku_dropper_carry import defender, sortie


def test_a_boss_only_card_is_never_a_non_boss_egg_dropper():
    # シャルドネ56 + しゅじんこう90 = 146 → 卵落 > 3。バルムンク(5)は最強の
    # 卵落札なので、修正前はこの出撃で1枠を取っていた。
    order, mem, inventory = sortie('1-A2', stock=('バルムンク',))
    defender(mem, general='シャルドネ')
    assert reference.egg_droppers(['バルムンク'], 146) == ['バルムンク']
    wanted = p._deploy_cards(order, mem)
    assert p._dropper_card_inventory(mem, order, inventory, wanted) is False
    assert 'dropper_card_kit' not in mem


def test_a_non_boss_default_tactic_for_a_boss_only_card_is_reserved():
    step = 'I:x:1'
    order = {'step': step, 'general': 'ココット', 'source': 'イーピヨル',
             'target': 'ドンダム', 'cards': ['バルムンク', 'ミックミー'], 'after': None}
    mem = {'chapter': 3, 'tick': 100, 'orders': {step: 'launched'}, '_records': []}
    _world(mem)['launched_orders'] = {step: order}
    tactics = {t['card']: t for t in p._tactics(mem, step) if t.get('step') == step}
    assert tactics['バルムンク']['boss_only'] is True
    assert tactics['ミックミー']['boss_only'] is False


def bound_execution(cards=('バルムンク', 'ミックミー')):
    """A confirmed off-chart sortie: the measured departure kit is the authority."""
    step = 'I:x:1'
    mem = {'chapter': 1, 'tick': 20,
           '_run_identity': {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime',
                             'generation': 741, 'lease_id': 'test-lease'},
           'active': step, 'orders': {step: 'pending'}, 'order_context': {},
           'garrison': {'カストーラ': ['ゼウス', 'ヴィーナス']}}
    order = {'step': step, 'general': 'ゼウス', 'source': 'カストーラ',
             'target': 'ゴーメン', 'cards': list(cards), 'after': None}
    frozen = confirm_departure_order(order, mem, 'ゼウス', 'カストーラ', list(cards))
    assert frozen is not None
    mem['order_context'][step] = {'actual_general': 'ゼウス', 'departure_order': frozen,
                                  'observed_metric': {'general': 'ゼウス',
                                                      'cards': list(cards)}}
    _flow(mem)['sortie_attempt'] = {'step': step, 'target_seen': True,
                                    'confirmation_tick': 20}
    return mem, step


def test_planned_cards_keep_only_the_measured_departure_kit():
    mem, step = bound_execution()
    mem['card_override'] = {step: ['イッテツーン', 'イッテツーン']}
    cur = {'step': step, 'ally': 'ゼウス', 'enemy': 'ガルバンゾー',
           'entry_evidence': None}
    planned = p._planned_battle_cards(mem, cur, 'ガルバンゾー')
    assert 'ミックミー' in planned
    assert 'イッテツーン' not in planned    # 在庫0で携行していない
    assert 'バルムンク' not in planned      # ボス専用で通常戦では使えない


def test_phantom_planned_cards_no_longer_block_the_hopeless_retreat():
    cur = {'ally': 'ココット', 'enemy': 'ゼウス', 'side': 'attack', 'castle': 'ドンダム',
           'ally_hp': 14, 'enemy_hp': 85, 'start_ally_hp': 24, 'start_enemy_hp': 85,
           'survival': {'exhausted': True, 'cards_uncertain': False,
                        'egg_attempted': False},
           'own_egg_positive_observed': False}
    phantom = dict(cur, planned_cards=['バルムンク', 'イッテツーン', 'イッテツーン'])
    assert p._hero_retreat_needed(phantom) is False
    assert phantom['retreat_assessment']['reason'] != 'hopeless_attack_melee'
    usable = dict(cur, planned_cards=[])
    assert p._hero_retreat_needed(usable) is True
    assert usable['retreat_assessment']['reason'] == 'hopeless_attack_melee'
