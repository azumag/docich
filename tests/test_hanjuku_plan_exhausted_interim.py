"""An adopted plan with no startable order must not stop the fallback sortie.

g1107 (2026-11-03 07:24-07:33Z, chapter 5, month 44-6..44-7): a worker plan was
adopted under request id `d2bca0d0`. None of its four pending orders could
start, so `next_order` answered None and `map_step` idled on the field. The
mechanical fallback never ran: `_mechanical_chart` returned as soon as the
stored plan's request id matched the current situation, even though
`_plan_pending` had just reported that no order of that plan can run. Nine
generals stayed in the home castle, two castles stayed lost and the month
advanced without a single sortie.

When the adopted plan has no startable orders, the mechanical path (the
production default) must generate and adopt a complete replacement plan from
the current situation. An interim hold is only valid when no safe candidates
can be constructed.

The conftest enables the speedrun chart for the whole suite, so these tests
disable it explicitly: the defect is in the mechanical path production uses.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world


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


def test_a_blocked_adopted_plan_is_replaced_by_full_mechanical_chart():
    mem = memory()
    step = adopt(mem, after=['captured', 'ゴーメン'])      # ゴーメン is not ours
    assert replan._plan_pending(mem) is False
    expected = replan.interim_candidates(mem)
    assert len(expected) > 1, 'must test full-plan generation, not a single fallback'

    replan._off_chart(mem)

    plan = mem['chart_plan']
    assert plan['source'] == 'mechanical', plan
    assert len(plan['orders']) == len(expected), plan
    assert step not in [o['step'] for o in plan['orders']]
    assert any(o['purpose'] == 'retake' and o['target'] == 'ジョンリギ'
               and o['source'] == HOME for o in plan['orders']), plan
    assert interim(mem) is None
    assert [r for r in mem['_records'] if r['decision'] == 'chart_adjust_applied'
            and r.get('source') == 'mechanical']


def test_an_exhausted_adopted_plan_regenerates_only_after_it_becomes_unstartable():
    mem = memory()
    step = adopt(mem, after=None)
    assert replan._plan_pending(mem) is True
    replan._off_chart(mem)
    assert mem['chart_plan']['source'] == 'jev'
    assert interim(mem) is None

    # The source castle was vacated, while the army is available elsewhere.
    # A stale actor name alone is not enough to invalidate _plan_pending.
    _world(mem)['garrison'][HOME] = []
    _world(mem)['garrison']['カストーラ'] = list(IDLE)
    assert replan._plan_pending(mem) is False
    replan._off_chart(mem)
    assert mem['chart_plan']['source'] == 'mechanical'
    assert step not in [o['step'] for o in mem['chart_plan']['orders']]
    assert interim(mem) is None


def test_a_chart_with_no_safe_candidates_records_hold_instead_of_fake_orders():
    # Every owned castle has a measured empty roster. Leaving the other
    # castles absent from garrison means "unread", not "empty", and correctly
    # creates source-probe candidates instead of a hold.
    mem = memory(garrison={c: [] for c in
                          (HOME, 'キカンドン', 'ナキューメラ', 'カストーラ')})
    adopt(mem, after=['captured', 'ゴーメン'])
    assert not replan.interim_candidates(mem)

    replan._off_chart(mem)

    assert mem['chart_plan']['source'] == 'jev'  # no replacement to adopt yet
    assert interim(mem) is None
    assert any(r['decision'] == 'chart_interim_hold' for r in mem['_records'])


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
    assert {o['general'] for o in attacks} <= set(_world(mem)['garrison'][other])
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


def test_a_multi_defender_castle_gets_more_than_four_wave_sorties():
    """しまばら kept producing successor defenders (g1163 2026-11-10 08-10).

    ながはま held eight spares; four wave sorties were not enough to clear a
    castle that answers each defeat with the next defender.
    """
    from docich.hanjuku.strategy import replan

    assert replan.INTERIM_WAVE_LIMIT >= 6
