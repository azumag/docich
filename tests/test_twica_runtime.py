import asyncio
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from docich.twica_ffmpeg import compose_args, encoding_geometry, OwnedReader
from docich.twica_operator import prepare, transfer, NotReady
from docich.twica_state import (atomic_json, control, exclusive, fresh, heartbeat,
                               new_control, read_json, status)
from docich.twica_overlay import SnapshotPublisher, private_directory
from docich.twica_service import serve
from docich.twica_renderer import browser_environment


def argv(cc=False):
    value = ['-hide_banner', '-thread_queue_size', '1024', '-f', 'x11grab', '-draw_mouse', '0',
             '-framerate', '30', '-video_size', '1280x720', '-i', ':99.0+0,0',
             '-thread_queue_size', '2048', '-isync', '0', '-f', 'pulse', '-i', 'soren_null.monitor',
             '-map', '0:v:0', '-map', '1:a:0']
    if cc:
        value += ['-vf', 'docichcc=socket=/run/user/1000/docich/ffmpeg-cc.sock', '-a53cc', '1']
    return value + ['-c:v', 'libx264', '-af', 'adelay=10:all=1,aresample=async=1:first_pts=0',
                    '-progress', 'pipe:1', '-f', 'flv', 'rtmp://127.0.0.1:1935/soren/live']


@pytest.mark.parametrize('cc', [False, True])
def test_integrates_existing_stream_audio_clock_cc_and_relay(cc):
    original = argv(cc)
    geometry = encoding_geometry(original)
    result = compose_args(original, 9, geometry)
    assert original == argv(cc)
    assert result.count('-i') == 3
    assert [result[i+1] for i,k in enumerate(result[:-1]) if k == '-map'] == ['[twica_out]', '1:a:0']
    assert result[-1] == original[-1]
    assert result[result.index('-af')+1] == original[original.index('-af')+1]
    assert result[result.index('-progress')+1] == 'pipe:1'
    assert 'pipe:9' in result and 'pipe:0' not in result
    graph = result[result.index('-filter_complex')+1]
    assert graph.startswith('[0:v:0][2:v:0]overlay=')
    assert 'eof_action=pass:repeatlast=0' in graph
    assert ':alpha=straight:' in graph
    assert '-vf' not in result
    assert ('docichcc=' in graph) == cc
    assert ('-a53cc' in result) == cc


@pytest.mark.parametrize('probe', [['-filters'], ['-h', 'encoder=libx264'], ['-devices'], ['-encoders'], ['-version']])
def test_capability_probes_are_not_encoding(probe):
    assert encoding_geometry(probe) is None


@pytest.mark.parametrize('modify', [lambda a: a + ['-i', 'x'], lambda a: a + ['-filter_complex', 'x'],
                                   lambda a: ['1:v:0' if x == '0:v:0' else x for x in a]])
def test_ambiguous_maps_and_graphs_rejected(modify):
    with pytest.raises(ValueError):
        encoding_geometry(modify(argv()))


def test_no_secrets_in_browser_environment(monkeypatch):
    monkeypatch.setenv('MY_API_KEY', 'sentinel-secret')
    monkeypatch.setenv('SOREN_DIRECT_TWICA_OVERLAY_URL', 'https://example.test/overlay/private')
    assert 'MY_API_KEY' not in browser_environment()
    assert 'SOREN_DIRECT_TWICA_OVERLAY_URL' not in browser_environment()
    assert browser_environment('bus')['PULSE_SINK'] == 'bus'


def test_prepare_idempotent_private_and_policy_not_reset(tmp_path):
    directory = tmp_path/'control'
    prepare(directory)
    first = control(directory)
    assert first['owner'] == 'legacy'
    assert directory.stat().st_mode & 0o077 == 0
    assert (directory/'control.json').stat().st_mode & 0o077 == 0
    common = new_control('common', pipeline_enabled=True)
    atomic_json(directory, 'control.json', common)
    prepare(directory)
    assert control(directory) == common
    assert first['generation'] != common['generation']


def test_invalid_or_missing_managed_control_stays_transparent(tmp_path, monkeypatch):
    directory = tmp_path/'control'
    frames = tmp_path/'frames'
    monkeypatch.setenv('DOCICH_TWICA_FRAME_DIR', str(frames))
    prepare(directory)
    with SnapshotPublisher(frames, 1, 1) as pub:
        pub.publish(bytes([255,255,255,255]), time.monotonic_ns())
        reader = OwnedReader(directory, 1, 1)
        assert reader.read() == bytes(4)
        atomic_json(directory, 'control.json', new_control('common'))
        assert reader.read() == bytes([255,255,255,255])
        (directory/'control.json').write_text('not-json')
        assert reader.read() == bytes(4)
        (directory/'control.json').unlink()
        assert reader.read() == bytes(4)


def test_state_rejects_symlink_fifo_and_stale_identity(tmp_path):
    directory = private_directory(tmp_path/'control')
    target = directory/'target'
    target.write_text('{}'); target.chmod(0o600)
    alias = directory/'x.json'; alias.symlink_to(target)
    assert read_json(alias) == {}
    alias.unlink(); os.mkfifo(alias,0o600)
    before=time.monotonic(); assert read_json(alias) == {}; assert time.monotonic()-before < .2
    heartbeat(directory,'fresh.json', ready=True)
    data=read_json(directory/'fresh.json'); assert fresh(data)
    data['identity']='fake'; assert not fresh(data)
    with exclusive(directory,'lock'):
        with pytest.raises(BlockingIOError):
            with exclusive(directory,'lock'): pass


def test_activation_requires_readiness_and_does_not_mutate_old_owner(tmp_path):
    directory=tmp_path/'control';prepare(directory);before=control(directory)
    with pytest.raises(NotReady, match='idle_boundary'):
        transfer(directory,'common')
    with pytest.raises(NotReady, match='renderer_not_ready'):
        transfer(directory,'common',idle_confirmed=True)
    heartbeat(directory,'renderer.json',ready=True,state='standby')
    with pytest.raises(NotReady, match='encoder_input_not_armed'):
        transfer(directory,'common',idle_confirmed=True)
    heartbeat(directory,'pipeline.json',ready=True)
    with pytest.raises(NotReady, match='legacy_guards_not_ready'):
        transfer(directory,'common',idle_confirmed=True)
    assert control(directory)==before


def test_handoff_waits_for_every_client_and_fails_closed(tmp_path):
    directory=tmp_path/'control';prepare(directory)
    heartbeat(directory,'renderer.json',ready=True,state='standby',subscribed=False)
    heartbeat(directory,'pipeline.json',ready=True)
    for role in ['game','shared']:
        heartbeat(directory,f'legacy-{role}.json', role=role, subscribed=True, generation=control(directory)['generation'])
    with pytest.raises(NotReady, match='handoff_timeout'):
        transfer(directory,'common',idle_confirmed=True,timeout_sec=.15)
    assert control(directory)['owner']=='none'


def test_full_two_phase_activation_and_rollback(tmp_path):
    directory=tmp_path/'control';prepare(directory)
    stop=threading.Event()
    seen=[]
    def participants():
        while not stop.wait(.02):
            c=control(directory)
            for role in ['game','shared']:
                heartbeat(directory,f'legacy-{role}.json', role=role,
                          subscribed=c['owner']=='legacy' and role==c.get('legacy_role','game'),generation=c['generation'])
            heartbeat(directory,'renderer.json',ready=True,state='active' if c['owner']=='common' else 'standby',
                      subscribed=c['owner']=='common',generation=c['generation'])
            heartbeat(directory,'pipeline.json',ready=True,frame_state='fresh' if c['owner']=='common' else 'inactive')
            seen.append(c['owner'])
    t=threading.Thread(target=participants);t.start()
    try:
        time.sleep(.08)
        r=transfer(directory,'common',idle_confirmed=True,timeout_sec=3)
        assert r['owner']=='common' and r['legacy_subscribers']==0
        r=transfer(directory,'legacy',idle_confirmed=True,timeout_sec=3,legacy_role='shared')
        assert r['owner']=='legacy'
        time.sleep(.1)
        assert status(directory)['legacy_subscribers']==1
        assert seen.count('none')>=2
    finally:stop.set();t.join()


def test_status_never_exposes_extra_untrusted_fields(tmp_path):
    directory=tmp_path/'control';prepare(directory)
    heartbeat(directory,'renderer.json', state='active', url='sentinel-secret', cookie='sentinel-secret')
    assert 'sentinel-secret' not in json.dumps(status(directory))
    assert 'url' not in status(directory)


def test_service_keeps_single_renderer_across_all_game_switches(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCICH_TWICA_FRAME_DIR',str(tmp_path/'frames'))
    async def scenario():
        directory=tmp_path/'control';prepare(directory)
        atomic_json(directory,'control.json',new_control('common'))
        heartbeat(directory,'pipeline.json',ready=True,width=320,height=180,fps=15)
        stop=asyncio.Event(); calls=[]
        async def preflight():calls.append('preflight')
        async def runner(url,directory,*,stop,report,**kwargs):
            calls.append('start');report('active');await stop.wait();calls.append('stop')
        task=asyncio.create_task(serve(directory,'https://example.test/overlay/x',stop,
                                     runner=runner,preflight=preflight,tick_sec=.01))
        for game in ['sorengame','soren91','retroarch','cli','nethack','paper','waiting','sorengame']:
            # The common runtime must not read this file or change its browser.
            (tmp_path/'game_switch.json').write_text(json.dumps({'active_game':game}))
            await asyncio.sleep(.03)
        assert calls==['preflight','start']
        stop.set();await task
        assert calls==['preflight','start','stop']
    asyncio.run(scenario())


def test_real_adapter_preserves_stdin_q_stdout_and_child_exit(tmp_path, monkeypatch):
    import subprocess
    import sys
    directory=tmp_path/'control';prepare(directory)
    monkeypatch.setenv('DOCICH_TWICA_STATE_DIR',str(directory))
    monkeypatch.setenv('DOCICH_TWICA_FRAME_DIR',str(tmp_path/'frames'))
    fake=tmp_path/'fake-ffmpeg'
    fake.write_text('#!'+sys.executable+'\nimport sys\nassert sys.stdin.readline()=="q\\n"\nprint("progress=end",flush=True)\n')
    fake.chmod(0o700)
    env=dict(os.environ,DOCICH_TWICA_REAL_FFMPEG=str(fake))
    result=subprocess.run([sys.executable,'-m','docich.twica_ffmpeg',*argv()],input='q\n',
                          text=True,capture_output=True,env=env,timeout=5)
    assert result.returncode==0,result.stderr
    assert result.stdout=='progress=end\n'
    assert read_json(directory/'pipeline.json')['ready'] is False
