"""Owner 2026-11-02: the speedrun chart is abolished; castles are taken
mechanically, nearest an owned castle first (this is the production default).

conftest turns the legacy chart on for the rest of the suite; this module runs
with it off, exactly as production does.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401  (import side effects of the package)
from docich.hanjuku.strategy import replan, routes
from docich.hanjuku.strategy.orders import _distance


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(captured=('キカンドン',), garrison=None, **extra):
    home = chart.home_castle(1)
    base = {'chapter': 1, 'tick': 1000, 'month': '1-7', 'captured': list(captured), 'orders': {},
            'garrison': {home: ['どうし', 'ゼウス', 'ヴィーナス'],
                         **{c: ['ココット', 'リーキ'] for c in captured}},
            '_records': []}
    if garrison:
        base['garrison'] = garrison
    return {**base, **extra}


def attack_targets(mem):
    return [o['target'] for o in replan.interim_candidates(mem).values() if o['purpose'] == 'attack']


def test_the_chart_orders_are_off_by_default_but_the_measured_table_stays():
    assert chart.speedrun_enabled(1) is False and chart.orders(1) == ()
    assert chart.orders(5) == () and len(chart.measured_orders(5)) > 0
    # Chapters 4 (boss sites) and 9 (moving boss) still take their boss orders from the chart.
    assert chart.speedrun_enabled(4) and chart.speedrun_enabled(9)
    assert len(chart.orders(4)) > 0


def test_the_env_switch_restores_the_chart(monkeypatch):
    monkeypatch.setenv(chart.SPEEDRUN_CHART_ENV, '1')
    assert chart.speedrun_enabled(1) and len(chart.orders(1)) > 0


def test_castles_are_attacked_nearest_an_owned_castle_first():
    mem = memory(captured=('キカンドン',))
    owned = {'アルマムーン', 'キカンドン'}
    targets = attack_targets(mem)
    assert targets, 'some attack candidate must exist'
    nearest = lambda c: min(_distance(1, o, c) for o in owned)
    assert targets == sorted(targets, key=lambda c: (nearest(c), c))
    assert 'けっかい' not in targets          # the boss waits for every other castle


def test_lost_castles_still_come_back_before_new_conquests():
    mem = memory(captured=('キカンドン',), lost=['ゴーメン'])
    first = next(iter(replan.interim_candidates(mem).values()))
    assert first['target'] == 'ゴーメン' and first['purpose'] == 'retake'


def test_the_boss_is_attacked_only_after_every_other_castle_is_owned():
    others = [c for c in chart.castles(1) if c not in ('アルマムーン', 'けっかい')]
    partial = memory(captured=others[:-1])
    assert 'けっかい' not in [o['target'] for o in replan.interim_candidates(partial).values()]
    full = memory(captured=others)
    # Attacking the boss is what the gate is about; the garrison rotation the
    # same plan emits (owner 2026-11-04) only moves generals between our castles.
    # Owner 2026-11-05: the boss takes every spare as a wave, so the same
    # target may appear more than once in one plan.
    assert set(attack_targets(full)) == {'けっかい'}


def test_an_unfinished_chart_order_no_longer_holds_progress_back():
    # The chart's boss order waits on captures that never come; mechanical mode
    # has no such order, so the next uncaptured castle is simply attacked.
    mem = memory(captured=('キカンドン',), orders={'1-B1': 'pending'})
    mem['variant'] = 'chart_unavailable'
    assert attack_targets(mem)


def test_off_chart_runs_without_requesting_or_adopting_a_model_chart():
    mem = memory(captured=('キカンドン',))
    mem['_adjusted'] = {'request_id': 'whatever', 'chapter': 1, 'orders': []}
    replan._off_chart(mem)
    kinds = [r['decision'] for r in mem['_records']]
    assert 'chart_adjust_request' not in kinds and 'chart_adjust_applied' in kinds
    plan = mem['chart_plan']
    assert plan['source'] == 'mechanical' and plan['orders']
    assert plan['orders'][0]['target'] in attack_targets(memory(captured=('キカンドン',))) + ['ゴーメン']


def test_chapter_three_still_marches_along_the_measured_road_table():
    assert routes.legs(3) == {(o['source'], o['target']) for o in chart.measured_orders(3)
                              if not chart.is_boss_castle(o['target'])}
    assert routes.legs(3)


def test_lost_castles_are_retaken_nearest_an_owned_castle_first():
    # #3266: each retake books a different general, so キカンドン needs three
    # sendable spares (it used to name the same head-of-list actor every time).
    mem = memory(captured=('キカンドン',), lost=['スペンソニア', 'ゴーメン', 'ナキューメラ'],
                 garrison={'アルマムーン': ['どうし', 'ゼウス', 'ヴィーナス'],
                           'キカンドン': ['ココット', 'リーキ', 'ヘラ', 'アテナ']})
    retakes = [o['target'] for o in replan.interim_candidates(mem).values() if o['purpose'] == 'retake']
    nearest = lambda c: min(_distance(1, o, c) for o in ('アルマムーン', 'キカンドン'))
    assert retakes == sorted(retakes, key=lambda c: (nearest(c), c)) and len(retakes) == 3
