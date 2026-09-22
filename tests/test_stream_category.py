"""The stream category follows the running game through the reviewed script."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich import stream_category_runner  # noqa: E402
from docich.adapters.program import PAPER_VIEW_NAME  # noqa: E402
from docich.stream_category import (  # noqa: E402
    PAPER_CATEGORY_ID,
    PAPER_CATEGORY_NAME,
    SCRIPT_NAME,
    StreamCategoryError,
    announce_running_view,
    announce_stream_game,
    announce_stream_paper,
    commit_hook,
    script_path,
    twitch_category,
)


class StreamCategoryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        (self.root / "config" / "games").mkdir(parents=True)
        self.soren = (self.root / "soren")
        self.soren.mkdir()
        self.soren = self.soren.resolve()
        (self.root / "config" / "docich.toml").write_text(
            "[paths]\nstate_dir = \"run\"\ngames_dir = \"config/games\"\n"
            f"[webui]\nsoren_root = \"{self.soren}\"\n",
            encoding="utf-8",
        )
        self._write_game("nethack", twitch=True)
        self._write_game("plain", twitch=False)
        self.g = config.load_global(self.root)
        self.spawned: list[dict] = []

    def _write_game(self, name: str, *, twitch: bool, category_id: str = "130") -> None:
        text = (
            f'[game]\nname = "{name}"\ntitle = "{name}"\nadapter = "cli"\n'
            f'[cli]\ncommand = "{name}"\n[agent]\nenabled = false\n'
        )
        if twitch:
            text += (
                f'[twitch]\ncategory_id = "{category_id}"\n'
                f'category_name = "NetHack"\ntitle_prefix = "[NetHack]"\n'
            )
        (self.root / "config" / "games" / f"{name}.toml").write_text(text, encoding="utf-8")

    def _install_script(self, *, executable: bool = True) -> Path:
        path = self.soren / SCRIPT_NAME
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def _recorder(self, argv, *, cwd, log_path):
        self.spawned.append({"argv": list(argv), "cwd": Path(cwd), "log_path": Path(log_path)})


class TestAnnounceStreamGame(StreamCategoryTestBase):
    def test_fixed_argv_names_only_the_game_and_the_reviewed_games_dir(self) -> None:
        script = self._install_script()

        self.assertTrue(announce_stream_game(self.g, "nethack", spawn=self._recorder))

        call = self.spawned[0]
        self.assertEqual(
            call["argv"],
            [
                str(script.resolve()),
                "--game",
                "nethack",
                "--games-dir",
                str(Path(self.g.games_dir).resolve()),
                "--category-only",
            ],
        )
        # The script loads its own .env from the Soren root, so it must run
        # there -- and no token/channel id is ever passed through this call.
        self.assertEqual(call["cwd"].resolve(), self.soren)
        joined = " ".join(call["argv"]).lower()
        for secret in ("token", "client_id", "broadcaster", "oauth"):
            self.assertNotIn(secret, joined)

    def test_paper_view_uses_explicit_non_game_category(self) -> None:
        script = self._install_script()

        self.assertTrue(announce_stream_paper(self.g, spawn=self._recorder))

        call = self.spawned[0]
        self.assertEqual(
            call["argv"],
            [
                str(script.resolve()),
                "--category-id",
                PAPER_CATEGORY_ID,
                "--category-only",
                "--category-name",
                PAPER_CATEGORY_NAME,
            ],
        )
        self.assertNotIn("--game", call["argv"])
        self.assertNotIn("--title-prefix", call["argv"])

    def test_a_game_without_a_twitch_category_is_left_alone(self) -> None:
        self._install_script()
        self.assertFalse(announce_stream_game(self.g, "plain", spawn=self._recorder))
        self.assertEqual(self.spawned, [])

    def test_an_empty_or_non_string_category_id_is_not_announced(self) -> None:
        self._install_script()
        for raw in ('category_id = ""', "category_id = 130", "# no category_id"):
            with self.subTest(raw=raw):
                (self.root / "config" / "games" / "odd.toml").write_text(
                    '[game]\nname = "odd"\ntitle = "odd"\nadapter = "cli"\n'
                    '[cli]\ncommand = "odd"\n[agent]\nenabled = false\n'
                    f"[twitch]\n{raw}\n",
                    encoding="utf-8",
                )
                self.assertIsNone(twitch_category(self.g, "odd"))
                self.assertFalse(announce_stream_game(self.g, "odd", spawn=self._recorder))
        self.assertEqual(self.spawned, [])

    def test_an_unknown_game_is_not_announced(self) -> None:
        self._install_script()
        self.assertFalse(announce_stream_game(self.g, "absent", spawn=self._recorder))
        self.assertEqual(self.spawned, [])

    def test_a_bogus_game_name_never_reaches_the_script(self) -> None:
        self._install_script()
        for name in ("", "../escape", "nethack;id", "net hack", "a" * 200):
            with self.subTest(name=name):
                with self.assertRaises(StreamCategoryError):
                    announce_stream_game(self.g, name, spawn=self._recorder)
        self.assertEqual(self.spawned, [])

    def test_a_missing_or_non_executable_script_is_reported_not_guessed(self) -> None:
        with self.assertRaises(StreamCategoryError):
            announce_stream_game(self.g, "nethack", spawn=self._recorder)
        self._install_script(executable=False)
        with self.assertRaises(StreamCategoryError):
            announce_stream_game(self.g, "nethack", spawn=self._recorder)
        self.assertEqual(self.spawned, [])

    def test_script_path_follows_the_configured_soren_root(self) -> None:
        self.assertEqual(script_path(self.g), self.soren / SCRIPT_NAME)


class TestRunningView(StreamCategoryTestBase):
    """A committed switch announces whichever view it put on screen."""

    def test_a_committed_game_uses_its_twitch_category(self) -> None:
        script = self._install_script()
        self.assertTrue(announce_running_view(self.g, "nethack", spawn=self._recorder))
        self.assertEqual(self.spawned[0]["argv"][0], str(script.resolve()))
        self.assertIn("--game", self.spawned[0]["argv"])
        self.assertIn("nethack", self.spawned[0]["argv"])

    def test_a_committed_paper_view_uses_the_explicit_category(self) -> None:
        script = self._install_script()
        self.assertTrue(announce_running_view(self.g, PAPER_VIEW_NAME, spawn=self._recorder))
        self.assertEqual(
            self.spawned[0]["argv"],
            [
                str(script.resolve()),
                "--category-id",
                PAPER_CATEGORY_ID,
                "--category-only",
                "--category-name",
                PAPER_CATEGORY_NAME,
            ],
        )

    def test_a_game_without_a_category_is_left_alone(self) -> None:
        self._install_script()
        self.assertFalse(announce_running_view(self.g, "plain", spawn=self._recorder))
        self.assertEqual(self.spawned, [])

    def test_commit_hook_announces_the_game_it_is_given(self) -> None:
        self._install_script()
        hook = commit_hook(self.g, spawn=self._recorder)
        hook("nethack")
        self.assertEqual(self.spawned[0]["argv"][-1], "--category-only")
        self.assertIn("nethack", self.spawned[0]["argv"])


class TestSpawnMechanics(StreamCategoryTestBase):
    def test_child_is_detached_and_its_output_kept_in_a_private_log(self) -> None:
        script = self._install_script()
        with mock.patch("docich.stream_category.subprocess.Popen") as popen:
            announce_stream_game(self.g, "nethack")

        kwargs = popen.call_args.kwargs
        child = popen.call_args.args[0]
        runner = Path(__file__).resolve().parents[1] / "src/docich/stream_category_runner.py"
        self.assertEqual(Path(child[0]).resolve(), Path(sys.executable).resolve())
        self.assertEqual(Path(child[1]).resolve(), runner.resolve())
        self.assertEqual(
            Path(child[2]).resolve(),
            (Path(self.g.state_dir) / "logs" / "stream-category.lock").resolve(),
        )
        self.assertEqual(child[3], "--")
        self.assertEqual(Path(child[4]).resolve(), script.resolve())
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(Path(kwargs["cwd"]).resolve(), self.soren)
        log = Path(self.g.state_dir) / "logs" / "stream-game.log"
        self.assertTrue(log.is_file())
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)

    def test_systemd_oneshot_submits_an_independent_transient_unit(self) -> None:
        script = self._install_script()
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"INVOCATION_ID": "parent-corner"}, clear=False), \
             mock.patch("docich.stream_category.subprocess.run", side_effect=fake_run), \
             mock.patch("docich.stream_category.subprocess.Popen",
                        side_effect=AssertionError("unsafe parent cgroup")):
            announce_stream_game(self.g, "nethack")

        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[:4], ["systemd-run", "--user", "--quiet", "--collect"])
        self.assertTrue(any(item.startswith("--unit=docich-stream-category-") for item in argv))
        self.assertIn("--property=Type=exec", argv)
        self.assertIn("--property=RuntimeMaxSec=120", argv)
        self.assertIn("--property=TimeoutStopSec=15", argv)
        self.assertTrue(any(item.startswith("--property=StandardOutput=append:") for item in argv))
        self.assertTrue(any(item.startswith("--working-directory=") for item in argv))
        separator = argv.index("--")
        child = argv[separator + 1:]
        runner = Path(__file__).resolve().parents[1] / "src/docich/stream_category_runner.py"
        self.assertEqual(Path(child[0]).resolve(), Path(sys.executable).resolve())
        self.assertEqual(Path(child[1]).resolve(), runner.resolve())
        self.assertEqual(
            Path(child[2]).resolve(),
            (Path(self.g.state_dir) / "logs" / "stream-category.lock").resolve(),
        )
        self.assertEqual(child[3], "--")
        self.assertEqual(Path(child[4]).resolve(), script.resolve())
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["timeout"], 30)

    def test_category_runner_executes_the_reviewed_command_under_the_lock(self) -> None:
        lock = self.root / "run" / "logs" / "stream-category.lock"
        command = ["/bin/echo", "category-only"]
        completed = subprocess.CompletedProcess(command, 0)
        with mock.patch(
            "docich.stream_category_runner.subprocess.run", return_value=completed
        ) as run:
            self.assertEqual(
                stream_category_runner.main([str(lock), "--", *command]), 0
            )
        run.assert_called_once_with(command, check=False)
        self.assertTrue(lock.is_file())

    def test_category_runner_does_not_interleave_updates(self) -> None:
        lock = self.root / "run" / "logs" / "stream-category.lock"
        events = self.root / "events.txt"
        worker = self.root / "worker.py"
        worker.write_text(
            "import pathlib, sys, time\n"
            "pathlib.Path(sys.argv[1]).open('a').write('start:' + sys.argv[2] + '\\n')\n"
            "time.sleep(0.1)\n"
            "pathlib.Path(sys.argv[1]).open('a').write('end:' + sys.argv[2] + '\\n')\n",
            encoding="utf-8",
        )
        runner = Path(__file__).resolve().parents[1] / "src/docich/stream_category_runner.py"
        command = [sys.executable, str(runner), str(lock), "--",
                   sys.executable, str(worker), str(events)]
        first = subprocess.Popen([*command, "first"])
        time.sleep(0.02)
        second = subprocess.Popen([*command, "second"])
        self.assertEqual(first.wait(timeout=5), 0)
        self.assertEqual(second.wait(timeout=5), 0)
        lines = events.read_text(encoding="utf-8").splitlines()
        self.assertIn(lines, [
            ["start:first", "end:first", "start:second", "end:second"],
            ["start:second", "end:second", "start:first", "end:first"],
        ])

    def test_log_is_appended_so_history_survives_repeated_switches(self) -> None:
        self._install_script()
        log = Path(self.g.state_dir) / "logs" / "stream-game.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("earlier\n", encoding="utf-8")
        os.chmod(log, 0o600)
        with mock.patch("docich.stream_category.subprocess.Popen"):
            announce_stream_game(self.g, "nethack")
        self.assertEqual(log.read_text(encoding="utf-8"), "earlier\n")

    def test_a_failed_spawn_is_a_stream_category_error(self) -> None:
        self._install_script()
        with mock.patch(
            "docich.stream_category.subprocess.Popen", side_effect=OSError("no exec")
        ):
            with self.assertRaises(StreamCategoryError):
                announce_stream_game(self.g, "nethack")


if __name__ == "__main__":
    unittest.main()
