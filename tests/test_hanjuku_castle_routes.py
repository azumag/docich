"""Do not mistake a straight-line sortie for a route across chapter-3 cliffs."""
import copy
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import routes, replan, orders
from docich.hanjuku.flows import deploy, map as map_flow
from docich.hanjuku_screen import Screen


def state():
    names = set(chart.castles(3))
    return {'chapter': 3, 'month': '18-2', 'tick': 1000,
            'captured': sorted(names - {'アルマムーン', 'ドンダム'}),
            'lost': ['ドンダム'], 'orders': {o['step']: 'launched' for o in chart.orders(3)},
            'garrison': {'アルマムーン': ['どうし'], 'グリン': ['ヘラ'],
                         'コラダイン': ['エシャロット'], 'イーピヨル': ['ピオーネ'],
                         'メガパタ': ['ペコリーノ'], 'アバロン': ['アテナ'],
                         'パタゴーヌ': ['ガスパチョ'], 'リグア': ['ユイートル']},
            'sorties': {}, 'launched_orders': {}, '_records': [],
            'house_eggs': {}, 'general_location_unknown': []}


def test_route_uses_owned_castles_and_never_the_home_to_dondam_diagonal():
    mem = state()
    mem['garrison']['アルマムーン'] = ['どうし', 'ミュスカデ', 'チコリ']
    owned = set(mem['garrison'])
    assert not routes.allowed(mem, 'アルマムーン', 'ドンダム')
    assert routes.path(mem, 'アルマムーン', 'ドンダム', owned) == (
        'アルマムーン', 'コラダイン', 'イーピヨル', 'ドンダム')
    candidates = list(replan.interim_candidates(mem).values())
    first = candidates[0]
    assert (first['source'], first['target'], first['purpose']) == (
        'アルマムーン', 'コラダイン', 'move')
    assert all(routes.allowed(mem, o['source'], o['target']) for o in candidates)
    assert routes.path(mem, 'アルマムーン', 'ドンダム', owned - {'コラダイン'}) is None


def test_adjusted_order_and_source_override_cannot_bypass_the_route():
    mem = state()
    order = {'step': 'A:plan:J1', 'source': 'アルマムーン', 'target': 'ドンダム',
             'general': 'ピオーネ', 'cards': [], 'after': None}
    assert not orders._ready(order, mem)
    order['source'] = 'イーピヨル'
    assert orders._ready(order, mem)
    mem['source_override'] = {order['step']: 'アルマムーン'}
    assert not orders._ready(order, mem)


def add_transfer(mem, source, target, general='ミュスカデ'):
    step = source + '-hop'
    mem['sorties'][step] = {'source': source, 'target': target, 'general': general,
                            'status': 'en_route', 'tick': mem['tick'] - 10}
    mem['launched_orders'][step] = {'purpose': 'move', 'source': source,
                                    'target': target, 'general': general}
    return step


def test_transfer_is_forwarded_only_after_the_actual_roster_receipt():
    mem = state()
    step = add_transfer(mem, 'アルマムーン', 'コラダイン')
    candidates = list(replan.interim_candidates(mem).values())
    assert candidates[0]['source'] == 'コラダイン'
    assert candidates[0]['target'] == 'イーピヨル'
    assert candidates[0]['source_probe'] is True
    before = copy.deepcopy(mem['garrison'])
    routes.observed_roster(mem, 'コラダイン', ['エシャロット'])
    assert mem['sorties'][step]['status'] == 'en_route'
    assert not routes.probe_due(mem, 'コラダイン')
    assert mem['garrison'] == before
    mem['tick'] += routes.PROBE_INTERVAL
    assert routes.probe_due(mem, 'コラダイン')
    # The existing general-list reader publishes the list, then this hook
    # releases only a transfer whose named actor is physically listed there.
    mem['garrison']['コラダイン'] = ['エシャロット', 'ミュスカデ']
    routes.observed_roster(mem, 'コラダイン', mem['garrison']['コラダイン'])
    assert mem['sorties'][step]['status'] == 'arrived'
    assert mem['_records'][-1]['decision'] == 'sortie_move_arrival_observed'
    first = next(iter(replan.interim_candidates(mem).values()))
    assert (first['source'], first['target'], first['purpose']) == (
        'コラダイン', 'イーピヨル', 'move')
    assert not first.get('source_probe')


def test_frontline_arrival_unlocks_the_final_attack_and_other_chapters_stay_unchanged():
    mem = state()
    step = add_transfer(mem, 'コラダイン', 'イーピヨル')
    mem['garrison']['イーピヨル'] = ['ピオーネ', 'ミュスカデ']
    routes.observed_roster(mem, 'イーピヨル', mem['garrison']['イーピヨル'])
    assert mem['sorties'][step]['status'] == 'arrived'
    first = next(iter(replan.interim_candidates(mem).values()))
    assert (first['source'], first['target'], first['purpose']) == (
        'イーピヨル', 'ドンダム', 'retake')
    for chapter in (1, 2, 4, 12):
        assert routes.allowed({'chapter': chapter}, 'source', 'target')


def test_wrong_castle_old_transfer_or_nontransfer_never_confirms_arrival():
    mem = state()
    step = add_transfer(mem, 'アルマムーン', 'コラダイン')
    routes.observed_roster(mem, 'イーピヨル', ['ミュスカデ'])
    assert mem['sorties'][step]['status'] == 'en_route'

    mem['sorties'][step]['tick'] = mem['tick'] - routes.SORTIE_BUSY_TICKS
    routes.observed_roster(mem, 'コラダイン', ['ミュスカデ'])
    assert mem['sorties'][step]['status'] == 'en_route'
    mem['sorties'][step]['tick'] = mem['tick']
    mem['launched_orders'][step]['purpose'] = 'attack'
    routes.observed_roster(mem, 'コラダイン', ['ミュスカデ'])
    assert mem['sorties'][step]['status'] == 'en_route'
    mem['launched_orders'][step]['purpose'] = 'move'
    mem['launched_orders'][step]['target'] = 'グリン'
    routes.observed_roster(mem, 'コラダイン', ['ミュスカデ'])
    assert mem['sorties'][step]['status'] == 'en_route'


@pytest.mark.parametrize('kind', ['general_list', 'sortie_confirm', 'map_target', 'world_map'])
def test_hotloaded_unsafe_pending_order_cannot_finish_the_old_confirmation(kind):
    mem = state()
    order = {'step': 'I:old:1', 'general': 'ミュスカデ', 'source': 'アルマムーン',
             'target': 'ドンダム', 'cards': [], 'after': None, 'purpose': 'retake'}
    mem.update(active=order['step'], chart_adjust={'interim_order': order},
               y_jump={'step': order['step'], 'goal': 'ドンダム', 'mode': 'target'})
    screen = Screen([], None, '', kind=kind)
    if kind == 'map_target':
        actions = map_flow.target_step(screen, mem, None)
    elif kind == 'world_map':
        actions = map_flow.world_map_step(screen, mem, None)
    else:
        actions = deploy.deploy_step(screen, mem)
    assert all(a.get('buttons') == ['b'] for a in actions)
    assert mem['active'] is None and 'y_jump' not in mem
    assert mem['orders'][order['step']] == 'failed'
    assert mem['_records'][-1]['decision'] == 'sortie_route_unverified'
