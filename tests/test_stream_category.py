"""The stream category follows the running game through the reviewed script."""
from __future__ import annotations

import os
import json
import shutil
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
    viewer_title_args,
    VIEWER_GAME_NAMES,
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
                "--activity", "NetHack", "--strategy", "AIプレイ配信",
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
                "--category-name",
                PAPER_CATEGORY_NAME,
                "--activity", "ペーパートレード", "--strategy", "AIの検証配信",
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
                "--category-name",
                PAPER_CATEGORY_NAME,
                "--activity", "ペーパートレード", "--strategy", "AIの検証配信",
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
        self.assertEqual(self.spawned[0]["argv"][-4:], viewer_title_args("nethack"))
        self.assertIn("nethack", self.spawned[0]["argv"])

    def test_each_switch_replaces_the_previous_game_title(self) -> None:
        self._install_script()
        for game in ("hanjuku-hero", "sorengame", "soren91"):
            self._write_game(game, twitch=True)
        hook = commit_hook(self.g, spawn=self._recorder)
        sequence = ("hanjuku-hero", "sorengame", "soren91", "paper-view", "hanjuku-hero")
        with mock.patch.dict(os.environ, {"STREAM_GAME_STRATEGY": "PRIVATE-OLD-STRATEGY"}):
            for game in sequence:
                hook(game)
        self.assertEqual(len(self.spawned), len(sequence))
        for game, call in zip(sequence, self.spawned):
            argv = call["argv"]
            self.assertEqual(argv[-4:], viewer_title_args(game))
            self.assertNotIn("--category-only", argv)
            self.assertNotIn("--title-only", argv)
            self.assertNotIn("--day", argv)  # day stays owned by the reviewed updater
            self.assertNotIn("PRIVATE-OLD-STRATEGY", " ".join(argv))

    def test_all_catalog_games_have_public_viewer_labels(self) -> None:
        catalog = Path(__file__).resolve().parents[1] / "config" / "games"
        for path in catalog.glob("*.toml"):
            self.assertIn(path.stem, VIEWER_GAME_NAMES)
        self.assertEqual(viewer_title_args("hanjuku-hero")[1], "半熟英雄")
        self.assertEqual(viewer_title_args("sorengame")[1], "ソ連ゲーム")

    def test_unknown_safe_id_is_explicit_and_bad_ids_are_rejected(self) -> None:
        self.assertEqual(viewer_title_args("new-game"),
                         ["--activity", "new-game", "--strategy", "AIプレイ配信"])
        for game in ("", "../secret", "x;id", "bad name"):
            with self.assertRaises(ValueError):
                viewer_title_args(game)

    def test_title_update_preserves_hook_error_contract(self) -> None:
        self._install_script()
        # The coordinator already catches StreamCategoryError from this hook;
        # adding title arguments does not introduce another transport path.
        with mock.patch("docich.stream_category._spawn", side_effect=StreamCategoryError("unavailable")):
            with self.assertRaises(StreamCategoryError):
                commit_hook(self.g)("nethack")

    def test_reviewed_updater_receives_public_title_and_category_together(self) -> None:
        source = Path(__file__).resolve().parents[1] / "games/soviet_now/update_stream_game.sh"
        if not source.is_file():
            self.skipTest("pinned soviet_now submodule not initialized")
        script = self.soren / SCRIPT_NAME
        shutil.copyfile(source, script)
        script.chmod(0o755)
        stub_dir = self.root / "stub-bin"
        stub_dir.mkdir()
        result = self.root / "patch.json"
        stub = stub_dir / "curl"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "a=sys.argv[1:]\n"
            "if '-X' in a:\n"
            " assert a[a.index('-X')+1]=='PATCH'\n"
            " pathlib.Path(os.environ['TEST_PATCH']).write_text(a[a.index('-d')+1])\n"
            " pathlib.Path(a[a.index('-o')+1]).write_text('')\n"
            " print('204',end='')\n"
            "elif any('oauth2/validate' in x for x in a):\n"
            " print(json.dumps({'client_id':'test','login':'test','scopes':['channel:manage:broadcast']}))\n"
            "else:\n"
            " print(json.dumps({'data':[{'title':'OLD PRIVATE WORK','game_id':'1','game_name':'Old'}]}))\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = {
            "PATH": str(stub_dir) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "TWITCH_GAME_TOKEN": "test-only", "TWITCH_BROADCASTER_ID": "test-only",
            "STREAM_GAME_STRATEGY": "PRIVATE-OLD-STRATEGY", "TEST_PATCH": str(result),
            "OPS_BRIEF_FILE": str(self.root / "private-brief"),
        }
        (self.root / "private-brief").write_text("- PRIVATE-OPS-BRIEF\n")
        for game in ("nethack", "sorengame", "hanjuku-hero", "paper-view"):
            if game != "paper-view":
                self._write_game(game, twitch=True)
            announce_running_view(self.g, game, spawn=self._recorder)
            argv = self.spawned[-1]["argv"]
            proc = subprocess.run([*argv, "--day", "202"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(result.read_text())
            args = viewer_title_args(game)
            self.assertEqual(payload["title"], f"[day202] {args[1]} {args[3]}")
            self.assertEqual(payload["game_id"], PAPER_CATEGORY_ID if game == "paper-view" else "130")
            self.assertNotIn("PRIVATE", json.dumps(payload))


class TestSpawnMechanics(StreamCategoryTestBase):
    def test_child_is_detached_and_its_output_kept_in_a_private_log(self) -> None:
        script = self._install_script()
        with mock.patch.dict(os.environ, {"INVOCATION_ID": ""}, clear=False), \
             mock.patch("docich.stream_category.subprocess.Popen") as popen:
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

        # Reproduce the tick service environment that caused the production
        # failures: no user-bus variables at all (#947).
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"INVOCATION_ID": "parent-corner"}, clear=True), \
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
        # The submission carries the user-bus environment itself; the tick unit
        # it runs under does not reliably provide it (#947).
        self.assertEqual(kwargs["env"]["XDG_RUNTIME_DIR"], f"/run/user/{os.getuid()}")

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
        with mock.patch.dict(os.environ, {"INVOCATION_ID": ""}, clear=False), \
             mock.patch("docich.stream_category.subprocess.Popen"):
            announce_stream_game(self.g, "nethack")
        self.assertEqual(log.read_text(encoding="utf-8"), "earlier\n")

    def test_a_failed_spawn_is_a_stream_category_error(self) -> None:
        self._install_script()
        with mock.patch.dict(os.environ, {"INVOCATION_ID": ""}, clear=False), \
             mock.patch(
                 "docich.stream_category.subprocess.Popen", side_effect=OSError("no exec")
             ):
            with self.assertRaises(StreamCategoryError):
                announce_stream_game(self.g, "nethack")


if __name__ == "__main__":
    unittest.main()


class StreamTitleSyncSkipObservationTests(StreamCategoryTestBase):
    def test_missing_category_records_fixed_skip_reason(self):
        with mock.patch("docich.stream_category._record_title_sync_skip") as record:
            self.assertFalse(announce_stream_game(self.g, "plain", spawn=self._recorder))
        record.assert_called_once_with(self.g, "category_not_configured")
        self.assertEqual(self.spawned, [])

    def test_missing_updater_records_fixed_skip_reason(self):
        with mock.patch("docich.stream_category._record_title_sync_skip") as record:
            with self.assertRaises(StreamCategoryError):
                announce_stream_game(self.g, "nethack", spawn=self._recorder)
        record.assert_called_once_with(self.g, "updater_missing")
        self.assertEqual(self.spawned, [])

    def test_dispatch_failure_records_fixed_skip_reason_and_preserves_error(self):
        self._install_script()
        failure = StreamCategoryError("dispatch failed")
        with mock.patch("docich.stream_category._record_title_sync_skip") as record:
            with mock.patch("docich.stream_category._spawn", side_effect=failure):
                with self.assertRaisesRegex(StreamCategoryError, "dispatch failed"):
                    announce_stream_game(self.g, "nethack")
        record.assert_called_once_with(self.g, "dispatch_failed")

    def test_recorder_uses_fixed_cli_reason_and_minimal_environment(self):
        from docich.stream_category import _record_title_sync_skip

        helper = self.soren / "lib" / "stream_title_sync.py"
        helper.parent.mkdir()
        helper.write_text("# test helper\\n", encoding="utf-8")
        with mock.patch("docich.stream_category.subprocess.run") as run:
            _record_title_sync_skip(self.g, "dispatch_failed")
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(argv, [sys.executable, str(helper), "--record-skip", "dispatch_failed"])
        self.assertEqual(kwargs["cwd"], str(self.soren))
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin"})
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 2)
        self.assertFalse(kwargs["check"])
        run.reset_mock()
        _record_title_sync_skip(self.g, "PRIVATE-TITLE")
        run.assert_not_called()
