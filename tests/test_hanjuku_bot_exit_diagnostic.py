"""The bot brain names the raising line when it exits with code 2.

g1133 11-05: the agent logged "hanjuku-bot: invalid observation" 400+ times and the
decided actions were dropped with no hint of which call raised.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = REPO_ROOT / "brains" / "hanjuku" / "bot.py"


def _load_bot():
    spec = importlib.util.spec_from_file_location("docich_hanjuku_bot_under_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bot = _load_bot()


def _run(obs, **patches):
    err, out = io.StringIO(), io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(json.dumps(obs))), \
            redirect_stderr(err), redirect_stdout(out):
        code = bot.main()
    return code, err.getvalue(), json.loads(out.getvalue())


def _decided(actions, state, *, persist=None, atomic_write_json=None):
    """Run main() with a final decision and only the side effects under test."""
    obs = {"game": "hanjuku-hero", "screenshot": "s.png",
           "meta": {"runtime_dir": str(bot.ROOT / "run" / "runtimes" / "x")}}
    frame = mock.Mock()
    frame.resized.return_value = frame
    frame.digest.return_value = "d"
    with mock.patch.object(bot, "read_png", return_value=frame), \
            mock.patch.object(bot, "read_record", return_value={}), \
            mock.patch.object(bot.hanjuku_resume, "inherit_running_chapter"), \
            mock.patch.object(bot.hanjuku_experience, "load", return_value={}), \
            mock.patch.object(bot.hanjuku_chart_adjust, "load", return_value=None), \
            mock.patch.object(bot, "recall_input_receipts", return_value=None), \
            mock.patch.object(bot, "card_scroll_receipt", return_value=None), \
            mock.patch.object(bot, "departure_input_receipt", return_value=None), \
            mock.patch.object(bot, "overtaken_frames", return_value=[]), \
            mock.patch.object(bot, "decide", return_value=(actions, state)), \
            mock.patch.object(bot, "overtaken_confirm", return_value=None), \
            mock.patch.object(bot, "observation_interval_ms", return_value=1500), \
            mock.patch.object(bot, "persist", side_effect=persist or (lambda *a, **k: None)), \
            mock.patch.object(bot, "publish_adjust_request"), \
            mock.patch.object(bot, "ask_interim"), \
            mock.patch.object(bot, "atomic_write_json", side_effect=atomic_write_json):
        return _run(obs)


class BotExitDiagnosticTest(unittest.TestCase):
    def test_wrong_game_names_the_raising_function(self):
        code, err, out = _run({"game": "other"})
        self.assertEqual(code, 2)
        self.assertIn("invalid observation ValueError('wrong game')", err)
        self.assertIn("bot.py:", err)
        self.assertIn(":main", err)
        self.assertEqual(out["actions"], [])

    def test_policy_error_names_the_class_and_location(self):
        root = bot.ROOT
        runtime = root / "run" / "runtimes" / "x"
        obs = {"game": "hanjuku-hero", "screenshot": "s.png", "meta": {"runtime_dir": str(runtime)}}
        with mock.patch.object(bot, "read_png", side_effect=RuntimeError("boom")):
            code, err, out = _run(obs)
        self.assertEqual(code, 2)
        self.assertIn("policy_error RuntimeError('boom')", err)
        self.assertEqual(out["actions"], [])

    def test_mixed_key_state_keeps_the_decided_actions(self):
        # a state whose dict holds both int and str keys cannot be sort_keys-dumped
        state = {"policy": {"seen": {1: "a", "x": "b"}}, "_records": [{"k": {2: 1, "y": 2}}]}
        actions = [{"type": "pad", "buttons": ["a"], "hold_ms": 100}]
        written = {}
        obs = {"game": "hanjuku-hero", "screenshot": "s.png",
               "meta": {"runtime_dir": str(bot.ROOT / "run" / "runtimes" / "x")}}
        frame = mock.Mock()
        frame.resized.return_value = frame
        frame.digest.return_value = "d"
        with mock.patch.object(bot, "read_png", return_value=frame), \
                mock.patch.object(bot, "read_record", return_value={}), \
                mock.patch.object(bot.hanjuku_resume, "inherit_running_chapter"), \
                mock.patch.object(bot.hanjuku_experience, "load", return_value={}), \
                mock.patch.object(bot.hanjuku_chart_adjust, "load", return_value=None), \
                mock.patch.object(bot, "recall_input_receipts", return_value=None), \
                mock.patch.object(bot, "card_scroll_receipt", return_value=None), \
                mock.patch.object(bot, "departure_input_receipt", return_value=None), \
                mock.patch.object(bot, "overtaken_frames", return_value=[]), \
                mock.patch.object(bot, "decide", return_value=(actions, state)), \
                mock.patch.object(bot, "overtaken_confirm", return_value=None), \
                mock.patch.object(bot, "observation_interval_ms", return_value=1500), \
                mock.patch.object(bot, "persist", side_effect=lambda rt, st, rec, *a, **k: written.update(st=st, rec=rec)), \
                mock.patch.object(bot, "publish_adjust_request"), \
                mock.patch.object(bot, "ask_interim"), \
                mock.patch.object(bot, "atomic_write_json"):
            code, err, out = _run(obs)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["actions"], actions)
        self.assertIn("mixed_keys state /policy/seen", err)
        self.assertIn("mixed_keys records", err)
        json.dumps(written["st"], sort_keys=True)
        json.dumps(written["rec"], sort_keys=True)

    def test_set_value_keeps_the_decided_actions_and_is_persisted_as_a_list(self):
        # g1153 11-08: the v519 carry manifest stored a `set` in the policy
        # memory. Every bot-state write raised TypeError, the bot exited 2 and
        # the agent dropped the decided input until the run ended input_stalled.
        state = {"step": 7, "policy": {"post_month_status": {
            "manifest_satisfied": {"どうし", "ゼウス"}}}}
        actions = [{"type": "pad", "buttons": ["b"], "hold_ms": 100}]
        written = {}
        code, err, out = _decided(
            actions, state,
            persist=lambda rt, st, *a, **k: written.update(st=st))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["actions"], actions)
        self.assertIn("non_json state /policy/post_month_status/manifest_satisfied[set]", err)
        saved = written["st"]["policy"]["post_month_status"]["manifest_satisfied"]
        self.assertEqual(sorted(saved), sorted(["どうし", "ゼウス"]))
        json.dumps(written["st"], sort_keys=True)

    def test_bot_state_write_failure_keeps_the_decided_actions(self):
        # A persistence failure must never turn a final decision into a
        # nonzero exit: the agent discards the printed actions on exit 2.
        actions = [{"type": "pad", "buttons": ["b"], "hold_ms": 100}]

        def _raise(*args, **kwargs):
            raise TypeError("Object of type set is not JSON serializable")

        code, err, out = _decided(actions, {"step": 1},
                                  atomic_write_json=_raise)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["actions"], actions)
        self.assertIn("state_write_failed TypeError", err)

    def test_evidence_write_failure_keeps_the_decided_actions(self):
        actions = [{"type": "pad", "buttons": ["b"], "hold_ms": 100}]

        def _raise(*args, **kwargs):
            raise KeyError("decision_trace")

        code, err, out = _decided(actions, {"step": 1}, persist=_raise)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["actions"], actions)
        self.assertIn("persist_failed KeyError", err)


if __name__ == "__main__":
    unittest.main()
