import datetime as dt
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from docich import paper_corner_restore as restore_op
from docich.adapters.program import PAPER_VIEW_NAME

WORKFLOW = ROOT / ".github/workflows/paper-corner-emergency-restore.yml"


class PaperCornerEmergencyRestoreTests(unittest.TestCase):
    def test_service_stop_targets_only_scheduled_paper_unit(self):
        proc = SimpleNamespace(returncode=0)
        run = mock.Mock(return_value=proc)
        restore_op._stop_scheduled_service(run=run)
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["systemctl", "--user", "stop", "docich-paper-corner.service"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_restore_stops_wedged_runner_then_uses_existing_manager_restore(self):
        manager = mock.Mock()
        manager.stop.side_effect = ["already-running", "completed"]
        manager._active_game.return_value = "sorengame"
        with mock.patch.object(restore_op, "load_global", return_value=object()), \
             mock.patch.object(restore_op, "FastPaperCornerManager", return_value=manager), \
             mock.patch.object(restore_op, "_stop_scheduled_service") as stop_service:
            result = restore_op.restore(Path("config.toml"), sleep=lambda _: None)
        stop_service.assert_called_once()
        self.assertEqual(manager.stop.call_count, 2)
        self.assertEqual(result, {"status": "restored", "result": "completed"})

    def test_terminal_state_can_reenter_restore_only_from_current_day_state(self):
        manager = mock.Mock()
        manager.stop.side_effect = ["not-active", "completed"]
        manager._active_game.side_effect = [PAPER_VIEW_NAME, "sorengame"]
        manager._read_state.return_value = {
            "status": "completed",
            "date": "2026-09-16",
            "previous_game": "sorengame",
        }
        manager.tz = ZoneInfo("Asia/Tokyo")
        manager.clock = lambda: dt.datetime(2026, 9, 16, 22, 55, tzinfo=manager.tz).timestamp()
        with mock.patch.object(restore_op, "load_global", return_value=object()), \
             mock.patch.object(restore_op, "FastPaperCornerManager", return_value=manager), \
             mock.patch.object(restore_op, "_stop_scheduled_service"):
            result = restore_op.restore(Path("config.toml"), sleep=lambda _: None)
        saved = manager.save.call_args.args[0]
        self.assertEqual(saved["status"], "restoring")
        self.assertEqual(result["result"], "completed")


class PaperCornerEmergencyRestoreWorkflowTests(unittest.TestCase):
    def test_workflow_is_owner_only_fixed_issue_operation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn("github.event.issue.number == 569", text)
        self.assertIn("github.event.issue.user.id == 9018513", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("environment: vm-operations", text)
        self.assertIn("group: vm-operations-${{ github.repository }}", text)
        self.assertIn("restore-scheduled", text)
        self.assertIn("bin/docich-paper-corner-restore", text)
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("ForwardAgent=no", text)
        self.assertIn("ClearAllForwardings=yes", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("event.comment.body", text)


if __name__ == "__main__":
    unittest.main()
