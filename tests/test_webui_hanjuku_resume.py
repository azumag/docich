import json
from types import SimpleNamespace

import pytest

from docich import corner_rotation, webui


def _handler(tmp_path):
    handler = object.__new__(webui._Handler)
    handler.g = SimpleNamespace(state_dir=tmp_path)
    sent = {}
    handler._send_json = lambda status, payload: sent.update(status=status, payload=payload)

    def fail_error(*args, **kwargs):
        pytest.fail(f"unexpected WebUI error: args={args!r} kwargs={kwargs!r}")

    handler._send_error_json = fail_error
    return handler, sent


@pytest.mark.parametrize(
    ("resume_latest", "expected_kwargs", "expected_action"),
    [
        (False, {}, "start"),
        (True, {"resume_latest": True}, "resume-latest"),
    ],
)
def test_hanjuku_webui_start_and_resume_share_durable_queue(
    tmp_path, monkeypatch, resume_latest, expected_kwargs, expected_action
):
    calls = []
    request_id = "req-hanjuku-webui"

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
    handler, sent = _handler(tmp_path)
    row = SimpleNamespace(id="hanjuku-hero", game="hanjuku-hero", adapter="game")

    assert handler._handle_hanjuku_corner_start(row, resume_latest=resume_latest) == 200
    assert calls == [("hanjuku-hero", expected_kwargs)]
    assert sent["status"] == 200
    assert sent["payload"]["action"] == expected_action
    assert sent["payload"]["corner"]["target"] == "rotation"


def test_hanjuku_resume_control_is_explicit_in_webui_resources():
    html = webui.INDEX_HTML
    assert 'id="corners-resume-latest"' in html
    assert 'action:"resume-latest"' in html
    assert 'c.game==="hanjuku-hero"' in html
    assert "通常の開始予約は新規開始のままです" in html
