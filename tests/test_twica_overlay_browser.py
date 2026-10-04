"""Real Chromium pixels; no production URLs, accounts, or events are used."""
import asyncio
import os
import time

from PIL import Image
from playwright.async_api import async_playwright
import pytest

from docich.twica_overlay import SnapshotPublisher, SnapshotReader
from docich.twica_renderer import capture_once, ChromiumCapture, TRANSPARENT_PAGE_STYLE

pytestmark = pytest.mark.skipif(
    os.environ.get('DOCICH_TWICA_BROWSER_TESTS') != '1',
    reason='opt-in real Chromium contract (enabled in dedicated CI)',
)


@pytest.mark.parametrize('fast', [False, True])
def test_actual_browser_preserves_alpha_white_and_animation_progress(tmp_path, fast):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None,
            )
            try:
                page = await browser.new_page(viewport={'width': 120, 'height': 72}, device_scale_factor=1)
                await page.set_content('''
                    <style>
                    html,body { margin:0; background:white; }
                    #white { position:fixed; left:40px; top:20px; width:20px; height:20px; background:white; }
                    #half { position:fixed; left:60px; top:20px; width:20px; height:20px; background:rgba(255,0,0,.5); }
                    </style><div id="white"></div><div id="half"></div>
                ''')
                # A long finite animation would be fast-forwarded by animations='disabled'.
                await page.evaluate('''() => {
                  window.a = document.querySelector('#white').animate(
                    [{transform:'translateY(0)'},{transform:'translateY(10px)'}],
                    {duration:60000, fill:'forwards'});
                }''')
                capture = await ChromiumCapture.create(page) if fast else None
                with SnapshotPublisher(tmp_path / 'overlay', 120, 72) as publisher:
                    assert await capture_once(page, publisher, ttl_ms=5000, capture=capture)
                    reader = SnapshotReader(tmp_path / 'overlay', 120, 72, ttl_ms=5000)
                    pixels = reader.read()
                    assert reader.state == 'fresh'
                    image = Image.frombytes('RGBA', (120, 72), pixels)
                    assert image.getpixel((0, 0))[3] == 0
                    assert image.getpixel((45, 30)) == (255, 255, 255, 255)
                    assert image.getpixel((65, 30)) == (255, 0, 0, 128)
                    assert await page.evaluate('window.a.playState') == 'running'
                    assert await page.evaluate('window.a.currentTime') < 60000
            finally:
                await browser.close()
    asyncio.run(scenario())


def test_fast_capture_matches_standard_pixels_and_navigation(tmp_path):
    import io
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None,
            )
            try:
                page = await browser.new_page(viewport={'width': 120, 'height': 72}, device_scale_factor=1)
                html = '''<style>html,body{margin:0;background:white}
                #card{position:absolute;left:20px;top:12px;width:70px;height:40px;
                  background:rgba(10,50,120,.75);box-shadow:0 0 5px black;color:white}</style>
                <div id="card">123 ABC</div>'''
                await page.set_content(html)
                reference = await page.screenshot(type='png', omit_background=True,
                    animations='allow', scale='css', style=TRANSPARENT_PAGE_STYLE)
                expected = Image.open(io.BytesIO(reference)).convert('RGBA').tobytes()
                capture = await ChromiumCapture.create(page)
                with SnapshotPublisher(tmp_path / 'overlay', 120, 72) as publisher:
                    reader = SnapshotReader(tmp_path / 'overlay', 120, 72, ttl_ms=5000)
                    assert await capture_once(page, publisher, ttl_ms=5000, capture=capture)
                    assert reader.read() == expected
                    # App CSS and head replacement must not reintroduce an opaque background.
                    await page.add_style_tag(content='html,body{background:white!important}')
                    await page.evaluate("document.getElementById('docich-transparent-capture').remove()")
                    assert await capture_once(page, publisher, ttl_ms=5000, capture=capture)
                    assert reader.read() == expected
                    # A fresh document must receive the same transparency policy.
                    from urllib.parse import quote
                    await page.goto('data:text/html,' + quote(html))
                    await page.evaluate('document.fonts.ready')
                    assert await capture_once(page, publisher, ttl_ms=5000, capture=capture)
                    assert reader.read() == expected
            finally:
                await browser.close()
    asyncio.run(scenario())
