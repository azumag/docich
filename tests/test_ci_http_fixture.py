"""Tests for test-only HTTP polling: scope, real I/O and cleanup are preserved."""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
import sys

import pytest

_spec = importlib.util.spec_from_file_location(
    '_docich_http_test_configuration', Path(__file__).with_name('conftest.py'))
_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_config)


class RecordingServer:
    def serve_forever(self, poll_interval=0.5):
        return poll_interval


def test_only_test_subclass_gets_a_shorter_default():
    fast = _config._short_polling_server_type(RecordingServer)
    assert issubclass(fast, RecordingServer)
    assert fast().serve_forever() == 0.01
    assert RecordingServer().serve_forever() == 0.5


@pytest.mark.parametrize('interval', [0.0, 0.02, 0.5, 2.0])
def test_explicit_poll_intervals_are_not_overridden(interval):
    fast = _config._short_polling_server_type(RecordingServer)
    assert fast().serve_forever(interval) == interval
    assert fast().serve_forever(poll_interval=interval) == interval


def test_serving_errors_are_not_swallowed():
    class BrokenServer:
        def serve_forever(self, poll_interval=0.5):
            raise RuntimeError('fixture server failed')
    fast = _config._short_polling_server_type(BrokenServer)
    with pytest.raises(RuntimeError, match='fixture server failed'):
        fast().serve_forever()


@pytest.mark.parametrize('filename', [
    'test_webui.py', 'test_webui_resources.py', 'test_tsuitate_beta_control.py',
    'test_game_switch.py', 'test_ci_full_suite.py',
])
def test_fixture_scope_and_constructor_restoration(monkeypatch, filename):
    # Exercise the fixture itself without importing the production application.
    fake_webui = SimpleNamespace(ThreadingHTTPServer=RecordingServer)
    fake_package = SimpleNamespace(webui=fake_webui)
    requested = []
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, 'docich', fake_package)
        def fixture(name):
            requested.append(name)
            assert name == 'monkeypatch'
            return patch
        request = SimpleNamespace(node=SimpleNamespace(path=Path(filename)),
                                  getfixturevalue=fixture)
        _config._short_poll_for_http_api_fixtures.__wrapped__(request)
        expected = 0.01 if filename in _config._HTTP_API_TEST_FILES else 0.5
        assert fake_webui.ThreadingHTTPServer().serve_forever() == expected
        assert RecordingServer().serve_forever() == 0.5
    assert fake_webui.ThreadingHTTPServer is RecordingServer
    assert requested == (['monkeypatch'] if filename in _config._HTTP_API_TEST_FILES else [])


def test_real_requests_and_blocking_shutdown_are_preserved():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == '/ok' else 403)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *_):
            pass

    original = ThreadingHTTPServer.serve_forever
    server_type = _config._short_polling_server_type(ThreadingHTTPServer)
    server = server_type(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    try:
        for path, expected in [('/ok', 200), ('/forbidden', 403)]:
            connection.request('GET', path)
            response = connection.getresponse()
            assert response.status == expected
            assert response.read() == b'ok'
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert server.socket.fileno() == -1
    assert ThreadingHTTPServer.serve_forever is original
