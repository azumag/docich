"""Chapter 9 before its boss phase only marches every general to the next castle.

Owner 2026-11-11: ９話においては単純に機械的に次の城にすべての将軍を送っていき、
それ以外の操作はdisableにしてほしい.
"""
import copy

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.state.regions import plan as _plan_region
from docich.hanjuku.strategy import corridor
from docich.hanjuku.strategy import orders as O

from test_hanjuku_off_chart import _move_list

ROAD = chart.corridor(9)
HOME, FRONT, NEXT = ROAD[0], ROAD[1], ROAD[2]


def _mem(**extra):
    mem = {'chapter': 9, 'tick': 5000, 'month': '19-4', 'captured': [FRONT], 'lost': [NEXT],
           'orders': {}, 'garrison': {HOME: ['バトウラ'], FRONT: []}, '_records': [],
           # a retake of NEXT still marching: it held every wave before (v666)
           'sorties': {'I:old': {'general': 'シナモン', 'source': FRONT, 'target': NEXT,
                                 'purpose': 'retake', 'status': 'en_route', 'tick': 4900}}}
    mem.update(extra)
    return mem


def _orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def test_the_front_retakes_and_the_rear_moves_one_hop_without_names():
    attack, move = _orders(_mem())
    assert (attack['source'], attack['target'], attack['purpose']) == (FRONT, NEXT, 'retake')
    assert (move['source'], move['target'], move['purpose']) == (HOME, FRONT, 'move')
    assert all(o['source_probe'] and o['general'] == policy.chart_adjust.PROBE_GENERAL for o in (attack, move))


def test_next_order_ignores_reservations_and_stale_garrisons():
    mem = _mem()
    with chart.map_context(mem):
        order = O.next_order(mem)
    assert order and order['source'] == FRONT    # FRONT recorded empty, NEXT reserved: still sent


def test_a_launched_step_gives_the_castle_a_fresh_step():
    mem = _mem()
    first = _orders(mem)[0]['step']
    _plan_region(mem).setdefault('orders', {})[first] = 'launched'
    assert _orders(mem)[0]['step'] != first and _orders(mem)[0]['source'] == FRONT


def test_a_drained_castle_is_skipped_until_someone_arrives_or_the_month_changes():
    # v677: a march that was only *sent* still has the general on the road, so
    # the plan would open this castle's list, find nobody and lose a whole
    # sortie cycle. Only a confirmed arrival refills it.
    mem = _mem()
    corridor.mark_drained(mem, FRONT, 'empty')
    assert [o['source'] for o in _orders(mem)] == [HOME]
    from docich.hanjuku.state.regions import sorties as _sorties
    _sorties(mem)['I:in'] = {'general': 'ミント', 'source': HOME, 'target': FRONT,
                             'status': 'en_route', 'tick': 5001}
    assert [o['source'] for o in _orders(mem)] == [HOME]
    _sorties(mem)['I:in']['status'] = 'arrived'
    assert FRONT in [o['source'] for o in _orders(mem)]
    mem = _mem()
    corridor.mark_drained(mem, FRONT, 'empty')
    mem['month'] = '19-5'
    # v697: a month change alone does not re-open a castle nobody was sent into.
    assert [o['source'] for o in _orders(mem)] == [HOME]


def test_the_boss_phase_leaves_mechanical_mode():
    mem = _mem(captured=list(ROAD[1:]), lost=[])
    with chart.map_context(mem):
        assert corridor.mechanical(mem) is False
        assert corridor.orders(mem) == []


def _deploy(mem, order, names):
    mem.update(active=order['step'], picked=[], chart_adjust={'request_id': 'm', 'interim_order': order})
    with chart.map_context(mem):
        return policy.deploy_step(_move_list(names), mem)


def test_an_attack_with_only_the_hero_closes_the_list_and_drains_the_castle():
    mem = _mem()
    attack = _orders(mem)[0]
    assert _deploy(mem, attack, [chart.HERO]) == [policy.pad('b'), policy.pad('b')]
    assert mem[corridor.DRAINED][FRONT]['kind'] == 'hero'
    assert not [r for r in mem['_records'] if r.get('decision') in ('order_substitute', 'general_list_scanned')]


def test_a_move_takes_the_hero_when_he_is_the_last():
    mem = _mem()
    move = _orders(mem)[1]
    assert _deploy(mem, move, [chart.HERO]) == [policy.pad('a')]
    assert _plan_region(mem)['general_override'][move['step']] == chart.HERO


def test_no_monthly_status_pass_is_armed():
    mem = _mem()
    with chart.map_context(mem):
        post.arm(mem)
    assert 'post_month_status' not in mem


def test_the_live_g1163_memory_starts_a_march_order():
    """Replay invariant (14:16 production memory): an order is always startable."""
    import json
    from pathlib import Path
    snapshot = Path(__file__).parent / 'fixtures' / 'hanjuku_g1163_ch9_idle_memory.json'
    mem = json.loads(snapshot.read_text())
    with chart.map_context(mem):
        assert corridor.mechanical(mem)
        order = O.next_order(copy.deepcopy(mem))
    assert order and order['corridor'] and order['source'] in ('ハヤク', 'アルマムーン')


def test_a_listed_general_goes_even_if_a_march_record_says_he_is_away():
    """g1163 14:59-15:04: タオ listed アルファルファ, still recorded as marching."""
    from docich.hanjuku.state.regions import sorties as _sorties
    mem = _mem()
    _sorties(mem)['I:stale'] = {'general': 'アルファルファ', 'source': FRONT, 'target': NEXT,
                                'status': 'en_route', 'tick': 4990}
    attack = _orders(mem)[0]
    assert _deploy(mem, attack, ['アルファルファ']) == [policy.pad('a')]
    assert _plan_region(mem)['general_override'][attack['step']] == 'アルファルファ'
    assert corridor.DRAINED not in mem
