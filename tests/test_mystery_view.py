"""Offline view tests for the mystery case board (#684)."""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import mystery_view
from docich.mystery_case import generate, reveal
from docich.mystery_view import (
    DEFAULT_PORT,
    MAX_PAYLOAD_BYTES,
    MysteryViewError,
    PAYLOAD_FILENAME,
    handler_for,
    payload_path,
    read_payload,
    write_payload,
)


def _payload(tmp_path, *, revealed=False):
    case = generate(31)
    return {
        "schema": "mystery-view",
        "version": 1,
        "runtime_id": "g4-a1b2c3d4",
        "generation": 4,
        "phase": "reveal" if revealed else "intro",
        "case": mystery_view_public_board(case, revealed),
        "poll": {"question": "次に調べる場所は？", "status": "solo", "choices": [
            {"label": "書斎", "value": "study", "votes": 3, "winner": True}]},
        "prediction": {"status": "resolved", "resolved": True, "hit": True},
        "updated_at": 12345,
    }


def mystery_view_public_board(case, revealed):
    from docich.mystery_case import public_board

    return public_board(case, revealed=reveal(case) if revealed else None)


def test_payload_roundtrip_and_fail_soft_reads(tmp_path):
    path = payload_path(tmp_path)
    assert read_payload(path) is None  # absent → preparing board, not an error
    payload = _payload(tmp_path)
    write_payload(path, payload)
    assert read_payload(path) == payload

    path.write_text('{"schema": "other"}', encoding="utf-8")
    assert read_payload(path) is None
    path.write_text("not json", encoding="utf-8")
    assert read_payload(path) is None
    path.write_text('{"schema": "mystery-view", "phase": "weird", "case": {}}',
                    encoding="utf-8")
    assert read_payload(path) is None
    path.write_text('x' * (MAX_PAYLOAD_BYTES + 1), encoding="utf-8")
    assert read_payload(path) is None

    big = dict(payload)
    big["case"] = {"commitment": "x" * 64, "filler": "y" * (MAX_PAYLOAD_BYTES + 1)}
    with pytest.raises(MysteryViewError):
        write_payload(path, big)
    # The failed write left the previous payload untouched (atomic replace).
    assert read_payload(path) is None or read_payload(path) == payload


def test_handler_serves_html_and_the_generation_bound_payload(tmp_path):
    path = payload_path(tmp_path)
    write_payload(path, _payload(tmp_path))
    Handler = handler_for(path, "g4-a1b2c3d4", generation=4, lease_id=str(uuid.uuid4()))

    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert 'lang="ja"' in html and 'translate="no"' in html
        assert 'content="notranslate"' in html

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mystery", timeout=5) as response:
            import json
            data = json.loads(response.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["runtime_id"] == "g4-a1b2c3d4"
        assert data["generation"] == 4
        assert data["case"]["commitment"]
        assert "reveal" not in data["case"]

        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/mystery")
        req.add_header("Host", "evil.example:1")
        try:
            urllib.request.urlopen(req, timeout=5)
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 403
        assert raised

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            raised = False
        except urllib.error.HTTPError as exc:
            raised = exc.code == 404
        assert raised
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_payload_absent_reports_preparing_not_an_error(tmp_path):
    """The readiness contract: the server answers with its identity even when
    the corner has not published the first case yet (the production first
    fire failed exactly here — 503/ok:false timed out the GameSwitch gate)."""
    Handler = handler_for(payload_path(tmp_path), "g4-x")
    import json
    import socket
    from http.server import ThreadingHTTPServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/mystery", timeout=5) as response:
            assert response.status == 200
            data = json.loads(response.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["case"] is None and data["reason"] == "case-unavailable"
        assert data["runtime_id"] == "g4-x"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_handler_identity_arguments_are_validated(tmp_path):
    with pytest.raises(MysteryViewError):
        handler_for(payload_path(tmp_path), "bad id!")
    with pytest.raises(MysteryViewError):
        handler_for(payload_path(tmp_path), "g4-x", generation=0)
    with pytest.raises(MysteryViewError):
        handler_for(payload_path(tmp_path), "g4-x", lease_id="not-a-uuid")


def test_default_port_is_the_reserved_mystery_port():
    # 8803-8806 are taken by weather/tsuitate/yoso/doushi.
    assert DEFAULT_PORT == 8807
