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


if __name__ == "__main__":
    unittest.main()
