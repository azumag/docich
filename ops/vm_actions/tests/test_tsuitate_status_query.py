import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "ops/vm_actions"
AUTH = HERE / "authorize_tsuitate_status_query.py"
GATEWAY = HERE / "tsuitate_status_gateway.py"
REPLY = HERE / "tsuitate_status_query_reply.py"
WORKFLOW = ROOT / ".github/workflows/tsuitate-status-query.yml"
ENTRY = HERE / "gateway_entry.py"
INSTALLER = HERE / "install_vm_gateway.sh"
RUN_ID = "hourly-2026102900"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TsuitateStatusAuthorizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load(AUTH, "authorize_tsuitate_status_query_test")

    def env(self, **overrides):
        base = {
            "GITHUB_REPOSITORY": "azumag/docich",
            "GITHUB_REPOSITORY_ID": "1327276249",
            "GITHUB_REPOSITORY_OWNER": "azumag",
            "GITHUB_REPOSITORY_OWNER_ID": "9018513",
            "GITHUB_ACTOR": "azumag",
            "GITHUB_ACTOR_ID": "9018513",
            "GITHUB_TRIGGERING_ACTOR": "azumag",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_DEFAULT_BRANCH": "main",
            "GITHUB_WORKFLOW_REF": (
                "azumag/docich/.github/workflows/tsuitate-status-query.yml@refs/heads/main"
            ),
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "2938",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
            "COMMENT_BODY": f"/tsuitate-status production {RUN_ID}",
        }
        base.update(overrides)
        return base

    def test_exact_owner_query_is_authorized(self):
        self.assertEqual(
            self.module.authorize(self.env()),
            {
                "operation": "tsuitate-status-query",
                "target": "production",
                "ref": "main",
                "run_id": RUN_ID,
            },
        )

    def test_command_and_identity_variants_are_rejected(self):
        variants = [
            {"COMMENT_BODY": "/tsuitate-status production"},
            {"COMMENT_BODY": f"/tsuitate-status production {RUN_ID} "},
            {"COMMENT_BODY": f" /tsuitate-status production {RUN_ID}"},
            {"COMMENT_BODY": "/tsuitate-status production fixture-run"},
            {"COMMENT_BODY": f"/tsuitate-status preview {RUN_ID}"},
            {"COMMENT_AUTHOR": "other"},
            {"COMMENT_AUTHOR_ID": "1"},
            {"GITHUB_ACTOR_ID": "1"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"ISSUE_NUMBER": "1"},
            {"GITHUB_SHA": "bad"},
        ]
        for override in variants:
            with self.subTest(override=override):
                with self.assertRaisesRegex(ValueError, "authorization_denied"):
                    self.module.authorize(self.env(**override))


class TsuitateStatusGatewayProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load(GATEWAY, "tsuitate_status_gateway_test")

    def base(self, **overrides):
        data = {
            "state": "playing",
            "runId": RUN_ID,
            "gameId": "private-live-game",
            "completedGames": 0,
            "reservedGames": 1,
            "readyForNextRun": False,
            "brainVersion": "linear-v1",
            "errorCode": None,
            "playerView": {"privateToken": "must-not-leak", "yourPieces": ["secret"]},
            "decisions": [{"usi": "7g7f"}],
        }
        data.update(overrides)
        return data

    def test_live_identity_and_unknown_fields_never_leave_gateway(self):
        projected = self.module.project_status(self.base(), RUN_ID)
        self.assertEqual(projected["gameId"], None)
        self.assertEqual(projected["gameResult"], None)
        encoded = json.dumps(projected)
        self.assertNotIn("private-live-game", encoded)
        self.assertNotIn("playerView", encoded)
        self.assertNotIn("must-not-leak", encoded)
        self.assertNotIn("decisions", encoded)
        self.assertEqual(
            set(projected),
            {"schema", "status", "runId", "state", "gameId", "completedGames",
             "reservedGames", "readyForNextRun", "brainVersion", "errorCode", "gameResult"},
        )

    def test_finished_run_exposes_only_public_identity_and_terminal_summary(self):
        projected = self.module.project_status(self.base(
            state="finished",
            gameId="public-game-123",
            completedGames=1,
            readyForNextRun=True,
            terminalResult={
                "outcome": "loss",
                "reason": "foul_limit",
                "endedAt": "2026-10-29T00:28:30.123Z",
                "moveNumber": 73,
                "resultConfidence": "verified",
                "privateBoard": "must-not-leak",
            },
        ), RUN_ID)
        self.assertEqual(projected["gameId"], "public-game-123")
        self.assertEqual(projected["gameResult"], {
            "outcome": "loss",
            "reason": "foul_limit",
            "endedAt": "2026-10-29T00:28:30.123Z",
            "moveNumber": 73,
            "resultConfidence": "verified",
        })
        self.assertNotIn("privateBoard", json.dumps(projected))

    def test_mismatched_run_and_terminal_without_game_id_fail_closed(self):
        with self.assertRaisesRegex(self.module.QueryError, "run_mismatch"):
            self.module.project_status(self.base(runId="hourly-2026102901"), RUN_ID)
        with self.assertRaisesRegex(self.module.QueryError, "terminal_identity_missing"):
            self.module.project_status(self.base(
                state="finished", gameId=None, completedGames=1, readyForNextRun=True
            ), RUN_ID)

    def test_gateway_command_is_exact_and_bounded(self):
        sha = "b" * 40
        self.assertEqual(
            self.module.parse_command(
                f"tsuitate_status_query docich production {sha} {RUN_ID}"
            ),
            (sha, RUN_ID),
        )
        for command in (
            f"tsuitate_status_query docich production {sha}",
            f"tsuitate_status_query docich production {sha} fixture-run",
            f"tsuitate_status_query docich preview {sha} {RUN_ID}",
            f"tsuitate_status_query docich production {sha} {RUN_ID} extra",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(self.module.QueryError, "invalid_command"):
                    self.module.parse_command(command)


class TsuitateStatusReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load(REPLY, "tsuitate_status_reply_test")

    def env(self, path: Path, **overrides):
        base = {
            "SOURCE_COMMENT_ID": "123",
            "ACTIONS_RUN_ID": "456",
            "RUN_ATTEMPT": "1",
            "RUN_SHA": "c" * 40,
            "TSUITATE_RUN_ID": RUN_ID,
            "QUERY_OUTCOME": "success",
            "STATUS_FILE": str(path),
        }
        base.update(overrides)
        return base

    def test_reply_contains_finished_identity_but_no_unreviewed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({
                "schema": 1,
                "status": "ok",
                "runId": RUN_ID,
                "state": "finished",
                "gameId": "public-game-123",
                "completedGames": 1,
                "reservedGames": 1,
                "readyForNextRun": True,
                "brainVersion": "linear-v1",
                "errorCode": None,
                "gameResult": {
                    "outcome": "win", "reason": "checkmate",
                    "endedAt": "2026-10-29T00:28:30Z", "moveNumber": 81,
                    "resultConfidence": "verified",
                },
            }), encoding="utf-8")
            body = self.module.build_reply(self.env(path))["body"]
        self.assertIn("Game-ID: public-game-123", body)
        self.assertIn("Outcome: win", body)
        self.assertNotIn("playerView", body)

    def test_failure_reply_never_reads_or_echoes_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text("PRIVATE_CONTENT", encoding="utf-8")
            body = self.module.build_reply(self.env(path, QUERY_OUTCOME="failure"))["body"]
        self.assertIn("Reason: query_unavailable", body)
        self.assertNotIn("PRIVATE_CONTENT", body)


class TsuitateStatusStaticContractTests(unittest.TestCase):
    def test_workflow_is_owner_only_read_only_and_never_uses_generic_exec(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 2938", text)
        self.assertIn("startsWith(github.event.comment.body, '/tsuitate-status production hourly-')", text)
        self.assertIn("authorize_tsuitate_status_query.py", text)
        self.assertIn("tsuitate_status_query docich production $SHA $TSUITATE_RUN_ID", text)
        self.assertIn("Require production to equal event SHA", text)
        self.assertIn("Recheck current protected main before publishing", text)
        self.assertNotIn('"exec docich production $SHA"', text)
        self.assertNotIn("playerView", text)
        self.assertNotIn("workflow_dispatch:", text)

    def test_installed_gateway_dispatch_and_installer_are_explicit(self):
        entry = ENTRY.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("tsuitate_status_query", entry)
        self.assertIn("tsuitate_status_gateway", entry)
        self.assertIn("tsuitate_status_gateway.py", installer)
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


if __name__ == "__main__":
    unittest.main()
