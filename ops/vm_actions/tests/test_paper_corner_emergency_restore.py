import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from docich import paper_corner_restore as restore_op
from docich.adapters.program import PAPER_VIEW_NAME
from ops.vm_actions import paper_restore_queue

REQUEST_WORKFLOW = ROOT / ".github/workflows/paper-corner-emergency-request.yml"
RESTORE_WORKFLOW = ROOT / ".github/workflows/paper-corner-emergency-restore.yml"
REPLAY_WORKFLOW = ROOT / ".github/workflows/paper-corner-emergency-replay.yml"


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
        manager.tz = ZoneInfo("Asia/Tokyo")
        manager.clock = lambda: dt.datetime(2026, 9, 16, 22, 0, tzinfo=manager.tz).timestamp()
        manager._read_state.return_value = {}
        manager.g = SimpleNamespace(state_dir=Path("/nonexistent-paper-state"))
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
        manager.path = Path("/nonexistent-paper-state/paper_corner.json")
        manager.g = SimpleNamespace(state_dir=Path("/nonexistent-paper-state"))
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


class PaperRestoreQueueTests(unittest.TestCase):
    @staticmethod
    def _comment(body: str, *, bot: bool = True) -> str:
        return json.dumps(
            {
                "body": body,
                "user": {
                    "login": "github-actions[bot]" if bot else "someone-else",
                    "type": "Bot" if bot else "User",
                },
            }
        )

    def test_owner_command_and_bot_markers_form_a_durable_queue(self):
        nonce = paper_restore_queue.parse_command(
            '{"operation":"restore-scheduled","confirm":"production","nonce":"r-1"}'
        )
        self.assertEqual(nonce, "r-1")
        comments = [
            self._comment("<!-- docich:paper-restore-request nonce=r-1 -->"),
            self._comment("<!-- docich:paper-restore-request nonce=spoof -->", bot=False),
        ]
        self.assertEqual(paper_restore_queue.nonce_state_from_lines(comments, nonce), "pending")
        self.assertEqual(paper_restore_queue.oldest_pending_nonce(comments), nonce)

    def test_completion_marker_closes_request_and_queue_chooses_oldest_pending(self):
        comments = [
            self._comment("<!-- docich:paper-restore-request nonce=done -->"),
            self._comment("<!-- docich:paper-restore-complete nonce=done -->"),
            self._comment("<!-- docich:paper-restore-request nonce=next -->"),
        ]
        self.assertEqual(paper_restore_queue.nonce_state_from_lines(comments, "done"), "complete")
        self.assertEqual(paper_restore_queue.oldest_pending_nonce(comments), "next")

    def test_cli_outputs_only_safe_state_tokens(self):
        comments = self._comment("<!-- docich:paper-restore-request nonce=cli -->") + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comments.jsonl"
            path.write_text(comments, encoding="utf-8")
            output = []
            with mock.patch("builtins.print", side_effect=output.append):
                self.assertEqual(paper_restore_queue.main(["queue", str(path)]), 0)
            self.assertEqual(output, ["needed=true", "nonce=cli"])


class PaperCornerEmergencyRestoreWorkflowTests(unittest.TestCase):
    def test_request_workflow_is_owner_only_and_outside_vm_concurrency_lane(self):
        text = REQUEST_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn("github.event.issue.number == 569", text)
        self.assertIn("github.event.issue.user.id == 9018513", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertNotIn("concurrency:", text)
        self.assertIn("docich:paper-restore-request", text)
        self.assertIn("uses: ./.github/workflows/paper-corner-emergency-restore.yml", text)

    def test_restore_workflow_is_the_serialized_fixed_operation(self):
        text = RESTORE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertIn("paper-corner-emergency-request.yml@refs/heads/main", text)
        self.assertIn("paper-corner-emergency-replay.yml@refs/heads/main", text)
        self.assertNotIn("github.event_name == 'workflow_call'", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("environment: vm-operations", text)
        self.assertIn("group: vm-operations-${{ github.repository }}", text)
        self.assertIn("restore-scheduled", text)
        self.assertIn("bin/docich-paper-corner-restore", text)
        self.assertIn("docich:paper-restore-complete", text)
        self.assertIn("gh api --method POST", text)
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("ForwardAgent=no", text)
        self.assertIn("ClearAllForwardings=yes", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("event.comment.body", text)

    def test_replay_dispatcher_is_scheduled_and_does_not_drop_normal_pending_runs(self):
        text = REPLAY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("schedule:", text)
        self.assertIn("cron: '*/5 * * * *'", text)
        self.assertNotIn("concurrency:", text)
        self.assertIn("paper_restore_queue.py queue", text)
        self.assertIn("uses: ./.github/workflows/paper-corner-emergency-restore.yml", text)


if __name__ == "__main__":
    unittest.main()
