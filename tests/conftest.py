"""Keep real HTTP API tests without a half-second shutdown poll per case.

Only the three Web UI API fixture modules below opt in. Their requests,
handlers, authentication, socket I/O, shutdown and join remain real. Production
server defaults, unrelated tests, and explicitly supplied polling intervals
are unchanged; this is not a timeout reduction or a mocked network response.
"""
from __future__ import annotations

import pytest

_HTTP_API_TEST_FILES = frozenset({
    'test_webui.py',
    'test_webui_resources.py',
    'test_tsuitate_beta_control.py',
})


def _short_polling_server_type(server_type):
    class ShortPollingHTTPServer(server_type):
        def serve_forever(self, poll_interval=0.01):
            return super().serve_forever(poll_interval=poll_interval)

    return ShortPollingHTTPServer


@pytest.fixture(autouse=True)
def _short_poll_for_http_api_fixtures(request):
    if request.node.path.name not in _HTTP_API_TEST_FILES:
        return
    # Do not create a monkeypatch fixture for every unrelated test in the suite.
    monkeypatch = request.getfixturevalue('monkeypatch')
    from docich import webui

    # Replace only this module's constructor for this test. Do not monkeypatch
    # socketserver/http.server globally, shorten request deadlines, or skip
    # server.shutdown()/server_close(). monkeypatch restores the constructor.
    monkeypatch.setattr(webui, 'ThreadingHTTPServer',
                        _short_polling_server_type(webui.ThreadingHTTPServer))
