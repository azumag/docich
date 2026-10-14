"""Fixed control-Issue replies must not expose source/transport contents."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import hanjuku_evidence_query_reply as reply


class ReplyTests(unittest.TestCase):
    def env(self, **changes):
        return {"SOURCE_COMMENT_ID": "123", "RUN_ID": "456", "RUN_ATTEMPT": "2",
                "RUN_SHA": "a" * 40, "QUERY_MODE": "list", "RUNTIME_ID": "",
                "UPLOAD_OUTCOME": "success", "ARTIFACT_ID": "789",
                "ARTIFACT_DIGEST": "b" * 64, **changes}

    def test_success_links_exact_request_attempt_and_artifact(self):
        body = reply.reply(self.env())["body"]
        for value in ("issues/1339#issuecomment-123", "actions/runs/456/attempts/2",
                      "Artifact ID: `789`", "retention: 1 day", "b" * 64):
            self.assertIn(value, body)
        exported = reply.reply(self.env(QUERY_MODE="export", RUNTIME_ID="g7-1234abcd"))["body"]
        self.assertIn("Runtime: `g7-1234abcd`", exported)

    def test_failure_never_claims_an_artifact_or_includes_untrusted_logs(self):
        for outcome in ("failure", "cancelled", "skipped"):
            body = reply.reply(self.env(UPLOAD_OUTCOME=outcome, ARTIFACT_ID="secret",
                                       ARTIFACT_DIGEST="secret", ERROR="secret"))["body"]
            self.assertIn("not available", body)
            self.assertNotIn("secret", body)
            self.assertNotIn("Artifact ID:", body)

    def test_invalid_metadata_is_rejected(self):
        for key, value in (("SOURCE_COMMENT_ID", "1339\n@someone"), ("RUN_ID", "../1"),
                           ("RUN_ATTEMPT", "0"), ("RUN_SHA", "main"), ("QUERY_MODE", "exec"),
                           ("RUNTIME_ID", "active"), ("UPLOAD_OUTCOME", "unknown"),
                           ("ARTIFACT_ID", "1\nsecret"), ("ARTIFACT_DIGEST", "short")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                reply.reply(self.env(**{key: value}))
        with self.assertRaises(ValueError):
            reply.reply(self.env(QUERY_MODE="export", RUNTIME_ID="latest"))

    def test_cli_failure_is_fixed_and_contains_no_input(self):
        result = subprocess.run([sys.executable, str(HERE / "hanjuku_evidence_query_reply.py")],
                                env=dict(os.environ, **self.env(RUN_ID="PRIVATE_INPUT")),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "Hanjuku result metadata rejected\n")

    def test_workflow_replies_only_after_authorization_to_fixed_issue(self):
        workflow=(HERE.parents[1]/'.github/workflows/hanjuku-evidence-query.yml').read_text()
        self.assertIn("always() && steps.auth.outcome == 'success'", workflow)
        self.assertIn("repos/azumag/docich/issues/1339/comments", workflow)
        self.assertIn('issues: write',workflow)
        self.assertIn('github.actor_id == 9018513',workflow)
        self.assertIn('steps.upload.outputs.artifact-id',workflow)
        self.assertIn('steps.upload.outputs.artifact-digest',workflow)
        self.assertIn('--input "$work/reply.json" > /dev/null',workflow)
        self.assertLess(workflow.index('uses: actions/upload-artifact@'),workflow.index('Return fixed result metadata'))
        self.assertLess(workflow.index('Return fixed result metadata'),workflow.index('Clear runner staging'))


if __name__ == '__main__':
    unittest.main()
