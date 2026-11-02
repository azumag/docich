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
    # v305: the hero keeps two others, so one of three spares moves.
    mem['garrison']['アルマムーン'] = ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート']
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


def test_a_relay_castle_lost_after_planning_is_not_attacked_as_an_empty_move():
    mem = state()
    order = {'step': 'I:relay:1', 'source': 'アルマムーン', 'target': 'コラダイン',
             'general': 'ミュスカデ', 'cards': [], 'after': None, 'purpose': 'move'}
    assert orders._ready(order, mem)
    mem['captured'].remove('コラダイン')
    mem['lost'].append('コラダイン')
    assert not orders._ready(order, mem)
    mem['active'] = order['step']
    assert deploy._hold_unrouted_sortie(mem, order) is not None
    assert mem['active'] is None


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


def test_world_map_route_check_preserves_unregistered_map_no_input_hold(monkeypatch):
    mem = state()
    monkeypatch.setitem(chart.CASTLES, 3, {})
    order = {'step': 'I:old:1', 'general': 'ミュスカデ', 'source': 'アルマムーン',
             'target': 'ドンダム', 'cards': [], 'after': None, 'purpose': 'retake'}
    mem.update(active=order['step'], chart_adjust={'interim_order': order},
               _run_identity=None,
               y_jump={'step': order['step'], 'goal': 'ドンダム', 'mode': 'target'})
    with chart.map_context(mem):
        assert map_flow.world_map_step(Screen([], None, '', kind='world_map'), mem, None) == []
    assert mem['active'] is None and 'y_jump' not in mem
    assert mem['_map_registration_hold'] is True
    assert mem['_records'][-1]['decision'] == 'map_coordinates_unavailable'


def test_rear_spare_can_return_along_chart_legs_to_reinforce_the_front():
    mem = state()
    mem['garrison']['グリン'] = []
    mem['garrison']['リグア'] = ['ヘラ', 'キャンディー']
    # The live layout has no spare at home or the front, only at the end of
    # a chart branch. A directed-only graph would strand this reinforcement.
    route = routes.path(mem, 'リグア', 'ドンダム', set(mem['garrison']))
    assert route == ('リグア', 'グリン', 'アルマムーン', 'コラダイン', 'イーピヨル', 'ドンダム')
    first = next(iter(replan.interim_candidates(mem).values()))
    assert (first['source'], first['target'], first['purpose']) == ('リグア', 'グリン', 'move')
    assert routes.allowed(mem, 'リグア', 'グリン')
    mem['captured'].remove('グリン')
    mem['lost'].append('グリン')
    assert not routes.allowed(mem, 'リグア', 'グリン')
    assert routes.path(mem, 'リグア', 'ドンダム', set(mem['garrison']) - {'グリン'}) is None


def test_unsafe_pending_interim_does_not_block_its_routed_replacement():
    mem = state()
    mem['garrison']['リグア'] = ['ヘラ', 'キャンディー']
    old = {'step': 'I:old:1', 'source': 'リグア', 'target': 'ドンダム',
           'general': 'ヘラ', 'purpose': 'retake', 'cards': [], 'after': None}
    mem['orders'][old['step']] = 'pending'
    pending = {'interim_order': old}
    replan._mechanical_interim(mem, pending, 'new-request', 'synthetic-digest')
    new = pending['interim_order']
    assert new['step'] != old['step']
    assert (new['source'], new['target'], new['purpose']) == ('リグア', 'グリン', 'move')


def rigua_stall():
    """g1075 2026-10-29 11:38 JST: every castle but リグア is ours, グリン (the
    only leg to リグア) is recorded empty, and the two moves sent there have
    left the busy window without their arrival being read."""
    mem = state()
    mem['tick'] = 66558
    mem['captured'] = sorted(set(chart.castles(3)) - {'リグア', chart.boss_castle(3)})
    mem['lost'] = ['リグア']
    mem['garrison'] = {'アバロン': ['ソーピニヨン'], 'アルマムーン': ['どうし'], 'イーピヨル': [],
                       'グリン': [], 'コラダイン': ['ミュスカデ'], 'ドンダム': ['ピオーネ'],
                       'パタゴーヌ': ['アポロン'], 'メガパタ': ['デュオニソス']}
    for step, general, tick in (('I:380571:2', 'アテナ', 65575), ('I:b59ab6:1', 'ポワソン', 66126)):
        mem['sorties'][step] = {'source': 'アルマムーン', 'target': 'グリン', 'general': general,
                                'status': 'en_route', 'tick': tick}
        mem['launched_orders'][step] = {'purpose': 'move', 'source': 'アルマムーン',
                                        'target': 'グリン', 'general': general, 'step': step}
        mem['orders'][step] = 'launched'
    retake = {'step': 'I:b59ab6:2', 'general': 'しょうぐん', 'source': 'グリン', 'target': 'リグア',
              'cards': ['イッテツーン', 'グリンボー', 'グリンボー'], 'after': None,
              'purpose': 'retake', 'source_probe': True,
              'note': '暫定: しょうぐんで奪われたリグアを奪還'}
    mem['chart_adjust'] = {'interim_order': retake}
    return mem, retake


def test_a_castle_recorded_empty_is_probed_after_its_transfers_left_the_busy_window():
    mem, retake = rigua_stall()
    assert routes.incoming(mem, 'グリン') == []            # both moves are past the busy window
    assert {s for s, _ in routes.unread_dispatches(mem, 'グリン')} == {'I:380571:2', 'I:b59ab6:1'}
    assert routes.probe_due(mem, 'グリン')
    assert orders._source_list_open(retake, mem)
    assert orders.next_order(mem)['step'] == 'I:b59ab6:2'
    # The probe opens the real list; the reading decides, never a guess.
    candidates = list(replan.interim_candidates(mem).values())
    assert (candidates[0]['source'], candidates[0]['target'], candidates[0]['purpose']) == (
        'グリン', 'リグア', 'retake')
    assert candidates[0]['source_probe'] is True


def test_a_read_list_closes_the_probe_and_the_stuck_retake_is_replaced():
    mem, retake = rigua_stall()
    # The list read after both moves shows nobody: no unread dispatch remains,
    # old transfers are not confirmed, and the source stays closed.
    routes.observed_roster(mem, 'グリン', [])
    mem['tick'] += routes.PROBE_INTERVAL
    assert routes.unread_dispatches(mem, 'グリン') == []
    assert not routes.probe_due(mem, 'グリン')
    assert {mem['sorties'][s]['status'] for s in ('I:380571:2', 'I:b59ab6:1')} == {'en_route'}
    assert not orders._source_list_open(retake, mem)
    assert orders.next_order(mem) is None or orders.next_order(mem)['step'] != 'I:b59ab6:2'
    # With no other candidate it is left unstarted (a hold is recorded) ...
    state_ = {'interim_order': retake}
    replan._mechanical_interim(mem, state_, 'c0ffee000000', 'synthetic-digest')
    assert mem['_records'][-1]['decision'] == 'chart_interim_hold'
    # ... and it no longer holds a startable replacement: a spare at home
    # stages the next general toward グリン instead of waiting on the old probe.
    mem['garrison']['アルマムーン'] = ['どうし', 'ゼウス', 'ミルフィーユ', 'ジェラート']   # v305: two stay
    replan._mechanical_interim(mem, state_, 'c0ffee000000', 'synthetic-digest-2')
    new = state_['interim_order']
    assert new['step'] != 'I:b59ab6:2'
    assert (new['general'], new['source'], new['target'], new['purpose']) == (
        'ゼウス', 'アルマムーン', 'グリン', 'move')
    # A new transfer sent after that reading reopens the list once due.
    mem['tick'] += 5
    step = add_transfer(mem, 'アルマムーン', 'グリン', general='ゼウス')
    assert routes.probe_due(mem, 'グリン')
    assert {s for s, _ in routes.unread_dispatches(mem, 'グリン')} == {step}


def test_probe_never_opens_a_castle_whose_list_was_read_after_the_last_dispatch():
    mem, retake = rigua_stall()
    mem['route_probe_read_tick'] = {'グリン': 66300}     # read after both moves were sent
    assert routes.unread_dispatches(mem, 'グリン') == []
    assert not routes.probe_due(mem, 'グリン')
    assert orders.next_order(mem) is None or orders.next_order(mem)['step'] != 'I:b59ab6:2'
    # Other chapters never probe, and a non-probe order never opens an empty list.
    assert not routes.probe_due({**mem, 'chapter': 2}, 'グリン')
    mem.pop('route_probe_read_tick')
    assert not orders._source_list_open({**retake, 'source_probe': False}, mem)


def victoria_drain():
    """g1075 2026-10-29 23:03 JST, chapter 4 (#3037): ビクトリア is recorded
    empty and its list was never read; アポロン and アルファルファ were sent
    there and have left the busy window, so the same move went out again."""
    mem = {'chapter': 4, 'tick': 101173,
           'captured': ['ビクトリア', 'ミヒラギアン', 'マラコビア', 'アルカ'],
           'lost': ['フヌイヌム', 'ネクスドリア'], 'orders': {},
           'garrison': {'アルカ': ['ブカティーニ'], 'ビクトリア': [], 'マラコビア': ['ラズベリー'],
                        'アルマムーン': ['どうし', 'バーミセリ', 'トレビス', 'ココット'],
                        'ミヒラギアン': ['アテナ']},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    for step, general, tick in (('I:0fa434:2', 'アポロン', 95400), ('I:49a839:1', 'アルファルファ', 96181)):
        mem['sorties'][step] = {'source': 'アルマムーン', 'target': 'ビクトリア', 'general': general,
                                'status': 'en_route', 'tick': tick}
        mem['launched_orders'][step] = {'purpose': 'move', 'source': 'アルマムーン',
                                        'target': 'ビクトリア', 'general': general, 'step': step}
        mem['orders'][step] = 'launched'
    return mem


def moves_into(mem, castle):
    return [o for o in replan.interim_candidates(mem).values()
            if o['purpose'] == 'move' and o['target'] == castle]


def test_a_castle_sent_moves_since_its_last_reading_is_not_refilled_in_chapter_4():
    mem = victoria_drain()
    assert routes.incoming(mem, 'ビクトリア') == []        # both moves are past the busy window
    assert len(routes.unread_dispatches(mem, 'ビクトリア')) == 2
    assert not moves_into(mem, 'ビクトリア')
    assert not routes.probe_due(mem, 'ビクトリア')          # no list probe outside chapter 3


def test_a_read_list_in_chapter_4_makes_an_empty_castle_a_move_target_again():
    mem = victoria_drain()
    mem['tick'] += 1
    routes.observed_roster(mem, 'ビクトリア', [])
    assert routes.unread_dispatches(mem, 'ビクトリア') == []
    assert [o['source'] for o in moves_into(mem, 'ビクトリア')] == ['アルマムーン']


def test_lost_castle_gating_the_boss_path_is_retaken_first():
    """Owner 2026-11-02 (g1097): 5-C4 waits on captured バスカビル while minor
    retakes cycle — a lost castle gating the boss chain outranks the rest."""
    mem = {'chapter': 5, 'month': '38-10', 'tick': 1000,
           'captured': ['テラビル', 'ヒュペルボレア', 'カルーン', 'アルマムーン'],
           'lost': ['ボッサール', 'バスカビル', 'アイアイエー', 'オトラント'],
           'orders': {o['step']: 'launched' for o in chart.orders(5)},
           'garrison': {'アルマムーン': ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート', 'エピィ'],
                        'テラビル': ['ラビオリ'], 'ヒュペルボレア': ['ピスタチオ'],
                        'カルーン': ['セルリアク']},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    cands = list(replan.interim_candidates(mem).values())
    retakes = [c['target'] for c in cands if c.get('purpose') == 'retake']
    assert retakes[:2] == ['バスカビル', 'アイアイエー']
    assert set(retakes) == {'ボッサール', 'バスカビル', 'アイアイエー', 'オトラント'}


def test_boss_gate_ordering_is_scoped_to_lost_castles():
    """The gate only reorders castles actually lost — a captured gate castle
    does not fabricate a retake."""
    mem = {'chapter': 5, 'month': '38-10', 'tick': 1000,
           'captured': ['テラビル', 'ヒュペルボレア', 'カルーン', 'アルマムーン',
                        'バスカビル', 'アイアイエー'],
           'lost': ['ボッサール', 'オトラント'],
           'orders': {o['step']: 'launched' for o in chart.orders(5)},
           'garrison': {'アルマムーン': ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート', 'エピィ'],
                        'テラビル': ['ラビオリ'], 'ヒュペルボレア': ['ピスタチオ'],
                        'カルーン': ['セルリアク'], 'バスカビル': ['ココット'],
                        'アイアイエー': ['エシャロット']},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    cands = list(replan.interim_candidates(mem).values())
    retakes = [c['target'] for c in cands if c.get('purpose') == 'retake']
    assert set(retakes) == {'ボッサール', 'オトラント'}


def test_source_past_an_own_castle_on_the_march_is_avoided():
    """Owner 2026-11-02 (g1097): アルマムーン→クロチェット always stopped at
    オトラント on the way — a source behind another own castle never reaches
    the target, so the nearer front-line castle is picked instead."""
    mem = {'chapter': 5, 'month': '39-9', 'tick': 1000,
           'captured': ['アルマムーン', 'オトラント', 'テラビル'],
           'lost': [], 'orders': {o['step']: 'launched' for o in chart.orders(5)},
           'garrison': {'アルマムーン': ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート'],
                        'オトラント': ['グレナデン', 'バジル'],
                        'テラビル': ['ラビオリ']},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    cands = list(replan.interim_candidates(mem).values())
    kuro = next(c for c in cands if c.get('target') == 'クロチェット')
    assert kuro['source'] == 'オトラント'


def test_march_blocked_geometry():
    assert replan._march_blocked(5, 'アルマムーン', 'クロチェット',
                                 {'アルマムーン', 'オトラント'})
    assert replan._march_blocked(5, 'アルマムーン', 'プトレマイス',
                                 {'アルマムーン', 'バスカビル'})
    assert not replan._march_blocked(5, 'オトラント', 'クロチェット',
                                     {'アルマムーン', 'オトラント'})
    assert not replan._march_blocked(5, 'アルマムーン', 'オトラント',
                                     {'アルマムーン', 'オトラント', 'カルーン'})
