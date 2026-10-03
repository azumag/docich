"""Real Linux X windows -> existing-style FFmpeg command -> common RGBA output.

No production account, endpoint, display, service or encoder is used.
"""
import ctypes
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image
import pytest

from docich.twica_operator import prepare
from docich.twica_state import atomic_json, new_control, read_json, fresh

pytestmark=pytest.mark.skipif(os.environ.get('DOCICH_TWICA_BROWSER_TESTS')!='1', reason='dedicated real-pixel CI')
ROOT=Path(__file__).resolve().parents[1]


def wait(predicate, seconds=8):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError('fixture condition timed out')


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'''<!doctype html><style>html,body{margin:0;background:transparent}
        #card{position:fixed;left:50%;top:60px;transform:translateX(-50%);width:80px;height:60px;background:white}
        </style><div id="card"></div>'''
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


@pytest.mark.parametrize('use_http', [False, True])
def test_real_pipeline_stays_live_over_six_native_presenters_and_renderer_stop(tmp_path, use_http):
    if use_http and os.environ.get('DOCICH_TWICA_NETWORK_TESTS') != '1':
        pytest.skip('real HTTP fixture requires the CI browser networking environment')
    assert shutil.which('Xvfb') and shutil.which('ffmpeg')
    directory=tmp_path/'state';frames=tmp_path/'frames';prepare(directory)
    read_fd,write_fd=os.pipe()
    display=subprocess.Popen(['Xvfb','-displayfd',str(write_fd),'-screen','0','320x180x24','-nolisten','tcp'],
                             pass_fds=(write_fd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.close(write_fd)
    encoder=renderer=None;connection=None
    server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    lib=ctypes.CDLL('libX11.so.6')
    lib.XOpenDisplay.argtypes=[ctypes.c_char_p];lib.XOpenDisplay.restype=ctypes.c_void_p
    lib.XDefaultRootWindow.argtypes=[ctypes.c_void_p];lib.XDefaultRootWindow.restype=ctypes.c_ulong
    lib.XCreateSimpleWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_int,
        ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.c_ulong,ctypes.c_ulong]
    lib.XCreateSimpleWindow.restype=ctypes.c_ulong
    for name in ['XMapWindow','XClearWindow']:
        getattr(lib,name).argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    lib.XSetWindowBackground.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong]
    lib.XFlush.argtypes=[ctypes.c_void_p];lib.XCloseDisplay.argtypes=[ctypes.c_void_p]
    try:
        assert select.select([read_fd],[],[],5)[0]
        number=os.read(read_fd,32).decode().strip();assert number.isdigit()
        disp=':'+number
        connection=lib.XOpenDisplay(disp.encode());assert connection
        root=lib.XDefaultRootWindow(connection)
        lib.XSetWindowBackground(connection,root,0x003080);lib.XClearWindow(connection,root)
        window=lib.XCreateSimpleWindow(connection,root,0,22,240,136,0,0,0x30D030)
        lib.XMapWindow(connection,window);lib.XFlush(connection)
        env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),DOCICH_TWICA_STATE_DIR=str(directory),
                 DOCICH_TWICA_FRAME_DIR=str(frames),DOCICH_TWICA_REAL_FFMPEG=shutil.which('ffmpeg'),
                 SOREN_DIRECT_TWICA_OVERLAY_URL=f'http://127.0.0.1:{server.server_port}/overlay/x',
                 DOCICH_TWICA_RENDER_FPS='15')
        output=tmp_path/'out.mkv';log=tmp_path/'encoder.log'
        with log.open('wb') as logs:
            args=['-hide_banner','-loglevel','warning','-thread_queue_size','128',
                  '-f','x11grab','-draw_mouse','0','-framerate','10','-video_size','320x180','-i',disp+'.0+0,0',
                  '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-map','0:v:0','-map','1:a:0',
                  '-c:v','ffv1','-pix_fmt','bgra','-c:a','pcm_s16le','-t','7','-y',str(output)]
            encoder=subprocess.Popen([sys.executable,'-m','docich.twica_ffmpeg',*args],env=env,
                                      stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=logs)
            renderer_command = [sys.executable,'-m','docich.twica_service'] if use_http else [
                sys.executable, str(ROOT/'tests/twica_snapshot_fixture.py')]
            renderer=subprocess.Popen(renderer_command,env=env,
                                       stdout=subprocess.DEVNULL,stderr=logs)
            wait(lambda: read_json(directory/'pipeline.json').get('ready') is True)
            atomic_json(directory,'control.json',new_control('common',pipeline_enabled=True))
            wait(lambda: read_json(directory/'renderer.json').get('state')=='active')
            original_pid=read_json(directory/'pipeline.json')['encoder_pid']
            colours=[0x30D030,0xD03030,0x3030D0,0xD0D030,0x30D0D0,0xD030D0]
            for colour in colours:
                lib.XSetWindowBackground(connection,window,colour);lib.XClearWindow(connection,window);lib.XFlush(connection)
                time.sleep(.25)
                assert encoder.poll() is None
                assert read_json(directory/'pipeline.json')['encoder_pid']==original_pid
            renderer.terminate();renderer.wait(timeout=10)
            assert encoder.poll() is None
            assert encoder.wait(timeout=12)==0,log.read_text()
        decoded=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-map','0:v:0','-pix_fmt','rgba','-f','rawvideo','pipe:1'],
                               capture_output=True,check=True,timeout=10).stdout
        size=320*180*4;assert len(decoded)%size==0
        images=[Image.frombytes('RGBA',(320,180),decoded[i:i+size]) for i in range(0,len(decoded),size)]
        assert len(images)>=50
        # x=234 is INSIDE the opaque native game window, not merely the sidebar.
        foreground=[im.getpixel((234,90))[:3] for im in images]
        assert (255,255,255) in foreground
        assert all(im.getpixel((5,5))[:3]==(0,48,128) for im in images)
        assert all(pixel==(208,48,208) for pixel in foreground[-5:])
        # All game sources change independently while the single common layer is white.
        visible_colours={im.getpixel((20,90))[:3] for im in images if im.getpixel((234,90))[:3]==(255,255,255)}
        assert len(visible_colours)>=5
    finally:
        for child in [renderer,encoder]:
            if child is not None and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
        if connection:lib.XCloseDisplay(connection)
        display.terminate();display.wait(timeout=5);os.close(read_fd)
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_real_browser_legacy_handoff_has_one_subscription_and_preserves_eight_events():
    if os.environ.get('DOCICH_TWICA_NETWORK_TESTS') != '1':
        pytest.skip('real HTTP fixture requires the CI browser networking environment')
    import playwright
    driver=Path(playwright.__file__).parent/'driver/package'
    soren=Path(os.environ.get('DOCICH_SOREN_ROOT',str(ROOT/'games/soviet_now')))
    env=dict(os.environ,DOCICH_NODE_PLAYWRIGHT=str(driver),DOCICH_SOREN_ROOT=str(soren))
    result=subprocess.run(['node',str(ROOT/'tests/twica_legacy_browser_fixture.mjs')],env=env,
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout.strip().splitlines()[-1])
    assert data=={'passed':True,'events':8,'max_subscriptions':1,'total_subscriptions':3}
