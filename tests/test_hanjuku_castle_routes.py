"""Do not mistake a straight-line sortie for a route across chapter-3 cliffs."""
import copy
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import routes, replan, orders
from docich.hanjuku.flows import deploy, map as map_flow
from docich.hanjuku_screen import Screen
from docich.hanjuku.state.regions import world as _world, plan as _plan_region


def state():
    names = set(chart.castles(3))
    return {'chapter': 3, 'month': '18-2', 'tick': 1000,
            'captured': sorted(names - {'アルマムーン', 'ドンダム'}),
            'lost': ['ドンダム'], 'orders': {o['step']: 'launched' for o in chart.orders(3)},
            'garrison': {'アルマムーン': ['どうし'], 'グリン': ['ヘラ'],
                         'コラダイン': ['エシャロット'], 'イーピヨル': ['ピオーネ'],
                         'メガパタ': ['ペコリーノ'], 'アバロン': ['アテナ'],
                         'パタゴーヌ': ['ガスパチョ'], 'リグア': ['ユイートル']},
            'world': {'sorties': {}}, 'launched_orders': {}, '_records': [],
            'house_eggs': {}, 'general_location_unknown': []}


def test_route_uses_owned_castles_and_never_the_home_to_dondam_diagonal():
    mem = state()
    # v305: the hero keeps two others, so one of three spares moves.
    # #3266: the boss wave books one spare, so a fourth non-hero general is
    # needed for the staging move to still have somebody to send.
    _world(mem)['garrison']['アルマムーン'] = ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート', 'エピィ',
                                   'ポワソン', 'キャンディー']
    owned = set(_world(mem)['garrison'])
    assert not routes.allowed(mem, 'アルマムーン', 'ドンダム')
    assert routes.path(mem, 'アルマムーン', 'ドンダム', owned) == (
        'アルマムーン', 'コラダイン', 'イーピヨル', 'ドンダム')
    candidates = list(replan.interim_candidates(mem).values())
    # Owner 2026-11-05: boss waves take the spare generals first, so the
    # staging move may not exist in a boss-open state; when it does, it still
    # starts at the home castle.
    move = next((o for o in candidates if o['target'] == 'コラダイン'), None)
    assert move is None or (move['source'], move['purpose']) == ('アルマムーン', 'move')
    assert all(routes.allowed(mem, o['source'], o['target']) for o in candidates)
    assert routes.path(mem, 'アルマムーン', 'ドンダム', owned - {'コラダイン'}) is None


def test_adjusted_order_and_source_override_cannot_bypass_the_route():
    mem = state()
    order = {'step': 'A:plan:J1', 'source': 'アルマムーン', 'target': 'ドンダム',
             'general': 'ピオーネ', 'cards': [], 'after': None}
    assert not orders._ready(order, mem)
    order['source'] = 'イーピヨル'
    assert orders._ready(order, mem)
    _plan_region(mem)['source_override'] = {order['step']: 'アルマムーン'}
    assert not orders._ready(order, mem)


def test_a_relay_castle_lost_after_planning_is_not_attacked_as_an_empty_move():
    mem = state()
    order = {'step': 'I:relay:1', 'source': 'アルマムーン', 'target': 'コラダイン',
             'general': 'ミュスカデ', 'cards': [], 'after': None, 'purpose': 'move'}
    assert orders._ready(order, mem)
    mem['captured'].remove('コラダイン')
    mem['lost'].append('コラダイン')
    assert not orders._ready(order, mem)
    _plan_region(mem)['active'] = order['step']
    assert deploy._hold_unrouted_sortie(mem, order) is not None
    assert _plan_region(mem)['active'] is None


def add_transfer(mem, source, target, general='ミュスカデ'):
    step = source + '-hop'
    _world(mem)['sorties'][step] = {'source': source, 'target': target, 'general': general,
                            'status': 'en_route', 'tick': mem['tick'] - 10}
    _world(mem)['launched_orders'][step] = {'purpose': 'move', 'source': source,
                                    'target': target, 'general': general}
    return step


def test_transfer_is_forwarded_only_after_the_actual_roster_receipt():
    mem = state()
    step = add_transfer(mem, 'アルマムーン', 'コラダイン')
    # Owner 2026-11-08: while the transfer is still marching the list cannot
    # show the second general, so no departure is planned from the one-general
    # castle. The list is opened only once the busy window has passed.
    assert not [o for o in replan.interim_candidates(mem).values()
                if o['source'] == 'コラダイン']
    mem['tick'] += routes.SORTIE_BUSY_TICKS
    candidates = list(replan.interim_candidates(mem).values())
    assert candidates[0]['source'] == 'コラダイン'
    assert candidates[0]['target'] == 'イーピヨル'
    assert candidates[0]['source_probe'] is True
    assert candidates[0].get('roster_probe') is True   # read-only arrival check
    before = copy.deepcopy(_world(mem)['garrison'])
    routes.observed_roster(mem, 'コラダイン', ['エシャロット'])
    assert _world(mem)['sorties'][step]['status'] == 'en_route'
    assert not routes.probe_due(mem, 'コラダイン')
    assert _world(mem)['garrison'] == before
    # The march limit passes; the settled transfer is confirmed only when its
    # named actor is physically listed in the castle.
    mem['tick'] += 2000
    orders._relaunch_overdue_march(mem)
    assert _world(mem)['sorties'][step]['status'] == 'overdue'
    # The existing general-list reader publishes the list, then this hook
    # releases only a transfer whose named actor is physically listed there.
    _world(mem)['garrison']['コラダイン'] = ['エシャロット', 'ミュスカデ', 'ピオーネ', 'ポワソン', 'キャンディー']
    routes.observed_roster(mem, 'コラダイン', _world(mem)['garrison']['コラダイン'])
    assert _world(mem)['sorties'][step]['status'] == 'arrived'
    assert mem['_records'][-1]['decision'] == 'sortie_move_arrival_observed'
    # Isolate relay routing: if the boss is still open, boss waves correctly
    # consume the spares before this optional move is ever planned.
    mem['boss_victories'] = {'ボス': 1}
    candidates = list(replan.interim_candidates(mem).values())
    move = next((o for o in candidates if o['target'] == 'イーピヨル'), None)
    assert move is not None, candidates
    assert (move['source'], move['target'], move['purpose']) == (
        'コラダイン', 'イーピヨル', 'move')
    assert not move.get('source_probe')


def test_frontline_arrival_unlocks_the_final_attack_and_other_chapters_stay_unchanged():
    mem = state()
    step = add_transfer(mem, 'コラダイン', 'イーピヨル')
    _world(mem)['garrison']['イーピヨル'] = ['ピオーネ', 'ミュスカデ']
    routes.observed_roster(mem, 'イーピヨル', _world(mem)['garrison']['イーピヨル'])
    assert _world(mem)['sorties'][step]['status'] == 'arrived'
    first = next(iter(replan.interim_candidates(mem).values()))
    assert (first['source'], first['target'], first['purpose']) == (
        'イーピヨル', 'ドンダム', 'retake')
    for chapter in (1, 2, 4, 12):
        assert routes.allowed({'chapter': chapter}, 'source', 'target')


def test_wrong_castle_old_transfer_or_nontransfer_never_confirms_arrival():
    mem = state()
    step = add_transfer(mem, 'アルマムーン', 'コラダイン')
    routes.observed_roster(mem, 'イーピヨル', ['ミュスカデ'])
    assert _world(mem)['sorties'][step]['status'] == 'en_route'

    _world(mem)['sorties'][step]['tick'] = mem['tick'] - routes.SORTIE_BUSY_TICKS
    routes.observed_roster(mem, 'コラダイン', ['ミュスカデ'])
    assert _world(mem)['sorties'][step]['status'] == 'en_route'
    _world(mem)['sorties'][step]['tick'] = mem['tick']
    _world(mem)['launched_orders'][step]['purpose'] = 'attack'
    routes.observed_roster(mem, 'コラダイン', ['ミュスカデ'])
    assert _world(mem)['sorties'][step]['status'] == 'en_route'
    _world(mem)['launched_orders'][step]['purpose'] = 'move'
    _world(mem)['launched_orders'][step]['target'] = 'グリン'
    routes.observed_roster(mem, 'コラダイン', ['ミュスカデ'])
    assert _world(mem)['sorties'][step]['status'] == 'en_route'


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
    assert _plan_region(mem)['active'] is None and 'y_jump' not in mem
    assert _plan_region(mem)['orders'][order['step']] == 'failed'
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
    assert _plan_region(mem)['active'] is None and 'y_jump' not in mem
    assert mem['_map_registration_hold'] is True
    assert mem['_records'][-1]['decision'] == 'map_coordinates_unavailable'


def test_rear_spare_can_return_along_chart_legs_to_reinforce_the_front():
    mem = state()
    _world(mem)['garrison']['グリン'] = []
    _world(mem)['garrison']['リグア'] = ['ヘラ', 'キャンディー', 'ポワソン', 'アポロン', 'ゼウス']
    # The live layout has no spare at home or the front, only at the end of
    # a chart branch. A directed-only graph would strand this reinforcement.
    route = routes.path(mem, 'リグア', 'ドンダム', set(_world(mem)['garrison']))
    assert route == ('リグア', 'グリン', 'アルマムーン', 'コラダイン', 'イーピヨル', 'ドンダム')
    mem['boss_victories'] = {'ボス': 1}  # keep boss waves from consuming relay spares
    candidates = list(replan.interim_candidates(mem).values())
    move = next((o for o in candidates if o['target'] == 'グリン'), None)
    assert move is not None, candidates
    assert (move['source'], move['target'], move['purpose']) == ('リグア', 'グリン', 'move')
    assert routes.allowed(mem, 'リグア', 'グリン')
    mem['captured'].remove('グリン')
    mem['lost'].append('グリン')
    assert not routes.allowed(mem, 'リグア', 'グリン')
    assert routes.path(mem, 'リグア', 'ドンダム', set(_world(mem)['garrison']) - {'グリン'}) is None


def test_unsafe_pending_interim_does_not_block_its_routed_replacement():
    mem = state()
    _world(mem)['garrison']['リグア'] = ['ヘラ', 'キャンディー', 'ポワソン']
    old = {'step': 'I:old:1', 'source': 'リグア', 'target': 'ドンダム',
           'general': 'ヘラ', 'purpose': 'retake', 'cards': [], 'after': None}
    _plan_region(mem)['orders'][old['step']] = 'pending'
    pending = {'interim_order': old}
    replan._mechanical_interim(mem, pending, 'new-request', 'synthetic-digest')
    new = pending['interim_order']
    assert new['step'] != old['step']
    assert (new['source'], new['target'], new['purpose']) != ('リグア', 'グリン', 'retake')


def rigua_stall():
    """g1075 2026-10-29 11:38 JST: every castle but リグア is ours, グリン (the
    only leg to リグア) is recorded empty, and the two moves sent there have
    left the busy window without their arrival being read."""
    mem = state()
    mem['tick'] = 66558
    mem['captured'] = sorted(set(chart.castles(3)) - {'リグア', chart.boss_castle(3)})
    mem['lost'] = ['リグア']
    _world(mem)['garrison'] = {'アバロン': ['ソーピニヨン'], 'アルマムーン': ['どうし'], 'イーピヨル': [],
                       'グリン': [], 'コラダイン': ['ミュスカデ'], 'ドンダム': ['ピオーネ'],
                       'パタゴーヌ': ['アポロン'], 'メガパタ': ['デュオニソス']}
    for step, general, tick in (('I:380571:2', 'アテナ', 65575), ('I:b59ab6:1', 'ポワソン', 66126)):
        _world(mem)['sorties'][step] = {'source': 'アルマムーン', 'target': 'グリン', 'general': general,
                                'status': 'en_route', 'tick': tick}
        _world(mem)['launched_orders'][step] = {'purpose': 'move', 'source': 'アルマムーン',
                                        'target': 'グリン', 'general': general, 'step': step}
        _plan_region(mem)['orders'][step] = 'launched'
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
    assert {_world(mem)['sorties'][s]['status'] for s in ('I:380571:2', 'I:b59ab6:1')} == {'en_route'}
    assert not orders._source_list_open(retake, mem)
    assert orders.next_order(mem) is None or orders.next_order(mem)['step'] != 'I:b59ab6:2'
    # With every other measured garrison reduced to one defender there is
    # no runnable replacement; do not manufacture a doomed sortie.
    state_ = {'interim_order': retake}
    replan._mechanical_interim(mem, state_, 'c0ffee000000', 'synthetic-digest')
    assert state_['interim_order']['step'] == 'I:b59ab6:2'
    assert mem['_records'][-1]['decision'] == 'chart_interim_hold'
    state_.pop('interim_order')
    # ... and it no longer holds a startable replacement: a spare at home
    # stages the next general toward グリン instead of waiting on the old probe.
    _world(mem)['garrison']['アルマムーン'] = ['どうし', 'ゼウス', 'ミルフィーユ', 'ジェラート',
                                   'エピィ']   # v305: two stay; #3266: a booked spare stays busy
    replan._mechanical_interim(mem, state_, 'c0ffee000000', 'synthetic-digest-2')
    new = state_['interim_order']
    assert new['step'] != 'I:b59ab6:2'
    # #3266: the boss wave books a spare first. Owner 2026-11-05: the boss
    # waves now lead and may be the replacement itself.
    assert new['step'] != 'I:b59ab6:2'
    if new.get('purpose') == 'move':
        assert (new['source'], new['target']) == ('アルマムーン', 'グリン')
        assert new['general'] not in ('どうし', 'エピィ')
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
                        'アルマムーン': ['どうし', 'バーミセリ', 'トレビス', 'ココット', 'ゼウス',
                                   'ミルフィーユ', 'ジェラート'],
                        'ミヒラギアン': ['アテナ']},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    for step, general, tick in (('I:0fa434:2', 'アポロン', 95400), ('I:49a839:1', 'アルファルファ', 96181)):
        _world(mem)['sorties'][step] = {'source': 'アルマムーン', 'target': 'ビクトリア', 'general': general,
                                'status': 'en_route', 'tick': tick}
        _world(mem)['launched_orders'][step] = {'purpose': 'move', 'source': 'アルマムーン',
                                        'target': 'ビクトリア', 'general': general, 'step': step}
        _plan_region(mem)['orders'][step] = 'launched'
    return mem


def moves_into(mem, castle):
    return [o for o in replan.interim_candidates(mem).values()
            if o['purpose'] == 'move' and o['target'] == castle]


def test_a_castle_sent_moves_since_its_last_reading_is_not_refilled_in_chapter_4():
    mem = victoria_drain()
    assert routes.incoming(mem, 'ビクトリア') == []        # both moves are past the busy window
    assert len(routes.unread_dispatches(mem, 'ビクトリア')) == 2
    assert not moves_into(mem, 'ビクトリア')
    # Outside chapter 3 the list is opened once by a probe instead (g1097 2026-11-03):
    # the moved generals are otherwise invisible and never sortie.
    assert routes.probe_due(mem, 'ビクトリア')


def test_a_read_list_in_chapter_4_makes_an_empty_castle_a_move_target_again():
    mem = victoria_drain()
    mem['tick'] += 1
    routes.observed_roster(mem, 'ビクトリア', [])
    assert routes.unread_dispatches(mem, 'ビクトリア') == []
    assert [o['source'] for o in moves_into(mem, 'ビクトリア')] == ['アルマムーン']


def test_an_overdue_move_still_suppresses_refills_and_binds_on_later_read():
    """g1097 2026-11-02: the overdue march relaunch popped the sortie's
    launched_orders record, so the dispatch stopped counting as unread and
    the same "empty" castle received move after move (アルマムーン→アイアイエー
    x4+). An overdue move keeps suppressing refills until the castle's own
    list is read, and that read binds the arrival."""
    mem = victoria_drain()
    step = 'I:0fa434:2'
    _world(mem)['sorties'][step].update(status='overdue', purpose='move')
    _world(mem)['launched_orders'].pop(step)                  # relaunch pops the snapshot
    assert {s for s, _ in routes.unread_dispatches(mem, 'ビクトリア')} == {
        'I:0fa434:2', 'I:49a839:1'}
    assert not moves_into(mem, 'ビクトリア')
    routes.observed_roster(mem, 'ビクトリア', ['アポロン'])
    assert _world(mem)['sorties'][step]['status'] == 'arrived'
    assert mem['_records'][-1]['decision'] == 'sortie_move_arrival_observed'


def test_an_overdue_non_move_march_is_not_an_unread_dispatch():
    mem = victoria_drain()
    step = 'I:0fa434:2'
    _world(mem)['sorties'][step].update(status='overdue')
    _world(mem)['launched_orders'][step]['purpose'] = 'attack'
    assert {s for s, _ in routes.unread_dispatches(mem, 'ビクトリア')} == {'I:49a839:1'}
    routes.observed_roster(mem, 'ビクトリア', ['アポロン'])
    assert _world(mem)['sorties'][step]['status'] == 'overdue'


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
    # #3266: a general planned for one sortie is busy for the rest of the plan.
    # アルマムーン can spare two here (hero + HERO_CASTLE_GUARD stay), so the
    # remaining lost castles wait for the next plan rather than reusing them.
    assert set(retakes) == {'バスカビル', 'アイアイエー'}


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
           'garrison': {'アルマムーン': ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート', 'エピィ',
                                   'ポセイドン'],
                        'オトラント': ['グレナデン', 'バジル', 'マンゴスチン', 'キャンディー',
                                   'リースリング', 'クミン'],
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


def test_blocked_only_source_sorties_from_the_blocking_castle():
    """A blocked route can depart from the blocking castle only while a
    second general remains to defend it."""
    mem = {'chapter': 5, 'month': '39-9', 'tick': 1000,
           'captured': ['アルマムーン', 'オトラント', 'テラビル'],
           'lost': [], 'orders': {o['step']: 'launched' for o in chart.orders(5)},
           # #3266: generals named for earlier targets are planned-busy, so
           # アルマムーン needs enough depth to still be staffed at クロチェット.
           'garrison': {'アルマムーン': ['どうし', 'ミュスカデ', 'チコリ', 'ジェラート', 'エピィ',
                                   'ポセイドン', 'クミン'],
                        'オトラント': ['グレナデン', 'キャンディー', 'バジル',
                                       'マンゴスチン', 'リースリング', 'ゼウス'],
                        # Retake reservations may consume earlier spares.
                        'テラビル': ['ラビオリ']},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    cands = list(replan.interim_candidates(mem).values())
    kuro = next(c for c in cands if c.get('target') == 'クロチェット')
    assert kuro['source'] == 'オトラント'
    assert kuro['general'] in _world(mem)['garrison']['オトラント']
    assert len(_world(mem)['garrison']['オトラント']) >= 2


def test_pending_order_behind_an_own_castle_is_redirected_to_the_blocker():
    """Owner 2026-11-02 (g1097): a pending order baked with アルマムーン→
    クロチェット can never arrive — next_order re-points it at オトラント."""
    mem = {'chapter': 5, 'month': '39-9', 'tick': 1000,
           'captured': ['アルマムーン', 'オトラント', 'テラビル'], 'lost': [],
           'orders': {'A:x:J1': 'pending'},
           'launched_orders': {'A:x:J1': {'step': 'A:x:J1', 'source': 'アルマムーン',
                                          'target': 'クロチェット', 'general': 'エピィ',
                                          'cards': [], 'after': None}},
           'garrison': {'アルマムーン': ['どうし', 'ミュスカデ'], 'オトラント': ['グレナデン'],
                        'テラビル': ['ラビオリ']},
           'sorties': {}, '_records': [], 'house_eggs': {}, 'general_location_unknown': []}
    orders.next_order(mem)
    assert _plan_region(mem)['source_override']['A:x:J1'] == 'オトラント'
    rec = next(r for r in mem['_records'] if r['decision'] == 'sortie_source_march_block')
    assert rec['expected_metric']['source'] == 'アルマムーン'
    assert rec['observed_metric']['blocker'] == 'オトラント'


def test_a_clear_pending_order_keeps_its_baked_source():
    mem = {'chapter': 5, 'month': '39-9', 'tick': 1000,
           'captured': ['アルマムーン', 'オトラント'], 'lost': [],
           'orders': {'A:x:J1': 'pending'},
           'launched_orders': {'A:x:J1': {'step': 'A:x:J1', 'source': 'オトラント',
                                          'target': 'クロチェット', 'general': 'グレナデン',
                                          'cards': [], 'after': None}},
           'garrison': {'アルマムーン': ['どうし'], 'オトラント': ['グレナデン']},
           'sorties': {}, '_records': [], 'house_eggs': {}, 'general_location_unknown': []}
    orders.next_order(mem)
    assert 'source_override' not in _plan_region(mem) or 'A:x:J1' not in _plan_region(mem)['source_override']


def test_boss_order_unlocks_when_its_chain_is_held_despite_a_side_loss():
    """Owner 2026-11-02: クロシェット取り返し中でも並行でボス城に攻め込める —
    all_captured no longer waits on castles off the boss chain."""
    b1 = next(o for o in chart.all_orders(5) if o['step'] == '5-B1')
    mem = {'chapter': 5, 'month': '40-2', 'tick': 100,
           'captured': ['アルマムーン', 'テラビル', 'ヒュペルボレア', 'カルーン',
                        'ボッサール', 'ジュメルズ', 'オトラント', 'アイアイエー',
                        'バスカビル', 'プトレマイス'],
           'lost': ['クロチェット'],
           'orders': {}, 'launched_orders': {}, 'sorties': {}, 'garrison': {},
           '_records': [], 'house_eggs': {}, 'general_location_unknown': []}
    assert orders._boss_path_captured(mem)
    assert orders._ready(b1, mem)


def test_boss_order_still_waits_when_its_chain_is_broken():
    b1 = next(o for o in chart.all_orders(5) if o['step'] == '5-B1')
    mem = {'chapter': 5, 'month': '40-2', 'tick': 100,
           'captured': ['アルマムーン', 'テラビル', 'ヒュペルボレア', 'カルーン',
                        'ボッサール', 'ジュメルズ', 'オトラント', 'アイアイエー',
                        'プトレマイス', 'クロチェット'],
           'lost': ['バスカビル'],
           'orders': {}, 'launched_orders': {}, 'sorties': {}, 'garrison': {},
           '_records': [], 'house_eggs': {}, 'general_location_unknown': []}
    assert not orders._boss_path_captured(mem)
    assert not orders._ready(b1, mem)


def aiaie_stall():
    """g1097 2026-11-03 00:25 JST, chapter 5: ジュメルズ/クロチェット/カルーン are lost,
    アルマムーン holds six, アイアイエー is recorded empty from a reading 26k ticks
    old, and 14 moves went there (most settled 'overdue'). Nobody ever sortied
    from アイアイエー."""
    mem = {'chapter': 5, 'month': '42-6', 'tick': 100774,
           'captured': ['テラビル', 'ヒュペルボレア', 'ボッサール', 'アイアイエー',
                        'バスカビル', 'プトレマイス', 'オトラント'],
           'lost': ['ジュメルズ', 'クロチェット', 'カルーン'], 'orders': {},
           'garrison': {'アイアイエー': [],
                        'アルマムーン': ['どうし', 'ペコリーノ', 'クランベリー', 'マスカット',
                                   'デーメーテール', 'ミルフィーユ'],
                        'オトラント': ['マンゴスチン'], 'テラビル': ['リースリング'],
                        'バスカビル': ['マカデミア'], 'ヒュペルボレア': ['ピスタチオ'],
                        'プトレマイス': ['キャンディー'], 'ボッサール': ['ポセイドン']},
           'route_probe_read_tick': {'アイアイエー': 74177},
           'sorties': {}, 'launched_orders': {}, '_records': [],
           'house_eggs': {}, 'general_location_unknown': []}
    for step, general, tick, status in (('A:0ca8ce6a:M1', 'ジェラート', 88300, 'overdue'),
                                        ('A:d0871b56:M2', 'ピスタチオ', 93200, 'overdue'),
                                        ('A:86477f11:M3', 'ラビオリ', 99900, 'overdue')):
        _world(mem)['sorties'][step] = {'source': 'アルマムーン', 'target': 'アイアイエー',
                                'general': general, 'status': status, 'tick': tick,
                                'purpose': 'move'}
        _plan_region(mem)['orders'][step] = 'failed'
    return mem


def first_candidate(mem):
    return next(iter(replan.interim_candidates(mem).values()))


def test_moved_generals_are_found_by_opening_the_castle_they_were_sent_into():
    mem = aiaie_stall()
    assert routes.incoming(mem, 'アイアイエー') == []
    assert len(routes.unread_dispatches(mem, 'アイアイエー')) == 3
    assert routes.arrival_probe_due(mem, 'アイアイエー', cooldown=True)
    first = first_candidate(mem)
    # The list of the castle the moves went into is opened before アルマムーン
    # sends yet another sortie: it is the only arrival receipt.
    assert (first['source'], first['general'], first['source_probe']) == (
        'アイアイエー', replan.MOVE_ANY_GENERAL, True)
    assert first['purpose'] in ('retake', 'attack')
    assert not moves_into(mem, 'アイアイエー')                  # and nobody is sent there again


def test_the_probe_order_may_open_the_list_while_its_cooldown_holds_back_a_new_one():
    mem = aiaie_stall()
    step, candidate = next(iter(replan.interim_candidates(mem).items()))
    order = {**candidate, 'step': step}
    mem['arrival_probe_tick'] = {'アイアイエー': mem['tick'] - 3}
    assert routes.probe_due(mem, 'アイアイエー')                # the running probe keeps its source open
    assert orders._source_list_open(order, mem)
    assert not routes.arrival_probe_due(mem, 'アイアイエー', cooldown=True)
    assert first_candidate(mem)['source'] == 'アルマムーン'      # no second probe within the interval
    mem['tick'] += routes.PROBE_INTERVAL
    assert first_candidate(mem)['source'] == 'アイアイエー'


def test_a_read_list_ends_the_probe_and_the_found_generals_sortie_from_there():
    mem = aiaie_stall()
    mem['tick'] += 5
    routes.observed_roster(mem, 'アイアイエー', ['ピオーネ', 'ココット', 'ラビオリ'])
    _world(mem)['garrison']['アイアイエー'] = ['ピオーネ', 'ココット', 'ラビオリ']
    assert routes.unread_dispatches(mem, 'アイアイエー') == []
    assert not routes.probe_due(mem, 'アイアイエー')
    first = first_candidate(mem)
    assert first.get('source_probe') is not True
    assert first['general'] in ('ピオーネ', 'ココット', 'ラビオリ') or first['source'] == 'アルマムーン'


def test_a_transfer_still_marching_is_waited_for_before_the_list_is_opened():
    mem = aiaie_stall()
    _world(mem)['sorties']['A:86477f11:M3'].update(status='en_route', tick=mem['tick'] - 50)
    _world(mem)['launched_orders']['A:86477f11:M3'] = {
        'purpose': 'move', 'source': 'アルマムーン', 'target': 'アイアイエー',
        'general': 'ラビオリ', 'step': 'A:86477f11:M3'}
    assert routes.incoming(mem, 'アイアイエー')
    assert not routes.arrival_probe_due(mem, 'アイアイエー', cooldown=True)
    assert first_candidate(mem).get('source_probe') is not True


# g1097 2026-11-03: the mechanical plan is rebuilt under the same request id whenever
# gold/month/order states change, and again hours later when the situation recurs.
# Positional ids M1..Mn named a different order each time, so a stale 'launched'
# kept カルーン (M1, 6.5 h) and クロチェット (M3) from ever starting.
def plan_steps(mem):
    return {o['target']: o['step'] for o in (_plan_region(mem).get('chart_plan') or {}).get('orders') or []}


def rebuild(mem):
    mem['route_probe_read_tick']['アイアイエー'] = mem['tick']      # its list was read: no probe order
    _plan_region(mem)['chart_plan'] = None
    replan._mechanical_chart(mem, mem.setdefault('chart_adjust', {}), '0123456789abcdef', 'digest')
    return plan_steps(mem)


def test_a_plan_step_id_names_the_order_not_its_position():
    mem = aiaie_stall()
    first = rebuild(mem)
    assert len(set(first.values())) == len(first) >= 3
    assert not any(step.rsplit(':', 1)[-1] in ('M1', 'M2', 'M3', 'M4') for step in first.values())
    mem['tick'] += 50
    _world(mem)['garrison']['アルマムーン'] = _world(mem)['garrison']['アルマムーン'][::-1]   # a rebuild with other leads
    assert rebuild(mem) == first                       # same order -> same id, whatever its position


def test_an_old_launched_flag_does_not_block_an_order_the_plan_offers_again():
    mem = aiaie_stall()
    steps = rebuild(mem)
    karun = steps['カルーン']
    _plan_region(mem)['orders'][karun] = 'launched'
    _world(mem)['sorties'][karun] = {'source': 'アルマムーン', 'target': 'カルーン', 'general': 'キッシュ',
                             'status': 'arrived', 'tick': mem['tick'] - 17000}
    _plan_region(mem)['general_override'] = {karun: 'キッシュ'}
    _plan_region(mem)['order_context'] = {karun: {'actual_general': 'キッシュ'}}
    rebuild(mem)
    assert _plan_region(mem)['orders'][karun] == 'pending'
    assert karun not in _plan_region(mem)['general_override'] and karun not in _plan_region(mem).get('order_context', {})
    assert mem['_records'][-1]['decision'] in ('plan_step_released', 'chart_adjust_applied')
    assert any(r['decision'] == 'plan_step_released' and r['chart_step'] == karun for r in mem['_records'])


def test_an_order_really_marching_keeps_its_launched_flag():
    mem = aiaie_stall()
    steps = rebuild(mem)
    step = steps['ジュメルズ']
    _plan_region(mem)['orders'][step] = 'launched'
    _world(mem)['sorties'][step] = {'source': 'アルマムーン', 'target': 'ジュメルズ', 'general': 'セルリアク',
                            'status': 'en_route', 'tick': mem['tick'] - 100}
    rebuild(mem)
    assert _plan_region(mem)['orders'][step] == 'launched'
