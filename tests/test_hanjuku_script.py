"""Token-free policy, real PNG input, generation and terminal evidence contracts."""
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from docich import hanjuku_run
from docich.adapters.base import AdapterError
from docich.hanjuku_bot import decide, classify, _TITLE_ROWS
from docich.hanjuku_pixels import Frame, read_png

IDENTITY={'game':'hanjuku-hero','runtime_id':'g1-abcdef','generation':1,'lease_id':'lease'}


def frame(color=(30,90,50)):
    return Frame(256,224,bytes(color)*(256*224))


def test_png_round_trip_and_corruption_rejected(tmp_path):
    source=frame()
    path=tmp_path/'screen.png'
    path.write_bytes(source.png_bytes())
    assert read_png(path)==source
    raw=bytearray(path.read_bytes())
    raw[-5]^=1
    path.write_bytes(raw)
    with pytest.raises(ValueError): read_png(path)


def test_native_unfiltered_png_keeps_every_pixel_and_same_size_is_noop(tmp_path):
    size = 897 * 672 * 3
    source = Frame(897, 672, (bytes(range(256)) * ((size + 255) // 256))[:size])
    assert len(source.rgb) == source.width * source.height * 3
    path = tmp_path / 'native.png'
    path.write_bytes(source.png_bytes())
    assert read_png(path) == source
    normalized = source.resized()
    assert normalized.resized() is normalized


def test_terminal_only_after_300_continuous_seconds_and_durable_log(tmp_path):
    for now in range(0,300,10):
        result=hanjuku_run.observe(tmp_path,IDENTITY,frame(),now=now,wall=1000+now)
        assert result['terminal_reason'] is None
    result=hanjuku_run.observe(tmp_path,IDENTITY,frame(),now=300,wall=1300)
    assert result['terminal_reason']=='screen_stalled'
    assert result['unchanged_seconds']==300
    assert hanjuku_run.terminal(tmp_path,IDENTITY)==result
    logs=[json.loads(line) for line in (tmp_path/'hanjuku_events.jsonl').read_text().splitlines()]
    assert logs[-1]['terminal_reason']=='screen_stalled'
    assert (tmp_path/'hanjuku_frames'/logs[-1]['snapshot']).is_file()


@pytest.mark.parametrize('break_kind',['change','gap','pause','clock_rewind'])
def test_sample_break_resets_stasis_instead_of_false_game_over(tmp_path,break_kind):
    for now in range(0,291,10):
        hanjuku_run.observe(tmp_path,IDENTITY,frame(),now=now,wall=1000+now)
    result=hanjuku_run.observe(tmp_path,IDENTITY,
        frame((40,90,50)) if break_kind=='change' else frame(),
        now={'gap':400,'clock_rewind':100}.get(break_kind,300),wall=1400,
        playing=break_kind!='pause')
    assert result['terminal_reason'] is None
    assert result['unchanged_seconds']==0


def test_animated_screen_keeps_playing_beyond_twenty_minutes(tmp_path):
    for now in range(0,1301,10):
        result=hanjuku_run.observe(tmp_path,IDENTITY,frame((now%255,90,50)),now=now,wall=1000+now)
    assert result['terminal_reason'] is None
    assert result['observations']==131


def test_other_generation_or_symlink_cannot_supply_terminal_evidence(tmp_path):
    hanjuku_run.observe(tmp_path,IDENTITY,frame(),now=0,wall=1000)
    with pytest.raises(AdapterError,match='identity'):
        hanjuku_run.terminal(tmp_path,{**IDENTITY,'generation':2})
    log=tmp_path/'hanjuku_events.jsonl'
    log.unlink()
    log.symlink_to(tmp_path/hanjuku_run.RUN_FILE)
    with pytest.raises(AdapterError,match='symlink'):
        hanjuku_run.event(tmp_path,{'event':'test'})


def test_actions_are_bounded_pad_only_and_black_transition_waits():
    state={}
    for index in range(100):
        actions,state=decide(frame((40,140,20)),state)
        for action in actions:
            assert action['type']=='pad'
            assert action['buttons'][0] in {'a','b','up','down','left','right','start'}
            assert 0<action['hold_ms']<=1800
    assert decide(frame((0,0,0)),state)[0]==[]


def test_scripted_corner_defers_improvement_without_spawning(manager):
    # Reuse the real manager fixture from the registration contract.
    state={'game':'hanjuku-hero','date':'2026-09-22'}
    manager._spawn_improve_once(state)
    assert state['improve_job']=={'spawned':False,'reason':'hanjuku-improvement-deferred'}


from test_hanjuku_retro_registration import manager


def title_frame():
    rgb=bytearray(256*224*3)
    for y,row in enumerate(_TITLE_ROWS,192):
        for bit in range(120):
            if row & (1 << (119-bit)):
                i=(y*256+68+bit)*3
                rgb[i:i+3]=b"\xff\xff\xff"
    return Frame(256,224,bytes(rgb))


def test_title_return_requires_a_started_game_and_multiple_timed_observations(tmp_path):
    title=title_frame()
    assert classify(title)=='title'
    for now in range(4):
        run=hanjuku_run.observe(tmp_path,IDENTITY,title,now=now,wall=1000+now)
        assert not run['terminal_candidate']
        assert run['terminal_reason'] is None
    # An attract-demo battle alone is insufficient to establish a played run.
    run=hanjuku_run.observe(tmp_path,IDENTITY,frame(),now=4,wall=1004)
    run=hanjuku_run.observe(tmp_path,IDENTITY,title,now=5,wall=1005)
    assert not run['terminal_candidate']
    from docich.game_switch import atomic_write_json
    atomic_write_json(tmp_path/hanjuku_run.RUN_FILE,{**run,'name_entered':True,'gameplay_seen':True})
    for now in (6,7):
        run=hanjuku_run.observe(tmp_path,IDENTITY,title,now=now,wall=1000+now)
        assert run['terminal_candidate'] and run['terminal_reason'] is None
    run=hanjuku_run.observe(tmp_path,IDENTITY,title,now=8,wall=1008)
    assert run['terminal_reason']=='game_over'
    assert run['terminal_evidence']=='title_return_after_gameplay'
    # Terminal evidence is latched even if a later screen is different.
    assert hanjuku_run.observe(tmp_path,IDENTITY,frame(),now=9,wall=1009)==run


def test_waiting_boundary_keeps_bot_live_until_stasis_then_needs_no_save(adapter):
    import time
    from docich import game_switch
    from docich.retroarch_boundary import read_record, BOUNDARY_FILE, require_input_open
    adapter.game.raw['hanjuku']={'script_bot':True}
    identity=hanjuku_run.runtime_identity(adapter.spec)
    with pytest.raises(game_switch.DeadlineExceededError):
        adapter.request_round_boundary('script-end',time.monotonic()+.02,None)
    require_input_open(adapter.spec.runtime_dir)
    for now in range(0,301,10):
        hanjuku_run.observe(adapter.spec.runtime_dir,identity,frame(),now=now,wall=1000+now)
    adapter.request_round_boundary('script-end',time.monotonic()+1,None)
    boundary=read_record(adapter.spec.runtime_dir/BOUNDARY_FILE)
    assert boundary['outcome']=='screen_stalled' and 'checkpoint' not in boundary
    with pytest.raises(AdapterError,match='holds input'):
        require_input_open(adapter.spec.runtime_dir)
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)


def test_scripted_corner_ignores_old_elapsed_deadline(manager,monkeypatch):
    from datetime import datetime,timezone
    monkeypatch.setattr(manager,'_read_state',lambda:{'status':'active','game':'hanjuku-hero','ends_at':'2000-01-01T00:00:00+00:00'})
    from unittest.mock import Mock
    finish=Mock()
    monkeypatch.setattr(manager,'_finish_locked',finish)
    manager._reconcile_stale_locked(datetime.now(timezone.utc))
    finish.assert_not_called()


from test_retroarch_safe_boundary import adapter


def test_map_without_measured_cursor_never_sends_blind_orders():
    """v1 confirmed and marched along a fixed route; v2 waits until the
    chart policy can read the cursor, the menus and the target castle."""
    rgb=bytearray(frame((40,140,20)).rgb)
    for y in (16,17):
        for x in range(35,110):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((255,56,57))
    menu=Frame(256,224,bytes(rgb))
    assert classify(menu)=='field_menu'
    state={}
    for screen in (frame((40,140,20)),menu,frame((40,140,20)),frame((40,140,20))):
        actions,state=decide(screen,state)
        assert actions==[]


def test_green_map_encounter_prompt_is_confirmed_not_waited_on():
    rgb=bytearray(frame((40,140,20)).rgb)
    for y in (16,17):
        for x in range(16,160):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((123,255,57))
    for x in (16,17):
        for y in range(16,80):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((123,255,57))
    prompt=Frame(256,224,bytes(rgb))
    assert classify(prompt)=='battle_intro'
    actions,_=decide(prompt,{'field_step':4})
    assert actions[0]['buttons']==['a']


def test_corner_waits_past_deadline_then_restores_only_on_terminal(manager, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    state={'status':'active','game':'hanjuku-hero','previous_game':'sorengame','bot_identity':dict(IDENTITY),
           'ends_at':'2000-01-01T00:00:00+00:00'}
    observations=iter([
        {'phase':'battle','terminal_reason':None,'actions_sent':5},
        {'phase':'field','terminal_reason':None,'actions_sent':6},
        {'phase':'field','terminal_reason':'screen_stalled','actions_sent':6,'unchanged_seconds':300},
    ])
    manager.store.canonical.load=Mock(return_value=({'active':IDENTITY},False))
    monkeypatch.setattr('docich.agent.fence.shared_section',lambda root,fn:fn())
    monkeypatch.setattr('docich.adapters.make_adapter',lambda *a,**kw:SimpleNamespace(
        observe=lambda:SimpleNamespace(meta={'hanjuku':next(observations)})))
    monkeypatch.setattr(manager,'_rotation_stop_result',lambda:None)
    monkeypatch.setattr(manager,'_read_state',lambda:dict(state))
    monkeypatch.setattr(manager,'_write_state',lambda update:state.update(update))
    monkeypatch.setattr(manager,'_repair_active_agent',Mock())
    sleep=Mock()
    monkeypatch.setattr(manager,'_sleep',sleep)
    finish=Mock(return_value='restored')
    monkeypatch.setattr(manager,'_finish_locked',finish)
    verified=Mock(return_value={'terminal_evidence':None,'generation':IDENTITY['generation']})
    monkeypatch.setattr('docich.hanjuku_run.terminal',verified)
    assert manager._wait_and_finish(state)=='restored'
    assert verified.call_args.args[1]==IDENTITY
    assert sleep.call_count==2
    finish.assert_called_once()
    assert state['ends_at'] is None and state['end_reason']=='screen_stalled'
    assert state['bot_runtime_id']==IDENTITY['runtime_id']


def test_corrupt_terminal_record_cannot_authorize_teardown(tmp_path):
    from docich.game_switch import atomic_write_json
    atomic_write_json(tmp_path/hanjuku_run.RUN_FILE,dict(IDENTITY,
        terminal_reason='screen_stalled',unchanged_seconds=0,frame_sha256='0'*64))
    with pytest.raises(AdapterError,match='terminal evidence'):
        hanjuku_run.terminal(tmp_path,IDENTITY)


@pytest.mark.parametrize('busy_kind', ['input', 'switch'])
def test_corner_observation_contention_retries_then_finishes_only_on_game_over(manager, monkeypatch, busy_kind):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from docich.game_switch import DeadlineExceededError, GameSwitchBusyError
    from docich.naming import runtime_directory
    state={'status':'active','game':'hanjuku-hero','previous_game':'sorengame','bot_identity':dict(IDENTITY)}
    runtime=runtime_directory(manager.g.state_dir,IDENTITY['runtime_id'])
    runtime.mkdir(parents=True)
    error=DeadlineExceededError('busy') if busy_kind=='input' else GameSwitchBusyError('busy')
    observe=Mock(side_effect=[error,SimpleNamespace(meta={'hanjuku':{
        'phase':'title','terminal_reason':'game_over','actions_sent':5}})])
    manager.store.canonical.load=Mock(return_value=({'active':IDENTITY},False))
    monkeypatch.setattr('docich.agent.fence.shared_section',lambda root,fn:fn())
    monkeypatch.setattr('docich.adapters.make_adapter',lambda *a,**kw:SimpleNamespace(observe=observe))
    monkeypatch.setattr(manager,'_rotation_stop_result',lambda:None)
    monkeypatch.setattr(manager,'_read_state',lambda:dict(state))
    monkeypatch.setattr(manager,'_write_state',lambda update:state.update(update))
    sleep=Mock()
    monkeypatch.setattr(manager,'_sleep',sleep)
    finish=Mock(return_value='restored')
    monkeypatch.setattr(manager,'_finish_locked',finish)
    monkeypatch.setattr('docich.hanjuku_run.terminal',Mock(return_value={
        'terminal_evidence':'title_return_after_gameplay','generation':IDENTITY['generation']}))
    assert manager._wait_hanjuku(state)=='restored'
    assert finish.call_args.args[0]['terminal_evidence']=='title_return_after_gameplay'
    sleep.assert_called_once_with(2.)
    finish.assert_called_once()
    assert observe.call_count==2 and manager.store.canonical.load.call_count==2
    assert state['end_reason']=='game_over'
    # #1085 L4: the chart review runs once on game over, before teardown.
    assert finish.call_args.args[0]['chart_review']['file']=='hanjuku_chart_review.json'
    assert (runtime/'hanjuku_chart_review.json').exists()
    retry=json.loads((runtime/'hanjuku_events.jsonl').read_text())
    assert retry['event']=='observation_retry' and 'terminal_reason' not in retry


@pytest.mark.parametrize('evidence',[None,'invalid'])
def test_corner_never_restores_on_unverified_terminal_evidence(manager, monkeypatch, evidence):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from docich.retro_corner import RetroCornerError
    state={'status':'active','game':'hanjuku-hero','previous_game':'sorengame','bot_identity':dict(IDENTITY)}
    manager.store.canonical.load=Mock(return_value=({'active':IDENTITY},False))
    monkeypatch.setattr('docich.agent.fence.shared_section',lambda root,fn:fn())
    monkeypatch.setattr('docich.adapters.make_adapter',lambda *a,**kw:SimpleNamespace(
        observe=lambda:SimpleNamespace(meta={'hanjuku':{'phase':'title','terminal_reason':'game_over'}})))
    monkeypatch.setattr(manager,'_rotation_stop_result',lambda:None)
    monkeypatch.setattr(manager,'_read_state',lambda:dict(state))
    monkeypatch.setattr(manager,'_write_state',lambda update:state.update(update))
    finish=Mock()
    monkeypatch.setattr(manager,'_finish_locked',finish)
    if evidence=='invalid':
        # No durable run record in the runtime: identity/evidence cannot verify.
        check=Mock(side_effect=AdapterError('invalid Hanjuku terminal evidence'))
    else:
        check=Mock(return_value=None)
    monkeypatch.setattr('docich.hanjuku_run.terminal',check)
    with pytest.raises((AdapterError,RetroCornerError)):
        manager._wait_hanjuku(state)
    finish.assert_not_called()


def test_corner_unknown_observation_failure_is_not_silently_retried(manager, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    manager.store.canonical.load=Mock(return_value=({'active':IDENTITY},False))
    monkeypatch.setattr('docich.agent.fence.shared_section',lambda root,fn:fn())
    monkeypatch.setattr('docich.adapters.make_adapter',lambda *a,**kw:SimpleNamespace(
        observe=Mock(side_effect=AdapterError('ownership unknown'))))
    monkeypatch.setattr(manager,'_rotation_stop_result',lambda:None)
    with pytest.raises(AdapterError,match='ownership unknown'):
        manager._wait_hanjuku({'status':'active','game':'hanjuku-hero','bot_identity':dict(IDENTITY)})


def test_optional_concert_exits_instead_of_selecting_the_same_track():
    rgb=bytearray(frame((160,110,60)).rgb)
    for y in range(150,208):
        for x in range(18,236):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((16,72,57))
    for y in range(128):
        for x in (*range(32),*range(224,256)):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((200,0,0))
    concert=Frame(256,224,bytes(rgb))
    assert classify(concert)=='concert'
    assert decide(concert,{})[0][0]['buttons']==['a']
    for x in range(200,240):
        i=(184*256+x)*3
        rgb[i:i+3]=bytes((197,141,74))
    assert decide(Frame(256,224,bytes(rgb)),{})[0][0]['buttons']==['b']


def test_red_curtain_merchant_exits_price_list_then_advances_farewell():
    rgb=bytearray(frame((160,110,60)).rgb)
    for y in range(128):
        for x in (*range(32),*range(224,256)):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((200,0,0))
    for y in range(150,208):
        for x in range(18,236):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((16,72,57))
    farewell=Frame(256,224,bytes(rgb))
    for y in range(30,145):
        for x in range(120,236):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((16,72,57))
    shop=Frame(256,224,bytes(rgb))
    assert classify(shop)=='shop'
    actions,state=decide(shop,{})
    assert actions[0]['buttons']==['b']
    # The real shop keeps the price panel open for its exit confirmation.
    # Reconstruct the observed text mask without storing ROM/game images.
    from docich.hanjuku_bot import _SHOP_EXIT_ROWS
    for y,row in enumerate(_SHOP_EXIT_ROWS,180):
        for bit in range(128):
            if row & (1 << (127-bit)):
                i=(y*256+24+bit)*3
                rgb[i:i+3]=b'\xff\xff\xff'
    confirmation=Frame(256,224,bytes(rgb))
    assert classify(confirmation)=='shop'
    actions,state=decide(confirmation,state)
    assert actions[0]['buttons']==['a']
    # An unrelated/blank prompt still cancels; never blindly alternate A/B.
    assert decide(shop,state)[0][0]['buttons']==['b']
    assert decide(farewell,state)[0][0]['buttons']==['a']


@pytest.mark.parametrize('previous', [None, 'sorengame'])
def test_terminal_observation_cannot_restore_a_replaced_lease(manager, monkeypatch, previous):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from docich import game_switch
    from docich.retro_corner import RetroCornerError
    from test_coordinator import FakeAdapterFactory
    factory = FakeAdapterFactory({'hanjuku-hero': {}, 'sorengame': {}})
    manager.coordinator = game_switch.GameSwitchCoordinator(manager.store, factory)
    assert manager.coordinator.start('hanjuku-hero').status == 'succeeded'
    canonical, _ = manager.store.canonical.load()
    active = canonical['active']
    identity = {k: active[k] for k in ('game', 'runtime_id', 'generation', 'lease_id')}
    state = {'status': 'active', 'game': 'hanjuku-hero', 'previous_game': previous,
             'bot_identity': identity}
    before = {k: list(v.runtime.events) for k, v in factory.adapters.items()}
    def observe():
        # The observation belongs to A, then another owner acquires this
        # same game/runtime under a new lease before the corner can finish.
        canonical['active']['lease_id'] = '22222222-2222-4222-8222-222222222222'
        manager.store.canonical.save(canonical)
        return SimpleNamespace(meta={'hanjuku': {'terminal_reason': 'game_over'}})
    monkeypatch.setattr('docich.agent.fence.shared_section', lambda root, fn: fn())
    monkeypatch.setattr('docich.adapters.make_adapter', lambda *a, **k: SimpleNamespace(observe=observe))
    monkeypatch.setattr('docich.hanjuku_run.terminal', Mock(return_value={
        'generation': active['generation'], 'terminal_evidence': 'verified-A'}))
    monkeypatch.setattr(manager, '_rotation_stop_result', lambda: None)
    monkeypatch.setattr(manager, '_read_state', lambda: dict(state))
    monkeypatch.setattr(manager, '_write_state', lambda update: state.update(update))
    monkeypatch.setattr(manager, '_active_game_reader', lambda: 'hanjuku-hero')
    with pytest.raises(RetroCornerError, match='expected source runtime identity'):
        manager._wait_hanjuku(state)
    assert state['status'] == 'failed'
    assert {k: v.runtime.events for k, v in factory.adapters.items()} == before
    assert manager.store.canonical.load()[0]['active']['lease_id'] == '22222222-2222-4222-8222-222222222222'


def test_sent_input_links_to_bound_decision_not_another_lease(tmp_path):
    from types import SimpleNamespace
    from docich.game_switch import atomic_write_json
    trace = {**IDENTITY, 'decision_id': 'g1:1:7', 'frame_sha256': 'b' * 64}
    atomic_write_json(tmp_path / 'hanjuku_run.json', {**IDENTITY, 'frame_sha256': 'a' * 64})
    atomic_write_json(tmp_path / 'hanjuku_bot.json', {'decision_trace': trace})
    action = SimpleNamespace(type='pad', buttons=['a'], hold_ms=100)
    hanjuku_run.action_sent(tmp_path, IDENTITY, action)
    record = json.loads((tmp_path / 'hanjuku_events.jsonl').read_text().splitlines()[-1])
    assert record['decision_id'] == 'g1:1:7'
    assert record['decision_frame_sha256'] == 'b' * 64
    assert record['frame_sha256'] == 'a' * 64
    atomic_write_json(tmp_path / 'hanjuku_bot.json', {'decision_trace': {**trace, 'lease_id': 'old'}})
    hanjuku_run.action_sent(tmp_path, IDENTITY, action)
    assert json.loads((tmp_path / 'hanjuku_events.jsonl').read_text().splitlines()[-1])['decision_id'] is None


@pytest.mark.parametrize('queued', [False, True])
def test_corner_binds_committed_start_before_first_observation(manager, monkeypatch, queued):
    import uuid
    from unittest.mock import Mock
    from docich import game_switch
    from docich.retro_corner import RetroCornerError
    from test_coordinator import FakeAdapterFactory
    factory = FakeAdapterFactory({'hanjuku-hero': {}})
    manager.coordinator = game_switch.GameSwitchCoordinator(manager.store, factory)
    monkeypatch.setattr(manager, '_validate_games', lambda *a: None)
    monkeypatch.setattr(manager, '_announce_start_locked', lambda *a: None)
    if queued:
        request = str(uuid.uuid4())
        manager.store.initialize()
        with manager.store.transaction() as tx:
            tx.enqueue_request(request, 'start', 'hanjuku-hero')
        starting = {**manager._default_state(), 'status': 'starting', 'game': 'hanjuku-hero', 'previous_game': None,
                    'switch_request_id': request}
        state, pending = manager._resume_queued_start_locked(starting, manager._local_now())
    else:
        state, pending = manager._begin_locked(manager._local_now(), scheduled=False,
                                               target_override='hanjuku-hero')
    assert pending is None and state['status'] == 'active'
    canonical, _ = manager.store.canonical.load()
    expected = {k: canonical['active'][k] for k in ('game', 'runtime_id', 'generation', 'lease_id')}
    assert state['bot_identity'] == expected == manager._read_state()['bot_identity']
    # Another same-name lease replaces A before the first observation.
    canonical['active']['lease_id'] = str(uuid.uuid4())
    manager.store.canonical.save(canonical)
    observe = Mock()
    monkeypatch.setattr('docich.adapters.make_adapter', observe)
    monkeypatch.setattr(manager, '_rotation_stop_result', lambda: None)
    finish = Mock()
    monkeypatch.setattr(manager, '_finish_locked', finish)
    with pytest.raises(RetroCornerError, match='identity changed'):
        manager._wait_hanjuku(state)
    observe.assert_not_called()
    finish.assert_not_called()


def test_missing_start_identity_cannot_adopt_the_first_active_runtime(manager, monkeypatch):
    from unittest.mock import Mock
    from docich.retro_corner import RetroCornerError
    observe = Mock()
    monkeypatch.setattr('docich.adapters.make_adapter', observe)
    with pytest.raises(RetroCornerError, match='identity missing'):
        manager._wait_hanjuku({'status': 'active', 'game': 'hanjuku-hero'})
    observe.assert_not_called()
