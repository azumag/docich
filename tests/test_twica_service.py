"""Real browser ownership, cursor recovery and fail-transparent service checks."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from dataclasses import replace
import pytest
from playwright.async_api import async_playwright
from docich.twica_config import load_common_config
from docich.twica_overlay import SnapshotReader
from docich.twica_renderer import SESSION_CHECKPOINT, browser_environment
from docich.twica_renderer_service import serve
from docich.twica_state import OwnerControl, component, fresh, heartbeat, owner

pytestmark = pytest.mark.skipif(os.environ.get('DOCICH_TWICA_NETWORK_BROWSER_TESTS') != '1', reason='dedicated Chromium CI')

@contextmanager
def upstream():
    state={'requests':0}
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
        def do_GET(self):
            if self.path.startswith('/overlay/'):
                state['requests']+=1
            self.send_response(200); self.send_header('Content-Type','text/html');self.end_headers()
            self.wfile.write(b'<style>body{margin:0}div{position:fixed;left:40%;top:20%;width:20%;height:60%;background:white}</style><div></div>')
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/overlay/test',state
    finally: server.shutdown();server.server_close();thread.join()

async def until(predicate, timeout=8):
    end=asyncio.get_running_loop().time()+timeout
    while asyncio.get_running_loop().time()<end:
        if predicate(): return
        await asyncio.sleep(.05)
    raise AssertionError('bounded condition was not reached')


def test_service_keeps_one_page_across_game_changes_and_stops_before_rollback(tmp_path, monkeypatch):
    with upstream() as (url,up):
        monkeypatch.setenv('SOREN_DIRECT_TWICA_OVERLAY_URL',url)
        config=load_common_config(tmp_path,{'DOCICH_TWICA_COMMON_ENABLED':'1',
               'DOCICH_TWICA_FRAME_DIR':str(tmp_path/'frames'),'DOCICH_TWICA_FPS':'10'})
        config=replace(config,proxy_ports=())
        async def scenario():
            stop=asyncio.Event(); task=asyncio.create_task(serve(config,width=120,height=72,stop=stop))
            async def encoder():
                while not stop.is_set():
                    heartbeat(config.state,'compositor',state='running',frames_sent=10)
                    await asyncio.sleep(.2)
            feed=asyncio.create_task(encoder())
            try:
                await until(lambda:fresh(component(config.state,'renderer')))
                assert up['requests']==0
                await asyncio.to_thread(OwnerControl(config.state,proxy_ports=(),timeout=3).activate)
                reader=SnapshotReader(config.frames,120,72)
                await until(lambda:reader.read() and reader.state=='fresh')
                assert up['requests']==1
                for game in ['sorengame','soren91','retroarch','cli','nethack','paper','waiting']:
                    (tmp_path/'game_switch.json').write_text(json.dumps({'active':{'game':game}}))
                    await asyncio.sleep(.15)
                    assert up['requests']==1
                    reader.read();assert reader.state=='fresh'
                await asyncio.to_thread(OwnerControl(config.state,proxy_ports=(),timeout=5).rollback)
                assert owner(config.state)['mode']=='legacy'
                assert component(config.state,'renderer')['browser_active'] is False
                assert not (config.frames/'frame.rgba').exists()
            finally:
                stop.set()
                await asyncio.wait_for(task,10)
                await feed
        asyncio.run(scenario())


def test_durable_session_checkpoint_restores_twica_only(tmp_path):
    with upstream() as (url,_):
        async def scenario():
            async with async_playwright() as p:
                options=dict(headless=True,env=browser_environment(),
                    executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None)
                context=await p.chromium.launch_persistent_context(str(tmp_path/'profile'),**options)
                await context.add_init_script(SESSION_CHECKPOINT)
                page=context.pages[0];await page.goto(url)
                await page.evaluate("sessionStorage.setItem('twica-overlay-pollstate:test','cursor-7');sessionStorage.setItem('unrelated','not-copied')")
                await context.close()
                context=await p.chromium.launch_persistent_context(str(tmp_path/'profile'),**options)
                try:
                    await context.add_init_script(SESSION_CHECKPOINT)
                    page=context.pages[0];await page.goto(url)
                    assert await page.evaluate("sessionStorage.getItem('twica-overlay-pollstate:test')")=='cursor-7'
                    assert await page.evaluate("sessionStorage.getItem('unrelated')") is None
                finally: await context.close()
        asyncio.run(scenario())
