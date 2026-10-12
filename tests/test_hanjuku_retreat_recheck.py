"""Retreat selection queues a bounded, named observation, never an arrival."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_chart_bot import Canvas, _camp_frame, camp_menu
from test_hanjuku_house import roster, status

ID = {'game': 'hanjuku-hero', 'runtime_id': 'recheck-test', 'generation': 1, 'lease_id': 'lease-test'}
MAP = Screen([], None, '', kind='map', cursor=(26, 66))
FRAME = _camp_frame()


def memory(name='ゼウス'):
    mem = {'chapter': 1, 'tick': 100, '_run_identity': dict(ID), '_records': [],
           'sorties': {'old': {'general': name, 'status': 'arrived', 'target': 'キカンドン', 'tick': 80}},
           'battle': {'ally': name, 'enemy': 'タピオカ', 'ally_hp': 6, 'enemy_hp': 35,
                      'start_ally_hp': 24, 'side': 'attack', 'castle': 'キカンドン',
                      'away': 1, 'cards_used': [], 'hero_retreat': {'selected': 1}}}
    p.battle_end(mem, 'map')
    return mem


def main_menu():
    c = Canvas(); c.text(48, 47, 'しょうぐん'); c.hand(26, 41)
    screen = parse(c.frame()); screen.kind = 'main_menu'
    return screen


def focus(mem):
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
    assert p.camp_recall_step(main_menu(), mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(parse(roster(selected=1)), mem, FRAME) == [p.pad('select')]
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]


def test_actual_selection_surviving_actor_only_queues_unknown_location():
    mem = memory()
    row = mem['retreat_rechecks']['ゼウス']
    assert row['identity'] == ID and row['chapter'] == 1
    assert row['status'] == 'needs_observation'
    assert row['attempts'] == 0
    assert not mem.get('recall')
    assert mem['sorties']['old']['status'] == 'arrived'
    assert not mem.get('garrison')


@pytest.mark.parametrize('change', [ {'hero_retreat': {}}, {'ally_hp': 0}, {'ally_hp': None}, {'side': 'defense'} ])
def test_unselected_dead_unknown_or_defending_actor_does_not_queue(change):
    mem = memory(); mem.pop('retreat_rechecks', None)
    mem['battle'] = {'ally': 'ゼウス', 'enemy': 'タピオカ', 'ally_hp': 6, 'enemy_hp': 35,
                     'away': 1, 'side': 'attack', 'cards_used': [], 'hero_retreat': {'selected': 1}, **change}
    p.battle_end(mem, 'map')
    assert not mem.get('retreat_rechecks')


def test_named_focus_requires_current_tent_and_matching_status_before_return():
    mem = memory(); before_sorties = deepcopy(mem['sorties'])
    focus(mem)
    screen = parse(status('ゼウス', main=False, castle=False, hp=6))
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert mem['recall']['named_status']['hp'] == 6
    assert mem['recall']['general'] == 'ゼウス'
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]
    assert mem['recall']['stage'] == 'dest'
    assert mem['sorties'] == before_sorties
    assert not mem.get('house_eggs') and not mem.get('garrison')


@pytest.mark.parametrize('name,hp,castle', [('ヴィーナス', 6, False), ('ゼウス', 0, False), ('ゼウス', None, False), ('ゼウス', 6, True)])
def test_wrong_dead_unknown_or_castle_status_never_authorizes_return(name, hp, castle):
    mem = memory(); focus(mem)
    screen = parse(status(name, main=False, castle=castle, hp=6 if hp is None else hp))
    if hp is None: screen.lines = [r for r in screen.lines if r.y != 47]
    if castle: screen.text += 'しろのなかにいます'
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')
    if name == 'ゼウス' and hp == 0:
        assert 'ゼウス' not in (mem.get('retreat_rechecks') or {})
    else:
        assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert not mem.get('recall_skip')


def test_missing_camp_after_named_select_never_uses_saved_cell(monkeypatch):
    mem = memory()
    p.camp_recall_step(MAP, mem, FRAME); p.camp_recall_step(main_menu(), mem, FRAME)
    p.camp_recall_step(parse(roster(selected=1)), mem, FRAME)
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [])
    for _ in range(3): assert p.camp_recall_step(MAP, mem, FRAME) == []
    assert not mem.get('recall') and not mem.get('recall_skip')
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


@pytest.mark.parametrize('kind', ['battle', 'battle_menu', 'defense_started', 'month_menu', 'yes_no'])
def test_interrupt_discards_ui_permission_but_keeps_named_recheck(kind):
    mem = memory(); focus(mem)
    assert p.camp_recall_step(Screen([], None, '', kind=kind), mem, FRAME) is None
    assert not mem.get('recall')
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


@pytest.mark.parametrize('change', [ {'chapter': 2}, {'_run_identity': {**ID, 'lease_id': 'other'}}, {'_run_identity': None} ])
def test_other_chapter_or_run_does_not_reuse_named_ui_permission(change):
    mem = memory(); focus(mem); mem.update(change)
    actions = p.camp_recall_step(camp_menu(3), mem, FRAME)
    assert actions != [p.pad('a')]
    assert not mem.get('retreat_rechecks') and not mem.get('recall')


def test_unknown_cursor_and_repeated_missing_actor_stop_finitely():
    mem = memory(); p.camp_recall_step(MAP, mem, FRAME)
    screen = main_menu(); screen.hand = None
    for _ in range(3): assert p.camp_recall_step(screen, mem, FRAME) in ([], [p.pad('b')])
    assert not mem.get('recall')
    mem['tick'] += 30; p.camp_recall_step(MAP, mem, FRAME); p.camp_recall_step(main_menu(), mem, FRAME)
    for _ in range(32): assert p.camp_recall_step(parse(roster(names=('どうし',))), mem, FRAME) == [p.pad('down')]
    assert p.camp_recall_step(parse(roster(names=('どうし',))), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')


def test_missing_dispatch_receipt_does_not_suppress_other_camps(monkeypatch):
    mem = memory(); focus(mem)
    p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, FRAME)
    mem['recall'].update(stage='await_dispatch', steps=5, a_inputs=2, request_trace={'decision_id': 'new'})
    assert p.camp_recall_step(MAP, mem, FRAME) == []
    assert mem['recall_verification']['status'] == 'dispatch_unconfirmed'
    assert not mem.get('recall_skip')
    assert mem['retreat_rechecks']['ゼウス']['retry_after'] == mem['tick'] + 30
    # Cooldown applies to the named observation, while any other fresh camp is allowed.
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (80, 80), 'clipped': False}])
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('right', 8)]
    assert not mem['recall'].get('retreat_recheck')


def test_matching_inputs_do_not_mark_camp_or_arrival_complete():
    mem = memory(); focus(mem)
    p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, FRAME)
    trace = {'decision_id': 'new'}
    mem['recall'].update(stage='await_dispatch', a_inputs=2, request_trace=trace)
    mem['_recall_inputs'] = {'request_trace': trace, 'a_inputs': 2}
    assert p.camp_recall_step(MAP, mem, FRAME) == []
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'arrival_unconfirmed'
    assert mem['recall_verification']['status'] == 'arrival_unconfirmed'
    assert mem['sorties']['old']['status'] == 'arrived'
    assert not mem.get('garrison')


def test_named_flow_routes_main_menu_in_decide(monkeypatch):
    from docich import hanjuku_screen
    mem = memory(); p.camp_recall_step(MAP, mem, FRAME)
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *_a, **_k: main_menu())
    actions, updated = decide(FRAME, {'policy': mem}, run_identity=ID)
    assert actions == [p.pad('a')]
    assert updated['policy']['recall']['stage'] == 'recheck_pick'


def named_menu(mem):
    focus(mem)
    assert p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]


@pytest.mark.parametrize('owner,button', [('own', 'a'), ('enemy', 'right'), (None, 'right')])
def test_real_owned_destination_remains_required_after_named_status(monkeypatch, owner, button):
    mem = memory(); named_menu(mem)
    x, y = p.chart.castles(1)['アルマムーン']; ox, oy = p.WORLD_MAP_OFFSET[1]
    monkeypatch.setattr(p, 'world_cursor', lambda _frame: (x / 8 + ox, y / 8 + oy))
    monkeypatch.setattr(p, 'world_flags', lambda _frame, _chapter: {'アルマムーン': owner} if owner else {})
    actions = p.camp_recall_step(Screen([], None, '', kind='world_map'), mem, FRAME)
    assert actions[0] == p.pad(button)
    if owner == 'own':
        assert actions == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
        assert mem['recall']['stage'] == 'await_dispatch'
        assert mem['recall']['goal'] == 'アルマムーン'
    else:
        assert mem['recall']['stage'] == 'dest'
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


def test_nameless_local_picker_does_not_borrow_an_owned_roof(monkeypatch):
    mem = memory(); named_menu(mem)
    monkeypatch.setattr(p, 'castle_roofs', lambda _frame: [{'kind': 'own', 'target': (26, 66), 'clipped': False}])
    assert p.camp_recall_step(Screen([], None, '', kind='map_target', marker=(26, 66)), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall') and mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


def test_lost_status_proof_never_reaches_destination_confirmation():
    mem = memory(); named_menu(mem); mem['recall'].pop('named_status')
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')


def test_queue_is_bounded_deduplicated_and_retries_have_a_finite_budget():
    from docich import hanjuku_camp_recheck as recheck, hanjuku_roster
    mem = memory()
    for n in range(hanjuku_roster.MAX_GENERALS + 4):
        recheck.capture(mem, {'ally': f'テスト{n}', 'ally_hp': 6, 'side': 'attack', 'hero_retreat': {'selected': 1}})
    assert len(mem['retreat_rechecks']) == hanjuku_roster.MAX_GENERALS
    mem['retreat_rechecks'] = {'ゼウス': mem['retreat_rechecks']['ゼウス']}
    for n in range(recheck.ATTEMPT_LIMIT):
        mem['tick'] = 100 + 30 * n
        assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
        screen = main_menu(); screen.hand = None
        for _ in range(3): p.camp_recall_step(screen, mem, FRAME)
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == recheck.ATTEMPT_LIMIT
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    mem['tick'] += 30
    assert p.camp_recall_step(MAP, mem, Canvas().frame()) is None
    assert not mem.get('recall')


def test_changed_chapter_removes_the_queue():
    mem = memory()
    p._enter_chapter(mem, 2, reason='test', evidence='フーリック')
    assert not mem.get('retreat_rechecks')


def test_actual_picker_uses_the_existing_receipt_path():
    from test_hanjuku_camp_receipts import picker
    mem = memory(); mem['chapter'] = 2
    # Capture anew after the chapter change so the pending record belongs to it.
    from docich import hanjuku_camp_recheck as recheck
    recheck.capture(mem, {'ally': 'ゼウス', 'ally_hp': 6, 'side': 'attack', 'hero_retreat': {'selected': 1}})
    named_menu(mem)
    frame = picker(); screen = parse(frame, phase='field')
    assert p.camp_recall_step(screen, mem, frame) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert mem['recall']['stage'] == 'await_dispatch' and mem['recall']['goal'] == 'アルマムーン'
    assert p.camp_recall_step(screen, mem, frame) == []


def test_same_failed_camp_cannot_fall_immediately_into_anonymous_recall():
    mem = memory(); named_menu(mem)
    mem['recall'].update(stage='await_dispatch', steps=5, a_inputs=2)
    p.camp_recall_step(MAP, mem, FRAME)
    assert p.camp_recall_step(MAP, mem, FRAME) is None
    assert not mem.get('recall') and not mem.get('recall_skip')
    mem['tick'] += 30
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
    assert mem['recall']['retreat_recheck']
    assert not mem['recall'].get('named_status')


def test_two_roofs_keep_the_same_failed_camp_backoff_when_map_animates(monkeypatch):
    mem = memory()
    monkeypatch.setattr(p, 'castle_roofs', lambda _frame: [
        {'kind': 'own', 'target': (90, 90)}, {'kind': 'enemy', 'target': (180, 90)}])
    named_menu(mem); mem['recall'].update(stage='await_dispatch', steps=5, a_inputs=2)
    p.camp_recall_step(MAP, mem, FRAME)
    rgb = bytearray(FRAME.rgb); rgb[0:3] = b'\x00\x00\x00'
    from docich.hanjuku_pixels import Frame
    changed = Frame(FRAME.width, FRAME.height, bytes(rgb))
    assert p.camp_recall_step(MAP, mem, changed) is None
    assert not mem.get('recall')


def test_foreground_preemption_invalidates_status_before_other_policy_input(monkeypatch):
    from docich import hanjuku_screen
    mem = memory(); named_menu(mem)
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *_a, **_k: Screen([], None, '', kind='battle'))
    monkeypatch.setattr(p, 'defender_egg_pending_step', lambda *_a: [p.pad('b')])
    actions, updated = decide(FRAME, {'policy': mem}, run_identity=ID)
    assert actions == [p.pad('b')]
    assert not updated['policy'].get('recall')
    assert updated['policy']['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    updated['policy']['tick'] += 30
    assert p.camp_recall_step(MAP, updated['policy'], FRAME) == [p.pad('x')]
    assert not updated['policy']['recall'].get('named_status')


def test_a_new_pending_record_cannot_reauthorize_an_old_ui_episode():
    from docich import hanjuku_camp_recheck as recheck
    mem = memory(); named_menu(mem)
    mem['tick'] += 1
    recheck.capture(mem, {'ally': 'ゼウス', 'ally_hp': 6, 'side': 'attack', 'hero_retreat': {'selected': 1}})
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 0


def test_named_status_has_a_finite_freshness_budget():
    mem = memory(); named_menu(mem); mem['tick'] += p.RECALL_LIMIT + 1
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')


def test_changed_view_cannot_send_an_exhausted_actor_through_anonymous_recall(monkeypatch):
    mem = memory(); mem['retreat_rechecks']['ゼウス'].update(attempts=3)
    # A different camera/current tent has no durable identity yet.
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (90, 90), 'clipped': False}])
    changed_map = Screen([], None, '', kind='map', cursor=(90, 90))
    assert p.camp_recall_step(changed_map, mem, FRAME) == [p.pad('a')]
    assert not mem['recall'].get('retreat_recheck')
    # It must read the real occupant before choosing きかん.
    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall') and not mem.get('recall_skip')
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 3
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert p.camp_recall_step(changed_map, mem, FRAME) is None


def test_anonymous_actual_other_actor_can_return_without_resetting_blocked_actor(monkeypatch):
    mem = memory(); mem['retreat_rechecks']['ゼウス'].update(attempts=3)
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (90, 90), 'clipped': False}])
    changed_map = Screen([], None, '', kind='map', cursor=(90, 90))
    assert p.camp_recall_step(changed_map, mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(parse(status('ヴィーナス', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['recall']['general'] == 'ヴィーナス'
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 3
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


def test_anonymous_checked_other_actor_uses_owned_picker_without_resetting_named_budget(monkeypatch):
    mem = memory(); mem['retreat_rechecks']['ゼウス'].update(attempts=3)
    p.camp_recall_step(MAP, mem, FRAME)
    p.camp_recall_step(camp_menu(1), mem, FRAME)
    p.camp_recall_step(parse(status('ヴィーナス', main=False, hp=6)), mem, FRAME)
    p.camp_recall_step(camp_menu(3), mem, FRAME)
    x, y = p.chart.castles(1)['アルマムーン']; ox, oy = p.WORLD_MAP_OFFSET[1]
    monkeypatch.setattr(p, 'world_cursor', lambda _frame: (x / 8 + ox, y / 8 + oy))
    monkeypatch.setattr(p, 'world_flags', lambda _frame, _chapter: {'アルマムーン': 'own'})
    assert p.camp_recall_step(Screen([], None, '', kind='world_map'), mem, FRAME) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 3


def test_roof_view_backoff_survives_state_json_roundtrip(monkeypatch):
    import json
    mem = memory()
    monkeypatch.setattr(p, 'castle_roofs', lambda _frame: [
        {'kind': 'own', 'target': (90, 90)}, {'kind': 'enemy', 'target': (180, 90)}])
    named_menu(mem); mem['recall'].update(stage='await_dispatch', steps=5, a_inputs=2)
    p.camp_recall_step(MAP, mem, FRAME)
    mem = json.loads(json.dumps(mem))
    rgb = bytearray(FRAME.rgb); rgb[0:3] = b'\x00\x00\x00'
    from docich.hanjuku_pixels import Frame
    assert p.camp_recall_step(MAP, mem, Frame(FRAME.width, FRAME.height, bytes(rgb))) is None


def test_anonymous_status_does_not_switch_an_intended_hero_to_another_actor():
    mem = memory(); mem['retreat_rechecks']['ゼウス'].update(attempts=3)
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]
    mem['recall']['hero'] = True  # a tagged anonymous owner still validates its intended hero
    assert p.camp_recall_step(parse(status('ヴィーナス', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 3


def test_changing_unanchored_views_cannot_scan_an_exhausted_actor_forever(monkeypatch):
    from docich.hanjuku_pixels import Frame
    from docich import hanjuku_roster
    mem = memory(); mem['retreat_rechecks']['ゼウス'].update(attempts=3)
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (26, 66), 'clipped': False}])
    def changed(n):
        rgb = bytearray(FRAME.rgb); rgb[0:3] = bytes((n + 1, 0, 0))
        return Frame(FRAME.width, FRAME.height, bytes(rgb))
    for n in range(hanjuku_roster.MAX_GENERALS):
        frame = changed(n)
        assert p.camp_recall_step(MAP, mem, frame) == [p.pad('a')]
        assert p.camp_recall_step(camp_menu(1), mem, frame) == [p.pad('a')]
        assert p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, frame) == [p.pad('b')]
    assert mem['retreat_anonymous_budget']['reads'] == hanjuku_roster.MAX_GENERALS
    for n in range(4):
        assert p.camp_recall_step(MAP, mem, changed(n + hanjuku_roster.MAX_GENERALS)) is None
    assert len(mem['retreat_rechecks']) == 1
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 3
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert not mem.get('recall') and not mem.get('recall_skip')


def test_an_old_anonymous_menu_without_current_tent_cannot_adopt_the_named_queue():
    mem = memory()
    mem['recall'] = {'stage': 'menu', 'steps': 0, 'target': [26, 66]}
    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('b')]
    assert p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, FRAME) is None
    assert not mem.get('recall')
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 0
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert not any(r.get('resulting_event') == 'named_camp_observed' for r in mem['_records'])


@pytest.mark.parametrize('name', ['', 'a' * 33, 'ゼ ウス', 'ゼ\nウス', 'ゼ\ufffdウス', 123])
def test_hotload_rejects_unclean_actor_names(name):
    from docich import hanjuku_camp_recheck as recheck
    mem = memory(); row = deepcopy(mem['retreat_rechecks']['ゼウス'])
    mem['retreat_rechecks'] = {name: row}
    assert recheck._scope(mem) == {}
    assert not mem.get('retreat_rechecks')


@pytest.mark.parametrize('change', [{'chapter': True}, {'chapter': '1'}, {'chapter': 2},
    {'tick': True}, {'tick': -1}, {'tick': '100'}, {'tick': 101},
    {'retry_after': False}, {'retry_after': -1}, {'retry_after': '100'}, {'retry_after': 99},
    {'attempts': True}, {'attempts': -1}, {'attempts': '0'}, {'attempts': 4},
    {'status': 'arrived'}, {'status': None}, {'status': []}, {'status': {}}, {'identity': {**ID, 'generation': 2}},
    {'identity': {**ID, 'generation': True}}, {'identity': {**ID, 'generation': 1.0}},
    {'identity': {k: v for k, v in ID.items() if k != 'lease_id'}}])
def test_hotload_rejects_malformed_rows_without_restoring_arrival_or_inventory(change):
    from docich import hanjuku_camp_recheck as recheck
    mem = memory(); mem['retreat_rechecks']['ゼウス'].update(change)
    assert recheck._scope(mem) == {}
    assert not mem.get('retreat_rechecks')
    assert not mem.get('garrison') and not mem.get('house_eggs')
    assert mem['sorties']['old']['status'] == 'arrived'


@pytest.mark.parametrize('value', [None, [], ['ゼウス'], 'bad', {'ゼウス': None}])
def test_malformed_hotload_queue_stays_unknown(value):
    from docich import hanjuku_camp_recheck as recheck
    mem = memory(); mem['retreat_rechecks'] = value
    assert recheck._scope(mem) == {}
    assert not mem.get('garrison')


def test_hotload_oversized_valid_queue_is_bounded_before_any_work():
    from docich import hanjuku_camp_recheck as recheck, hanjuku_roster
    mem = memory(); row = mem['retreat_rechecks']['ゼウス']
    mem['retreat_rechecks'] = {f'テスト{n}': deepcopy(row) for n in range(64)}
    bounded = recheck._scope(mem)
    assert len(bounded) == hanjuku_roster.MAX_GENERALS
    assert len(mem['retreat_rechecks']) == hanjuku_roster.MAX_GENERALS
    assert not mem.get('recall') and not mem.get('garrison')


def test_main_status_fallback_field_does_not_authorize_a_camp_return():
    from docich import hanjuku_house
    mem = memory(); focus(mem)
    screen = parse(status('ゼウス', main=True, castle=False, hp=6))
    screen.text = screen.text.replace('いどうしています', '')
    info = hanjuku_house.general_status(screen)
    assert info['location'] == 'field' and not info['location_observed']
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


def test_actual_field_panel_without_a_location_phrase_is_grounded_in_current_tent():
    from docich import hanjuku_house
    mem = memory(); focus(mem)
    screen = parse(status('ゼウス', main=False, hp=6))
    info = hanjuku_house.general_status(screen)
    assert info['location'] == 'field' and not info['location_observed']
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]
    assert mem['recall']['stage'] == 'dest'
    assert not mem.get('garrison') and not mem.get('house_eggs')


@pytest.mark.parametrize('start_ui', [False, True])
@pytest.mark.parametrize('kind,text', [('name_entry', ''), ('text', 'ゼウスがはいかにくわわった!'),
                                     ('text', 'ゼウスがはいかにくわわった！')])
def test_new_name_or_join_observation_drops_old_intent_and_ui_immediately(start_ui, kind, text):
    mem = memory()
    if start_ui: named_menu(mem)
    mem['retreat_anonymous_budget'] = {'key': {}, 'reads': 31}
    assert p.camp_recall_step(Screen([], None, text, kind=kind), mem, FRAME) is None
    assert not mem.get('recall') and not mem.get('retreat_rechecks')
    assert not mem.get('retreat_anonymous_budget') and not mem.get('garrison')
    assert not any(r.get('resulting_event') in ('arrival_confirmed', 'dead') for r in mem['_records'])


def test_later_observed_zero_hp_invalidates_that_actors_old_intent_without_claiming_death():
    mem = memory(); named_menu(mem)
    mem['battle'] = {'ally': 'ゼウス', 'enemy': 'タピオカ', 'ally_hp': 0, 'enemy_hp': 35,
                     'away': 1, 'side': 'attack', 'cards_used': []}
    p.battle_end(mem, 'map')
    assert not mem.get('recall') and not mem.get('retreat_rechecks')
    assert p.summary(mem)['generals_lost'] is None
    assert not any(r.get('resulting_event') in ('arrival_confirmed', 'dead') for r in mem['_records'])


def test_another_actors_zero_hp_does_not_erase_the_pending_named_actor():
    from docich import hanjuku_camp_recheck as recheck
    mem = memory()
    recheck.capture(mem, {'ally': 'ヴィーナス', 'side': 'attack', 'ally_hp': 0})
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


def test_name_handler_is_not_delayed_by_an_old_recheck(monkeypatch):
    from docich import hanjuku_screen
    mem = memory(); named_menu(mem)
    called = []
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *_a, **_k: Screen([], None, '', kind='name_entry'))
    def name_step(_screen, observed):
        called.append(observed)
        assert not observed.get('recall') and not observed.get('retreat_rechecks')
        return [p.pad('b')]
    monkeypatch.setattr(p, 'name_step', name_step)
    actions, updated = decide(FRAME, {'policy': mem}, run_identity=ID)
    assert actions == [p.pad('b')] and len(called) == 1
    assert not updated['policy'].get('retreat_anonymous_budget')


def test_repeated_new_name_observations_do_not_reclaim_an_already_removed_intent():
    from docich import hanjuku_camp_recheck as recheck
    mem = memory()
    recheck.interrupt(Screen([], None, '', kind='name_entry'), mem)
    count = sum(r['decision'] == 'retreat_recheck_invalidated' for r in mem['_records'])
    assert count == 1
    recheck.interrupt(Screen([], None, '', kind='name_entry'), mem)
    assert sum(r['decision'] == 'retreat_recheck_invalidated' for r in mem['_records']) == count
