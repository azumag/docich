from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docich import config
from docich import jev_corner as jev_mod
from docich.jev_corner import (
    JevCornerConfig,
    JevCornerError,
    JevCornerManager,
    _recover_precommit_failure,
    _expired_pre_stop_request,
    _run_bridge_relaunch,
    _stale_precommit_recovery_category,
    diagnose,
    load_jev_corner_config,
    recover_bridge,
    refresh_bridge,
)


class FakeJevAdapter:
    def __init__(self, root: Path):
        self.root = root
        self.calls = []
        self.preflights = 0

    def preflight(self, deadline, cancel):
        self.preflights += 1

    def reconfigure_player(self, **kwargs):
        self.calls.append(kwargs)
        path = self.root / "tmp/state/game_lifecycle/player_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = {}
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
        state = {
            "schema": 1,
            "game": "sorengame",
            "policy": kwargs["target_policy"],
            "player_generation": int(previous.get("player_generation", 0)) + 1,
            "game_generation": previous.get("game_generation"),
            "run_id": kwargs["run_id"],
            "config_hash": kwargs["config_hash"],
        }
        path.write_text(json.dumps(state), encoding="utf-8")
        return state


class JevCornerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config/games").mkdir(parents=True)
        (self.root / "config/docich.toml").write_text(
            "[paths]\nstate_dir = 'run'\ngames_dir = 'config/games'\n",
            encoding="utf-8",
        )
        (self.root / "config/games/sorengame.toml").write_text(
            """
[game]
name = "sorengame"
title = "Soren"
adapter = "soren"

[lifecycle]
require_round_boundary = true

[soren]
root = "/tmp/soren-test"

[jev_corner]
enabled = false
one_game = true
max_requests_per_run = 500
""",
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.soren_root = self.root / "soren"
        self.adapter = FakeJevAdapter(self.soren_root)
        self.manager = JevCornerManager(
            self.g,
            config=JevCornerConfig(enabled=False),
            adapter=self.adapter,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_bridge_relaunch_restarts_the_tmux_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capability = root / "tmp/state/game_lifecycle/player_capabilities.json"
            capability.parent.mkdir(parents=True)
            capability.write_text(json.dumps({"schema": 1, "pid": 4242}), encoding="utf-8")
            calls = []
            with patch("docich.jev_corner.subprocess.run", side_effect=lambda argv, **kw: calls.append(argv)):
                with patch("docich.jev_corner.os.kill", return_value=None):
                    _run_bridge_relaunch(root)
            self.assertEqual(calls[0][:3], ["tmux", "kill-session", "-t"])
            self.assertEqual(calls[1][:4], ["tmux", "new-session", "-d", "-s"])
            self.assertIn("soviet_local.mjs", calls[1][-1])

    def test_config_is_disabled_by_default_but_manual_start_is_explicit(self):
        loaded = load_jev_corner_config(self.g)
        self.assertFalse(loaded.enabled)
        result = self.manager.start(timeout_s=1)
        self.assertEqual(result.status, "active")
        self.assertEqual(self.adapter.calls[0]["target_policy"], "jev")
        self.assertEqual(self.adapter.calls[0]["expected_player_generation"], 0)

    def test_diagnose_uses_fixed_category_when_bridge_capability_is_missing(self):
        self.assertEqual(diagnose(self.g), "capability_missing")

    def test_recovery_only_clears_uncommitted_matching_corner_state(self):
        corner = {
            "status": "recovery_required",
            "policy": "jev",
            "started_at": None,
            "completed_at": None,
            "player_generation": 4,
            "request_id": "123e4567-e89b-12d3-a456-426614174000",
        }
        player = {"policy": "existing", "player_generation": 4}
        self.assertTrue(_recover_precommit_failure(corner, player))
        corner["last_error"] = "Soren player_policy_v1 capabilityを確認できません"
        self.assertTrue(_recover_precommit_failure(corner, None))
        corner["last_error"] = "別の失敗"
        self.assertFalse(_recover_precommit_failure(corner, None))
        corner["status"] = "active"
        self.assertFalse(_recover_precommit_failure(corner, player))
        corner["status"] = "recovery_required"
        player["player_generation"] = 5
        self.assertFalse(_recover_precommit_failure(corner, player))

    def test_expired_pre_stop_request_is_reversible_only_before_stopping(self):
        payload = {
            "request": {"request_id": "req-1", "deadline_epoch": 1},
            "ack": {"request_id": "req-1", "status": "boundary"},
        }
        self.assertTrue(_expired_pre_stop_request(payload))
        payload["ack"]["status"] = "stop_requested"
        self.assertTrue(_expired_pre_stop_request(payload))
        payload["ack"]["status"] = "accepted"
        self.assertTrue(_expired_pre_stop_request(payload))
        payload["ack"]["status"] = "waiting"
        self.assertTrue(_expired_pre_stop_request(payload))
        payload["ack"]["status"] = "timeout"
        self.assertTrue(_expired_pre_stop_request(payload))
        payload["resource"] = {"status": "stopped"}
        self.assertFalse(_expired_pre_stop_request(payload))
        payload.pop("resource")
        payload["ack"]["status"] = "stopping"
        self.assertFalse(_expired_pre_stop_request(payload))
        payload["ack"]["status"] = "boundary"
        payload["ack"]["request_id"] = "other"
        self.assertFalse(_expired_pre_stop_request(payload))

    def test_stale_precommit_diagnose_classifies_lifecycle_without_mutation(self):
        class StatusAdapter:
            def __init__(self, root, payload):
                self.root = root
                self.payload = payload

            def _status(self, deadline, cancel):
                return self.payload

            @staticmethod
            def _ack(payload):
                return payload.get("ack", {})

        class Manager:
            def __init__(self, adapter):
                self.adapter = adapter

        payload = {
            "request": {"game": "sorengame", "request_id": "req-1"},
            "ack": {"game": "sorengame", "request_id": "req-1", "status": "boundary"},
            "resource": {"game": "sorengame", "request_id": "req-1"},
        }
        manager = Manager(StatusAdapter(self.soren_root, payload))
        self.assertEqual(_stale_precommit_recovery_category(manager), "corner_stale_precommit_boundary_live")

    def test_one_game_state_requires_explicit_finish_to_restore_existing(self):
        self.manager.start(timeout_s=1)
        with self.assertRaises(JevCornerError):
            self.manager.start(timeout_s=1)

        result = self.manager.finish(timeout_s=1)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.adapter.calls[-1]["target_policy"], "existing")
        self.assertEqual(self.adapter.calls[-1]["expected_player_generation"], 1)
        state = self.manager.status()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["player_state"]["policy"], "existing")

    def test_adapter_failure_leaves_recovery_required(self):
        class Failing(FakeJevAdapter):
            def reconfigure_player(self, **kwargs):
                self.calls.append(kwargs)
                raise RuntimeError("boundary unavailable")

        manager = JevCornerManager(
            self.g,
            config=JevCornerConfig(enabled=False),
            adapter=Failing(self.soren_root),
        )
        with self.assertRaises(JevCornerError):
            manager.start(timeout_s=1)
        self.assertEqual(manager.status()["status"], "recovery_required")


class JevProgramBoundaryTests(unittest.TestCase):
    """Issue #809: bridge operator honors the shared program slot."""

    def _make_g(self, root: Path, soren_root: Path):
        (root / "config/games").mkdir(parents=True)
        (root / "config/docich.toml").write_text(
            "[paths]\nstate_dir = 'run'\ngames_dir = 'config/games'\n",
            encoding="utf-8",
        )
        (root / "config/games/sorengame.toml").write_text(
            f"""
[game]
name = "sorengame"
title = "Soren"
adapter = "soren"

[lifecycle]
require_round_boundary = true

[soren]
root = "{soren_root}"

[jev_corner]
enabled = false
one_game = true
max_requests_per_run = 500
""",
            encoding="utf-8",
        )
        return config.load_global(root)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.soren_root = self.root / "soren"
        self.g = self._make_g(self.root, self.soren_root)
        self.program_state = self.soren_root / "tmp/state"
        self.program_state.mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def _occupy_with_paper_active(self):
        paper_state = self.program_state / "paper_corner.json"
        paper_state.write_text(json.dumps({"status": "active"}), encoding="utf-8")
        (self.program_state / "docich_program_active.json").write_text(
            json.dumps({"owner_state": str(paper_state)}), encoding="utf-8"
        )

    def _queue_retro_waiting(self):
        import time

        queue_dir = self.program_state / "docich_program_queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        (queue_dir / "retro_corner.json").write_text(
            json.dumps({"status": "waiting", "requested_at": time.time()}),
            encoding="utf-8",
        )

    def test_diagnose_reports_program_busy_before_capability(self):
        # capability file is absent AND paper owns the slot: the operator must
        # see program_busy, not a misleading capability_missing (issue #809).
        self._occupy_with_paper_active()
        self.assertEqual(diagnose(self.g), "program_busy")

    def test_diagnose_reports_program_busy_for_queued_retro(self):
        self._queue_retro_waiting()
        self.assertEqual(diagnose(self.g), "program_busy")

    def test_diagnose_keeps_capability_missing_when_program_free(self):
        self.assertEqual(diagnose(self.g), "capability_missing")

    def test_refresh_bridge_refuses_without_side_effects(self):
        self._occupy_with_paper_active()
        with patch.object(jev_mod, "JevCornerManager") as manager_cls:
            with self.assertRaises(JevCornerError):
                refresh_bridge(self.g)
            manager_cls.assert_not_called()

    def test_recover_bridge_refuses_without_side_effects(self):
        self._occupy_with_paper_active()
        with patch.object(
            jev_mod, "_assert_stopped_bridge_recovery"
        ) as bridge_recovery:
            with self.assertRaises(JevCornerError):
                recover_bridge(self.g)
            bridge_recovery.assert_not_called()

    def test_program_free_leaves_recovery_path_intact(self):
        self.assertIsNone(jev_mod._program_busy_reason(self.g))


if __name__ == "__main__":
    unittest.main()
