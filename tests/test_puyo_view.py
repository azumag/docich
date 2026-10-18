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
        "--live",
        "--inbox-file", str(spec.runtime_dir / puyo_program.CHAT_INBOX_FILENAME),
        "--state-dir", str(g.state_dir),
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


# ---------------------------------------------------------------------------
# live runtime (PR-4): the engine thread inside the view server process
# ---------------------------------------------------------------------------


def test_handler_serves_the_current_body_of_a_callable_provider():
    payload = match_payload_from_state(played_state(seed=7))
    lease = str(uuid.uuid4())
    live = {"ok": True, "phase": "MATCH", "user": payload["user"],
            "ai": payload["ai"], "winner": None,
            "participants": ["たろう"]}
    handler = handler_for(lambda: live, "g4-view", generation=4, lease_id=lease)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        # change the body between requests: the callable wins over copying
        status, body = _get(server.server_port, "/api/puyo")
        assert status == 200
        data = json.loads(body)
        assert data["participants"] == ["たろう"] and data["phase"] == "MATCH"
        assert data["runtime_id"] == "g4-view" and data["lease_id"] == lease
        live["participants"].append("じろう")
        status, body = _get(server.server_port, "/api/puyo")
        assert json.loads(body)["participants"] == ["たろう", "じろう"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def test_handler_reports_a_fixed_body_when_the_provider_fails():
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 handler_for(lambda: (_ for _ in ()).throw(RuntimeError("x")),
                                             "g4-view"))
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        status, body = _get(server.server_port, "/api/puyo")
        assert status == 200 and json.loads(body) == {"ok": False}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def test_live_match_consumes_the_chat_inbox_and_records_a_full_round(tmp_path):
    from docich.puyo_view import LiveMatch, _snapshot_file

    inbox = tmp_path / "chat_inbox.jsonl"
    inbox.write_text(
        '{"user_id":"u1","display_name":"たろう","text":"!puyo join"}\n'
        "not-json-line\n"
        '{"user_id":"u1","display_name":"たろう","text":"!puyo nonsense"}\n'
        '{"user_id":"u1","display_name":"たろう","text":"!puyo left"}\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    payload_path = tmp_path / "puyo_match.json"
    stop = threading.Event()
    live_match = LiveMatch(payload_path, inbox_file=inbox, state_dir=state_dir,
                           seed=7, stop=stop, sleep=lambda _s: None)

    body = live_match.body()
    live_match.step()  # first tick also drains the inbox (interval-gated)
    assert body["phase"] == "MATCH"
    assert live_match.body()["participants"] == ["たろう"]
    assert live_match.body()["phase"] == "MATCH"

    steps = 0
    while steps < 6000:
        steps += 1
        # each round end drops one terminal snapshot onto the payload file
        if payload_path.exists():
            break
        live_match.step()
    else:
        stop.set()
        pytest.fail("no terminal round snapshot was written")

    stored = json.loads(payload_path.read_text(encoding="utf-8"))
    assert stored["schema"] == 1
    assert stored["payload"]["schema"] == 1
    ledger = json.loads((state_dir / "puyo_corner_ledger.json").read_text())
    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["participants"] == ["u1"]
    assert live_match.body()["participants"] == ["たろう"]
    # the queue drained through the chat-driven side; no filters attached
    assert live_match.engine["phase"] in ("MATCH", "ROUND_OVER", "END")

    # the runner thread model: one daemon thread, bounded by the stop event
    second = LiveMatch(payload_path, seed=7, stop=stop, sleep=lambda _s: None)
    thread = second.start_thread()
    assert thread is not None
    stop.set()
    thread.join(5.0)
    assert not thread.is_alive()


def test_snapshot_writer_is_best_effort(tmp_path):
    from docich import puyo_corner as pc
    from docich.puyo_view import _snapshot_file

    # a broken path never raises: crash mirrors are best-effort only
    _snapshot_file(tmp_path / "missing-dir" / "puyo_match.json",
                   {"phase": "MATCH"})
    assert not (tmp_path / "missing-dir").exists()
    terminal = pc.new_state({}, seed=7)
    pc.start(terminal, now="2026-10-18T01:00:00+00:00")
    pc.play_match(terminal)
    if terminal["phase"] == "ROUND_OVER":
        pc.finish(terminal, now="2026-10-18T01:00:30+00:00")
    path = tmp_path / "snap.json"
    _snapshot_file(path, terminal)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema"] == 1 and stored["payload"]["schema"] == 1


def test_consume_inbox_never_replays_after_size_overflow(tmp_path, monkeypatch):
    """Regression (review finding): a file growing past the old rewind point
    must be drained forward once, never re-applied from offset 0."""
    from docich import puyo_corner as pc
    from docich.puyo_view import LiveMatch

    inbox = tmp_path / "chat_inbox.jsonl"
    live = LiveMatch(tmp_path / "puyo_match.json", inbox_file=inbox,
                     seed=7, sleep=lambda _s: None)
    line = b'{"user_id":"u1","display_name":"P1","text":"!puyo join"}\n'
    inbox.write_bytes(line * 218000)  # ~10.0 MB: over the old 10,000,200 rewind point

    calls = {"n": 0}
    real = pc.observe_chat

    def spy(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pc, "observe_chat", spy)
    while live._offset < inbox.stat().st_size:
        live.consume_inbox()
    assert calls["n"] == 218000  # every line observed exactly once
    assert live._offset == inbox.stat().st_size
    assert len(live.engine["roster"]) == 1
    live.consume_inbox()  # no appends: a pure no-op, never a replay
    assert calls["n"] == 218000
    assert live._offset == inbox.stat().st_size


def test_consume_inbox_treats_a_shrunk_file_as_rotation(tmp_path):
    from docich.puyo_view import LiveMatch

    inbox = tmp_path / "chat_inbox.jsonl"
    live = LiveMatch(tmp_path / "puyo_match.json", inbox_file=inbox,
                     seed=7, sleep=lambda _s: None)
    inbox.write_text(
        '{"user_id":"u1","display_name":"P1","text":"!puyo join"}\n'
        '{"user_id":"u2","display_name":"P2","text":"!puyo join"}\n',
        encoding="utf-8")
    live.consume_inbox()
    assert live._offset == inbox.stat().st_size
    # a shorter rewrite is a rotation: consumed forward from its start
    inbox.write_text('{"user_id":"u3","display_name":"P3","text":"!puyo join"}\n',
                     encoding="utf-8")
    live.consume_inbox()
    assert live._offset == inbox.stat().st_size
    assert {row["user_id"] for row in live.engine["roster"]} == {"u1", "u2", "u3"}


def test_snapshot_body_overlays_the_falling_pair():
    """Regression (review finding): the falling pair is visible in the live
    body while it moves, and the engine board itself stays untouched."""
    from docich import puyo_corner as pc
    from docich.puyo_view import _snapshot_body

    engine = new_state({}, seed=7)
    start(engine, now="2026-10-18T01:00:00+00:00")
    pair = engine["user_pair"]
    old_axis, old_satellite = pc.pair_cells(pair)
    body = _snapshot_body(engine)
    board = body["user"]["board"]
    assert board[old_axis[1]][old_axis[0]] == pair["axis"]
    assert board[old_satellite[1]][old_satellite[0]] == pair["satellite"]
    assert engine["user_board"][old_axis[1]][old_axis[0]] is None  # display-only
    pc.apply_action(engine, "user", "left")
    body = _snapshot_body(engine)
    board = body["user"]["board"]
    (ax, ay), (sx, sy) = pc.pair_cells(engine["user_pair"])
    assert board[ay][ax] == pair["axis"] and board[sy][sx] == pair["satellite"]
    # the previously occupied satellite cell is clear again as the pair moved
    assert board[old_satellite[1]][old_satellite[0]] is None


def test_consume_inbox_discards_an_oversized_line_through_its_newline(tmp_path):
    """Regression: an oversized line whose prefix is an exact multiple of the
    read window is dropped window by window; the offset lands exactly on the
    crafted tail, which the old code then applied as a fresh line. The fix
    keeps discarding through the line's newline instead."""
    from docich.puyo_view import INBOX_READ_BUDGET_BYTES, LiveMatch

    inbox = tmp_path / "chat_inbox.jsonl"
    live = LiveMatch(tmp_path / "puyo_match.json", inbox_file=inbox,
                     seed=7, sleep=lambda _s: None)
    # prefix exactly 2 windows: after the second drop the offset lands at the
    # crafted tail, so the old code parsed and applied it on the next poll
    giant = b"x" * (INBOX_READ_BUDGET_BYTES * 2) \
        + b'{"user_id":"u9","display_name":"P9","text":"!puyo join"}'
    inbox.write_bytes(giant + b"\n")
    while live._offset < inbox.stat().st_size:
        live.consume_inbox()
    assert live.engine["roster"] == []  # the crafted tail was never applied
    assert live._offset == inbox.stat().st_size
    with inbox.open("ab") as stream:
        stream.write(b'{"user_id":"u1","display_name":"P1","text":"!puyo join"}\n')
    live.consume_inbox()
    assert [row["user_id"] for row in live.engine["roster"]] == ["u1"]


def test_consume_inbox_keeps_discarding_across_eof_until_newline(tmp_path):
    """Regression: EOF does not end the discard. An unterminated line can
    still be appended to; its continuation — even a crafted valid join — is
    part of the same line and must stay discarded until the newline."""
    from docich.puyo_view import INBOX_READ_BUDGET_BYTES, LiveMatch

    inbox = tmp_path / "chat_inbox.jsonl"
    live = LiveMatch(tmp_path / "puyo_match.json", inbox_file=inbox,
                     seed=7, sleep=lambda _s: None)
    # prefix exactly one window, then a crafted join with NO trailing newline
    inbox.write_bytes(b"x" * INBOX_READ_BUDGET_BYTES
                      + b'{"user_id":"u9","display_name":"P9","text":"!puyo join"}')
    live.consume_inbox()  # window full of the prefix: drop, enter discard
    live.consume_inbox()  # EOF: the crafted tail is skipped, discard persists
    assert live._offset == inbox.stat().st_size
    assert live.engine["roster"] == []
    # the line is appended to: the continuation is still the same line
    with inbox.open("ab") as stream:
        stream.write(b'{"user_id":"u9","display_name":"P9","text":"!puyo join"}\n')
    live.consume_inbox()
    assert live._offset == inbox.stat().st_size
    assert live.engine["roster"] == []  # discarded through the newline
    # a real new line after that newline applies normally
    with inbox.open("ab") as stream:
        stream.write(b'{"user_id":"u1","display_name":"P1","text":"!puyo join"}\n')
    live.consume_inbox()
    assert [row["user_id"] for row in live.engine["roster"]] == ["u1"]


def test_consume_inbox_keeps_discarding_across_eof_for_multi_window_giant(tmp_path):
    """Regression: a giant line spanning several read windows must keep its
    discard state when it reaches EOF. Otherwise an appended continuation
    (even a crafted valid join) is re-interpreted as a fresh chat line."""
    from docich.puyo_view import INBOX_READ_BUDGET_BYTES, LiveMatch

    inbox = tmp_path / "chat_inbox.jsonl"
    live = LiveMatch(tmp_path / "puyo_match.json", inbox_file=inbox,
                     seed=7, sleep=lambda _s: None)
    # two full windows of junk, no newline, ending exactly at EOF
    inbox.write_bytes(b"x" * (INBOX_READ_BUDGET_BYTES * 2))
    while live._offset < inbox.stat().st_size:
        live.consume_inbox()
    assert live._discarding is True
    assert live._offset == inbox.stat().st_size
    # a crafted join appended as a continuation of the same physical line
    with inbox.open("ab") as stream:
        stream.write(b'{"user_id":"u9","display_name":"P9","text":"!puyo join"}\n')
    live.consume_inbox()
    assert live._offset == inbox.stat().st_size
    assert live.engine["roster"] == []
