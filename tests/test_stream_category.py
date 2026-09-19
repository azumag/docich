"""The stream category/title follows the running game through the reviewed script."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.stream_category import (  # noqa: E402
    PAPER_CATEGORY_ID,
    PAPER_CATEGORY_NAME,
    SCRIPT_NAME,
    StreamCategoryError,
    announce_stream_game,
    announce_stream_paper,
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
            [str(script.resolve()), "--game", "nethack", "--games-dir", str(Path(self.g.games_dir).resolve())],
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


class TestSpawnMechanics(StreamCategoryTestBase):
    def test_child_is_detached_and_its_output_kept_in_a_private_log(self) -> None:
        script = self._install_script()
        with mock.patch("docich.stream_category.subprocess.Popen") as popen:
            announce_stream_game(self.g, "nethack")

        kwargs = popen.call_args.kwargs
        self.assertEqual(Path(popen.call_args.args[0][0]).resolve(), script.resolve())
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(Path(kwargs["cwd"]).resolve(), self.soren)
        log = Path(self.g.state_dir) / "logs" / "stream-game.log"
        self.assertTrue(log.is_file())
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)

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
