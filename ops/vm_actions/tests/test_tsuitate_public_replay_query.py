import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops/vm_actions/tsuitate_public_replay_query.py"
WORKFLOW = ROOT / ".github/workflows/tsuitate-status-query.yml"
RUN_ID = "hourly-2026102902"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def detail():
    raw = [
        {"usi": "7g7f", "by": "sente", "ms": 1000, "foulsBefore": 0},
        {"usi": "3c3d", "by": "gote", "ms": 800, "foulsBefore": 0},
        {"usi": "2g2f", "by": "sente", "ms": 5000, "foulsBefore": 2},
        {"usi": "8c8d", "by": "gote", "ms": 900, "foulsBefore": 0},
        {"usi": "2f2e", "by": "sente", "ms": 2000, "foulsBefore": 2},
    ]
    annotated = [
        {"n": n, "side": move["by"], "usi": move["usi"], "ms": move["ms"]}
        for n, move in enumerate(raw, 1)
    ]
    return {
        "color": "sente",
        "review": {
            "gameId": "fixture-game",
            "result": "gote_win",
            "reason": "checkmate",
            "moves": raw,
            "fouls": {"sente": 3, "gote": 0},
            "foulAttempts": 3,
            "ratings": {
                "sente": {"before": 1000, "after": 998},
                "gote": {"before": 1000, "after": 1002},
            },
            "names": {"sente": "docich", "gote": "kakuren_bot"},
        },
        "kifu": {"moves": annotated, "moveCount": len(annotated)},
    }


class PublicReplayProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load(HELPER, "tsuitate_public_replay_query_test")

    def test_analysis_uses_only_public_replay_derived_facts(self):
        positions = [
            ([], "plain-1", {}),
            ([], "check-on-sente", {}),
            ([], "plain-3", {}),
            ([], "plain-4", {}),
            ([], "check-on-gote", {}),
        ]

        def checked(board, side):
            return ((board == "check-on-sente" and side == "sente")
                    or (board == "check-on-gote" and side == "gote"))

        with mock.patch.object(self.module.analysis, "_positions", return_value=positions), \
             mock.patch.object(self.module.analysis, "_checked", side_effect=checked):
            facts = self.module.analyze_detail(detail())

        self.assertEqual(facts["color"], "sente")
        self.assertEqual(facts["outcome"], "loss")
        self.assertEqual(facts["fouls"], 3)
        self.assertEqual(facts["foulTurns"], [{"n": 3, "usi": "2g2f", "added": 2}])
        self.assertEqual(facts["terminalFoulsWithoutAcceptedMove"], 1)
        self.assertEqual(facts["checksReceived"], [{
            "checkN": 2,
            "checkUsi": "3c3d",
            "responseN": 3,
            "responseUsi": "2g2f",
            "responseMs": 5000,
            "responseFoulsBefore": 2,
        }])
        self.assertEqual(facts["checksGiven"][0]["checkN"], 5)
        self.assertIsNone(facts["checksGiven"][0]["responseN"])
        self.assertEqual(facts["slowestOwn"][0], {"n": 3, "usi": "2g2f", "ms": 5000})
        self.assertEqual(facts["candidateOrdering"], "not_exposed_by_public_replay")

    def test_finished_verified_status_fetches_exact_public_game_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({
                "schema": 1,
                "status": "ok",
                "runId": RUN_ID,
                "state": "finished",
                "gameId": "fixture-game",
                "completedGames": 1,
                "reservedGames": 1,
                "readyForNextRun": True,
                "brainVersion": "tsuitate-brain-v11",
                "errorCode": None,
                "gameResult": {
                    "outcome": "loss",
                    "reason": "checkmate",
                    "endedAt": "2026-10-29T02:35:21.384Z",
                    "moveNumber": 5,
                    "resultConfidence": "verified",
                },
            }), encoding="utf-8")
            positions = [([], "plain", {}) for _ in range(5)]
            with mock.patch.object(
                    self.module, "read_kifu_detail", return_value=(detail(), "ok")) as reader, \
                 mock.patch.object(self.module.analysis, "_positions", return_value=positions), \
                 mock.patch.object(self.module.analysis, "_checked", return_value=False):
                body = self.module.build_reply(path, RUN_ID)["body"]

        reader.assert_called_once_with("fixture-game", {
            "outcome": "loss",
            "reason": "checkmate",
            "endedAt": "2026-10-29T02:35:21.384Z",
            "moveNumber": 5,
            "resultConfidence": "verified",
        })
        self.assertIn("Public-Replay: verified", body)
        self.assertIn("Replay-URL: https://beta.tsuitate.info/games/fixture-game", body)
        self.assertIn("Bot-Foul-Turns: 3:2g2f:+2,terminal:+1", body)
        self.assertIn("Candidate-Ordering: not_exposed_by_public_replay", body)
        self.assertNotIn("playerView", body)
        self.assertNotIn("decisions", body)

    def test_nonterminal_status_never_fetches_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({
                "schema": 1,
                "status": "ok",
                "runId": RUN_ID,
                "state": "playing",
                "gameId": None,
                "completedGames": 0,
                "reservedGames": 1,
                "readyForNextRun": False,
                "brainVersion": "tsuitate-brain-v11",
                "errorCode": None,
                "gameResult": None,
            }), encoding="utf-8")
            with mock.patch.object(self.module, "read_kifu_detail") as reader:
                body = self.module.build_reply(path, RUN_ID)["body"]
        reader.assert_not_called()
        self.assertIn("Public-Replay: not-terminal", body)

    def test_workflow_runs_public_replay_only_after_status_and_never_generic_exec(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Analyze the verified public replay after terminal", text)
        self.assertIn("tsuitate_public_replay_query.py", text)
        self.assertIn("if: steps.status.outcome == 'success'", text)
        self.assertIn("Return bounded public replay facts to the control Issue", text)
        self.assertNotIn('"exec docich production $SHA"', text)
        self.assertNotIn("playerView", text)


if __name__ == "__main__":
    unittest.main()
