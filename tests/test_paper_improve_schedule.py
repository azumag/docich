from __future__ import annotations

import inspect
import json

import pytest

from docich import paper_corner, paper_improve_retry
from docich.trading import paper_improve_schedule as sched

NOW = 1_800_000_000.0
HOUR = 3600.0


def write_status(tmp_path, **fields):
    payload = {"status": "improved", "phase": "done", "started_at": NOW - 30 * HOUR,
               "updated_at": NOW - 30 * HOUR}
    payload.update(fields)
    (tmp_path / "paper_improve_status.json").write_text(json.dumps(payload), encoding="utf-8")


def no_pending(_dir):
    return False


def gate(tmp_path, **kw):
    kw.setdefault("pending_exists", no_pending)
    return sched.evaluate_gate(tmp_path, now=NOW, **kw)


def test_runs_when_nothing_blocks_even_without_status_file(tmp_path):
    decision = gate(tmp_path)
    assert decision.run and decision.reason == "run"


def test_emergency_stop_file_wins_over_everything(tmp_path):
    (tmp_path / sched.DISABLED_FILENAME).write_text("", encoding="utf-8")
    write_status(tmp_path, status="running", updated_at=NOW - 60)
    decision = gate(tmp_path, pending_exists=lambda _d: True)
    assert not decision.run and decision.reason == "disabled"


def test_fresh_running_status_skips_but_stale_running_does_not(tmp_path):
    write_status(tmp_path, status="running", started_at=NOW - 600, updated_at=NOW - 600)
    assert gate(tmp_path).reason == "already-running"
    # stale running (>70 min) is not "already-running"; it still started <20h ago.
    write_status(tmp_path, status="running", started_at=NOW - 80 * 60, updated_at=NOW - 71 * 60)
    assert gate(tmp_path).reason == "daily-limit"
    write_status(tmp_path, status="running", started_at=NOW - 30 * HOUR, updated_at=NOW - 30 * HOUR)
    assert gate(tmp_path).run


def test_pending_experiment_skips(tmp_path):
    decision = gate(tmp_path, pending_exists=lambda _d: True)
    assert not decision.run and decision.reason == "pending-exists"


def test_unreadable_pending_state_does_not_block(tmp_path):
    def boom(_d):
        raise OSError("x")

    assert gate(tmp_path, pending_exists=boom).run


def test_default_pending_loader_uses_real_state_files(tmp_path):
    assert sched.evaluate_gate(tmp_path, now=NOW).run
    (tmp_path / "strategy_experiment_pending.json").write_text("{broken", encoding="utf-8")
    assert sched.evaluate_gate(tmp_path, now=NOW).run  # invalid pending is unusable


@pytest.mark.parametrize("status", ["failed", "improved", "rejected", "rolled_back"])
def test_attempt_within_20h_is_daily_limited_whatever_the_outcome(tmp_path, status):
    write_status(tmp_path, status=status, started_at=NOW - 19 * HOUR, updated_at=NOW - 19 * HOUR)
    decision = gate(tmp_path)
    assert not decision.run and decision.reason == "daily-limit"


def test_attempt_older_than_20h_runs_again_even_after_failure(tmp_path):
    write_status(tmp_path, status="failed", phase="generate",
                 started_at=NOW - 20 * HOUR - 1, updated_at=NOW - 20 * HOUR)
    assert gate(tmp_path).run


@pytest.mark.parametrize("status", ["skipped", "dry-run"])
def test_attempts_that_never_reached_the_ai_do_not_count(tmp_path, status):
    write_status(tmp_path, status=status, started_at=NOW - 60, updated_at=NOW - 60)
    assert gate(tmp_path).run


def test_far_future_started_at_does_not_block_forever(tmp_path):
    write_status(tmp_path, status="failed", started_at=NOW + 100 * HOUR, updated_at=NOW + 100 * HOUR)
    assert gate(tmp_path).run


def test_corrupt_status_file_is_ignored(tmp_path):
    (tmp_path / "paper_improve_status.json").write_text("not json", encoding="utf-8")
    assert gate(tmp_path).run


def test_gate_writes_nothing(tmp_path):
    write_status(tmp_path, status="failed", started_at=NOW - HOUR, updated_at=NOW - HOUR)
    before = sorted(p.name for p in tmp_path.iterdir())
    gate(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def _disable(d):
    (d / sched.DISABLED_FILENAME).write_text("")


def _running(d):
    write_status(d, status="running", started_at=NOW - 60, updated_at=NOW - 60)


def _failed_recently(d):
    write_status(d, status="failed", started_at=NOW - HOUR, updated_at=NOW - HOUR)


@pytest.mark.parametrize("setup,reason", [
    (_disable, "disabled"),
    (_running, "already-running"),
    (_failed_recently, "daily-limit"),
])
def test_run_scheduled_never_calls_the_improve_path_when_gated(tmp_path, setup, reason):
    setup(tmp_path)
    calls = []
    out = sched.run_scheduled(tmp_path, run=lambda: calls.append(1) or {}, now=NOW,
                              pending_exists=no_pending)
    assert calls == []
    assert out["status"] == "skipped" and out["reason"] == reason
    assert out["gate"]["decision"] == "skip" and out["scheduled"] is True


def test_run_scheduled_pending_gate_never_calls_the_improve_path(tmp_path):
    calls = []
    out = sched.run_scheduled(tmp_path, run=lambda: calls.append(1) or {}, now=NOW,
                              pending_exists=lambda _d: True)
    assert calls == [] and out["reason"] == "pending-exists"


def test_run_scheduled_calls_once_and_reports_gate(tmp_path):
    calls = []
    out = sched.run_scheduled(tmp_path, run=lambda: calls.append(1) or {"status": "improved"},
                              now=NOW, pending_exists=no_pending)
    assert calls == [1]
    assert out["status"] == "improved" and out["gate"] == {"decision": "run", "reason": "run"}


def test_dry_run_bypasses_gate_without_ai(tmp_path):
    _disable(tmp_path)
    out = sched.run_scheduled(tmp_path, run=lambda: {"status": "dry-run"}, dry_run=True, now=NOW)
    assert out["status"] == "dry-run" and out["gate"]["reason"] == "dry-run"


def test_scheduled_entry_never_reaches_run_paper_improve_when_gated(tmp_path, monkeypatch):
    import docich.trading.paper_improve as improve

    def forbidden(*a, **k):
        raise AssertionError("AI path must not run when gated")

    monkeypatch.setattr(improve, "run_paper_improve", forbidden)
    _disable(tmp_path)
    out = paper_improve_retry.run_scheduled_improve(object(), tmp_path, "codex", False, now=NOW)
    assert out["reason"] == "disabled"


def test_scheduled_entry_delegates_to_shared_path_when_open(tmp_path, monkeypatch):
    import docich.trading.paper_improve as improve

    seen = []

    def fake(g, *, trading_dir, agents, dry_run, **k):
        seen.append((trading_dir, agents, dry_run))
        return {"status": "improved"}

    monkeypatch.setattr(improve, "run_paper_improve", fake)
    monkeypatch.setattr(sched, "_default_pending_loader", no_pending)
    out = paper_improve_retry.run_scheduled_improve(object(), tmp_path, " codex ", False, now=NOW)
    assert seen == [(tmp_path, "codex", False)]
    assert out["gate"]["decision"] == "run"


def test_routing_recognises_scheduled_command_only():
    f = paper_improve_retry.is_paper_improve_scheduled_invocation
    assert f(["--config", "c.toml", "trading", "paper-improve-scheduled"])
    assert f(["trading", "--state-dir", "x", "paper-improve-scheduled"])
    assert not f(["trading", "paper-improve"])
    assert not paper_improve_retry.is_paper_improve_invocation(["trading", "paper-improve-scheduled"])


def test_corner_trigger_is_unchanged():
    source = inspect.getsource(paper_corner.PaperCornerManager._spawn_improve_once)
    assert "'trading', 'paper-improve'," in source
    assert "scheduled" not in source
