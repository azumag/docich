"""Regressions for chapter 9 front reinforcement and rear probe prioritization."""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.flows import deploy
from docich.hanjuku.state.regions import plan as _plan_region, sorties as _sorties
from docich.hanjuku.strategy import corridor, routes

from test_hanjuku_off_chart import _move_list

ROAD = chart.corridor(9)
A, B, C, D, E = ROAD[0], ROAD[1], ROAD[2], ROAD[3], ROAD[4]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def _send_movers(mem, source, target, n, *, status='arrived', tick=None):
    sorties = corridor._world(mem).setdefault('sorties', {})
    for _ in range(n):
        step = f'{corridor.PREFIX}{source}>{target}:{100 + len(sorties)}'
        sorties[step] = {
            'general': f'将軍{len(sorties)}',
            'source': source,
            'target': target,
            'purpose': 'move',
            'status': status,
            'tick': int(mem['tick']) if tick is None else tick,
        }
        _plan_region(mem).setdefault('orders', {})[step] = 'launched'


def _mem(owned, garrison, *, tick=5000, lost=(), **extra):
    mem = {
        'chapter': 9,
        'tick': tick,
        'month': '23-5',
        'captured': [c for c in owned if c != chart.home_castle(9)],
        'lost': list(lost),
        'orders': {},
        'garrison': garrison,
        'world': {'garrison': garrison, 'sorties': {}, 'launched_orders': {}},
        '_records': [],
    }
    mem.update(extra)
    return mem


def _orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def test_wait_mass_and_defended_front_open_refill_wave_even_with_marching_attacker():
    # Front D held at 2 residents by front_wait_mass after losing to a >=30 HP defender;
    # target E still has an attacker en_route, and C already sent 6 arrived movers.
    mem = _mem(
        [A, B, C, D],
        {A: [], B: [], C: ['バトウラ', 'ココット'], D: ['リーキ', 'ベーグル']},
        sortie_history=[{'target': E, 'outcome': 'lost', 'enemy_hp': 40, 'tick': 4900}],
    )
    _orders(mem)
    _send_movers(mem, C, D, corridor.SQUAD, status='arrived')
    corridor._world(mem).setdefault('sorties', {})[f'{corridor.PREFIX}{D}>{E}:99'] = {
        'general': 'シナモン', 'source': D, 'target': E, 'purpose': 'attack',
        'status': 'en_route', 'tick': 4990,
    }
    mem['tick'] += 20
    out = _orders(mem)
    assert mem[corridor.WAVE]['refill'] == 1
    assert any(o['source'] == C and o['target'] == D and o['purpose'] == 'move' for o in out)

    # Recently defended front (_keep=2) with 2 residents also reopens refill once movers arrived,
    # whereas in-flight movers still hold the wave until they finish marching.
    defended = _mem(
        [A, B, C, D],
        {A: [], B: [], C: ['バトウラ'], D: ['リーキ', 'ベーグル']},
        defense_seen={D: 4950},
    )
    _orders(defended)
    _send_movers(defended, C, D, corridor.SQUAD, status='en_route')
    defended['tick'] += 20
    assert not any(o['source'] == C and o['purpose'] == 'move' for o in _orders(defended))
    assert 'refill' not in defended[corridor.WAVE]

    for s in _sorties(defended).values():
        s['status'] = 'arrived'
    out2 = _orders(defended)
    assert defended[corridor.WAVE]['refill'] == 1
    assert any(o['source'] == C and o['target'] == D and o['purpose'] == 'move' for o in out2)


def test_front_thin_prefers_newer_partial_reading():
    mem = _mem(
        [A, B, C, D],
        {A: [], B: [], C: [], D: ['リーキ', 'ベーグル', 'シナモン']},
        sortie_history=[{'target': E, 'outcome': 'lost', 'enemy_hp': 40, 'tick': 4900}],
    )
    world = corridor._world(mem)
    world['garrison_tick'] = {D: 4800}
    world['garrison_partial'] = {D: ['リーキ']}
    world['garrison_partial_tick'] = {D: 4950}
    assert corridor._front_thin(mem, D, target=E) is True

    # Older partial does not override a newer complete reading.
    world['garrison_tick'] = {D: 4980}
    assert corridor._front_thin(mem, D, target=E) is False


def test_due_probe_prefers_front_adjacent_and_does_not_preempt_front_refill_from_rear():
    mem = _mem(
        [A, B, C, D],
        {A: ['グリッシーニ'], B: ['ヘラ'], C: ['バトウラ'], D: ['リーキ']},
        tick=5000,
    )
    mem['route_probe_read_tick'] = {A: 1000, B: 1100, C: 2000}
    mem['world']['sorties'] = {
        f'{corridor.PREFIX}{A}>{B}:1': {
            'general': 'マフィン', 'source': A, 'target': B, 'purpose': 'move',
            'status': 'en_route', 'tick': 4850,
        },
        f'{corridor.PREFIX}{B}>{C}:2': {
            'general': 'ミント', 'source': B, 'target': C, 'purpose': 'move',
            'status': 'en_route', 'tick': 4900,
        },
    }
    routes_by = {s: routes.path(mem, s, E, {A, B, C, D}) for s in (A, B, C, D)}
    due = corridor._due_probe(mem, routes_by, E, ())
    # C (len 3) is closer to front D than B (len 4) or A (len 5)
    assert due is not None and due['source'] == C

    # Even with an active attack en_route? No, test idle orders():
    # When D is thin and C -> D move is already queued, rear B's probe_due must NOT jump ahead of C -> D.
    mem2 = _mem(
        [A, B, C, D],
        {A: ['グリッシーニ'], B: ['ヘラ'], C: ['バトウラ'], D: ['リーキ']},
        tick=5000,
    )
    mem2['route_probe_read_tick'] = {A: 1000, B: 1100, C: 5000}
    mem2['world']['sorties'] = {
        f'{corridor.PREFIX}{A}>{B}:1': {
            'general': 'マフィン', 'source': A, 'target': B, 'purpose': 'move',
            'status': 'en_route', 'tick': 4850,
        },
    }
    out = _orders(mem2)
    assert out[0]['source'] == C and out[0]['target'] == D and out[0]['purpose'] == 'move'


def test_roster_probe_prefers_closer_stale_drained_castle():
    mem = _mem(
        [A, B, C, D],
        {A: [], B: [], C: [], D: ['リーキ']},
        tick=5000,
        country_general_count={'count': 32, 'scope': [9, '23-5']},
        corridor_drained={
            A: {'kind': 'empty', 'tick': 1000, 'month': '23-5'},
            C: {'kind': 'empty', 'tick': 3000, 'month': '23-5'},
        },
    )
    out = _orders(mem)
    assert out and out[0].get('source_probe') is True
    assert out[0]['source'] == C


def test_partial_corridor_observation_updates_probe_read_tick_and_arrivals():
    mem = _mem(
        [A, B, C, D],
        {A: [], B: [], C: ['リーキ', 'ベーグル', 'シナモン', 'マフィン'], D: ['キャラウェイ']},
        tick=5000,
    )
    order = next(o for o in _orders(mem) if o['source'] == C and o['purpose'] == 'move')
    corridor._world(mem).setdefault('sorties', {})[f'{corridor.PREFIX}{B}>{C}:4'] = {
        'general': 'ベーグル', 'source': B, 'target': C, 'purpose': 'move',
        'status': 'overdue', 'tick': 4000,
    }
    mem['route_probe_read_tick'] = {C: 3500}
    assert routes.probe_due(mem, C) is True

    _plan_region(mem)['active'] = order['step']
    mem['chart_adjust'] = {'request_id': 'm', 'interim_order': order}
    screen = _move_list(['リーキ', 'ベーグル', 'シナモン', 'マフィン'])
    with chart.map_context(mem):
        actions = policy.deploy_step(screen, mem)
    assert actions == [policy.pad('a')]
    assert mem['route_probe_read_tick'][C] == 5000
    assert mem['world']['sorties'][f'{corridor.PREFIX}{B}>{C}:4']['status'] == 'arrived'
    assert routes.probe_due(mem, C) is False
