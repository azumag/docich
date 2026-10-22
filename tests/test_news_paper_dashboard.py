"""Offline tests for the read-only news-paper dashboard (N07).

No network beyond an ephemeral loopback server; no LLM/broker.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from docich.trading.news_paper.dashboard import (
    STATUS_ROUTE,
    DashboardConfig,
    DashboardConfigError,
    load_dashboard_config,
    make_server,
)


def _serve(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _request(server, path: str, method: str = "GET"):
    host, port = server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_disabled_dashboard_is_none(tmp_path: Path) -> None:
    path = tmp_path / "dash.toml"
    path.write_text("[dashboard]\nenabled = false\n", encoding="utf-8")
    assert load_dashboard_config(path) is None
    path.write_text("[worker]\nenabled = false\n", encoding="utf-8")
    assert load_dashboard_config(path) is None


def test_non_loopback_host_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dash.toml"
    path.write_text(
        f"""[dashboard]
enabled = true
host = "0.0.0.0"
port = 8799
state_dir = "{tmp_path}/state"
run_id = "r"
scenario_id = "s"
account_id = "a"
""",
        encoding="utf-8",
    )
    with pytest.raises(DashboardConfigError) as excinfo:
        load_dashboard_config(path)
    assert excinfo.value.code == "VALUE_OUT_OF_RANGE"


def test_invalid_port_and_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dash.toml"
    base = (
        '[dashboard]\nenabled = true\nhost = "127.0.0.1"\nport = {port}\n'
        f'state_dir = "{tmp_path}/state"\nrun_id = "r"\nscenario_id = "s"\naccount_id = "a"\n'
    )
    path.write_text(base.format(port=0), encoding="utf-8")
    with pytest.raises(DashboardConfigError):
        load_dashboard_config(path)
    path.write_text(base.format(port=8799) + 'extra = 1\n', encoding="utf-8")
    with pytest.raises(DashboardConfigError) as excinfo:
        load_dashboard_config(path)
    assert excinfo.value.code == "UNKNOWN_KEY"


def test_enabled_config_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "dash.toml"
    path.write_text(
        f"""[dashboard]
enabled = true
host = "127.0.0.1"
port = 8799
state_dir = "{tmp_path}/state"
run_id = "run1"
scenario_id = "scenario1"
account_id = "acct1"
""",
        encoding="utf-8",
    )
    config = load_dashboard_config(path)
    assert config is not None
    assert config.host == "127.0.0.1"
    assert config.port == 8799
    assert config.run_id == "run1"


def test_routes_headers_and_html_escaping(tmp_path: Path) -> None:
    config = DashboardConfig(
        state_dir=tmp_path, run_id="run1", scenario_id="scenario1", account_id="acct1", port=0
    )
    payload = {
        "state": "ok",
        "mode": "paper",
        "live_eligible": False,
        "run_id": "<script>alert(1)</script>",
        "scenario_id": "scenario1",
        "account_id": "acct1",
    }
    server = make_server(config, status_provider=lambda: payload)
    thread = _serve(server)
    try:
        code, headers, body = _request(server, "/")
        assert code == 200
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in headers
        assert headers["Referrer-Policy"] == "no-referrer"
        text = body.decode("utf-8")
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text

        code, headers, body = _request(server, STATUS_ROUTE)
        assert code == 200
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body)["run_id"] == "<script>alert(1)</script>"

        code, _, _ = _request(server, STATUS_ROUTE, method="POST")
        assert code == 405
        code, _, _ = _request(server, "/unknown")
        assert code == 404

        code, headers, body = _request(server, "/", method="HEAD")
        assert code == 200
        assert body == b""
        assert headers["Content-Type"].startswith("text/html")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_provider_failure_is_503_without_leaking_details(tmp_path: Path) -> None:
    config = DashboardConfig(
        state_dir=tmp_path, run_id="run1", scenario_id="scenario1", account_id="acct1", port=0
    )

    def boom():
        raise RuntimeError("secret detail must not leak")

    server = make_server(config, status_provider=boom)
    thread = _serve(server)
    try:
        code, _, body = _request(server, "/")
        assert code == 503
        assert b"secret detail" not in body
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_default_provider_reports_empty_state(tmp_path: Path) -> None:
    config = DashboardConfig(
        state_dir=tmp_path, run_id="run1", scenario_id="scenario1", account_id="acct1", port=0
    )
    server = make_server(config)
    thread = _serve(server)
    try:
        code, _, body = _request(server, STATUS_ROUTE)
        data = json.loads(body)
        assert code == 200
        assert data["state"] == "empty"
        assert data["mode"] == "paper"
        assert data["live_eligible"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
