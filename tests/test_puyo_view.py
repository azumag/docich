"""Read-only ぷよぷよクローン view and program-adapter contracts (offline only)."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sys
import threading
import uuid

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter  # noqa: E402
from docich.adapters import puyo_program  # noqa: E402
from docich.adapters import program as program_adapter  # noqa: E402
from docich.puyo_corner import finish, new_state, play_match, start  # noqa: E402
from docich.puyo_view import (  # noqa: E402
    DEFAULT_PORT,
    HTML,
    VIEW_NAME,
    PuyoViewError,
    handler_for,
    load_payload,
    match_payload_from_state,
    validate_match_payload,
)
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def played_state(seed=7):
    state = new_state({}, seed=seed)
    start(state, now="2026-10-18T01:00:00+00:00")
    play_match(state)
    if state["phase"] == "ROUND_OVER":
        finish(state, now="2026-10-18T01:00:09+00:00")
    return state


def stored_body(seed=7):
    payload = match_payload_from_state(played_state(seed))
    return json.dumps({"schema": 1, "payload": payload}, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def served(tmp_path):
    """One loopback view server bound to an ephemeral port."""
    path = tmp_path / "puyo_match.json"
    path.write_bytes(stored_body())
    payload = load_payload(path)
    lease = str(uuid.uuid4())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_for(payload, "g4-view", generation=4, lease_id=lease),
    )
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        yield payload, server.server_port, lease
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def _get(port, path, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Host": host} if host else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_match_payload_from_a_played_state_validates():
    state = played_state(seed=7)
    payload = match_payload_from_state(state)
    assert payload["schema"] == 1 and payload["ruleset_id"] == state["ruleset_id"]
    assert payload["outcome"] in ("user_win", "ai_win", "timeout", "failed")
    assert set(payload) >= {"user", "ai", "winner", "frames", "actions"}
    for side in ("user", "ai"):
        summary = payload[side]
        assert len(summary["board"]) == 12 and all(len(row) == 6 for row in summary["board"])
        assert 0 <= summary["score"] <= 10 ** 9
    # a second identical self-play yields an identical payload (determinism)
    assert match_payload_from_state(played_state(seed=7)) == payload


def test_validate_match_payload_rejects_bad_shapes():
    good = match_payload_from_state(played_state(seed=7))
    assert validate_match_payload(good) == good
    with pytest.raises(PuyoViewError):
        validate_match_payload({"schema": 1})
    for field, value in (
        ("schema", 2),
        ("ruleset_id", ""),
        ("outcome", "drawn"),
        ("winner", "ai_plus"),
        ("frames", -1),
        ("started_at", ""),
    ):
        broken = {**good, field: value}
        with pytest.raises(PuyoViewError):
            validate_match_payload(broken)
    short_board = {**good, "user": {**good["user"], "board": good["user"]["board"][:11]}}
    with pytest.raises(PuyoViewError):
        validate_match_payload(short_board)


def test_load_payload_missing_and_corrupt_files_fail_closed(tmp_path):
    with pytest.raises(PuyoViewError):
        load_payload(tmp_path / "missing.json")
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    with pytest.raises(PuyoViewError):
        load_payload(corrupt)
    unwrapped = tmp_path / "unwrapped.json"
    unwrapped.write_text(json.dumps({"payload": {}}), encoding="utf-8")
    with pytest.raises(PuyoViewError):
        load_payload(unwrapped)


def test_load_payload_revalidates_the_adapter_written_file(tmp_path):
    path = tmp_path / "puyo_match.json"
    path.write_bytes(stored_body(seed=42))
    payload = load_payload(path)
    assert payload["ok"] is True
    assert payload["user"]["board"] or payload["ai"]["board"]


def test_load_payload_rejects_a_missing_file_without_leaking_the_path(tmp_path, capsys):
    with pytest.raises(PuyoViewError) as exc:
        load_payload(tmp_path / "secret-name.json")
    assert "secret-name" not in str(exc.value)


def test_handler_rejects_unsafe_runtime_ids_and_generations():
    payload = match_payload_from_state(played_state(seed=7))
    with pytest.raises(PuyoViewError):
        handler_for(payload, "bad id spaces")
    with pytest.raises(PuyoViewError):
        handler_for(payload, "g4-view", generation=0)
    with pytest.raises(PuyoViewError):
        handler_for(payload, "g4-view", lease_id="not-a-uuid")


def test_view_serves_broadcast_html_and_the_runtime_bound_projection(served):
    payload, port, lease = served
    status, body = _get(port, "/broadcast")
    assert status == 200 and "ぷよぷよクローン" in body and "/api/puyo" in body
    status, body = _get(port, f"/api/puyo")
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True and data["runtime_id"] == "g4-view"
    assert data["generation"] == 4 and data["lease_id"] == lease
    assert isinstance(data["user"]["board"], list)
    status, _ = _get(port, "/api/unknown")
    assert status == 404


def test_view_rejects_a_foreign_host_header(served):
    _payload, port, _lease = served
    status, _body = _get(port, "/api/puyo", host="evil.example:1234")
    assert status == 403


def test_serve_rejects_unboundable_ports():
    from docich.puyo_view import serve

    payload = match_payload_from_state(played_state(seed=7))
    for bad_port in (1, 80, 65536):
        with pytest.raises(PuyoViewError):
            serve(payload, port=bad_port)


def test_main_fails_closed_without_a_payload(tmp_path, capsys):
    from docich.puyo_view import main

    exit_code = main([
        "--payload-file", str(tmp_path / "missing.json"),
        "--port", "0",
    ])
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out.strip())["ok"] is False


# ---------------------------------------------------------------------------
# program adapter (GameSwitch runtime registration; no network)
# ---------------------------------------------------------------------------


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    from types import SimpleNamespace

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


def test_puyo_program_view_is_registered_and_respects_the_game_viewport(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    # The shared broadcast helper builds the browser command; stub its
    # browser discovery like the other view tests do.
    monkeypatch.setattr(program_adapter, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, puyo_program.PuyoProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    assert adapter.dashboard_port == DEFAULT_PORT
    assert adapter.payload_path == spec.runtime_dir / "puyo_match.json"
    assert adapter._game_command() == [
        sys.executable, "-m", "docich.puyo_view",
        "--payload-file", str(adapter.payload_path),
        "--port", str(DEFAULT_PORT),
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


def test_puyo_preflight_plays_once_and_stores_the_transient_payload(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    monkeypatch.setattr(puyo_program.ProgramViewAdapter, "preflight", lambda *_: None)

    adapter.preflight(10 ** 9, None)

    stored = json.loads(adapter.payload_path.read_text(encoding="utf-8"))
    assert stored["schema"] == 1
    payload = stored["payload"]
    assert payload["schema"] == 1 and payload["outcome"] in (
        "user_win", "ai_win", "timeout", "failed")
    # one bounded match: the self-play must have reached a terminal phase
    assert payload["ended_at"]
    assert load_payload(adapter.payload_path)["ok"] is True
