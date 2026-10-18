"""Broadcast view contract for the participatory othello MVP (Issue #699).

Loopback-only presentation of the room Worker's spectator snapshot. No
operator token, no Twitch identity, no request ever reaches stdout logs.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from threading import Thread
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import othello_view as ov

SNAPSHOT_PLAYING = {
    "phase": "PLAYING",
    "roomId": "room-1",
    "board": [[0] * 8 for _ in range(8)],
    "turn": "black",
    "scores": {"black": 3, "white": 3},
    "movesPlayed": 2,
    "outcome": None,
    "yourTurn": True,
}
SNAPSHOT_IDLE = {"phase": "IDLE", "roomId": None, "board": None, "turn": None,
                 "scores": None, "movesPlayed": 0, "outcome": None}
SNAPSHOT_FINISHED = {
    "phase": "FINISHED", "roomId": "room-1",
    "board": [[1] * 8 for _ in range(8)],
    "scores": {"black": 64, "white": 0}, "movesPlayed": 60,
    "outcome": "viewer_win",
    "result": {"black": 64, "white": 0, "outcome": "viewer_win", "ended_at": "..."},
}


def test_japanese_page_declares_notranslate_markers():
    """Repo standard markers (broadcast-html-view-standard.md)."""
    from docich.broadcast_view import japanese_page_markers_missing
    assert japanese_page_markers_missing(ov.HTML) == []


def test_control_url_validation_is_fail_closed():
    assert ov.validate_control_url("http://127.0.0.1:8800/room/snapshot")
    assert ov.validate_control_url("https://othello.example.workers.dev/room/snapshot")
    for bad in ("", "ftp://x/", "http://example.com/other/path",
                "http://127.0.0.1:8800/room/../../admin",
                "https://host/snapshot?token=secret"):
        try:
            ov.validate_control_url(bad)
        except ov.OthelloViewError:
            continue
        raise AssertionError(bad)


def test_project_snapshot_rebuilds_and_bounds_payload():
    data = ov.project_snapshot(SNAPSHOT_PLAYING, "g4-a1b2c3d4", 4, "0f0e0d0c-1111-2222-3333-444455556666")
    assert data["ok"] is True
    assert data["phase"] == "PLAYING"
    assert data["roomId"] == "room-1"
    assert data["yourTurn"] is True
    assert data["scores"] == {"black": 3, "white": 3}
    # The payload never carries names or raw state beyond the projection.
    assert "viewer" not in data


def test_project_snapshot_idle_has_no_board():
    data = ov.project_snapshot(SNAPSHOT_IDLE, "g4-a1b2c3d4", 4, "0f0e0d0c-1111-2222-3333-444455556666")
    assert data["ok"] is True
    assert data["phase"] == "IDLE"
    assert "board" not in data


def test_project_snapshot_rejects_bad_shape():
    bad = {**SNAPSHOT_PLAYING, "board": [[9] * 8 for _ in range(8)]}
    try:
        ov.project_snapshot(bad, "g4-a1b2c3d4", 4, "0f0e0d0c-1111-2222-3333-444455556666")
    except ov.OthelloViewError:
        pass
    else:
        raise AssertionError("corrupt board must fail closed")


def test_project_snapshot_rejects_bad_runtime_identity():
    lease = "0f0e0d0c-1111-2222-3333-444455556666"
    for args in (("bad runtime", 4, lease), ("g4-a1b2c3d4", 0, lease), ("g4-a1b2c3d4", 4, "not-a-uuid")):
        try:
            ov.project_snapshot(SNAPSHOT_PLAYING, *args)
        except ov.OthelloViewError:
            continue
        raise AssertionError(args)


def test_handler_serves_page_and_api():
    """The loopback server answers / and /api/othello with JSON snapshots."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.error import HTTPError

    control = json.dumps(SNAPSHOT_FINISHED).encode("utf-8")

    class Control(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(control)))
            self.end_headers()
            self.wfile.write(control)

        def log_message(self, *_):
            pass

    control_server = HTTPServer(("127.0.0.1", 0), Control)
    Thread(target=control_server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{control_server.server_port}/room/snapshot"

    handler = ov.handler_for("g4-a1b2c3d4", 4, "0f0e0d0c-1111-2222-3333-444455556666",
                             control_url=url)
    server = HTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/broadcast", timeout=2) as r:
            html = r.read().decode("utf-8")
        assert "<html lang=\"ja\"" in html and "notranslate" in html

        with urllib.request.urlopen(base + "/api/othello", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["outcome"] == "viewer_win"
        assert data["runtime_id"] == "g4-a1b2c3d4"

        try:
            urllib.request.urlopen(base + "/nope", timeout=2)
            raise AssertionError("unknown path must 404")
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        control_server.shutdown()
