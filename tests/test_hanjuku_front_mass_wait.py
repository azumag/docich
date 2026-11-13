"""A repelled chapter-9 front waits until two attackers can march together.

g1163 2026-11-13 09:3x-10:5x: every attack on ナッテ went in alone (ally 0 vs
enemy 32/39/46 HP) while the next general was still walking. The one-general
front guard is needed to keep the castle, but with two residents it leaves only
one attacker, so the defender recovers between waves. Waiting for two extra
residents (``_keep + 2``) lets two march in consecutive observations; it is not
the "wait for six" the owner ruled out.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C, D = ROAD[0], ROAD[1], ROAD[2], ROAD[3]


def memory(residents, enemy_hp=39, tick=5000, lost=(), defender_hp=None):
    garrison = {A: ['リーキ'], B: ['ベーグル'], C: list(residents)}
    mem = {'chapter': 9, 'tick': tick, 'month': '36-6',
           'captured': [c for c in (A, B, C) if c != chart.home_castle(9)],
           'lost': list(lost), 'orders': {},
           'world': {'garrison': garrison, 'sorties': {}, 'launched_orders': {}},
           'sortie_history': [{'target': D, 'outcome': 'lost', 'enemy_hp': enemy_hp,
                               'tick': tick - 100}],
           '_records': []}
    if defender_hp is not None:
        mem['castle_defenders'] = {D: {'hp': defender_hp, 'general': 'マサラ'}}
    return mem


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o.get('source') == C
                and o.get('target') == D and o.get('purpose') == 'attack']


def decided(mem, name):
    return [rec for rec in mem.get('_records') or [] if rec.get('decision') == name]


def test_a_repelled_front_with_two_residents_waits_for_a_second_attacker():
    mem = memory(['ローズマリー', 'ベーグル'])
    assert attacks(mem) == []
    records = decided(mem, 'front_wait_mass')
    assert records and records[-1]['observed_metric']['need'] == 3


def test_three_residents_let_two_attackers_march():
    mem = memory(['ローズマリー', 'ベーグル', 'キャロット'])
    assert attacks(mem), 'two residents over the guard can march together'


def test_the_pair_wait_is_bounded():
    mem = memory(['ローズマリー', 'ベーグル'])
    assert attacks(mem) == []
    mem['tick'] += corridor.FRONT_MASS_WAIT
    assert attacks(mem), 'a front that cannot refill must still attack'
    assert decided(mem, 'front_mass_timeout')


def test_a_nearly_dead_defender_is_attacked_alone():
    mem = memory(['ローズマリー', 'ベーグル'], defender_hp=5)
    assert attacks(mem), 'a finish beats the pair wait'


def test_a_retake_also_waits_for_a_pair():
    """Solo retakes lost (マフィン/カシス vs a 30/48-HP defender)."""
    mem = memory(['ローズマリー', 'ベーグル'], lost=[D])
    with chart.map_context(mem):
        assert [o for o in corridor.orders(mem) if o.get('purpose') == 'retake'] == []
    assert decided(mem, 'front_wait_mass')


def test_three_residents_retake_together():
    mem = memory(['ローズマリー', 'ベーグル', 'キャロット'], lost=[D])
    with chart.map_context(mem):
        assert any(o.get('purpose') == 'retake' for o in corridor.orders(mem))


def test_a_healthy_win_does_not_wait():
    mem = memory(['ローズマリー', 'ベーグル'], enemy_hp=0)
    mem['sortie_history'][-1]['outcome'] = 'won'
    assert attacks(mem), 'no repelled defender means no pair wait'


def test_a_just_defended_front_keeps_two_home():
    """g1163 2026-11-13: オオキク was counter-attacked right after capture.

    The attack on ナッテ left one defender and the castle was lost again. A
    front with a measured せめこまれました keeps two residents.
    """
    mem = memory(['ローズマリー', 'ベーグル'], enemy_hp=0)
    mem['sortie_history'][-1]['outcome'] = 'won'
    assert attacks(mem), 'without a defence the second general still leaves'
    mem['_records'] = []
    mem['defense_seen'] = {C: mem['tick'] - 10}
    assert attacks(mem) == []
    assert decided(mem, 'front_guard_held')


def test_an_expired_front_guard_stays_expired_within_the_observation():
    """g1163 2026-11-13 15:49: timeout then hold for ドンドン in one second.

    ``orders`` runs several times per decision; the first expiry allowed the
    attack and the next call re-armed the hold, so the minted order vanished.
    """
    mem = memory(['ローズマリー'])
    mem['front_guard_hold'] = {C: mem['tick'] - corridor.FRONT_GUARD_WAIT}
    assert corridor._front_guard_expired(mem, C) is True
    assert corridor._front_guard_expired(mem, C) is True


def test_an_expired_mass_wait_stays_expired_within_the_observation():
    mem = memory(['ローズマリー', 'ベーグル'])
    mem['front_mass_hold'] = {C: mem['tick'] - corridor.FRONT_MASS_WAIT}
    assert corridor._front_mass_expired(mem, C) is True
    assert corridor._front_mass_expired(mem, C) is True
