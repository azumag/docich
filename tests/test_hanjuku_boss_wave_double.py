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
from docich.hanjuku.state.regions import plan as _plan_region


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


def test_the_boss_keeps_taking_waves_with_many_already_in_flight():
    # Owner 2026-11-05: no in-flight budget (BOSS_WAVE_LIMIT was unnecessary).
    # Even with six waves already marching, the next plan sends the spare.
    full = {f'A:test:M{i}': {'general': name, 'source': STAFFED5, 'target': BOSS5,
                             'status': 'en_route', 'tick': 990}
            for i, name in enumerate(['ゼウス', 'ココット', 'ヴィーナス', 'ビーツ',
                                      'トレビス', 'アマンディーヌ'])}
    mem = memory(sorties=full)
    # Six are marching: two idle generals must remain, so one can still go.
    mem['garrison'][STAFFED5].append('ポセイドン')
    attacks = boss_attacks(replan.interim_candidates(mem))
    assert attacks and attacks[0]['general'] not in (
        'ゼウス', 'ココット', 'ヴィーナス', 'ビーツ', 'トレビス', 'アマンディーヌ')


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
    _plan_region(mem)['chart_plan'] = {
        'schema': chart_adjust.SCHEMA, 'chapter': 5, 'request_id': rid,
        'source': 'mechanical',
        'orders': [{'step': step, 'general': 'ゼウス', 'source': STAFFED5,
                    'target': BOSS5, 'cards': [], 'after': None, 'note': ''}],
    }
    _plan_region(mem)['orders'] = {step: 'launched'}
    mem['sorties'] = boss_sortie('ゼウス', step=step)
    replan._off_chart(mem)
    plan = _plan_region(mem).get('chart_plan') or {}
    assert plan.get('source') == 'mechanical', plan
    targets = [order['target'] for order in plan.get('orders') or ()]
    assert BOSS5 in targets, plan
    assert all(order['step'] != step for order in plan['orders']), plan
    assert _plan_region(mem).setdefault('orders', {}).get(plan['orders'][0]['step']) in (None, 'pending')


def _failed_boss_step(mem):
    """Make the next mechanical plan offer a boss step already marked failed."""
    candidates = replan.interim_candidates(mem)
    boss = next(order for order in candidates.values() if order['target'] == BOSS5)
    local = replan._mechanical_step(boss, set(), mem)
    rid = chart_adjust.request_id(mem)
    step = chart_adjust.execution_step(rid, local)
    _plan_region(mem)['chart_plan'] = {
        'schema': chart_adjust.SCHEMA, 'chapter': 5, 'request_id': rid,
        'source': 'mechanical',
        'orders': [dict(boss, step=local, cards=[], after=None, note='')],
    }
    _plan_region(mem)['orders'] = {step: 'failed'}
    return step


def test_a_failed_boss_step_is_rearmed_for_the_next_plan():
    """g1133: a boss launch-miss failure must not end the chapter's sorties."""
    mem = memory()
    step = _failed_boss_step(mem)
    replan._off_chart(mem)
    assert _plan_region(mem)['orders'][step] == 'pending'
    assert any(record['decision'] == 'plan_step_rearmed' for record in mem['_records'])


def test_the_rearm_waits_for_its_cooldown():
    mem = memory()
    step = _failed_boss_step(mem)
    mem['boss_step_rearm'] = {step: {'month': mem['month'], 'count': 1, 'tick': mem['tick']}}
    replan._off_chart(mem)
    assert _plan_region(mem)['orders'][step] == 'failed'


def test_the_rearm_budget_is_per_month():
    mem = memory()
    step = _failed_boss_step(mem)
    mem['boss_step_rearm'] = {step: {'month': mem['month'],
                                     'count': replan.BOSS_STEP_REARM_LIMIT,
                                     'tick': mem['tick'] - 10_000}}
    replan._off_chart(mem)
    assert _plan_region(mem)['orders'][step] == 'failed'


def test_a_failed_non_boss_step_is_not_rearmed():
    mem = memory()
    step = chart_adjust.execution_step('rid', 'Mfeed')
    _plan_region(mem)['orders'] = {step: 'failed'}
    replan._rearm_failed_boss_steps(
        mem, [{'step': 'Mfeed', 'target': ORDINARY5[0], 'general': 'ゼウス'}], 'rid')
    assert _plan_region(mem)['orders'][step] == 'failed'


# --- a lost castle whose retake keeps failing must not hold the boss (g1133) ---

FAR5 = [c for c in ORDINARY5 if c != HOME5]
LOST5 = FAR5[0]


def _plan_with_failed_retakes(mem, castle, count):
    """A stored mechanical plan with ``count`` failed retake steps for ``castle``."""
    rid = chart_adjust.request_id(mem)
    orders, status = [], {}
    for i in range(count):
        step = chart_adjust.execution_step(rid, f'Mretake{i}')
        orders.append({'step': step, 'general': f'ココット{i}', 'source': HOME5,
                       'target': castle, 'cards': [], 'after': None, 'note': ''})
        status[step] = 'failed'
    _plan_region(mem)['chart_plan'] = {'schema': chart_adjust.SCHEMA, 'chapter': 5, 'request_id': rid,
                         'source': 'mechanical', 'orders': orders}
    _plan_region(mem)['orders'] = status
    return [order['step'] for order in orders]


def lost_memory():
    return memory(captured=[c for c in ORDINARY5 if c != LOST5], lost=[LOST5])


def test_a_lost_castle_with_a_fresh_retake_still_gates_the_boss():
    # No failed retake yet: the retake runs first, the boss waits (2026-11-02).
    candidates = replan.interim_candidates(lost_memory())
    assert boss_attacks(candidates) == []
    assert any(order['purpose'] == 'retake' for order in candidates.values())


def test_too_few_failed_retakes_do_not_release_the_boss():
    mem = lost_memory()
    _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS - 1)
    assert boss_attacks(replan.interim_candidates(mem)) == []


def test_a_stalled_retake_releases_the_boss_and_the_boss_leads():
    mem = lost_memory()
    _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS)
    candidates = replan.interim_candidates(mem)
    first = next(iter(candidates.values()))
    assert first['target'] == BOSS5 and first['purpose'] == 'attack', candidates
    # The retake stays in the plan as an ordinary later candidate.
    assert any(order['purpose'] == 'retake' and order['target'] == LOST5
               for order in candidates.values())


def test_the_stall_survives_the_plan_dropping_its_failed_steps():
    mem = lost_memory()
    _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS)
    replan._failed_retakes(mem, record=True)
    _plan_region(mem)['chart_plan'] = None            # the rebuilt plan no longer lists them
    assert boss_attacks(replan.interim_candidates(mem))


def test_a_castle_that_is_not_lost_still_blocks_the_boss():
    # One castle is lost and stalled, another is simply not captured yet.
    other = FAR5[1]
    mem = memory(captured=[c for c in ORDINARY5 if c not in (LOST5, other)], lost=[LOST5])
    _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS)
    assert boss_attacks(replan.interim_candidates(mem)) == []


def test_every_stalled_lost_castle_must_reach_the_limit():
    other = FAR5[1]
    mem = memory(captured=[c for c in ORDINARY5 if c not in (LOST5, other)],
                 lost=[LOST5, other])
    steps = _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS)
    assert boss_attacks(replan.interim_candidates(mem)) == []
    # The second castle's retakes fail as well: now neither gates the boss.
    mem['retake_failures'] = {'chapter': 5, 'castles': {
        other: [f'A:x:M{i}' for i in range(replan.RETAKE_STALL_FAILS)]}}
    assert boss_attacks(replan.interim_candidates(mem))
    assert steps


def test_a_retaken_castle_forgets_its_failures():
    mem = lost_memory()
    _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS)
    replan._failed_retakes(mem, record=True)
    mem['lost'] = []
    mem['captured'] = list(ORDINARY5)
    assert replan._failed_retakes(mem, record=True) == {}
    assert mem['retake_failures']['castles'] == {}


def test_failures_from_another_chapter_are_ignored():
    mem = lost_memory()
    mem['retake_failures'] = {'chapter': 4, 'castles': {
        LOST5: [f'A:x:M{i}' for i in range(replan.RETAKE_STALL_FAILS)]}}
    assert boss_attacks(replan.interim_candidates(mem)) == []


def test_the_mechanical_rebuild_records_the_failures_before_clearing_the_plan():
    mem = lost_memory()
    _plan_with_failed_retakes(mem, LOST5, replan.RETAKE_STALL_FAILS)
    replan._off_chart(mem)
    book = mem['retake_failures']['castles']
    assert len(book[LOST5]) == replan.RETAKE_STALL_FAILS
    targets = [order['target'] for order in (_plan_region(mem).get('chart_plan') or {}).get('orders') or ()]
    assert BOSS5 in targets, targets


HOME6 = chart.home_castle(6)
BOSS6 = chart.boss_castle(6)
ORDINARY6 = [c for c in chart.castles(6) if c != BOSS6]


def memory6(**overrides):
    mem = {
        'chapter': 6, 'tick': 1000, 'month': '49-9', '_records': [],
        'captured': list(ORDINARY6), 'lost': [],
        'garrison': {HOME6: ['どうし', 'ポワソン', 'ガスパチョ', 'バゲット'], 'イカリア': ['ゼウス']},
        'orders': {}, 'card_stock': {'イッテツーン': 3},
    }
    mem.update(overrides)
    return mem


def test_chapter_6_boss_is_targetable_once_every_castle_is_ours():
    """g1133 2026-11-05: without a boss cell next_order() returned None with
    all twelve castles ours, so the chapter could never be finished."""
    assert BOSS6 in chart.castles(6)
    candidates = replan.interim_candidates(memory6())
    assert any(o['target'] == BOSS6 for o in candidates.values())


def test_chapter_6_boss_waits_for_the_last_castle():
    mem = memory6(captured=[c for c in ORDINARY6 if c != 'アブダレ'])
    candidates = replan.interim_candidates(mem)
    assert not any(o['target'] == BOSS6 for o in candidates.values())
