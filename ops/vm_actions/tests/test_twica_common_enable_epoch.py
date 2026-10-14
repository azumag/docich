import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"
HELPER = ROOT / "ops/vm_actions/enable_twica_common_once.sh"
PY_HELPER = ROOT / "ops/vm_actions/enable_twica_common_once.py"
EPOCH = ROOT / "ops/vm_actions/twica_common_enable_epoch"

sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location("twica_enable_tested", PY_HELPER)
assert _spec and _spec.loader
twica_enable = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(twica_enable)


def runtime_snapshot(game="nethack", *, ready=True, cleanup=False, matched=True, alive=True):
    return {
        "canonical": {
            "present": True,
            "corrupt": False,
            "phase": "ready" if ready else "draining",
            "active": {"game": game},
        },
        "mirror": {"matches_canonical": True},
        "cleanup_pending": cleanup,
        "actual": {
            "active": {
                "game": game,
                "game_window": {
                    "exists": True,
                    "ownership": "matched" if matched else "mismatched",
                    "panes": "alive" if alive else "dead",
                },
                "adapter_session": {"applicable": False},
            }
        },
    }


class TwicaCommonEnableEpochTests(unittest.TestCase):
    def test_helper_uses_evidence_gate(self):
        shell = HELPER.read_text(encoding="utf-8")
        self.assertIn("enable_twica_common_once.py", shell)
        python = PY_HELPER.read_text(encoding="utf-8")
        self.assertIn("_select_shared_only", python)
        self.assertIn("collect_status", python)
        self.assertIn("arm_stream(True, shared_only=shared_only)", python)
        self.assertIn('["sudo", "-n", "systemctl", "restart", SHARED_OVERLAY_UNIT]', python)
        self.assertIn("SHARED_OVERLAY_HEALTH", python)

    def test_workflow_enable_is_push_production_epoch_gated(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Enable common TwiCa foreground for reviewed runtime epoch"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split(
            "- name: Refresh active Soren91 GAME OPS rails after reviewed Soren update", 1
        )[0]
        self.assertIn("twica_common_enable_epoch", block)
        self.assertIn("enable_twica_common_once.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)
        self.assertIn("shared_only_evidence_unavailable", block)
        self.assertIn("shared_overlay_restart_unavailable", block)
        self.assertIn("shared_overlay_health_not_ready", block)

    def test_epoch_is_third_attempt(self):
        self.assertEqual(EPOCH.read_text(encoding="utf-8"), "3\n")

    def test_both_live_guards_use_normal_activation(self):
        clients = [{"role": "game"}, {"role": "shared"}]
        original = twica_enable.fresh
        twica_enable.fresh = lambda _: True
        try:
            self.assertFalse(twica_enable._select_shared_only(clients, runtime_snapshot()))
        finally:
            twica_enable.fresh = original

    def test_shared_only_requires_verified_non_sorengame_runtime(self):
        clients = [{"role": "shared"}]
        original = twica_enable.fresh
        twica_enable.fresh = lambda _: True
        try:
            self.assertTrue(twica_enable._select_shared_only(clients, runtime_snapshot("nethack")))
            invalid = [
                runtime_snapshot("sorengame"),
                runtime_snapshot("nethack", ready=False),
                runtime_snapshot("nethack", cleanup=True),
                runtime_snapshot("nethack", matched=False),
                runtime_snapshot("nethack", alive=False),
            ]
            for snapshot in invalid:
                with self.assertRaisesRegex(
                    twica_enable.ActivationBlocked,
                    "shared_only_evidence_unavailable",
                ):
                    twica_enable._select_shared_only(clients, snapshot)
        finally:
            twica_enable.fresh = original


    def test_missing_legacy_guard_refreshes_shared_then_uses_shared_only(self):
        sequence = [[], [{"role": "shared"}]]
        original_fresh = twica_enable.fresh
        original_clients = twica_enable.legacy_clients
        original_restart = twica_enable._restart_shared_overlay
        twica_enable.fresh = lambda _: True
        twica_enable.legacy_clients = lambda _: sequence.pop(0)
        restarted = []
        twica_enable._restart_shared_overlay = lambda: restarted.append(True)
        try:
            self.assertTrue(
                twica_enable._select_after_optional_shared_refresh(
                    Path("/unused"), runtime_snapshot("nethack")
                )
            )
            self.assertEqual(restarted, [True])
        finally:
            twica_enable.fresh = original_fresh
            twica_enable.legacy_clients = original_clients
            twica_enable._restart_shared_overlay = original_restart


if __name__ == "__main__":
    unittest.main()
