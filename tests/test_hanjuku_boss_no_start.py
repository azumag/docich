"""docich#5775: boss-only months must recover a startable boss order.

The failure shape was zero ``order_start``: no companion gate was ever
reached. Two bounded recovery properties are pinned here:

* a live non-boss plan order may not keep deterministic boss candidate
  generation switched off for the whole boss-only month;
* when no legal boss wave exists, the same recovery must not fabricate one.

This is a mechanical-path fix (the production default): conftest turns the
legacy speedrun chart on for the rest of the suite, so this module runs with
it off, exactly as production does. With the chart on, the static boss order
6-B1 would start instead and the forced replan would never fire.
"""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.flows import map as map_flow
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import plan as _plan_region
from docich.hanjuku_screen import Screen
from test_hanjuku_off_chart import FRAME

@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


CH = 6
CASTLES = chart.castles(CH)
BOSS = chart.boss_castle(CH)
HOME = chart.home_castle(CH)
NORMAL = [c for c in CASTLES if not chart.is_boss_castle(c)]
MOVE_TARGET = next(c for c in NORMAL if c != HOME)


def ch6_map_screen(x, y):
    return Screen(lines=[], hand=None, text='', kind='map', cursor=(x, y),
                  header={'chapter': CH, 'year': 10, 'month': 11, 'gold': 9856})


def boss_only_mem(rows=None):
    garrison = {c: [] for c in NORMAL}
    garrison[HOME] = ['どうし', 'ゼウス', 'アテナ']
    mem = {
        'chapter': CH, 'month': '10-11', 'tick': 10000, 'gold': 9856,
        'world_map_tick': 10000,
        '_records': [], 'captured': list(NORMAL), 'lost': [],
        'orders': {}, 'garrison': garrison,
        'world': {'sorties': {}}, 'general_location_unknown': [],
        'general_statuses': {'statuses': rows or {}},
    }
    return mem


def install_nonboss_move_plan(mem):
    """A mechanical plan with one live non-boss move and no boss order."""
    rid = chart_adjust.request_id(mem)
    step = chart_adjust.execution_step(rid, 'Mmove')
    order = {'step': step, 'local_step': 'Mmove', 'general': 'ゼウス',
             'source': HOME, 'target': MOVE_TARGET, 'cards': [], 'after': None,
             'purpose': 'move', 'note': 'test move'}
    _plan_region(mem)['chart_plan'] = {
        'request_id': rid, 'source': 'mechanical', 'orders': [order],
        'purchases': None, 'recruitment': None,
    }
    _plan_region(mem)['orders'][step] = 'pending'
    return order


def boss_orders(mem):
    return [o for o in ((_plan_region(mem).get('chart_plan') or {}).get('orders') or ())
            if isinstance(o, dict) and o.get('target') == BOSS]


def test_boss_only_replan_passes_a_nonboss_pending_plan_and_starts_a_wave():
    mem = boss_only_mem()
    install_nonboss_move_plan(mem)

    actions = map_flow.map_step(ch6_map_screen(80, 90), mem, FRAME)

    starts = [r for r in mem['_records'] if r['decision'] == 'order_start']
    assert starts, actions
    # Assert on the order_start record itself (target/purpose), not on the
    # final chart plan: the forced replan rebuilds the plan, so looking the
    # started step up in it is brittle (CI hanjuku-suite (3/3) hit
    # StopIteration here while a boss wave had in fact started).
    boss_starts = [r for r in starts
                   if r.get('target') == BOSS and r.get('purpose') == 'attack']
    assert boss_starts, starts
    assert any(r['decision'] == 'boss_no_start_replan' for r in mem['_records'])


def test_forced_replan_does_not_fabricate_a_wave_when_everything_is_eggless():
    rows = {
        'ゼウス': {'hp': 60, 'egg': None, 'uses': None, 'broken': False},
        'アテナ': {'hp': 60, 'egg': None, 'uses': None, 'broken': False},
    }
    mem = boss_only_mem(rows=rows)
    install_nonboss_move_plan(mem)

    replan._off_chart(mem)

    assert any(r['decision'] == 'boss_no_start_replan' for r in mem['_records'])
    gaps = [r for r in mem['_records'] if r['decision'] == 'boss_candidate_gap']
    assert gaps and gaps[-1]['observed_metric']['kind'] == 'no_legal_candidate_measured_eggless'
    assert not [o for o in boss_orders(mem)
                if o.get('purpose') == 'attack' and not o.get('source_probe')]
    assert not [r for r in mem['_records'] if r['decision'] == 'order_start']
