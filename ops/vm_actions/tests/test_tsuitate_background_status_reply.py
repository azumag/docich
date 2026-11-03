import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REPLY = ROOT / "ops/vm_actions/tsuitate_status_query_reply.py"
BACKGROUND_RUN_ID = "background-20261103005438800000"


def load_reply():
    spec = importlib.util.spec_from_file_location("tsuitate_background_status_reply_test", REPLY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TsuitateBackgroundStatusReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_reply()

    def env(self, path: Path, **overrides):
        env = {
            "SOURCE_COMMENT_ID": "6595804957",
            "ACTIONS_RUN_ID": "41580903771",
            "RUN_ATTEMPT": "1",
            "RUN_SHA": "a" * 40,
            "TSUITATE_RUN_ID": BACKGROUND_RUN_ID,
            "QUERY_OUTCOME": "success",
            "STATUS_FILE": str(path),
        }
        env.update(overrides)
        return env

    def test_background_finished_status_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({
                "schema": 1,
                "status": "ok",
                "runId": BACKGROUND_RUN_ID,
                "state": "finished",
                "gameId": "public-game-123",
                "completedGames": 1,
                "reservedGames": 1,
                "readyForNextRun": True,
                "brainVersion": "linear-v1",
                "errorCode": None,
                "gameResult": {
                    "outcome": "loss",
                    "reason": "foul_limit",
                    "endedAt": "2026-11-03T01:15:00Z",
                    "moveNumber": 73,
                    "resultConfidence": "verified",
                },
            }), encoding="utf-8")
            body = self.module.build_reply(self.env(path))["body"]
        self.assertIn(f"Tsuitate-Run: {BACKGROUND_RUN_ID}", body)
        self.assertIn("Game-ID: public-game-123", body)
        self.assertIn("Outcome: loss", body)

    def test_background_failure_reply_does_not_require_status_file(self):
        missing = Path("/definitely/not/a/status/file")
        body = self.module.build_reply(
            self.env(missing, QUERY_OUTCOME="failure")
        )["body"]
        self.assertIn(f"Tsuitate-Run: {BACKGROUND_RUN_ID}", body)
        self.assertIn("Reason: query_unavailable", body)

    def test_malformed_background_run_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            for run_id in (
                "background-2026110300543880000",
                "background-202611030054388000000",
                "background-2026110300543880000x",
                f"{BACKGROUND_RUN_ID} ",
            ):
                with self.subTest(run_id=run_id):
                    with self.assertRaisesRegex(ValueError, "invalid_reply_metadata"):
                        self.module.build_reply(self.env(path, TSUITATE_RUN_ID=run_id))


if __name__ == "__main__":
    unittest.main()
