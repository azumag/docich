"""Real Chromium pixels; no production URLs, accounts, or events are used."""
import asyncio
import os
from PIL import Image
from playwright.async_api import async_playwright
import pytest
from docich.twica_overlay import SnapshotPublisher, SnapshotReader
from docich.twica_renderer import capture_once

pytestmark = pytest.mark.skipif(os.environ.get('DOCICH_TWICA_BROWSER_TESTS') != '1', reason='opt-in Chromium CI')

def test_actual_browser_preserves_alpha_white_and_animation_progress(tmp_path):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True,
                executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None)
            try:
                page = await browser.new_page(viewport={'width': 120, 'height': 72}, device_scale_factor=1)
                await page.set_content('''<style>
                    html,body { margin:0; background:white; }
                    #white { position:fixed; left:40px; top:20px; width:20px; height:20px; background:white; }
                    #half { position:fixed; left:60px; top:20px; width:20px; height:20px; background:rgba(255,0,0,.5); }
                    </style><div id="white"></div><div id="half"></div>''')
                await page.evaluate('''() => {
                  window.a = document.querySelector('#white').animate(
                    [{transform:'translateY(0)'},{transform:'translateY(10px)'}],
                    {duration:60000, fill:'forwards'});
                }''')
                with SnapshotPublisher(tmp_path / 'overlay', 120, 72) as publisher:
                    assert await capture_once(page, publisher, ttl_ms=5000)
                    reader = SnapshotReader(tmp_path / 'overlay', 120, 72, ttl_ms=5000)
                    image = Image.frombytes('RGBA', (120, 72), reader.read())
                    assert reader.state == 'fresh'
                    assert image.getpixel((0, 0))[3] == 0
                    assert image.getpixel((45, 30)) == (255, 255, 255, 255)
                    assert image.getpixel((65, 30)) == (255, 0, 0, 128)
                    assert await page.evaluate('window.a.playState') == 'running'
                    assert await page.evaluate('window.a.currentTime') < 60000
            finally: await browser.close()
    asyncio.run(scenario())
