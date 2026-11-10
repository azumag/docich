"""Issue #2841/#3265: cancelled sorties must not be reissued unchanged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku_font import TextLine
from docich.hanjuku.flows.deploy import _verify_sortie_source
from docich.hanjuku.state.regions import world as _world, plan as _plan_region


def _screen(words=()):
    lines = [TextLine(47 + 16 * i, tuple((144 + 8 * j, c) for j, c in enumerate(w)))
             for i, w in enumerate(words)]
    return Screen(kind='general_list', text=''.join(words), lines=lines,
                  hand=(122, 41, 139, 53))


def test_hero_broken_sortie_is_not_reissued_while_egg_stays_broken():
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['どうし']},
           'orders': {'1-A1': 'pending'}, 'picked': [], 'active': '1-A1',
           'house_eggs': {'どうし': {'broken': True, 'month': '1-6'}}}
    order = {'step': '1-A1', 'general': 'どうし', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'cards': [], 'after': None}
    p.deploy_step(Screen(lines=[], hand=None, text='', kind='castle_menu'), mem)
    assert _plan_region(mem)['orders']['1-A1'] != 'launched'
    assert not p._ready(order, mem)


def test_sortie_cancelled_observed_is_not_reissued_while_general_stays():
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['ゼウス', 'どうし']},
           'orders': {'new': 'pending'}, 'picked': [], 'active': 'new',
           'sorties': {'new': {'general': 'ゼウス', 'source': 'アルマムーン',
                              'status': 'launched_unconfirmed', 'tick': 50}}}
    _verify_sortie_source(_screen(['ゼウス', 'どうし']), mem, {'step': 'new'})
    order = {'step': 'new', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'cards': [], 'after': None}
    assert _plan_region(mem)['orders']['new'] != 'launched'
    assert not p._ready(order, mem)


def test_source_defender_cancel_is_not_reissued_while_defender_state_stays():
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['ゼウス']},
           'orders': {'I:1': 'pending'}, 'picked': [], 'active': 'I:1',
           'general_location_unknown': []}
    order = {'step': 'I:1', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'cards': [], 'after': None,
             'purpose': 'attack'}
    from docich.hanjuku.flows.deploy import _keep_sortie_defender
    screen = _screen(['ゼウス'])
    assert _keep_sortie_defender(screen, mem, order) is not None
    assert not p._ready(order, mem)


def test_equivalent_source_defender_cancel_survives_a_new_execution_step():
    """#3265: a replan must not reset #2841 by changing A:<request>:<step>."""
    old = {'step': 'A:old00001:Mabc123', 'general': 'ゼウス',
           'source': 'アルマムーン', 'target': 'ゴーメン', 'cards': [],
           'after': None, 'purpose': 'attack'}
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['ゼウス']},
           'orders': {old['step']: 'pending'}, 'picked': [], 'active': old['step'],
           'general_location_unknown': [], 'lost': [], 'captured': [], '_records': []}
    assert p._keep_sortie_defender(_screen(['ゼウス']), mem, old) is not None

    new = dict(old, step='A:new00002:Mabc123')
    _plan_region(mem)['orders'][new['step']] = 'pending'
    assert not p._ready(new, mem)
    assert any(r['decision'] == 'sortie_equivalent_suppressed' and
               r.get('chart_step') == new['step'] for r in mem['_records'])

    # A real roster change releases the logical-route guard immediately.
    _world(mem)['garrison']['アルマムーン'] = ['ゼウス', 'ヴィーナス']
    assert p._ready(new, mem)
    assert any(r['decision'] == 'sortie_equivalent_released' for r in mem['_records'])


def _launch_order(step):
    return {'step': step, 'general': 'ゼウス', 'source': 'アルマムーン',
            'target': 'ゴーメン', 'cards': [], 'after': None, 'purpose': 'attack'}


def _arm_unverified_launch(mem, step):
    order = _launch_order(step)
    _plan_region(mem).setdefault('orders', {})[step] = 'launched'
    mem.setdefault('launched_orders', {})[step] = dict(order)
    mem.setdefault('sorties', {})[step] = {
        'general': 'ゼウス', 'source': 'アルマムーン', 'target': 'ゴーメン',
        'status': 'en_route', 'tick': mem['tick'], 'purpose': 'attack'}
    mem.setdefault('launched', {})['ゴーメン'] = {'step': step, 'general': 'ゼウス'}
    _world(mem)['garrison']['アルマムーン'] = ['どうし']
    return {'general': 'ゼウス', 'source': 'アルマムーン', 'target': 'ゴーメン',
            'reads': p.LAUNCH_PROBE_READS, 'baseline': [], 'tick': mem['tick']}


def _launch_misses(mem):
    """#3274 migrated launch_miss into flow; old hotloads may still be flat."""
    flow = mem.get('flow')
    if isinstance(flow, dict) and 'launch_miss' in flow:
        return flow['launch_miss']
    return mem.get('launch_miss') or {}


def test_launch_probe_retry_budget_survives_equivalent_replanned_step():
    """#3261's two-miss bound is logical-route scoped, not execution-id scoped."""
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['どうし']},
           'general_location_unknown': [], 'lost': [], 'captured': [], '_records': []}

    first = 'A:old00001:Mabc123'
    p._rollback_unverified_launch(mem, first, _arm_unverified_launch(mem, first), [])
    assert _plan_region(mem)['orders'][first] == 'pending'
    assert _launch_misses(mem)[first] == 1

    # A rebuilt mechanical/adjusted plan gives the same sortie another
    # execution id. Before #3265 this reset launch_miss to 1 and could repeat.
    second = 'A:new00002:Mabc123'
    p._rollback_unverified_launch(mem, second, _arm_unverified_launch(mem, second), [])
    assert _launch_misses(mem)[second] == p.LAUNCH_MISS_LIMIT
    assert _plan_region(mem)['orders'][second] == 'failed'
    failed = [r for r in mem['_records'] if r['decision'] == 'sortie_launch_failed']
    assert failed and failed[-1]['chart_step'] == second
    assert failed[-1]['observed_metric']['misses'] == p.LAUNCH_MISS_LIMIT
