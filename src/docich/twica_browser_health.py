"""Passive, bounded browser evidence. Never reads event bodies or credentials.

Transport recovery belongs to the persistent TwiCa page, not its capturer.
HTTP evidence and card DOM evidence are distinct from screenshot liveness.
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit


CARD_PROBE = """() => {
  const card = document.querySelector('[data-overlay-card="true"]');
  if (!card) return {present:false, visible:false, images_ready:false};
  const box = card.getBoundingClientRect();
  const style = getComputedStyle(card);
  const images = Array.from(card.querySelectorAll('img'));
  return {present:true,
    visible:style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) > 0 && box.width > 0 && box.height > 0 &&
      box.right > 0 && box.bottom > 0 && box.left < innerWidth && box.top < innerHeight,
    images_ready:images.length > 0 && images.every(image => image.complete && image.naturalWidth > 0)};
}"""


def http_state(status: int) -> str:
    if type(status) is not int or not 100 <= status <= 599:
        return 'unknown'
    if 200 <= status < 300:
        return 'ok'
    if status in (401, 403):
        return 'denied'
    if status == 429:
        return 'rate_limited'
    if status >= 500:
        return 'server_error'
    return 'http_error'


class BrowserHealth:
    """Callbacks only record fixed categories/counters, never mutate the page."""
    def __init__(self, url: str, generation: str = ''):
        self._generation = generation
        parsed = urlsplit(url)
        self._origin = (parsed.scheme, parsed.hostname, parsed.port)
        self._path = parsed.path
        self._paths = {f'/api{parsed.path}/events': 'events',
                       f'/api{parsed.path}/realtime-config': 'config'}
        self._states = {'events': 'unobserved', 'config': 'unobserved'}
        self._counts = {'events_responses': 0, 'config_responses': 0,
                        'network_failures': 0, 'script_errors': 0,
                        'socket_frames': 0, 'cards_observed': 0}
        self._socket = 'unobserved'
        self._socket_generation = 0
        self._was_card = False
        self._due = 0.0

    def _increment(self, key: str) -> None:
        self._counts[key] = min(1_000_000, self._counts[key] + 1)

    def _endpoint(self, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.hostname, parsed.port) == self._origin:
                return self._paths.get(parsed.path)
        except (ValueError, TypeError):
            pass
        return None

    def response(self, response) -> None:
        endpoint = self._endpoint(response.url)
        if endpoint is not None:
            self._states[endpoint] = http_state(response.status)
            self._increment(endpoint + '_responses')

    def request_failed(self, request) -> None:
        endpoint = self._endpoint(request.url)
        if endpoint is not None:
            self._states[endpoint] = 'network_error'
            self._increment('network_failures')
        # Do not latch a fatal error or destroy the page/queue. The app's
        # existing WS/poll controller retries and a later response clears it.

    def script_error(self, _error) -> None:
        self._increment('script_errors')

    def websocket(self, socket) -> None:
        self._socket_generation += 1
        generation = self._socket_generation
        self._socket = 'opened'

        def frame(_payload):
            # A frame can be a heartbeat; do not call it a card/event receipt.
            if generation == self._socket_generation:
                self._socket = 'receiving'
                self._increment('socket_frames')

        def change(state):
            if generation == self._socket_generation:
                self._socket = state

        socket.on('framereceived', frame)
        socket.on('close', lambda *_: change('closed'))
        socket.on('socketerror', lambda *_: change('error'))

    def attach(self, page) -> None:
        page.on('response', self.response)
        page.on('requestfailed', self.request_failed)
        page.on('pageerror', self.script_error)
        page.on('websocket', self.websocket)

    def snapshot(self, page_url: str, dom: object) -> dict:
        try:
            parsed = urlsplit(page_url)
            page_state = 'overlay' if ((parsed.scheme, parsed.hostname, parsed.port) == self._origin
                         and parsed.path == self._path) else 'redirected'
        except (ValueError, TypeError):
            page_state = 'unknown'
        dom = dom if isinstance(dom, dict) else {}
        present = dom.get('present') is True
        if present and not self._was_card:
            self._increment('cards_observed')
        self._was_card = present
        return {'page_state': page_state, 'events_state': self._states['events'],
                'config_state': self._states['config'], 'socket_state': self._socket,
                'dom_state': 'observed' if type(dom.get('present')) is bool else 'unavailable',
                'card_present': present, 'card_visible': dom.get('visible') is True,
                'images_ready': dom.get('images_ready') is True, **self._counts}

    async def sample(self, page, directory) -> None:
        now = time.monotonic()
        if now < self._due:
            return
        self._due = now + 1.0
        try:
            dom = await asyncio.wait_for(page.evaluate(CARD_PROBE), timeout=0.5)
        except Exception:
            dom = None
        try:
            from .twica_state import heartbeat
            heartbeat(directory, 'browser-health.json',
                      generation=self._generation,
                      **self.snapshot(page.url, dom))
        except Exception:
            # Diagnostic IO must not break the renderer or change ownership.
            pass
