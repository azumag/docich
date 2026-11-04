"""Boss waves must keep going while one sortie is still marching.

Owner 2026-11-02 designed successive waves at the boss (every spare general,
not only the charted single sortie), but the generic "one recent expedition
per target" guard removed the boss again for up to SORTIE_BUSY_TICKS. At
chapter end the boss is the only target, so the planner answered no
candidates and the field map idled (owner report 2026-11-04).

Owner 2026-11-04: a second boss sortie is allowed while the first is in
flight; the spares and the one-defender rule still decide who goes.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.strategy import replan


@pytest.fixture(autouse=True)
def mechanical_path(monkeypatch):
    """Run the path production uses: no speedrun chart (conftest enables it)."""
    monkeypatch.delenv('DOCICH_HANJUKU_SPEEDRUN_CHART', raising=False)
    assert not chart.speedrun_enabled(5)


HOME5 = chart.home_castle(5)
BOSS5 = chart.boss_castle(5)
ORDINARY5 = [c for c in chart.castles(5) if c != BOSS5]
STAFFED5 = 'カルーン'


def memory(**overrides):
    mem = {
        'chapter': 5, 'tick': 1000, 'month': '47-2', '_records': [],
        'captured': list(ORDINARY5), 'lost': [],
        'garrison': {HOME5: ['どうし'],
                     STAFFED5: ['ゼウス', 'ココット', 'ヴィーナス', 'ビーツ',
                                'トレビス', 'アマンディーヌ', 'カペリーニ']},
        'orders': {}, 'card_stock': {'イッテツーン': 3},
    }
    mem.update(overrides)
    return mem


def boss_sortie(general, tick=990, step='A:test:M1'):
    return {step: {'general': general, 'source': STAFFED5, 'target': BOSS5,
                   'status': 'en_route', 'tick': tick}}


def boss_attacks(candidates):
    return [order for order in candidates.values() if order['target'] == BOSS5]


def test_a_second_boss_wave_is_planned_while_the_first_marches():
    mem = memory(sorties=boss_sortie('ゼウス'))
    attacks = boss_attacks(replan.interim_candidates(mem))
    assert attacks, 'the boss wave stopped while one sortie was still marching'
    assert attacks[0]['purpose'] == 'attack'
    # The marching general is not reused; a measured spare in the source goes.
    assert attacks[0]['general'] not in ('ゼウス', 'どうし')


def test_the_wave_budget_caps_concurrent_boss_sorties():
    # Four in flight still leaves room in the five-sortie budget: the owner's
    # double dispatch is inside it and the wave keeps going.
    four = {f'A:test:M{i}': {'general': name, 'source': STAFFED5, 'target': BOSS5,
                             'status': 'en_route', 'tick': 990}
            for i, name in enumerate(['ゼウス', 'ココット', 'ヴィーナス', 'ビーツ'])}
    assert boss_attacks(replan.interim_candidates(memory(sorties=four)))
    # At the budget no sixth sortie is planned even though spares remain.
    five = {f'A:test:M{i}': {'general': name, 'source': STAFFED5, 'target': BOSS5,
                             'status': 'en_route', 'tick': 990}
            for i, name in enumerate(['ゼウス', 'ココット', 'ヴィーナス', 'ビーツ', 'トレビス'])}
    assert boss_attacks(replan.interim_candidates(memory(sorties=five))) == []


def test_an_ordinary_target_still_waits_for_its_marching_sortie():
    target = ORDINARY5[0]
    mem = memory(captured=[c for c in ORDINARY5 if c != target],
                 sorties={'A:test:M3': {'general': 'ゼウス', 'source': STAFFED5,
                                        'target': target, 'status': 'en_route',
                                        'tick': 990}})
    candidates = replan.interim_candidates(mem)
    assert target not in {order['target'] for order in candidates.values()}
    # The staffed castle still has spares: the target, not the wave, is what
    # was withheld.
    assert candidates


def test_a_second_boss_wave_gets_its_own_step_id():
    first = {'source': STAFFED5, 'target': BOSS5, 'purpose': 'attack', 'general': 'ゼウス'}
    second = dict(first, general='ココット')
    # The first wave keeps the position-independent id (g1097).
    assert (replan._mechanical_step(first, set(), memory())
            == replan._mechanical_step(second, set(), memory()))
    # Built while one is still in flight, the next wave must not reuse the
    # launched step: the marching sortie would keep it 'launched' and the new
    # order would never start.
    marching = memory(sorties=boss_sortie('ゼウス'))
    assert (replan._mechanical_step(first, set(), marching)
            != replan._mechanical_step(second, set(), marching))
    # Every other target keeps its old identity.
    normal = {'source': STAFFED5, 'target': ORDINARY5[0], 'purpose': 'attack',
              'general': 'ゼウス'}
    assert (replan._mechanical_step(normal, set(), marching)
            == replan._mechanical_step(dict(normal, general='ココット'), set(), marching))


def test_an_exhausted_boss_plan_rebuilds_a_wave_with_a_new_step():
    mem = memory()
    step = 'Mdeadbeef'
    rid = chart_adjust.request_id(mem)
    mem['chart_plan'] = {
        'schema': chart_adjust.SCHEMA, 'chapter': 5, 'request_id': rid,
        'source': 'mechanical',
        'orders': [{'step': step, 'general': 'ゼウス', 'source': STAFFED5,
                    'target': BOSS5, 'cards': [], 'after': None, 'note': ''}],
    }
    mem['orders'] = {step: 'launched'}
    mem['sorties'] = boss_sortie('ゼウス', step=step)
    replan._off_chart(mem)
    plan = mem.get('chart_plan') or {}
    assert plan.get('source') == 'mechanical', plan
    targets = [order['target'] for order in plan.get('orders') or ()]
    assert BOSS5 in targets, plan
    assert all(order['step'] != step for order in plan['orders']), plan
    assert mem.setdefault('orders', {}).get(plan['orders'][0]['step']) in (None, 'pending')
