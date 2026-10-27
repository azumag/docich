"""Native synthetic frames exercise the registration transaction, not a ROM.

Coordinates in these fixtures are artificial chapter-local points. Public
image pixels are search hints only; no test claims a measured chapter-3 map.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from docich import hanjuku_chart as chart, hanjuku_policy as policy
from docich.hanjuku.flows import map_registration as survey
from docich.hanjuku.orchestrator import decide, NO_INPUT_HOLD_MAX
from docich.hanjuku.strategy import campaign
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import Screen, parse, castle_roofs
from test_hanjuku_chart_bot import Canvas, HAND, UNKNOWN
from test_hanjuku_sortie_evidence import actor_panel, measured_loaded_sortie

IDENTITY = {'game': 'hanjuku-hero', 'generation': 900,
            'runtime_id': 'g900-synthetic', 'lease_id': 'test-map-survey'}
HOME = (60, 28)
GRIN = (204, 188)


def memory():
    return {'chapter': 3, 'tick': 0, '_run_identity': dict(IDENTITY),
            'survey_run_identity': dict(IDENTITY), '_records': [], 'orders': {}}


def field(cursor=HOME, roofs=((HOME, 'own'), (GRIN, 'enemy')), camera=(0, 0)):
    c = Canvas((64, 140, 40))
    for (wx, wy), kind in roofs:
        x, y = wx-camera[0], wy-camera[1]
        color = (205, 40, 57) if kind == 'own' else (24, 96, 140)
        for row in range(y-5, y+9):
            for col in range(x-10, x+11):
                if 0 <= row < 224 and 0 <= col < 256:
                    c.put(col, row, color)
    x, y = cursor
    for row in (y+1, y+14):
        for col in (*range(x+2, x+5), *range(x+11, x+14)):
            c.put(col, row, (255, 255, 255))
    for row in (y+2, y+3, y+4):
        c.put(x+1, row, (255, 255, 255))
    return c.frame()


def status(name):
    c = Canvas((64, 140, 40))
    for y in range(16, 120):
        for x in range(16, 240):
            c.put(x, y, (16, 72, 57))
    for x in range(35, 111):
        c.put(x, 16, (255, 48, 48))
    c.text(16, 31, f'しゅつげき {name} じょう')
    c.text(16, 47, 'ステータス しゅうにゅう22G')
    return c.frame()


def feed(mem, frame):
    mem['tick'] += 1
    with chart.map_context(mem):
        return survey.step(parse(frame), mem, frame)


def home_receipt():
    mem = memory()
    assert parse(field()).cursor == HOME
    assert len(castle_roofs(field())) == 2
    assert feed(mem, field()) == []
    assert feed(mem, field()) == [policy.pad('a')]
    assert feed(mem, status('アルマムーン')) == []
    assert survey.verified_castles(mem, 3) == {}
    assert feed(mem, status('アルマムーン')) == [policy.pad('b')]
    assert feed(mem, field()) == []
    assert survey.verified_castles(mem, 3) == {'アルマムーン': HOME}
    return mem


def walk_to_grin(mem):
    cursor = list(HOME)
    for _ in range(40):
        actions = feed(mem, field(tuple(cursor)))
        if actions == [policy.pad('a')]:
            return
        for action in actions:
            direction = action['buttons'][0]
            frames = round(action['hold_ms']*60/1000)
            axis = 0 if direction in ('left', 'right') else 1
            cursor[axis] += frames * (1 if direction in ('right', 'down') else -1)
    pytest.fail('native free-cursor search did not reach the visible candidate')


def initial_route_receipt():
    mem = home_receipt()
    walk_to_grin(mem)
    feed(mem, status('グリン')); feed(mem, status('グリン'))
    feed(mem, field(GRIN))  # close the inspection transaction
    return mem


def caller_adopted_interim():
    """Use the real mechanical interim path; plant geography, never an order."""
    mem = home_receipt()
    from docich.hanjuku_map_sources import SOURCE_CASTLES
    proof = mem['map_registration']['castles']['アルマムーン']
    positions = {n: (400+64*i, 400+48*i) for i, n in enumerate(SOURCE_CASTLES[3])}
    positions.update(アルマムーン=HOME, グリン=GRIN)
    mem['map_registration']['castles'] = {
        n: {**proof, 'name': n, 'position': list(p)} for n, p in positions.items()}
    mem['orders'] = {o['step']: 'failed' for o in chart.all_orders(3)}
    mem['garrison'] = {'アルマムーン': ['ゼウス', 'どうし']}
    assert not mem.get('active') and not mem.get('chart_plan') and not mem.get('chart_adjust')
    _, state = decide(field(), {'policy': mem}, run_identity=IDENTITY)
    active = state['policy']['active']
    assert active.startswith('I:')
    assert state['policy']['chart_adjust']['interim_order']['step'] == active
    assert any(r['decision'] == 'chart_interim_order' for r in state['_records'])
    return state, active


@pytest.mark.parametrize('identity', [None, {}, {**IDENTITY, 'game': 'other'},
                                     {**IDENTITY, 'lease_id': ''}])
def test_real_interim_caller_stops_active_when_identity_is_missing_or_invalid(identity):
    state, active = caller_adopted_interim()
    actions, updated = decide(field(), state, run_identity=identity)
    assert actions == []
    assert updated['policy'].get('active') is None
    assert updated['policy']['orders'].get(active) in (None, 'pending')
    assert not updated['policy'].get('sorties')
    with chart.map_context(updated['policy']):
        assert not chart.castles(3)


def test_real_interim_caller_keeps_registered_order_with_the_same_valid_identity():
    state, active = caller_adopted_interim()
    _, updated = decide(field(), state, run_identity=IDENTITY)
    assert updated['policy']['active'] == active
    with chart.map_context(updated['policy']):
        assert len(chart.castles(3)) == 9


def test_adopted_interim_target_never_indexes_an_identity_invalidated_map():
    state, active = caller_adopted_interim()
    mem = state['policy']
    mem['_run_identity'] = None
    screen = Screen([], None, '', kind='map_target', marker=GRIN)
    with chart.map_context(mem):
        assert policy.target_step(screen, mem, field()) == []
    assert mem['active'] is None and mem['_map_registration_hold'] is True
    assert mem['orders'].get(active) in (None, 'pending')
    assert mem['_records'][-1]['decision'] == 'map_coordinates_unavailable'


def test_follow_general_never_overrides_to_an_unregistered_observed_castle():
    mem = initial_route_receipt()
    mem.update(captured=['メガパタ'], garrison={'メガパタ': ['ゼウス']})
    with chart.map_context(mem):
        order = policy.next_order(mem)
        assert mem.get('source_override', {}).get('3-Z1') != 'メガパタ'
        assert order is None or policy._source(order, mem) in chart.castles(3)
        policy.map_step(parse(field()), mem, field())  # never KeyError


def test_runtime_ready_checks_the_effective_source_override():
    mem = initial_route_receipt()
    mem['source_override'] = {'3-Z1': 'メガパタ'}
    with chart.map_context(mem):
        assert not policy._ready(chart.CHAPTER_3_ORDERS[0], mem)


@pytest.mark.parametrize('scenario', ['empty', 'lost', 'general_at_unregistered_castle'])
def test_unselectable_partial_order_does_not_preempt_discovery(scenario):
    mem = initial_route_receipt()
    mem['garrison'] = {'アルマムーン': []}
    if scenario == 'lost':
        mem['home_lost'] = True
    elif scenario == 'general_at_unregistered_castle':
        mem['captured'] = ['メガパタ']
        mem['garrison']['メガパタ'] = ['ゼウス']
    for _ in range(5):
        actions = feed(mem, field(GRIN))
        assert actions is not None
    assert mem['map_discovery']['candidate'] == 'メガパタ'


def test_native_frames_register_named_home_then_release_only_the_observed_first_sortie():
    mem = home_receipt()
    assert not chart.orders(3)  # no process-global mutation
    with chart.map_context(mem):
        assert chart.castles(3) == {'アルマムーン': HOME}
        assert not chart.orders(3)
    walk_to_grin(mem)
    assert feed(mem, status('グリン')) == []
    assert feed(mem, status('グリン')) == [policy.pad('b')]
    with chart.map_context(mem):
        assert chart.castles(3) == {'アルマムーン': HOME, 'グリン': GRIN}
        assert [o['step'] for o in chart.orders(3)] == ['3-Z1']
        assert policy.next_order(mem)['target'] == 'グリン'
        assert policy.nav_step(parse(field()), mem, field(), HOME, 'アルマムーン') == 'arrived'
    assert mem['variant'] == 'chart'
    assert not mem.get('sorties')  # surveying alone dispatches nobody
    logs = [r for r in mem['_records'] if r['decision'] == 'map_castle_confirmed']
    assert len(logs) == 2 and all(r['observed_metric']['field_frame'] for r in logs)


def test_orchestrator_uses_the_run_map_to_start_the_existing_chapter_three_sortie():
    state = {'policy': memory()}
    for frame in (field(), field(), status('アルマムーン'), status('アルマムーン'), field()):
        _, state = decide(frame, state, run_identity=IDENTITY)
    assert survey.verified_castles(state['policy'], 3) == {'アルマムーン': HOME}
    cursor = list(HOME)
    for _ in range(40):
        actions, state = decide(field(tuple(cursor)), state, run_identity=IDENTITY)
        if actions == [policy.pad('a')]:
            break
        for action in actions:
            d = action['buttons'][0]
            cursor[0 if d in ('left', 'right') else 1] += round(action['hold_ms']*60/1000)*(1 if d in ('right', 'down') else -1)
    else:
        pytest.fail('orchestrator never opened the candidate')
    for frame in (status('グリン'), status('グリン'), field(GRIN)):
        _, state = decide(frame, state, run_identity=IDENTITY)
    _, state = decide(field(GRIN), state, run_identity=IDENTITY)
    assert state['policy']['active'] == '3-Z1'
    assert state['policy']['variant'] == 'chart'
    assert not state['policy'].get('sorties')
    assert chart.castles(3) == {}


def test_centered_single_roof_bootstrap_reaches_two_roof_calibration_and_chapter_three_order():
    # Same source delta as chapter 3, but the home is centred: Grin starts
    # outside the native frame. The simulated camera clamps the free cursor
    # at the existing native bounds; commanded motion is not a map receipt.
    home, grin = (128, 104), (272, 264)
    roofs = ((home, 'own'), (grin, 'enemy'))
    cursor, camera = list(home), [0, 0]
    state = {'policy': memory()}
    first = field(tuple(cursor), roofs, tuple(camera))
    assert len([r for r in castle_roofs(first) if not r['clipped']]) == 1
    actions, state = decide(first, state, run_identity=IDENTITY)
    assert actions and all(a['buttons'][0] in {'left', 'right', 'up', 'down'} for a in actions)
    assert not state['policy'].get('map_registration')
    panel = None
    first_calibrated_frame = None
    for _ in range(100):
        for action in actions:
            button = action['buttons'][0]
            if button in {'left', 'right', 'up', 'down'}:
                assert panel is None
                distance = round(action['hold_ms']*60/1000)
                assert 1 <= distance <= survey.MAX_HOLD
                axis = 0 if button in {'left', 'right'} else 1
                proposed = cursor[axis]+distance*(1 if button in {'right', 'down'} else -1)
                clamped = max(8, min((232, 200)[axis], proposed))
                camera[axis] += proposed-clamped
                cursor[axis] = clamped
            elif button == 'a':
                assert first_calibrated_frame is not None  # no blind A from one roof
                at = tuple(cursor[i]+camera[i] for i in (0, 1))
                panel = 'アルマムーン' if at == home else 'グリン' if at == grin else None
                assert panel is not None
            elif button == 'b':
                panel = None
            else:
                pytest.fail(f'unexpected bootstrap input: {button}')
        frame = status(panel) if panel else field(tuple(cursor), roofs, tuple(camera))
        if not panel and len([r for r in castle_roofs(frame) if not r['clipped']]) >= 2:
            first_calibrated_frame = first_calibrated_frame or frame.digest()
        actions, state = decide(frame, state, run_identity=IDENTITY)
        mem = state['policy']
        if first_calibrated_frame is None:
            assert not mem.get('map_registration')
            assert not (mem.get('map_discovery') or {}).get('origin_frame')
        if mem.get('active') == '3-Z1':
            break
    else:
        pytest.fail('single-roof bootstrap never reached a valid chapter-3 order')
    cells = survey.verified_castles(mem, 3)
    assert set(cells) == {'アルマムーン', 'グリン'}
    assert tuple(cells['グリン'][i]-cells['アルマムーン'][i] for i in (0, 1)) == (144, 160)
    assert mem['map_registration']['origin_frame'] == first_calibrated_frame
    assert not mem.get('sorties') and not chart.castles(3)


@pytest.mark.parametrize('moving_cursor', [False, True])
def test_single_roof_bootstrap_stops_without_two_roofs_and_never_confirms(moving_cursor):
    mem = memory()
    home = (128, 104)
    pads = []
    for i in range(survey.MAX_BOOTSTRAP+5):
        frame = field((128+(i%2 if moving_cursor else 0), 104), ((home, 'own'),))
        actions = feed(mem, frame)
        assert actions is not None
        pads.extend(a['buttons'][0] for a in actions)
        assert not mem.get('map_registration')
        assert not mem['map_discovery'].get('origin_frame')
    assert pads and set(pads) <= {'left', 'right', 'up', 'down'}
    assert len(pads) <= (survey.MAX_BOOTSTRAP if moving_cursor else 3)
    assert mem['map_discovery']['bootstrap']['done'] is True
    assert any(r['decision'] == 'map_bootstrap_exhausted' for r in mem['_records'])


def test_survey_hold_cannot_turn_into_the_generic_frozen_screen_confirm():
    mem = home_receipt()
    walk_to_grin(mem)
    mem['map_discovery']['probe'].update(age=2*survey.MAX_PANEL, closing=True)
    frame = status('グリン')
    state = {'policy': mem, 'no_input_streak': NO_INPUT_HOLD_MAX+1,
             'no_input_frames': [frame.digest()], 'phase': 'field_menu'}
    actions, updated = decide(frame, state, run_identity=IDENTITY)
    assert actions == []
    assert not any(r['decision'] == 'no_input_hold_released' for r in updated['_records'])


def test_skipped_foreground_observations_invalidate_geometry_before_continuing():
    mem = home_receipt()
    mem['tick'] += 5  # e.g. a higher-priority monthly flow owned these frames
    assert feed(mem, field()) == []
    assert 'roof_world' not in mem['map_discovery']
    assert 'グリン' not in survey.verified_castles(mem, 3)


@pytest.mark.parametrize('label', ['メガパタ', 'ハドリバーグ', 'グリソ'])
def test_candidate_mismatch_foreign_chapter_and_unknown_name_never_become_coordinates(label):
    mem = home_receipt()
    walk_to_grin(mem)
    for _ in range(3):
        feed(mem, status(label))
    assert survey.verified_castles(mem, 3) == {'アルマムーン': HOME}
    with chart.map_context(mem):
        assert not chart.orders(3)


def test_public_hint_without_a_roof_never_authorizes_confirm_or_a_coordinate(monkeypatch):
    mem = home_receipt()
    # Preserve two support roofs, but put the expected search point in water.
    monkeypatch.setattr(survey, '_candidate', lambda *_: ('グリン', (130, 100)))
    actions = feed(mem, field((130, 100)))
    assert policy.pad('a') not in (actions or [])
    assert survey.verified_castles(mem, 3) == {'アルマムーン': HOME}


def test_two_distinct_observation_ticks_required_and_status_must_be_full():
    mem = memory()
    feed(mem, field())
    with chart.map_context(mem):
        assert survey.step(parse(field()), mem, field()) == []
    feed(mem, field())
    frame = status('アルマムーン')
    feed(mem, frame)
    with chart.map_context(mem):
        assert survey.step(parse(frame), mem, frame) == []  # same tick
    assert not survey.verified_castles(mem, 3)
    bad = Canvas(); bad.text(16, 31, 'アルマムーンじょう')
    for _ in range(2):
        feed(mem, bad.frame())
    assert not survey.verified_castles(mem, 3)


def test_existing_native_status_fixture_matches_only_its_real_chapter():
    frame = read_png(Path(__file__).parent/'fixtures/hanjuku/g706-resume-castle-status.png')
    screen = parse(frame)
    assert survey._label(screen, 2) == 'スペランザ'
    assert survey._label(screen, 3) is None


@pytest.mark.parametrize('decision', ['map_search_candidate', 'map_castle_confirmed',
                                     'map_registration_rejected', 'map_reanchored', None])
def test_registration_evidence_uses_the_existing_bounded_native_frame_ring(tmp_path, decision):
    import importlib.util
    import json
    path = Path(__file__).resolve().parents[1]/'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('map_registration_snapshot_test', path)
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    frame = field()
    state = {'step': 121, 'screen_kind': 'map', 'policy': {
        'map_discovery': {'probe': {'age': 0}} if decision is None else {}}}
    bot.persist(tmp_path, state, [{'decision': decision}] if decision else [],
                {'hanjuku': IDENTITY}, actions=[], frame_sha256=frame.digest(), frame=frame)
    row = json.loads((tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()[0])
    assert row['snapshot'] == 'decision-001.png'
    assert read_png(tmp_path/'hanjuku_frames'/row['snapshot']).digest() == frame.digest()


@pytest.mark.parametrize('before,after,expected', [
    ([(60, 28), (204, 188)], [(44, 28), (188, 188)], (16, 0)),
    ([(0, 0), (32, 0), (64, 0)], [(0, 0), (32, 0)], None),
    ([(0, 0)], [(0, 0), (32, 0)], None),
    ([(0, 0), (32, 16)], [(80, 80), (112, 96)], (-80, -80)),
])
def test_roof_translation_requires_unique_two_anchor_support(before, after, expected):
    assert survey.translation(before, after) == expected


def test_scroll_is_measured_from_roofs_and_a_wrong_input_budget_invalidates_tracking():
    state = {}
    roofs = castle_roofs(field())
    assert survey._pose(state, roofs, HOME) == (0, 0)
    state['origin_frame'] = 'a'*64
    state['last_input'] = [0, 1, 16]
    scrolled = castle_roofs(field(HOME, camera=(16, 0)))
    assert survey._pose(state, scrolled, HOME) == (16, 0)
    assert state['cursor_world'] == [76, 28]
    state['last_input'] = [0, 1, 1]
    assert survey._pose(state, castle_roofs(field(HOME, camera=(32, 0))), HOME) is None


def test_a_gap_requires_known_native_name_before_reanchoring_or_new_registration():
    mem = home_receipt()
    feed(mem, status('グリン'))  # unowned menu invalidates continuous tracking
    assert 'roof_world' not in mem['map_discovery']
    assert feed(mem, field()) == []
    assert feed(mem, field()) == [policy.pad('a')]
    assert feed(mem, status('アルマムーン')) == []
    assert feed(mem, status('アルマムーン')) == [policy.pad('b')]
    assert any(r['decision'] == 'map_reanchored' for r in mem['_records'])
    assert survey.verified_castles(mem, 3) == {'アルマムーン': HOME}


@pytest.mark.parametrize('change', ['identity', 'chapter', 'digest', 'collision', 'foreign', 'readings'])
def test_invalid_registration_envelope_never_reaches_chart_or_orders(change):
    mem = home_receipt()
    data = mem['map_registration']
    if change == 'identity':
        mem['_run_identity']['lease_id'] = 'different-lease'
    elif change == 'chapter':
        mem['chapter'] = 4
    elif change == 'digest':
        data['castles']['アルマムーン']['status_frame'] = ''
    elif change == 'readings':
        data['castles']['アルマムーン']['readings'] = 1
    else:
        name = 'ハドリバーグ' if change == 'foreign' else 'グリン'
        data['castles'][name] = {**data['castles']['アルマムーン'], 'name': name}
    with chart.map_context(mem):
        assert not chart.castles(3) and not chart.orders(3)


def test_map_context_resets_on_exceptions_and_isolates_threads_and_nested_runs():
    mem = home_receipt()
    with chart.map_context(mem):
        assert chart.castles(3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(chart.castles, 3).result() == {}
        with pytest.raises(RuntimeError):
            with chart.map_context(memory()):
                assert chart.castles(3) == {}
                raise RuntimeError('test')
        assert chart.castles(3)
    assert chart.castles(3) == {}
    campaign._enter_chapter(mem, 4, reason='synthetic chapter transition')
    assert 'map_registration' not in mem and 'map_discovery' not in mem


def test_new_run_discards_the_old_native_map_and_adopted_active_order():
    mem = home_receipt()
    walk_to_grin(mem)
    feed(mem, status('グリン')); feed(mem, status('グリン'))
    order = dict(chart.CHAPTER_3_ORDERS[0])
    mem.update(chart_plan={'orders': [order]}, active=order['step'])
    _, updated = decide(field(), {'policy': mem}, run_identity={**IDENTITY, 'lease_id': 'new-lease'})
    assert not updated['policy'].get('map_registration')
    assert not updated['policy'].get('chart_plan') and not updated['policy'].get('active')
    assert not chart.castles(3)


def test_runtime_plan_with_unmeasured_endpoint_stays_unready():
    mem = home_receipt()
    with chart.map_context(mem):
        assert not policy._ready(chart.CHAPTER_3_ORDERS[0], mem)


def test_complete_synthetic_registration_returns_exhausted_orders_to_the_existing_owner():
    mem = home_receipt()
    # Planted complete receipt tests ownership handoff, not native geography.
    proof = mem['map_registration']['castles']['アルマムーン']
    from docich.hanjuku_map_sources import SOURCE_CASTLES
    mem['map_registration']['castles'] = {
        n: {**proof, 'name': n, 'position': [60+32*i, 28+48*i]}
        for i, n in enumerate(SOURCE_CASTLES[3])}
    mem['orders'] = {o['step']: 'launched' for o in chart.all_orders(3)}
    assert len(survey.verified_castles(mem, 3)) == 9
    assert feed(mem, field()) is None


@pytest.mark.parametrize('chapter', [4, 9])
def test_generic_multi_site_and_moving_boss_are_never_static_navigation_points(chapter, monkeypatch):
    boss = chart.boss_castle(chapter)
    home = chart.home_castle(chapter)
    monkeypatch.setitem(chart.CASTLES, chapter, {home: (0, 0), boss: (64, 64)})
    assert boss not in chart.castles(chapter)
    assert not any(o['target'] == boss for o in chart.orders(chapter))
    order = {'target': boss, 'source': home, 'after': None}
    mem = {'chapter': chapter, 'captured': list(chart.CASTLE_NAMES[chapter])}
    assert not policy._ready(order, mem)


def test_four_boss_sites_do_not_become_ordinary_interim_targets(monkeypatch):
    home = chart.home_castle(4)
    monkeypatch.setitem(chart.CASTLES, 4, {home: (0, 0), **{
        site: (64*(i+1), 64) for i, site in enumerate(chart.boss_sites(4))}})
    mem = {'chapter': 4, 'garrison': {home: ['どうし', 'ゼウス']}}
    assert not any(chart.is_boss_castle(o['target']) for o in policy.interim_candidates(mem).values())
    for site in chart.boss_sites(4):
        assert not policy._ready({'target': site, 'source': home, 'after': None}, mem)


def king_battle(mem, king, *, site='ボス', entry=True, side='attack', win=True):
    mem['battle'] = {'away': 1, 'ally': 'ゼウス', 'enemy': king, 'castle': site,
                     'side': side, 'enemy_hp': 0 if win else 70, 'ally_hp': 40 if win else 0,
                     **({'entry_evidence': 'measured_boss_entry'} if entry else {})}
    policy.battle_end(mem, 'map')


def test_four_distinct_season_king_wins_are_not_owned_castles_or_a_single_boss_win():
    from docich.hanjuku_map_sources import SEASON_KINGS
    mem = {'chapter': 4, 'tick': 100, 'captured': ['マラコビア'], 'garrison': {}}
    kings = sorted(SEASON_KINGS)
    king_battle(mem, kings[0], site=chart.boss_sites(4)[0])
    king_battle(mem, kings[0], site=chart.boss_sites(4)[0])
    assert len(mem['boss_victories']) == 1 and not mem.get('boss_defeated')
    assert mem['boss_victories'][kings[0]]['site'] == chart.boss_sites(4)[0]
    for king in kings[1:]:
        king_battle(mem, king)
    assert set(mem['boss_victories']) == SEASON_KINGS
    assert mem['boss_defeated'] == 4 and mem['chapter'] == 4
    assert mem['captured'] == ['マラコビア'] and mem['garrison'] == {}
    assert mem['boss_victories'][kings[-1]]['site'] is None


@pytest.mark.parametrize('changes', [{'entry': False}, {'side': 'defense'}, {'win': False},
                                     {'side': 'defense', 'win': False}])
def test_unproven_season_king_result_is_never_victory_or_ownership(changes):
    mem = {'chapter': 4}
    king_battle(mem, 'サマカンテ', **changes)
    assert not mem.get('boss_victories') and not mem.get('boss_defeated')
    assert not mem.get('captured')
    assert not mem.get('garrison')
    assert not any((r.get('resulting_event') or '').startswith(('captured:', 'lost:'))
                   for r in mem.get('_records', []))


def adopted_interim_confirm_frame():
    """The reviewer's #2545 P2 frame: a real adopted interim, real panels.

    The sortie-confirm frame is built only from the adopted order's cards
    and actor plus the measured hand block; no order is planted by hand.
    """
    state, active = caller_adopted_interim()
    order = state['policy']['chart_adjust']['interim_order']
    panel = actor_panel(measured_loaded_sortie(tuple(order['cards'])), order['general'])
    canvas = Canvas()
    for line in panel.lines:
        for x, ch in line.cells:
            if ch != UNKNOWN:
                canvas.text(x, line.y, ch)
    for y in range(105, 119):
        for x in range(138, 157):
            canvas.put(x, y, HAND[(x + y) % 3])
    frame = canvas.frame()
    assert parse(frame).kind == 'sortie_confirm'
    return state, active, frame


@pytest.mark.parametrize('identity', [None, {}, {**IDENTITY, 'game': 'other'},
                                     {**IDENTITY, 'generation': 0}, {**IDENTITY, 'lease_id': ''}])
def test_deploy_continuation_stops_unsent_order_when_identity_fails(identity):
    """The #2545 P2 regression: no KeyError on a continuation screen.

    An adopted interim order can outlive the run map receipt that verified
    its endpoints. The deploy continuation (sortie confirm here) runs
    without ``_ready``, so it must re-verify both endpoints itself: a
    missing/invalid run identity stops the unsent order, returns no input
    and rewrites no order status instead of approving or crashing.
    """
    state, active, frame = adopted_interim_confirm_frame()
    actions, updated = decide(frame, state, run_identity=identity)
    assert actions == []
    assert updated['policy']['active'] is None
    assert updated['policy']['orders'].get(active) in (None, 'pending')
    assert not updated['policy'].get('sorties')
    assert any(r['decision'] == 'order_precondition_lost' for r in updated['_records'])
    with chart.map_context(updated['policy']):
        assert not chart.castles(3)
    # The next observation on the same screen backs out through the
    # order-less path, so the hold cannot wedge the screen forever.
    follow, followup = decide(frame, updated, run_identity=identity)
    assert follow == [policy.pad('b')]
    assert followup['policy']['active'] is None


def test_deploy_continuation_confirms_with_valid_identity_control():
    """The identity gate must not hold a fully verified continuation."""
    state, active, frame = adopted_interim_confirm_frame()
    actions, updated = decide(frame, state, run_identity=IDENTITY)
    assert actions == [policy.pad('a')]
    assert updated['policy']['active'] == active


@pytest.mark.parametrize('kind', ['castle_menu', 'general_list', 'card_select',
                                  'sortie_confirm'])
def test_deploy_screens_recheck_endpoints_per_observation(kind):
    """Every non-map continuation entrance applies the same condition."""
    state, active, frame = adopted_interim_confirm_frame()
    mem = state['policy']
    mem['_run_identity'] = None       # the key stays present: None must not opt out
    screen = Screen(lines=[], hand=None, text='')
    screen.kind = kind
    assert policy.deploy_step(screen, mem) == []
    assert mem['active'] is None
    assert mem['orders'].get(active) in (None, 'pending')
    assert mem.get('_map_registration_hold') is True
    assert any(r['decision'] == 'order_precondition_lost' for r in mem['_records'])
