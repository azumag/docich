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


def test_translucent_deployment_menu_advances_then_sends_march_order():
    rgb=bytearray(frame((40,140,20)).rgb)
    for y in (16,17):
        for x in range(35,110):
            i=(y*256+x)*3
            rgb[i:i+3]=bytes((255,56,57))
    menu=Frame(256,224,bytes(rgb))
    assert classify(menu)=='field_menu'
    actions,state=decide(frame((40,140,20)),{})
    assert actions[0]['buttons']==['a']
    actions,state=decide(menu,state)
    assert actions[0]['buttons']==['a']
    for button in ('left','right','up','a'):
        actions,state=decide(frame((40,140,20)),state)
        assert actions[0]['buttons']==[button]
    assert decide(frame((40,140,20)),state)[0]==[]


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
    state={'status':'active','game':'hanjuku-hero','previous_game':'sorengame',
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
    assert manager._wait_and_finish(state)=='restored'
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


def test_optional_concert_exits_instead_of_selecting_the_same_track():
    rgb=bytearray(frame((16,72,57)).rgb)
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
