"""A failed plan step must not stay dead for the rest of the situation.

g1153 2026-11-08 (chapter 3, ドンダム the only enemy castle left): the relay
move that staffs イーピヨル — the only owned castle with a measured leg to
ドンダム — failed once. ``next_order`` skips a 'failed' step forever, and the
rebuilt mechanical plan kept the same execution id for the same
(source>target:purpose) order while the request id stayed the same. Nothing
cleared the status, so イーピヨル kept a single general, no retake could ever
start, and the bot spent whole months on the monthly routine while the enemy
castle stayed standing.

The retry must wait out the same window ``_suppress_recent_failures`` uses and
stay capped per month, so a genuinely impossible order cannot drain the army.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart_adjust as chart_adjust
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import orders, replan
from docich.hanjuku.state.regions import world as _world


@pytest.fixture(autouse=True)
def mechanical_path(monkeypatch):
    """Run the path production uses: no speedrun chart (conftest enables it)."""
    monkeypatch.delenv('DOCICH_HANJUKU_SPEEDRUN_CHART', raising=False)
    assert not chart_adjust.chart.speedrun_enabled(3)


def memory():
    """The live g1153 garrison: every castle but ドンダム owned, one leg in."""
    castles = set(chart.castles(3))
    captured = sorted(castles - {'ドンダム'})
    return {
        'chapter': 3, 'month': '7-11', 'tick': 20000, '_records': [],
        'captured': captured, 'lost': ['ドンダム'],
        'garrison': {'アバロン': ['セミヨン'],
                     'アルマムーン': ['どうし', 'キール', 'デュオニソス'],
                     'イーピヨル': ['ココット'],
                     'グリン': ['アンディーブ'],
                     'コラダイン': ['ヘパイストス', 'トレビス'],
                     'パタゴーヌ': ['ユイートル', 'セルリアク'],
                     'メガパタ': ['エピィ', 'チコリ'],
                     'リグア': ['ポワソン']},
        'orders': {}, 'sorties': {}, 'launched_orders': {},
        'house_eggs': {}, 'general_location_unknown': [],
    }


def relay_step(mem):
    """(execution step, local step) of the staging move into イーピヨル."""
    rid = chart_adjust.request_id(mem)
    relay = next(o for o in replan.interim_candidates(mem).values()
                 if o['purpose'] == 'move' and o['target'] == 'イーピヨル')
    local = replan._mechanical_step(relay, set())
    return chart_adjust.execution_step(rid, local), local


def advance(mem, rid):
    replan._off_chart_mechanical(mem, mem.setdefault('chart_adjust', {}), rid,
                                 replan._defense_gate(mem))


def test_the_only_route_to_the_last_enemy_castle_is_a_chapter_three_leg():
    """イーピヨル is the only owned castle that can reach ドンダム (wave fix)."""
    from docich.hanjuku.strategy import routes
    mem = memory()
    assert routes.allowed(mem, 'イーピヨル', 'ドンダム')
    assert not routes.allowed(mem, 'パタゴーヌ', 'ドンダム')
    assert not routes.allowed(mem, 'メガパタ', 'ドンダム')
    owned = set(_world(mem)['garrison'])
    # No spare can reach ドンダム until the relay staffs イーピヨル, so no wave
    # may be planned from an unstartable source.
    assert replan._wave_pick(mem, 'ドンダム', owned, set(), False) is None
    # The whole plan may only offer orders that can actually start: the single
    # defender of イーピヨル leaves the relay move as the first step.
    candidates = list(replan.interim_candidates(mem).values())
    assert candidates
    assert all(routes.allowed(mem, o['source'], o['target']) for o in candidates)
    assert 'ドンダム' not in {o['target'] for o in candidates}
    _world(mem)['garrison']['イーピヨル'] = ['ココット', 'ピオーネ']
    picked = replan._wave_pick(mem, 'ドンダム', owned, set(), False)
    assert picked is not None and picked[0] == 'イーピヨル'


def test_a_failed_relay_move_is_retried_after_the_failure_window():
    mem = memory()
    rid = chart_adjust.request_id(mem)
    step, local = relay_step(mem)
    mem['orders'][step] = 'failed'
    mem['failed_local_step'] = {local: mem['tick'] - replan.PLAN_STALE_TICKS - 1}
    # A stale actor chosen by the failed attempt must not steer the retry
    # (live g1153: ココット was recorded for a move out of コラダイン while he
    # stood at イーピヨル).
    mem['general_override'] = {step: 'ココット'}
    mem['launched_orders'] = {step: {'general': 'ココット', 'source': 'コラダイン',
                                     'target': 'イーピヨル'}}
    advance(mem, rid)
    assert mem['orders'][step] == 'pending', mem['orders']
    assert step not in mem['general_override'], mem['general_override']
    assert step not in (_world(mem).get('launched_orders') or {})
    order = orders.next_order(mem)
    assert order is not None and order['step'] == step, order
    assert (order['purpose'], order['source'], order['target']) == (
        'move', 'コラダイン', 'イーピヨル')


def test_a_failure_inside_the_suppression_window_is_not_retried():
    mem = memory()
    rid = chart_adjust.request_id(mem)
    step, local = relay_step(mem)
    mem['orders'][step] = 'failed'
    mem['failed_local_step'] = {local: mem['tick'] - 10}
    advance(mem, rid)
    assert mem['orders'][step] == 'failed'
    assert orders.next_order(mem) is None


def test_the_retry_is_capped_per_month_and_step():
    mem = memory()
    rid = chart_adjust.request_id(mem)
    step, local = relay_step(mem)
    mem['orders'][step] = 'failed'
    mem['failed_local_step'] = {local: mem['tick'] - replan.PLAN_STALE_TICKS - 1}
    mem['plan_step_rearm'] = {step: {'month': mem['month'],
                                     'count': replan.NONBOSS_STEP_REARM_LIMIT}}
    advance(mem, rid)
    assert mem['orders'][step] == 'failed'
    # A new month gives the order its bounded retries again.
    mem['month'] = '8-1'
    advance(mem, rid)
    assert mem['orders'][step] == 'pending'


def test_a_failed_boss_step_is_rearmed_even_when_the_plan_is_unchanged():
    """A failed boss step is re-armed by the mechanical rebuild.

    The boss is the next target after the last normal castle (here ドンダム),
    and it has its own bounded re-arm for the g1133 stall. Assert that the
    rebuild re-arms it without changing the plan's steps, so the re-arm can
    never depend on the plan being replaced.
    """
    mem = memory()
    mem['captured'] = sorted(set(chart.castles(3)) - {'ボス'})
    mem['lost'] = []
    rid = chart_adjust.request_id(mem)
    advance(mem, rid)
    boss = [o for o in mem['chart_plan']['orders'] if chart.is_boss_castle(o['target'])]
    assert boss, mem['chart_plan']
    mem.setdefault('failed_local_step', {})
    for order in boss:
        mem['orders'][order['step']] = 'failed'
        mem['failed_local_step'][order['local_step']] = (
            mem['tick'] - replan.PLAN_STALE_TICKS - 1)
    mem['boss_step_rearm'] = {}
    applied = [r for r in mem['_records'] if r['decision'] == 'chart_adjust_applied']
    advance(mem, rid)
    # The rebuilt plan was identical (otherwise this would prove nothing).
    assert [r for r in mem['_records'] if r['decision'] == 'chart_adjust_applied'] == applied
    assert all(mem['orders'][order['step']] == 'pending' for order in boss)

