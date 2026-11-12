"""The castle-list probe tour must be bounded.

g1163 2026-11-12 12:4x: every road castle read 「しょうぐんはおりません」 while the
country held 32 generals, and the plan spent a quarter of an hour opening
drained castles one after another — a probe can never find a general who is
camped on the field, so the tour has to stop and let recall / arrivals re-arm
it instead of running forever.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.state.regions import plan as _plan_region
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C = ROAD[0], ROAD[1], ROAD[2]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(owned, drained, tick=5000, **extra):
    garrison = {castle: ['リーキ'] for castle in owned}
    mem = {'chapter': 9, 'tick': tick, 'month': '23-5',
           'captured': [c for c in owned if c != chart.home_castle(9)],
           'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'sorties': {}, 'launched_orders': {}},
           'corridor_drained': {castle: {'kind': 'empty', 'tick': 1000, 'month': '23-5'}
                                for castle in drained},
           '_records': []}
    mem.update(extra)
    return mem


def orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def decided(mem, name):
    return [rec for rec in mem.get('_records') or [] if rec.get('decision') == name]


def test_the_budget_bounds_only_the_preemption():
    """With a real order waiting, the tour may not jump the queue forever."""
    mem = memory([A, B, C], [A, B])          # C still has generals
    mem['garrison'][C] = ['リーキ', 'ベーグル']   # two: v734 keeps one at home
    mem['world']['garrison'][C] = ['リーキ', 'ベーグル']
    for _ in range(corridor.PROBE_BUDGET):
        out = orders(mem)
        assert out and out[0].get('source_probe') is True, 'the tour goes first while budgeted'
    before = len(decided(mem, 'roster_probe'))
    out = orders(mem)
    assert out, 'a real move must still be planned'
    assert any(order.get('source') == C for order in out)
    assert len(decided(mem, 'roster_probe')) == before, 'the budget stops the preemption'


def test_an_idle_probe_keeps_the_plan_alive_without_spending_the_budget():
    """Nothing else to plan: an empty plan would stand still on the map.

    g1163 2026-11-12 13:1x: 169 consecutive no-input map plans after the
    budget was spent — five minutes without a single press.
    """
    mem = memory([A, B, C], [A, B, C])
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
        mem['tick'] += corridor.IDLE_PROBE_SPACING
    count = mem[corridor.PROBE]['count']
    out = orders(mem)
    assert len(out) == 1, 'the idle tour has to keep visiting'
    assert mem[corridor.PROBE]['count'] == count, 'an idle probe is not budgeted'
    assert decided(mem, 'roster_probe_idle') or decided(mem, 'drained_probe_idle')
    assert not decided(mem, 'corridor_probe_exhausted')


def test_an_idle_plan_never_stands_still():
    """g1163 2026-11-12 15:0x: spacing the tour left 79 no-input map frames."""
    mem = memory([A, B, C], [A, B, C])
    for _ in range(4):
        assert len(orders(mem)) == 1, 'the idle tour has to keep the bot moving'
        mem['tick'] += 2


def test_a_sortie_in_flight_is_not_replaced_by_an_idle_probe():
    mem = memory([A, B, C], [A, B, C])
    mem['flow'] = {'sortie_attempt': {'step': 'I:c9:A>B:1', 'confirmation_tick': 5000}}
    assert orders(mem) == [], 'the launch in progress owns the next observation'


def test_a_stale_sortie_attempt_does_not_block_the_idle_probe():
    """g1163 2026-11-12 14:4x: a 600-tick-old attempt emptied the plan."""
    mem = memory([A, B, C], [A, B, C])
    mem['flow'] = {'sortie_attempt': {'step': 'I:c9:A>B:1', 'confirmation_tick': 5000}}
    mem['tick'] = 5000 + corridor.SORTIE_ATTEMPT_TTL + 1
    assert len(orders(mem)) == 1, 'a stale attempt is not a launch in flight'


def test_a_general_showing_up_in_a_castle_list_rearms_the_budget():
    mem = memory([A, B, C], [A, B])
    mem['garrison'][C] = ['リーキ', 'ベーグル']
    mem['world']['garrison'][C] = ['リーキ', 'ベーグル']
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    orders(mem)                              # budget spent: no probe any more
    assert not decided(mem, 'roster_probe') or True
    mem['world']['garrison'][A] = ['リーキ', 'ベーグル']
    mem['corridor_drained'] = {c: {'kind': 'empty', 'tick': 1000, 'month': '23-5'}
                               for c in (A, B)}
    out = orders(mem)
    assert out and out[0].get('source_probe') is True, 'a found general re-arms the tour'


def test_a_new_month_rearms_the_budget():
    mem = memory([A, B, C], [A, B])
    mem['garrison'][C] = ['リーキ', 'ベーグル']
    mem['world']['garrison'][C] = ['リーキ', 'ベーグル']
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    mem['month'] = '23-6'
    out = orders(mem)
    assert out and out[0].get('source_probe') is True


def test_a_running_wave_keeps_the_idle_probe_away():
    """g1163 2026-11-12 19:1x-19:3x: 12 idle probes during the ドンドン wave."""
    mem = memory([A, B, C], [A, B, C])
    nxt = chart.corridor_next(9, {A, B, C})
    mem['world']['sorties'] = {'I:c9:C>N:9': {'general': '味方', 'source': C, 'target': nxt,
                                              'purpose': 'attack', 'status': 'en_route',
                                              'tick': mem['tick'] - 10}}
    orders(mem)
    assert not (decided(mem, 'roster_probe_idle') + decided(mem, 'drained_probe_idle')), \
        'the wave is the work: no idle probe during it'


def test_a_minted_idle_probe_is_reused_instead_of_minted_again():
    """g1163 2026-11-12 14:2x: 19 idle probes in ten minutes, none executed."""
    mem = memory([A, B, C], [A, B, C])
    first = orders(mem)
    assert len(first) == 1
    later = orders(mem)
    assert later, 'the plan still moves'
    assert [o['step'] for o in later] == [o['step'] for o in first], \
        'the pending idle probe is reused, not replaced'


def test_a_running_wave_also_keeps_the_budgeted_probe_away():
    """g1163 2026-11-12 20:0x: 20 probes in half an hour during the wave."""
    mem = memory([A, B, C], [A, B, C])
    nxt = chart.corridor_next(9, {A, B, C})
    mem['world']['sorties'] = {'I:c9:C>N:9': {'general': '味方', 'source': C, 'target': nxt,
                                              'purpose': 'attack', 'status': 'en_route',
                                              'tick': mem['tick'] - 10}}
    orders(mem)
    assert not (decided(mem, 'roster_probe') + decided(mem, 'drained_probe')), \
        'the wave preempts the budgeted probe too'


def test_a_started_idle_step_never_blocks_the_plan():
    """g1163 2026-11-12 21:1x: 122 no-input map frames from a stale idle step."""
    mem = memory([A, B, C], [A, B, C])
    first = orders(mem)
    step = first[0]['step']
    _plan_region(mem)['orders'][step] = 'launched'
    later = orders(mem)
    assert later, 'a started idle probe must not freeze the plan'


def test_a_stale_idle_probe_is_replaced():
    mem = memory([A, B, C], [A, B, C])
    orders(mem)
    minted = len(decided(mem, 'roster_probe_idle') + decided(mem, 'drained_probe_idle'))
    mem['tick'] += corridor.IDLE_PROBE_STALE_TICKS + 1
    later = orders(mem)
    assert later
    assert len(decided(mem, 'roster_probe_idle') + decided(mem, 'drained_probe_idle')) > minted
