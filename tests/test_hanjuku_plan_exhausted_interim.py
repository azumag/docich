"""An adopted plan with no startable order must not stop the fallback sortie.

g1107 (2026-11-03 07:24-07:33Z, chapter 5, month 44-6..44-7): a worker plan was
adopted under request id `d2bca0d0`. None of its four pending orders could
start, so `next_order` answered None and `map_step` idled on the field. The
mechanical fallback never ran: `_mechanical_chart` returned as soon as the
stored plan's request id matched the current situation, even though
`_plan_pending` had just reported that no order of that plan can run. Nine
generals stayed in the home castle, two castles stayed lost and the month
advanced without a single sortie.

The speedrun path already falls back to one deterministic interim sortie for an
exhausted adopted plan; the mechanical path (the default since the speedrun
chart was abolished) now does the same.

The conftest enables the speedrun chart for the whole suite, so these tests
disable it explicitly: the defect is in the mechanical path production uses.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.strategy import replan


HOME = 'アルマムーン'
IDLE = ['ゼウス', 'ココット', 'ヴィーナス', 'どうし']


@pytest.fixture(autouse=True)
def mechanical_path(monkeypatch):
    """Run the path production uses: no speedrun chart (conftest enables it)."""
    monkeypatch.delenv('DOCICH_HANJUKU_SPEEDRUN_CHART', raising=False)
    assert not chart_adjust.chart.speedrun_enabled(1)


def memory(**overrides):
    mem = {
        'chapter': 1, 'tick': 1000, 'month': '1-5', '_records': [],
        'captured': ['キカンドン', 'ナキューメラ', 'カストーラ'],
        'lost': ['ジョンリギ'],
        'garrison': {HOME: list(IDLE)},
        'orders': {},
        'card_stock': {'イッテツーン': 3},
    }
    mem.update(overrides)
    return mem


def adopt(mem, *, after, source=HOME, general='ゼウス', target='ゴーメン'):
    """Adopt one plan order for exactly this situation, as a worker answer."""
    rid = chart_adjust.request_id(mem)
    step = chart_adjust.execution_step(rid, 'Mblocked')
    mem['chart_plan'] = {
        'request_id': rid, 'source': 'jev',
        'orders': [{'step': step, 'general': general, 'source': source, 'target': target,
                    'cards': [], 'after': after, 'note': 'adopted'}],
    }
    mem.setdefault('orders', {})[step] = 'pending'
    return step


def interim(mem):
    return (mem.get('chart_adjust') or {}).get('interim_order')


def test_a_blocked_adopted_plan_falls_back_to_one_interim_sortie():
    mem = memory()
    step = adopt(mem, after=['captured', 'ゴーメン'])      # ゴーメン is not ours
    assert replan._plan_pending(mem) is False
    replan._off_chart(mem)
    order = interim(mem)
    assert order is not None, 'a blocked adopted plan left the idle army with no sortie'
    assert order['purpose'] == 'retake' and order['target'] == 'ジョンリギ'
    assert order['source'] == HOME
    # The adopted plan is kept: a new situation, not this fallback, replaces it.
    assert mem['chart_plan']['orders'][0]['step'] == step
    assert [r for r in mem['_records'] if r['decision'] == 'chart_interim_order']


def test_a_startable_adopted_plan_still_suppresses_the_interim_sortie():
    mem = memory()
    step = adopt(mem, after=None)
    assert replan._plan_pending(mem) is True
    replan._off_chart(mem)
    assert interim(mem) is None
    assert mem['orders'][step] == 'pending'


def test_spare_generals_form_a_wave_on_the_first_target():
    """Owner 2026-11-04: 人数に余裕があれば一つの城へ波状攻撃.

    Four generals in the home castle and no other target: the first attack
    keeps one defender and the remaining spares join the same castle in
    successive sorties instead of one general per castle.
    """
    from docich import hanjuku_chart as chart
    castles = list(chart.castles(1))
    home = chart.home_castle(1)
    target = next(c for c in castles if c != home and not chart.is_boss_castle(c))
    # The hero's castle keeps two companions (guard), so the wave comes from
    # another owned castle with four measured generals (three spares).
    other = next(c for c in castles if c not in (home, target))
    mem = memory(captured=[c for c in castles if c != target], lost=[],
                 garrison={home: ['ゼウス', 'どうし'],
                           other: ['ココット', 'ヴィーナス', 'トレビス', 'ビーツ']})
    candidates = replan.interim_candidates(mem)
    attacks = [o for o in candidates.values() if o['target'] == target]
    assert len(attacks) == 3, candidates
    assert len({o['general'] for o in attacks}) == 3
    assert {o['general'] for o in attacks} <= set(mem['garrison'][other])
    assert {o['purpose'] for o in attacks} == {'attack'}


def test_two_generals_leave_a_defender_and_make_no_wave():
    from docich import hanjuku_chart as chart
    castles = list(chart.castles(1))
    home = chart.home_castle(1)
    target = next(c for c in castles if c != home and not chart.is_boss_castle(c))
    other = next(c for c in castles if c not in (home, target))
    mem = memory(captured=[c for c in castles if c != target], lost=[],
                 garrison={home: ['ゼウス', 'どうし'],
                           other: ['ココット', 'ヴィーナス']})
    candidates = replan.interim_candidates(mem)
    attacks = [o for o in candidates.values() if o['target'] == target]
    assert len(attacks) == 1, candidates


def test_an_exhausted_mechanical_plan_is_rebuilt_with_new_sorties():
    """Owner 2026-11-04: 計画が尽きたら月の終わりを待たず新計画を作る."""
    mem = memory()
    rid = chart_adjust.request_id(mem)
    step = 'Mdeadbeef'
    mem['chart_plan'] = {
        'schema': chart_adjust.SCHEMA, 'chapter': 1, 'request_id': rid,
        'source': 'mechanical',
        'orders': [{'step': step, 'general': 'ゼウス', 'source': HOME,
                    'target': 'ゴーメン', 'cards': [], 'after': None, 'note': ''}],
    }
    mem['orders'] = {step: 'launched'}
    mem['sorties'] = {step: {'source': HOME, 'target': 'ゴーメン', 'general': 'ゼウス',
                             'status': 'en_route', 'tick': mem['tick'] - 10}}
    replan._off_chart(mem)
    plan = mem.get('chart_plan') or {}
    assert plan.get('source') == 'mechanical', plan
    targets = [o['target'] for o in plan.get('orders') or []]
    assert targets, plan
    # The in-flight target is excluded; a new plan offers the remaining work.
    assert 'ジョンリギ' in targets, plan
    assert 'ゴーメン' not in targets or mem['orders'].get(step) != 'launched'
