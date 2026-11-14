"""A refused previous-runtime restore must leave a fixed stage token.

g1163 2026-11-14: `recover` kept failing with the generic `rollback_failed`
while the sorengame restore was refused at an unknown gate. The stage marker
is a bounded sidecar file, never free text.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from docich.game_switch import GameSwitchStore  # noqa: E402

REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"


class RestoreStageMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GameSwitchStore(Path(self.temp.name))
        self.store.canonical.initialize()

    def _stage(self):
        path = Path(self.temp.name) / game_switch.RESTORE_STAGE_FILE
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def test_only_fixed_tokens_are_recorded(self):
        game_switch._record_restore_stage(self.store.state_dir, "readiness-failed")
        self.assertEqual(self._stage()["stage"], "readiness-failed")
        game_switch._record_restore_stage(self.store.state_dir, "PRIVATE free text")
        self.assertEqual(self._stage()["stage"], "readiness-failed")

    def test_an_adapter_creation_failure_records_the_stage(self):
        state, _ = self.store.canonical.load()
        previous = {
            "game": "sorengame", "adapter": "retroarch", "generation": 1,
            "runtime_id": "g1-0123abcd",
            "lease_id": "123e4567-e89b-42d3-a456-426614174001",
            "game_window": "game-g1", "agent_window": "agent-g1",
            "adapter_session": "docich-game-g1",
            "started_at": "2026-11-14T08:00:00+00:00",
        }
        state.update(
            phase="failed", operation="switch", request_id=REQUEST_ID,
            active=None, candidate=None, previous=previous, retiring=[],
            next_generation=2,
            last_result={"request_id": REQUEST_ID, "operation": "switch",
                         "status": "failed", "from_game": "hanjuku-hero",
                         "to_game": "sorengame", "generation": 1,
                         "error_code": "start_failed"},
            last_error={"error_code": "start_failed", "detail": "x"},
        )
        self.store.canonical.save(state)
        coordinator = game_switch.GameSwitchCoordinator(
            self.store, lambda spec: (_ for _ in ()).throw(RuntimeError("no adapter")))
        import time
        with self.store.transaction() as tx:
            result = coordinator._restore_previous_locked(
                tx, REQUEST_ID, "switch", "sorengame", 1, previous,
                time.monotonic() + 5.0, warnings=[])
        self.assertIsNone(result)
        self.assertEqual(self._stage()["stage"], "adapter-unavailable")

    def test_diagnostics_project_only_fixed_stages(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "restore_stage_diag", root / "ops" / "vm_actions" / "collect_diagnostics.py")
        diag = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(diag)
        path = Path(self.temp.name) / "game-switch" / "restore_stage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"schema_version": 1, "stage": "readiness-failed",
             "updated_at": "2026-11-14T10:00:00+00:00"}), encoding="utf-8")
        out = diag._collect_game_switch_restore(self.temp.name, 1794655000)
        self.assertEqual(out["stage"], "readiness-failed")
        self.assertIsNotNone(out["age_sec"])
        path.write_text(json.dumps({"stage": "PRIVATE"}), encoding="utf-8")
        self.assertIsNone(diag._collect_game_switch_restore(self.temp.name, 1794655000)["stage"])



if __name__ == "__main__":
    unittest.main()
