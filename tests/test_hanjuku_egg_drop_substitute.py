"""#2968: against a measured egg-holding defender, the substitute is a general
whose max HP lets a stocked card drop that egg.

g1075-1d5f65e9 2026-10-29: the retake of リグア (defender ガルバンゾー, max HP 30,
egg, summons on contact) took the first listed general. With the live stock
(グリンボー 卵落 4, バルムンク 5, ミックミー 2) only リースリング (37) gets
(37 + 30) mod 16 = 3 under a card; デーメーテール (49) gets 15.
"""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows.deploy import _egg_drop_substitutes
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas

STOCK = {'グリンボー': 59, 'バルムンク': 12, 'ミックミー': 16}
ORDER = {'step': '1-C1', 'general': 'ココット', 'source': 'アルマムーン', 'target': 'ジョンリギ'}


def mem_for(defender='ガルバンゾー', stock=STOCK, chapter=1):
    mem = {'chapter': 1, 'active': '1-C1', 'orders': {'1-C1': 'pending'}, 'card_stock': dict(stock)}
    if defender:
        mem['castle_defenders'] = {'ジョンリギ': {'chapter': chapter, 'general': defender,
                                              'evidence': 'battle_panel'}}
    return mem


def general_list(*names):
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    for i, name in enumerate(names):
        c.text(144, 39 + 16 * i, name)
    c.hand(122, 33)
    screen = parse(c.frame())
    assert screen.kind == 'general_list'
    return screen


def test_the_live_candidates_pick_the_general_who_can_drop_the_egg():
    present = ['デーメーテール', 'レモン', 'ジェラート', 'リースリング']
    assert _egg_drop_substitutes(mem_for(), ORDER, present) == ['リースリング']


def test_the_deploy_step_selects_that_substitute_and_says_why():
    mem = mem_for()
    actions = policy.deploy_step(general_list('デーメーテール', 'リースリング'), mem)
    assert actions[0]['buttons'] == ['down']
    assert mem['general_override']['1-C1'] == 'リースリング'
    record = [r for r in mem['_records'] if r['decision'] == 'order_substitute'][-1]
    assert record['reason'] == '守備将軍の卵を落とせる最大HPの将軍を代役にする'


@pytest.mark.parametrize('mem', [
    mem_for(defender=None),                       # defender never measured
    mem_for(defender='ヘルメス'),                  # measured, holds no egg
    mem_for(stock={'ミックミー': 16}),             # no stocked card reaches any threshold
    mem_for(chapter=3),                           # a record from another chapter
], ids=['unknown', 'eggless', 'no_dropper', 'other_chapter'])
def test_otherwise_the_first_listed_general_stays_the_substitute(mem):
    actions = policy.deploy_step(general_list('デーメーテール', 'リースリング'), mem)
    assert mem['general_override']['1-C1'] == 'デーメーテール'
    assert actions == [policy.pad('a')]
    record = [r for r in mem['_records'] if r['decision'] == 'order_substitute'][-1]
    assert record['reason'] == '後続手順とボス条件を満たすため'
