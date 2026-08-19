import io
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.agent import brains  # noqa: E402


def _obs(adapter: str) -> base.Observation:
    return base.Observation(
        game="g", title="g", adapter=adapter, ts=time.time(), kind="text", text="hi"
    )


class BrainsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _game(self, **agent_kwargs) -> config.GameConfig:
        return config.GameConfig(
            name="testgame",
            title="Test Game",
            adapter="cli",
            raw={},
            agent=config.GameAgentConfig(**agent_kwargs),
            path=self.repo_root / "config" / "games" / "testgame.toml",
        )


class TestRandomBrain(BrainsTestBase):
    def test_retroarch_adapter_returns_single_pad_action(self):
        brain = brains.RandomBrain(self.g, self._game())
        for _ in range(20):
            actions = brain.decide(_obs("retroarch"))
            self.assertEqual(len(actions), 1)
            a = actions[0]
            self.assertEqual(a.type, "pad")
            self.assertEqual(a.hold_ms, 120)
            self.assertEqual(len(a.buttons), 1)
            self.assertIn(a.buttons[0], {"up", "down", "left", "right", "a", "b"})

    def test_cli_adapter_returns_single_text_action(self):
        brain = brains.RandomBrain(self.g, self._game())
        for _ in range(20):
            actions = brain.decide(_obs("cli"))
            self.assertEqual(len(actions), 1)
            a = actions[0]
            self.assertEqual(a.type, "text")
            self.assertIn(a.text, {"h", "j", "k", "l"})

    def test_browser_adapter_returns_wait_500ms(self):
        brain = brains.RandomBrain(self.g, self._game())
        actions = brain.decide(_obs("browser"))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].type, "wait")
        self.assertEqual(actions[0].ms, 500)

    def test_unknown_adapter_falls_back_to_wait(self):
        brain = brains.RandomBrain(self.g, self._game())
        actions = brain.decide(_obs("something-else"))
        self.assertEqual(actions[0].type, "wait")


class TestCommandBrainConstruction(BrainsTestBase):
    def test_empty_command_raises_adapter_error(self):
        game = self._game(brain="command", command="")
        with self.assertRaises(AdapterError):
            brains.CommandBrain(self.g, game)

    def test_string_command_is_shlex_split(self):
        game = self._game(brain="command", command="echo hello world")
        brain = brains.CommandBrain(self.g, game)
        self.assertEqual(brain.cmd, ["echo", "hello", "world"])

    def test_list_command_is_used_as_is(self):
        game = self._game(brain="command", command=[sys.executable, "-c", "pass"])
        brain = brains.CommandBrain(self.g, game)
        self.assertEqual(brain.cmd, [sys.executable, "-c", "pass"])


class TestCommandBrainDecide(BrainsTestBase):
    def test_success_parses_stdout_actions(self):
        script = 'print(\'{"actions":[{"type":"wait","ms":1}]}\')'
        game = self._game(brain="command", command=[sys.executable, "-c", script])
        brain = brains.CommandBrain(self.g, game)
        actions = brain.decide(_obs("cli"))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].type, "wait")
        self.assertEqual(actions[0].ms, 1)

    def test_reads_observation_json_from_stdin(self):
        # stdin から受け取った obs の game 名をそのまま special action の key に埋め込んで確認する
        script = (
            "import sys, json; "
            "obs = json.load(sys.stdin); "
            "print(json.dumps({'actions': [{'type': 'special', 'key': obs['game']}]}))"
        )
        game = self._game(brain="command", command=[sys.executable, "-c", script])
        brain = brains.CommandBrain(self.g, game)
        actions = brain.decide(_obs("cli"))
        self.assertEqual(actions[0].key, "g")  # _obs() の game="g"

    def test_nonzero_exit_returns_empty_list(self):
        game = self._game(brain="command", command=[sys.executable, "-c", "import sys; sys.exit(3)"])
        brain = brains.CommandBrain(self.g, game)
        buf = io.StringIO()
        with redirect_stderr(buf):
            actions = brain.decide(_obs("cli"))
        self.assertEqual(actions, [])

    def test_invalid_json_output_returns_empty_list(self):
        game = self._game(brain="command", command=[sys.executable, "-c", "print('not json at all')"])
        brain = brains.CommandBrain(self.g, game)
        buf = io.StringIO()
        with redirect_stderr(buf):
            actions = brain.decide(_obs("cli"))
        self.assertEqual(actions, [])

    def test_timeout_returns_empty_list(self):
        game = self._game(brain="command", command=[sys.executable, "-c", "import time; time.sleep(5)"])
        self.g.agent.brain_timeout_s = 0.2
        brain = brains.CommandBrain(self.g, game)
        buf = io.StringIO()
        with redirect_stderr(buf):
            actions = brain.decide(_obs("cli"))
        self.assertEqual(actions, [])

    def test_decide_runs_command_with_cwd_repo_root(self):
        # tmux window の cwd に依存せず、brain の相対パス参照 (例: "brains/hanjuku/brain.py")
        # が安定するよう、CommandBrain は procs.run に cwd=repo_root を渡す (hanjuku_brain.md §1)。
        script = (
            "import os, json; "
            "print(json.dumps({'actions': [{'type': 'special', 'key': os.getcwd()}]}))"
        )
        game = self._game(brain="command", command=[sys.executable, "-c", script])
        brain = brains.CommandBrain(self.g, game)
        actions = brain.decide(_obs("cli"))
        self.assertEqual(len(actions), 1)
        self.assertEqual(Path(actions[0].key).resolve(), self.repo_root.resolve())


class TestBuildBrain(BrainsTestBase):
    def test_command_kind_builds_command_brain(self):
        game = self._game(brain="command", command=[sys.executable, "-c", "pass"])
        brain = brains.build_brain(self.g, game)
        self.assertIsInstance(brain, brains.CommandBrain)

    def test_random_kind_builds_random_brain(self):
        game = self._game(brain="random")
        brain = brains.build_brain(self.g, game)
        self.assertIsInstance(brain, brains.RandomBrain)

    def test_unknown_kind_raises(self):
        game = self._game(brain="bogus")
        with self.assertRaises(AdapterError):
            brains.build_brain(self.g, game)


if __name__ == "__main__":
    unittest.main()
