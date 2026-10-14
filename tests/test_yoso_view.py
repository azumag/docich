"""Read-only 予想王 view and program-adapter contracts (no Twitch, no production)."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import prediction_leaderboard as lb  # noqa: E402
from docich.adapters import make_coordinator_adapter  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.adapters import cli_game, yoso_program  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.yoso_view import (  # noqa: E402
    DEFAULT_PORT,
    VIEW_NAME,
    YosoViewError,
    board,
    display_name,
    handler_for,
)
from test_prediction_leaderboard import predictor, with_top  # noqa: E402


def _ledger(tmp_path, rounds):
    ledger = lb.new_ledger()
    for remote in rounds:
        assert lb.record(ledger, remote, corner="hanjuku") is True
    lb.save(tmp_path / lb.FILE, ledger)
    return ledger


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        display=SimpleNamespace(
            name=":98", width=1280, height=720,
            viewport_x=0, viewport_y=90,
            viewport_width=960, viewport_height=540,
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


def _response(data, status=200):
    class Response:
        def __init__(self):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _limit):
            return json.dumps(data).encode("utf-8")

    return Response()


@pytest.fixture
def served(tmp_path):
    """One loopback view server bound to an ephemeral port."""
    state = tmp_path / "run"
    state.mkdir(parents=True)
    _ledger(state, [
        with_top("p1", {"o1": [predictor("u1", "alice"), predictor("u2", "bob", outcome="o2")]}),
        with_top("p2", {"o1": [predictor("u1", "alice"), predictor("u2", "bob", outcome="o2")]},
                 ended="2026-10-02T00:00:00Z"),
        with_top("p3", {"o1": [predictor("u1", "alice")]}, ended="2026-10-03T00:00:00Z"),
    ])
    lease = str(uuid.uuid4())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_for(state, "g4-view", generation=4, lease_id=lease),
    )
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        yield state, server.server_port, lease
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def _get(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_board_ranks_hits_and_keeps_one_shot_viewers_off_the_board(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    _ledger(state, [
        with_top("p1", {"o1": [predictor("u1", "alice"), predictor("u2", "bob", outcome="o2")]}),
        with_top("p2", {"o1": [predictor("u1", "alice")]}, ended="2026-10-02T00:00:00Z"),
        with_top("p3", {"o1": [predictor("u1", "alice"), predictor("u3", "carol", outcome="o2")]},
                 ended="2026-10-03T00:00:00Z"),
    ])

    data = board(state, clock=lambda: 1234.0)

    assert data["ok"] is True and data["updated_at"] == 1234
    assert data["rounds"] == 3 and data["viewers"] == 3
    assert data["min_rounds"] == lb.DEFAULT_MIN_ROUNDS
    assert [row["user_name"] for row in data["ranking"]] == ["Alice"]
    alice = data["ranking"][0]
    assert (alice["rank"], alice["rounds"], alice["hits"]) == (1, 3, 3)
    assert alice["hit_rate"] == 1.0 and alice["net"] == 300


def test_board_missing_ledger_is_an_empty_board_and_invalid_ledger_fails_closed(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    assert board(state)["ranking"] == []

    (state / lb.FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(lb.LeaderboardError):
        board(state)


def test_board_bounds_and_sanitizes_display_names(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    forged = with_top("p1", {"o1": [predictor("u1", "alice")]})
    forged["outcomes"][0]["top_predictors"][0]["user_name"] = "  \u202eAli\u0000ce  "
    _ledger(state, [forged])

    assert board(state)["ranking"] == []
    data = board(state, min_rounds=1)
    assert data["ranking"][0]["user_name"] == "Alice"
    with pytest.raises(YosoViewError):
        board(state, min_rounds=0)
    with pytest.raises(YosoViewError):
        board(state, limit=0)
    assert display_name("\u200b") == "（表示名なし）"
    assert len(display_name("x" * 200)) == 24


def test_view_serves_broadcast_html_and_a_runtime_bound_projection(served):
    _state, port, lease = served

    status, html = _get(port, "/broadcast")
    assert status == 200 and "予想王" in html and "上位予想者のみ集計" in html
    assert "GET /helix/predictions" in html
    # 日本語UIのブラウザ翻訳コントロール（日本語/English）を出さない。
    # 予想王は常に日本語表示で、翻訳した表示は集計の意味を変えてしまう。
    assert 'lang="ja" translate="no"' in html
    assert '<meta name="google" content="notranslate">' in html

    status, raw = _get(port, "/api/yosou")
    data = json.loads(raw)
    assert status == 200 and data["ok"] is True
    assert (data["runtime_id"], data["generation"], data["lease_id"]) == ("g4-view", 4, lease)
    # Bob appears in only 2 of the 3 recorded rounds, so min_rounds keeps him off.
    assert [row["user_name"] for row in data["ranking"]] == ["Alice"]
    assert data["rounds"] == 3 and data["min_rounds"] == lb.DEFAULT_MIN_ROUNDS

    status, _ = _get(port, "/nope")
    assert status == 404


def test_view_rejects_a_foreign_host_header(served):
    _state, port, _lease = served
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("GET", "/api/yosou", skip_host=True)
        connection.putheader("Host", f"example.invalid:{port}")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
    finally:
        connection.close()


def test_view_reports_unavailable_for_a_broken_ledger(served):
    state, port, _lease = served
    (state / lb.FILE).write_text("[]", encoding="utf-8")

    status, raw = _get(port, "/api/yosou")

    assert status == 503 and json.loads(raw)["ok"] is False


@pytest.mark.parametrize("generation", [True, 0, -1, "4"])
def test_view_handler_rejects_invalid_generation(tmp_path, generation):
    with pytest.raises(YosoViewError):
        handler_for(tmp_path, "g4-view", generation=generation)


def test_yoso_program_view_is_registered_and_respects_the_game_viewport(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(yoso_program, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, yoso_program.YosoProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    assert adapter._game_command() == [
        sys.executable, "-m", "docich.yoso_view",
        "--state-dir", str(g.state_dir), "--port", str(DEFAULT_PORT),
        "--runtime-id", spec.runtime_id, "--generation", str(spec.generation),
        "--lease-id", spec.lease_id,
    ]
    assert adapter._game_session_env()["PYTHONPATH"].endswith("src")
    viewer = adapter._viewer_command()
    assert viewer[0] == sys.executable
    assert ("--x", "0") == (viewer[viewer.index("--x")], viewer[viewer.index("--x") + 1])
    assert ("--y", "90") == (viewer[viewer.index("--y")], viewer[viewer.index("--y") + 1])
    assert ("--width", "960") == (viewer[viewer.index("--width")], viewer[viewer.index("--width") + 1])
    assert ("--height", "540") == (viewer[viewer.index("--height")], viewer[viewer.index("--height") + 1])
    assert f"--app=http://127.0.0.1:{DEFAULT_PORT}/broadcast" in viewer
    assert "--window-size=960,540" in viewer


def test_yoso_preflight_accepts_a_missing_ledger_and_rejects_a_broken_one(tmp_path, monkeypatch):
    g = _global(tmp_path)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    monkeypatch.setattr(yoso_program.ProgramViewAdapter, "preflight", lambda *_: None)

    adapter.preflight(time.monotonic() + 10, None)  # empty board is valid

    g.state_dir.mkdir(parents=True, exist_ok=True)
    (g.state_dir / lb.FILE).write_text("{broken", encoding="utf-8")
    with pytest.raises(AdapterError, match="予想台帳"):
        adapter.preflight(time.monotonic() + 10, None)


@pytest.mark.parametrize("identity", ["runtime_id", "generation", "lease_id"])
def test_yoso_readiness_rejects_another_runtime(identity, tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "ranking": [],
    }
    data[identity] = {
        "runtime_id": "g3-old",
        "generation": spec.generation - 1,
        "lease_id": str(uuid.uuid4()),
    }[identity]
    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(yoso_program, "urlopen", lambda *_args, **_kwargs: _response(data))
    now = time.monotonic()
    checks = iter((now, now + 5))
    monkeypatch.setattr(yoso_program, "monotonic", lambda: next(checks))

    with pytest.raises(ReadinessTimeoutError, match="not ready"):
        adapter.readiness(now + 5, None)


def test_yoso_readiness_accepts_only_the_exact_runtime_response(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "ranking": [],
    }
    requests = []

    def open_local(url, *, timeout):
        requests.append((url, timeout))
        return _response(data)

    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(yoso_program, "urlopen", open_local)

    adapter.readiness(time.monotonic() + 5, None)

    assert requests == [(f"http://127.0.0.1:{DEFAULT_PORT}/api/yosou", 2.0)]
