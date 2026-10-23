from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "reply_radio_cloudflare_rollout.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "reply-radio-cloudflare-rollout.yml"
EPOCH = ROOT / "ops" / "vm_actions" / "reply_radio_cloudflare_rollout_epoch"


def persistent_block(text: str) -> str:
    start = text.index('block = "\\n".join([')
    end = text.index("]) + \"\\n\"", start)
    return text[start:end]


class ReplyRadioCloudflareRolloutTests(unittest.TestCase):
    def test_rollout_shell_is_valid_and_one_time(self):
        run = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(EPOCH.read_text(encoding="utf-8"), "2026-10-24-v1\n")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("paths:\n      - 'ops/vm_actions/reply_radio_cloudflare_rollout_epoch'", workflow)
        self.assertIn("github.actor_id == 9018513", workflow)
        self.assertIn("github.triggering_actor == 'azumag'", workflow)
        self.assertIn("github.ref_protected == true", workflow)
        self.assertIn('data.get("status") == "configured"', workflow)
        self.assertIn('data.get("sha") == sys.argv[2]', workflow)
        self.assertIn('exec docich production $SHA', workflow)

    def test_real_canaries_precede_mutation_and_roll_back_on_failure(self):
        text = SCRIPT.read_text(encoding="utf-8")
        comment = text.index("from docich.comment_classifier import web_batch")
        radio = text.index("python3 -P -m docich.radio.consumer")
        backup = text.index("backup_file=\"$(mktemp")
        patch = text.index("Atomically replace only this rollout's marked block")
        self.assertLess(comment, radio)
        self.assertLess(radio, backup)
        self.assertLess(backup, patch)
        self.assertIn("cp -p -- \"$backup_file\" \"$env_file\"", text)
        self.assertIn("rollback_needed=1", text)
        self.assertIn("restart_radio_worker.sh", text)
        self.assertIn("restart_chat_kick_workers.sh", text)
        block = persistent_block(text)
        self.assertNotIn("DOCICH_ALLOW_REAL_AI=1", block)
        self.assertNotIn("DOCICH_JEV_ROUTE=", block)

    def test_rollout_scope_is_narrow_and_fail_closed(self):
        text = SCRIPT.read_text(encoding="utf-8")
        lower = text.lower()
        block = persistent_block(text)
        self.assertIn("DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED=1", block)
        self.assertIn("DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare", block)
        self.assertIn("DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=1", block)
        self.assertIn("DOCICH_RADIO_NATIVE_CORNERS=ai_knowledge", block)
        self.assertIn("DOCICH_RADIO_NATIVE_AGENTS=cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8", block)
        self.assertNotIn("finance", block)
        self.assertNotIn("RADIO_GENERATION_ENABLED", block)
        self.assertNotIn("opencode run", lower)
        self.assertNotIn("_run_opencode", lower)
        self.assertIn("COMMENT_CLASSIFIER_BACKEND=jev", text)
        self.assertIn("DOCICH_REPLY_ROUTING_ENABLED=1", text)
        self.assertIn("verified_web_rollout_enabled", text)


if __name__ == "__main__":
    unittest.main()
