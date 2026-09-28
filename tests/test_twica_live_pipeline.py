"""Opt-in isolated real X11 + PulseAudio + native Soren runner acceptance.

No production display/server/URL is used. CI installs the dependencies and opts in.
"""
import ctypes
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time
import pytest
from test_twica_service import upstream
from docich.twica_config import load_common_config
from docich.twica_state import component, fresh, OwnerControl
from docich.twica_stream import close_owned_group

pytestmark=pytest.mark.skipif(os.environ.get('DOCICH_TWICA_X11_TESTS')!='1',reason='isolated X11/PulseAudio CI')

def wait_for(check, seconds=15):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if check(): return
        time.sleep(.1)
    raise AssertionError('isolated pipeline condition timed out')

def test_native_record_foreground_audio_recovery_and_constant_encoder_pid(tmp_path,monkeypatch):
    for binary in ('Xvfb','pulseaudio','pactl','ffmpeg','ffprobe','xdpyinfo'):
        assert shutil.which(binary),f'required CI dependency missing: {binary}'
    soren=Path(os.environ['DOCICH_TWICA_SOREN_CHECKOUT']).resolve()
    root=Path(__file__).resolve().parents[1]
    # Preserve the installed browser location before giving the fixture a private
    # HOME. Production uses its configured executable or normal owner cache.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser_executable=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or p.chromium.executable_path
    children=[]; reader,writer=os.pipe()
    try:
        xvfb=subprocess.Popen(['Xvfb','-displayfd',str(writer),'-screen','0','320x180x24','-nolisten','tcp'],
                              pass_fds=(writer,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        children.append(xvfb);os.close(writer);writer=-1
        assert select.select([reader],[],[],5)[0]
        number=os.read(reader,32).strip().decode();assert number.isdigit()
        display=':'+number
        runtime=tmp_path/'runtime';runtime.mkdir(mode=0o700)
        pa=tmp_path/'pulse.pa';sock=runtime/'pulse.sock'
        pa.write_text(f'load-module module-native-protocol-unix socket={sock} auth-anonymous=1\nload-module module-null-sink sink_name=twica_fixture rate=48000 channels=2\n')
        env=dict(os.environ,HOME=str(tmp_path),XDG_RUNTIME_DIR=str(runtime),PULSE_SERVER='unix:'+str(sock))
        pulse=subprocess.Popen(['pulseaudio','-n','--daemonize=no','--exit-idle-time=-1','--use-pid-file=no','--file='+str(pa)],
                    env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        children.append(pulse);wait_for(sock.exists,5)
        x=ctypes.CDLL('libX11.so.6')
        x.XOpenDisplay.restype=ctypes.c_void_p;x.XOpenDisplay.argtypes=[ctypes.c_char_p]
        d=x.XOpenDisplay(display.encode());assert d
        x.XDefaultRootWindow.restype=ctypes.c_ulong;x.XDefaultRootWindow.argtypes=[ctypes.c_void_p]
        x.XCreateSimpleWindow.restype=ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.c_ulong,ctypes.c_ulong]
        for fn in ('XMapRaised','XClearWindow'):
            getattr(x,fn).argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        x.XSetWindowBackground.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong]
        x.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
        window=x.XCreateSimpleWindow(d,x.XDefaultRootWindow(d),0,23,240,135,0,0,0x123456)
        x.XMapRaised(d,window);x.XSync(d,0)
        output=tmp_path/'native.mkv';log=tmp_path/'wrapper.log'
        with upstream() as (url,requests):
            env.update(DOCICH_ROOT=str(root),DOCICH_TWICA_COMMON_ENABLED='1',DOCICH_TWICA_PYTHON=sys.executable,
                DOCICH_TWICA_STATE_DIR=str(tmp_path/'state'),DOCICH_TWICA_FRAME_DIR=str(runtime/'frames'),
                SOREN_DIRECT_TWICA_OVERLAY_URL=url,SOREN_STREAM_BACKEND='ffmpeg',SOREN_DIRECT_STREAM_DISPLAY=display,
                SOREN_DIRECT_STREAM_SIZE='320x180',SOREN_DIRECT_STREAM_PULSE_SOURCE='twica_fixture.monitor',
                SOREN_DIRECT_STREAM_STATE_DIR=str(tmp_path/'stream'),SOREN_DIRECT_STREAM_LOG_FILE=str(tmp_path/'ffmpeg.log'),
                SOREN_DIRECT_STREAM_FPS='15',SOREN_ENV_FILE=str(tmp_path/'nonexistent-env'),
                DOCICH_TWICA_AUDIO_SINK='twica_fixture',DOCICH_CC_ENABLED='0',
                SOREN_CHROME_EXECUTABLE_PATH=browser_executable)
            with log.open('wb') as stream:
                child=subprocess.Popen(['bash',str(soren/'direct_stream.sh'),'record','--output',str(output),'--duration','25'],
                    env=env,stdout=stream,stderr=stream,start_new_session=True)
                children.append(child)
                cfg=load_common_config(soren,env)
                try:
                    wait_for(lambda:fresh(component(cfg.state,'renderer')) and component(cfg.state,'compositor').get('frames_sent',0)>=2)
                except AssertionError:
                    ffmpeg_log=tmp_path/'ffmpeg.log'
                    raise AssertionError(json.dumps({'renderer':component(cfg.state,'renderer'),
                        'compositor':component(cfg.state,'compositor'),
                        'wrapper':log.read_text()[-2000:],
                        'ffmpeg':ffmpeg_log.read_text()[-2000:] if ffmpeg_log.exists() else 'not-started'})) from None
                OwnerControl(cfg.state,timeout=5).activate()
                wait_for(lambda:component(cfg.state,'compositor').get('frame_state')=='fresh')
                pid=component(cfg.state,'compositor')['ffmpeg_pid']
                for colour in (0x123456,0x654321,0x000080,0x008000,0x800080,0x000000):
                    x.XSetWindowBackground(d,window,colour);x.XClearWindow(d,window);x.XSync(d,0)
                    time.sleep(.35)
                    assert component(cfg.state,'compositor')['ffmpeg_pid']==pid
                    assert requests['requests']==1
                renderer=component(cfg.state,'renderer')
                os.kill(renderer['pid'],signal.SIGTERM)
                wait_for(lambda:component(cfg.state,'renderer').get('pid')!=renderer['pid'] and component(cfg.state,'renderer').get('browser_active') is True)
                wait_for(lambda:component(cfg.state,'compositor').get('frame_state')=='fresh')
                assert component(cfg.state,'compositor')['ffmpeg_pid']==pid
                assert requests['requests']==2
                child.wait(timeout=35)
                assert child.returncode==0,log.read_text()
        metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
        assert {s['codec_type'] for s in metadata['streams']}=={'audio','video'}
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vf','fps=4','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])
        size=320*180*3
        pixels=[raw[i+((90*320+236)*3):i+((90*320+236)*3)+3] for i in range(0,len(raw),size)]
        assert any(len(p)==3 and min(p)>220 for p in pixels)
        assert any(len(p)==3 and min(p)<100 for p in pixels)
    finally:
        for child in reversed(children):
            if child.poll() is None: close_owned_group(child,.3)
        if writer>=0:os.close(writer)
        os.close(reader)
