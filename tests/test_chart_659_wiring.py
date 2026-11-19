"""Offline production-wiring contracts for the chart corner (issue #659).

Covers the four CLINE partial-implementation bugs plus the broadcast contract:
the adapter is GameSwitch-registered, generates corner_659 content offline,
uses the shared browser standard, and the loopback view serves that content
fail-closed.  No network, no Twitch, no real browser.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sys
import threading
import uuid
from types import SimpleNamespace

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import chart_view  # noqa: E402
from docich.adapters import make_coordinator_adapter, program  # noqa: E402
from docich.adapters.chart_659_program import (  # noqa: E402
    VIEW_NAME, ChartProgramViewAdapter, make_chart_view_adapter,
)
from docich.broadcast_view import (  # noqa: E402
    BROADCAST_TRANSLATE_ARGS, JAPANESE_LANG_ARG, japanese_page_markers_missing,
)
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        repo_root=ROOT,
        display=SimpleNamespace(
            name=":98", width=1280, height=720,
            viewport_x=0, viewport_y=90, viewport_width=960, viewport_height=540,
        ),
    )


def _spec(tmp_path, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game=VIEW_NAME, adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _adapter(tmp_path, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")
    return make_coordinator_adapter(_global(tmp_path), _spec(tmp_path))


# --- registration / generation ------------------------------------------------


def test_adapter_is_game_switch_registered(tmp_path):
    adapter = _adapter(tmp_path)
    assert isinstance(adapter, ChartProgramViewAdapter)
    with pytest.raises(Exception):
        make_chart_view_adapter(_global(tmp_path),
                                _spec(tmp_path).replace(game="not-chart"))


def test_game_command_uses_python_module_not_missing_bin(tmp_path):
    command = _adapter(tmp_path)._game_command()
    assert command[1:3] == ["-m", "docich.chart_view"]
    assert "--payload-file" in command and "--port" in command
    assert "-m" in command and "serve" not in command  # no bin/docich chart-659 serve


def test_generation_and_view_payload_are_offline_and_bounded(tmp_path):
    adapter = _adapter(tmp_path)
    state = adapter.build_payload()
    payload = chart_view.build_view_payload(state)
    assert payload["ok"] is True
    assert payload["corner"] == chart_view.CORNER_ID
    assert payload["symbol"] == "USD/JPY"
    assert 1 <= payload["fps"] <= 60
    assert 1 <= len(payload["frames"]) <= 300
    assert payload["frames"][0]["symbol"] == "USD/JPY"
    assert len(payload["candles"]) <= 400
    assert all(row["side"] in {"buy", "sell", "hold"} for row in payload["signals"])
    assert payload["frameMs"] >= 16
    # The on-disk state file was actually produced by corner_659.
    assert (tmp_path / "run" / "chart_corner_659.json").exists()


def test_write_payload_is_validated_by_view_loader(tmp_path):
    adapter = _adapter(tmp_path)
    payload = adapter.write_payload()
    assert adapter.payload_path.exists()
    assert chart_view.load_payload(adapter.payload_path)["symbol"] == payload["symbol"]


# --- broadcast standard --------------------------------------------------------


def test_viewer_command_follows_broadcast_standard(tmp_path, monkeypatch):
    command = " ".join(_adapter(tmp_path, monkeypatch)._viewer_command())
    for argument in (*BROADCAST_TRANSLATE_ARGS, JAPANESE_LANG_ARG):
        assert argument in command
    assert "--app=http://127.0.0.1:" in command
    assert "--window-size=960,540" in command


def test_html_declares_japanese_notranslate_markers():
    assert japanese_page_markers_missing(chart_view.HTML) == []


# --- readiness identity bug (runtime_id must match the spec) --------------------


def test_readiness_identity_uses_spec_runtime_id(tmp_path):
    adapter = _adapter(tmp_path)
    spec = adapter.spec
    good = {
        "ok": True, "corner": chart_view.CORNER_ID,
        "runtime_id": spec.runtime_id, "generation": spec.generation,
        "lease_id": spec.lease_id, "frames": [{"frameIndex": 0}],
    }
    assert adapter._is_ready_response(good, spec) is True
    assert adapter._is_ready_response({**good, "runtime_id": "runtime_id"}, spec) is False
    assert adapter._is_ready_response({**good, "generation": spec.generation + 1}, spec) is False
    assert adapter._is_ready_response({**good, "ok": False}, spec) is False


# --- fail-closed view server ----------------------------------------------------


def _start_server(payload, runtime_id, generation, lease_id):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        chart_view.handler_for(payload, runtime_id, generation=generation, lease_id=lease_id),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(port, path, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request("GET", path, headers={"Host": host} if host else {})
    response = conn.getresponse()
    body = response.read()
    status = response.status
    conn.close()
    return status, body


def test_view_serves_html_and_identity_api(tmp_path):
    adapter = _adapter(tmp_path)
    payload = chart_view.build_view_payload(adapter.build_payload())
    spec = adapter.spec
    server, thread = _start_server(payload, spec.runtime_id, spec.generation, spec.lease_id)
    try:
        port = server.server_port
        status, body = _get(port, "/broadcast")
        assert status == 200 and b"translate=\"no\"" in body
        status, body = _get(port, chart_view.READY_PATH)
        data = json.loads(body)
        assert status == 200 and data["ok"] is True
        assert data["runtime_id"] == spec.runtime_id
        assert data["generation"] == spec.generation
        assert data["lease_id"] == spec.lease_id
        status, _ = _get(port, chart_view.READY_PATH, host="evil.example")
        assert status == 403
        status, _ = _get(port, "/nope")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(schema_version=99),
    lambda s: s.update(audio={"lines": [1]}),
    lambda s: s.update(commentary=[{"text": 5}]),
    lambda s: s.update(signals=[{"side": "???", "marker": "?"}]),
    lambda s: s.update(animation_frames=[]),
])
def test_malformed_payload_is_rejected(tmp_path, mutate):
    state = _adapter(tmp_path).build_payload()
    mutate(state)
    with pytest.raises(chart_view.ChartViewError):
        chart_view.build_view_payload(state)


def test_frame_signal_timestamps_survive_payload_conversion(tmp_path):
    state = _adapter(tmp_path).build_payload()
    first = dict(state["animation_frames"][0])
    state["animation_frames"] = [
        {**first, "frame_index": 0, "signal": {"timestamp": 1700000002}},
        {**first, "frame_index": 1, "signal": None},
        {**first, "frame_index": 2, "signal": {"timestamp": 1700000003}},
    ]
    frames = chart_view.build_view_payload(state)["frames"]
    assert [f["signal"] for f in frames] == [
        {"timestamp": 1700000002}, None, {"timestamp": 1700000003},
    ]


@pytest.mark.parametrize("signal", [[], {}, {"timestamp": float("inf")}])
def test_invalid_frame_signal_is_rejected(tmp_path, signal):
    state = _adapter(tmp_path).build_payload()
    state["animation_frames"][0]["signal"] = signal
    with pytest.raises(chart_view.ChartViewError):
        chart_view.build_view_payload(state)
