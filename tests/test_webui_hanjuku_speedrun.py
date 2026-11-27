"""WebUI manual-start route for the Hanjuku speedrun copy."""
import json
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

import pytest

from docich import corner_rotation, webui
from docich.config import load_global
from docich.corner_catalog import load_catalog

ROOT = Path(__file__).resolve().parents[1]
SPEEDRUN = "hanjuku-speedrun"


def _handler(tmp_path):
    """A Handler shell with the production catalog and a disposable state dir."""
    g = replace(load_global(ROOT, ROOT / "config/docich.soren-live.toml"),
                state_dir=Path(tmp_path))
    handler = object.__new__(webui._Handler)
    handler.g = g
    handler.headers = {}
    sent = {}
    handler._send_json = lambda status, payload: sent.update(status=status, payload=payload)
    handler._send_error_json = lambda status, code, message: sent.update(
        status=status, code=code, message=message)
    return handler, g, sent


def _post(handler, action):
    body = json.dumps({"action": action, "corner": SPEEDRUN, "confirm": True}).encode()
    handler._read_body = lambda: (body, None)


def _catalog_row(g):
    return next(row for row in load_catalog(g) if row.id == SPEEDRUN)


def test_speedrun_manual_start_queues_through_the_common_coordinator(tmp_path, monkeypatch):
    calls = []
    request_id = "req-speedrun-webui"

    class FakeManager:
        def __init__(self, config):
            self.config = config

        def queue_manual(self, game, **kwargs):
            calls.append((game, kwargs))
            (tmp_path / "corner_manual_queue.json").write_text(
                json.dumps({"corner": game, "request_id": request_id}),
                encoding="utf-8",
            )
            return {"status": "queued", "corner": game, "request_id": request_id}

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", FakeManager)
    handler, g, sent = _handler(tmp_path)
    _post(handler, "start")

    assert handler._handle_post_corners() == 200
    assert calls == [(SPEEDRUN, {})]
    assert sent["status"] == 200
    assert sent["payload"]["status"] == "queued"
    assert sent["payload"]["action"] == "start"
    assert sent["payload"]["corner"] == {
        "id": SPEEDRUN, "adapter": "game", "game": SPEEDRUN, "target": "rotation"}
    assert _catalog_row(g).manual_only is True


def test_speedrun_row_is_manual_bookable(tmp_path, monkeypatch):
    handler, g, _ = _handler(tmp_path)
    row = _catalog_row(g)
    assert row.manual_only is True
    assert SPEEDRUN in webui._CORNER_QUEUED_MANUAL_GAMES
    assert SPEEDRUN in webui._MANUAL_RESERVATION_CORNERS
    capabilities = webui._corner_manual_capabilities(g, row)
    assert capabilities == {
        "manual_start": True, "manual_stop": True, "manual_start_reason": None}


@pytest.mark.parametrize(
    ("action", "error"),
    [("resume-latest", "resume_latest_unsupported"),
     ("new-game", "new_game_unsupported")],
)
def test_speedrun_rejects_original_only_resume_actions(
        tmp_path, monkeypatch, action, error):
    class RefusingManager:
        def __init__(self, config):
            raise AssertionError("resume actions must be refused before the coordinator")

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", RefusingManager)
    handler, _, sent = _handler(tmp_path)
    _post(handler, action)

    assert handler._handle_post_corners() == 400
    assert sent["status"] == 400
    assert sent["code"] == error
    assert "半熟英雄だけ" in sent["message"]


def test_speedrun_queue_conflict_is_a_safe_webui_refusal(tmp_path, monkeypatch):
    class ConflictingManager:
        def __init__(self, config):
            pass

        def queue_manual(self, game, **kwargs):
            raise corner_rotation.RotationError(
                "private queue detail", reason_code="manual_queue_conflict")

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", ConflictingManager)
    handler, _, sent = _handler(tmp_path)
    row = SimpleNamespace(id=SPEEDRUN, game=SPEEDRUN, adapter="game")

    assert handler._handle_hanjuku_corner_start(row) == 409
    assert sent["status"] == 409
    assert sent["code"] == "hanjuku_manual_refused"
    assert "private queue detail" not in sent["message"]
