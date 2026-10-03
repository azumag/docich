import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('twica_browser_evidence', ROOT / 'src/docich/twica_browser_health.py')
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)
URL = 'https://example.test/overlay/fixture?token=PRIVATE_SENTINEL'
EVENTS = 'https://example.test/api/overlay/fixture/events?cursor=PRIVATE_SENTINEL'


class BrowserHealthTests(unittest.TestCase):
    def test_error_recovery_clears_transient_state_without_page_control(self):
        tracker = health.BrowserHealth(URL)
        tracker.request_failed(SimpleNamespace(url=EVENTS))
        self.assertEqual(tracker.snapshot(URL, {})['events_state'], 'network_error')
        tracker.response(SimpleNamespace(url=EVENTS, status=200))
        out = tracker.snapshot(URL, {})
        self.assertEqual(out['events_state'], 'ok')
        self.assertEqual(out['network_failures'], 1)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(out))
        self.assertNotIn('example.test', json.dumps(out))

    def test_http_error_is_not_missed_as_successful_network(self):
        tracker = health.BrowserHealth(URL)
        for status, label in [(401, 'denied'), (403, 'denied'), (429, 'rate_limited'),
                              (503, 'server_error'), (404, 'http_error'), (200, 'ok')]:
            tracker.response(SimpleNamespace(url=EVENTS, status=status))
            self.assertEqual(tracker.snapshot(URL, {})['events_state'], label)

    def test_unknown_http_codes(self):
        for status in [None, True, '200', -1, 999]:
            self.assertEqual(health.http_state(status), 'unknown')

    def test_foreign_requests_and_images_are_ignored(self):
        tracker = health.BrowserHealth(URL)
        for url in ['https://other.test/api/overlay/fixture/events',
                    'https://example.test/api/overlay/other/events',
                    'https://example.test/image?path=/api/overlay/fixture/events',
                    'https://example.test:8443/api/overlay/fixture/events',
                    'http://example.test/api/overlay/fixture/events']:
            tracker.request_failed(SimpleNamespace(url=url))
        self.assertEqual(tracker.snapshot(URL, {})['network_failures'], 0)

    def test_response_configuration_is_distinct(self):
        tracker = health.BrowserHealth(URL)
        tracker.response(SimpleNamespace(url='https://example.test/api/overlay/fixture/realtime-config', status=200))
        out = tracker.snapshot(URL, {})
        self.assertEqual(out['config_state'], 'ok')
        self.assertEqual(out['events_state'], 'unobserved')

    def test_error_text_and_socket_payload_never_persist(self):
        tracker = health.BrowserHealth(URL)
        tracker.script_error(Exception('PRIVATE_SENTINEL'))
        class Socket:
            def __init__(self): self.handlers = {}
            def on(self, name, callback): self.handlers[name] = callback
        socket = Socket(); tracker.websocket(socket)
        socket.handlers['framereceived']('PRIVATE_SENTINEL')
        out = tracker.snapshot(URL, {})
        self.assertEqual(out['script_errors'], 1)
        self.assertEqual(out['socket_frames'], 1)
        self.assertEqual(out['socket_state'], 'receiving')
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(out))

    def test_retired_socket_cannot_overwrite_recovered_socket(self):
        tracker = health.BrowserHealth(URL)
        sockets = []
        class Socket:
            def __init__(self): self.handlers = {}
            def on(self, name, callback): self.handlers[name] = callback
        for _ in range(2):
            socket = Socket(); sockets.append(socket); tracker.websocket(socket)
        sockets[1].handlers['framereceived']('heartbeat')
        sockets[0].handlers['close']()
        self.assertEqual(tracker.snapshot(URL, {})['socket_state'], 'receiving')

    def test_blank_screenshot_cannot_claim_card_dom(self):
        out = health.BrowserHealth(URL).snapshot(URL, {'present': False})
        self.assertFalse(out['card_visible'])
        self.assertEqual(out['cards_observed'], 0)
        self.assertEqual(out['dom_state'], 'observed')

    def test_dom_is_boolean_allowlist_only(self):
        tracker = health.BrowserHealth(URL)
        out = tracker.snapshot(URL, {'present': True, 'visible': True, 'images_ready': True,
                                     'text': 'PRIVATE_SENTINEL', 'token': 'PRIVATE_SENTINEL'})
        self.assertEqual(out['cards_observed'], 1)
        self.assertTrue(out['card_visible'])
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(out))
        self.assertEqual(tracker.snapshot(URL, {'present': True})['cards_observed'], 1)
        self.assertFalse(tracker.snapshot(URL, {'present': 'yes', 'visible': 'yes'})['card_visible'])

    def test_redirect_not_reported_as_correct_page(self):
        self.assertEqual(health.BrowserHealth(URL).snapshot('https://example.test/login', {})['page_state'], 'redirected')
        self.assertEqual(health.BrowserHealth(URL).snapshot('https://example.test/overlay/other', {})['page_state'], 'redirected')

    def test_bounded_counts_and_no_dom_text_read(self):
        tracker = health.BrowserHealth(URL)
        tracker._counts['script_errors'] = 1_000_000
        tracker.script_error(None)
        self.assertEqual(tracker.snapshot(URL, {})['script_errors'], 1_000_000)
        for forbidden in ['innerHTML', 'textContent', 'sessionStorage', 'localStorage', 'cookie']:
            self.assertNotIn(forbidden, health.CARD_PROBE)


if __name__ == '__main__':
    unittest.main()
