"""Regression: one HTTP failure must not destroy the app-owned display queue."""
import asyncio
import base64
import io
from types import SimpleNamespace

from PIL import Image
from docich.twica_renderer import run_renderer


def test_live_renderer_preserves_page_after_failed_events_request(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        calls = []
        image = io.BytesIO()
        Image.new('RGBA', (2, 1), (255, 255, 255, 128)).save(image, format='PNG')
        class Page:
            def __init__(self): self.handlers = {}; self.count = 0
            @property
            def context(self):
                page = self
                class Context:
                    async def new_cdp_session(self, target):
                        assert target is page
                        return page
                return Context()
            async def add_init_script(self, script): pass
            async def evaluate(self, script): pass
            async def send(self, method, options):
                if method != 'Page.captureScreenshot': return {}
                png = await self.screenshot()
                return {'data': base64.b64encode(png).decode('ascii')}
            def on(self, name, callback): self.handlers[name] = callback
            async def goto(self, *args, **kwargs):
                calls.append('goto')
                return SimpleNamespace(ok=True)
            async def screenshot(self, **kwargs):
                self.count += 1
                calls.append('frame')
                request = SimpleNamespace(url='https://example.test/api/overlay/fixture/events')
                if self.count == 1:
                    self.handlers['requestfailed'](request)
                elif self.count == 2:
                    self.handlers['response'](SimpleNamespace(url=request.url, status=200))
                elif self.count == 3:
                    stop.set()
                return image.getvalue()
            def is_closed(self): return False
        class Browser:
            async def new_page(self, **kwargs): calls.append('page'); return Page()
            async def close(self): calls.append('close')
        class Chromium:
            async def launch(self, **kwargs): calls.append('launch'); return Browser()
        class Factory:
            async def __aenter__(self): return SimpleNamespace(chromium=Chromium())
            async def __aexit__(self, *args): pass
        await run_renderer('https://example.test/overlay/fixture', tmp_path / 'frames',
                           width=2, height=1, fps=30, stop=stop, browser_factory=Factory)
        assert calls == ['launch', 'page', 'goto', 'frame', 'frame', 'frame', 'close']
    asyncio.run(scenario())
