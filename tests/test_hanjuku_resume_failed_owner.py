"""Regression coverage for a derived pre-AI rollback that leaves its owner failed."""
from pathlib import Path
import uuid

import pytest

from docich.game_switch import atomic_write_json
from docich import hanjuku_resume as resume

# Reuse the synthetic checkpoint/recovery fixtures. They contain no real save,
# ROM or production data.
pytest_plugins = ("test_hanjuku_resume",)


def test_matching_failed_derived_owner_is_reconciled_before_same_save_retry(
        failed_derived_attempt):
    s = failed_derived_attempt
    base = Path(s.g.state_dir)
    failed_request = s.retry_first["request_id"]
    atomic_write_json(base / "retro_corner.json", {
        "status": "failed",
        "game": "hanjuku-hero",
        "rotation_request_id": failed_request,
    })

    result = s.op.queue_failed_start(s.g, tmux=s.tmux, now=lambda: 1000000.0)

    assert result["status"] == "queued"
    assert result["request_id"] != failed_request
    owner = s.op._object(base / "retro_corner.json")
    assert owner["status"] == "interrupted"
    assert owner["game"] == "hanjuku-hero"
    assert owner["rotation_request_id"] == failed_request
    assert owner["startup_resume_recovery"] == result["request_id"]
    queued = s.manager.load(1000000.0)["queued_manual"]
    assert queued["request_id"] == result["request_id"]
    current = s.op._object(base / "hanjuku-resume" / resume.REQUEST_FILE)
    assert current["request_id"] == s.claim["request_id"]
    assert current["sha256"] == s.claim["sha256"]


def test_foreign_failed_owner_still_refuses_without_mutation(failed_derived_attempt):
    s = failed_derived_attempt
    base = Path(s.g.state_dir)
    foreign = {
        "status": "failed",
        "game": "hanjuku-hero",
        "rotation_request_id": str(uuid.uuid4()),
    }
    atomic_write_json(base / "retro_corner.json", foreign)
    before_owner = (base / "retro_corner.json").read_bytes()
    before_request = (base / "hanjuku-resume" / resume.REQUEST_FILE).read_bytes()
    before_ledger = s.manager.path.read_bytes()

    with pytest.raises(s.op.CancelRefused, match="owner_not_terminal"):
        s.op.queue_failed_start(s.g, tmux=s.tmux, now=lambda: 1000000.0)

    assert (base / "retro_corner.json").read_bytes() == before_owner
    assert (base / "hanjuku-resume" / resume.REQUEST_FILE).read_bytes() == before_request
    assert s.manager.path.read_bytes() == before_ledger
