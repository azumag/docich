import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters.program import PAPER_VIEW_NAME
from docich.paper_corner_watchdog import (
    ACTIVE_STALL_S,
    STARTING_TIMEOUT_S,
    WATCHDOG_GRACE_S,
    _deadline_reason,
    check,
)


def test_active_stall_fires_only_after_grace():
    state = {
        "status": "active",
        "date": "2026-09-16",
        "started_at": 1000.0,
        "last_progress_at": 2800.0,
    }
    assert _deadline_reason(
        state,
        now=2800.0 + ACTIVE_STALL_S + WATCHDOG_GRACE_S - 0.1,
        today="2026-09-16",
        active_game=PAPER_VIEW_NAME,
        manual_active=False,
    ) is None
    assert _deadline_reason(
        state,
        now=2800.0 + ACTIVE_STALL_S + WATCHDOG_GRACE_S,
        today="2026-09-16",
        active_game=PAPER_VIEW_NAME,
        manual_active=False,
    ) == "active-stalled"


def test_active_without_progress_falls_back_to_started_at():
    state = {"status": "active", "date": "2026-09-16", "started_at": 1000.0}
    assert _deadline_reason(
        state,
        now=1000.0 + ACTIVE_STALL_S + WATCHDOG_GRACE_S,
        today="2026-09-16",
        active_game=PAPER_VIEW_NAME,
        manual_active=False,
    ) == "active-stalled"


def test_starting_timeout_and_terminal_stuck_view_are_recoverable():
    starting = {"status": "starting", "date": "2026-09-16", "requested_at": 1000.0}
    assert _deadline_reason(
        starting,
        now=1000.0 + STARTING_TIMEOUT_S,
        today="2026-09-16",
        active_game=PAPER_VIEW_NAME,
        manual_active=False,
    ) == "starting-timeout"

    for status in ("failed", "restoring", "completed"):
        assert _deadline_reason(
            {"status": status, "date": "2026-09-16"},
            now=2000.0,
            today="2026-09-16",
            active_game=PAPER_VIEW_NAME,
            manual_active=False,
        ) == f"stuck-{status}"


def test_watchdog_never_touches_non_paper_or_manual_paper():
    state = {"status": "active", "date": "2026-09-16", "last_progress_at": 1000.0}
    assert _deadline_reason(
        state,
        now=5000.0 + ACTIVE_STALL_S,
        today="2026-09-16",
        active_game="sorengame91",
        manual_active=False,
    ) is None
    assert _deadline_reason(
        state,
        now=5000.0 + ACTIVE_STALL_S,
        today="2026-09-16",
        active_game=PAPER_VIEW_NAME,
        manual_active=True,
    ) is None


def test_watchdog_restores_stalled_scheduled_run(tmp_path):
    now = 1_789_563_000.0
    today = "2026-09-16"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fake_g = SimpleNamespace(state_dir=state_dir)
    manager = mock.Mock()
    manager.tz = ZoneInfo("Asia/Tokyo")
    manager._active_game.return_value = PAPER_VIEW_NAME
    manager._read_state.return_value = {
        "status": "active",
        "date": today,
        "started_at": now - ACTIVE_STALL_S - 2000,
        "last_progress_at": now - ACTIVE_STALL_S - WATCHDOG_GRACE_S,
    }
    restored = mock.Mock(return_value={"status": "restored", "result": "completed"})

    with mock.patch("docich.paper_corner_watchdog.load_global", return_value=fake_g), mock.patch(
        "docich.paper_corner_watchdog.FastPaperCornerManager", return_value=manager
    ):
        result = check(tmp_path / "config.toml", now_fn=lambda: now, restore_fn=restored)

    assert result["status"] == "restored"
    assert result["reason"] == "active-stalled"
    restored.assert_called_once_with(tmp_path / "config.toml")


def test_manual_state_suppresses_automatic_restore(tmp_path):
    now = 1_789_563_000.0
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "paper_corner_manual.json").write_text(
        json.dumps({"status": "active"}), encoding="utf-8"
    )
    fake_g = SimpleNamespace(state_dir=state_dir)
    manager = mock.Mock()
    manager.tz = ZoneInfo("Asia/Tokyo")
    manager._active_game.return_value = PAPER_VIEW_NAME
    manager._read_state.return_value = {
        "status": "active",
        "date": "2026-09-16",
        "last_progress_at": now - ACTIVE_STALL_S - 999,
    }
    restored = mock.Mock()

    with mock.patch("docich.paper_corner_watchdog.load_global", return_value=fake_g), mock.patch(
        "docich.paper_corner_watchdog.FastPaperCornerManager", return_value=manager
    ):
        result = check(tmp_path / "config.toml", now_fn=lambda: now, restore_fn=restored)

    assert result == {"status": "ok", "action": "none"}
    restored.assert_not_called()


def test_watchdog_timer_is_independent_and_frequent():
    root = Path(__file__).resolve().parents[1]
    service = (root / "scripts/systemd/docich-paper-corner-watchdog.service").read_text(encoding="utf-8")
    timer = (root / "scripts/systemd/docich-paper-corner-watchdog.timer").read_text(encoding="utf-8")
    assert "docich-paper-corner-watchdog" in service
    assert "OnUnitActiveSec=30s" in timer
    assert "AccuracySec=5s" in timer
    assert "Persistent=false" in timer
