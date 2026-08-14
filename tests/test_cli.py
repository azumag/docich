import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli  # noqa: E402


class TestParseArgsAcceptsAllSubcommands(unittest.TestCase):
    def setUp(self):
        self.parser = cli.build_parser()

    def test_doctor(self):
        args = self.parser.parse_args(["doctor"])
        self.assertEqual(args.command, "doctor")

    def test_games(self):
        args = self.parser.parse_args(["games"])
        self.assertEqual(args.command, "games")

    def test_up(self):
        args = self.parser.parse_args(["up"])
        self.assertEqual(args.command, "up")

    def test_down(self):
        args = self.parser.parse_args(["down"])
        self.assertEqual(args.command, "down")

    def test_start(self):
        args = self.parser.parse_args(["start", "nethack"])
        self.assertEqual(args.command, "start")
        self.assertEqual(args.game, "nethack")

    def test_stop(self):
        args = self.parser.parse_args(["stop"])
        self.assertEqual(args.command, "stop")

    def test_switch(self):
        args = self.parser.parse_args(["switch", "hanjuku-hero"])
        self.assertEqual(args.command, "switch")
        self.assertEqual(args.game, "hanjuku-hero")

    def test_status(self):
        args = self.parser.parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_snap_default(self):
        args = self.parser.parse_args(["snap"])
        self.assertEqual(args.command, "snap")
        self.assertIsNone(args.output)

    def test_snap_with_output(self):
        args = self.parser.parse_args(["snap", "-o", "out.png"])
        self.assertEqual(args.output, "out.png")

    def test_obs_without_game(self):
        args = self.parser.parse_args(["obs"])
        self.assertEqual(args.command, "obs")
        self.assertIsNone(args.game)

    def test_obs_with_game(self):
        args = self.parser.parse_args(["obs", "nethack"])
        self.assertEqual(args.game, "nethack")

    def test_send(self):
        args = self.parser.parse_args(["send", "-", '{"type": "wait", "ms": 1}'])
        self.assertEqual(args.command, "send")
        self.assertEqual(args.game, "-")
        self.assertEqual(args.json, '{"type": "wait", "ms": 1}')

    def test_ra_cmd(self):
        args = self.parser.parse_args(["ra-cmd", "SAVE_STATE"])
        self.assertEqual(args.command, "ra-cmd")
        self.assertEqual(args.cmd, ["SAVE_STATE"])

    def test_ra_cmd_multi_word(self):
        args = self.parser.parse_args(["ra-cmd", "SAVE_STATE_SLOT", "1"])
        self.assertEqual(args.cmd, ["SAVE_STATE_SLOT", "1"])

    def test_run_display(self):
        args = self.parser.parse_args(["run", "display"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.component, "display")
        self.assertIsNone(args.name)

    def test_run_game_with_name(self):
        args = self.parser.parse_args(["run", "game", "nethack"])
        self.assertEqual(args.component, "game")
        self.assertEqual(args.name, "nethack")

    def test_run_rejects_unknown_component(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["run", "bogus"])

    def test_global_config_option_before_subcommand(self):
        args = self.parser.parse_args(["--config", "/tmp/x.toml", "games"])
        self.assertEqual(args.config, "/tmp/x.toml")
        self.assertEqual(args.command, "games")

    def test_missing_subcommand_exits(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args([])

    def test_start_without_game_exits(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["start"])


class IsolatedConfigTestBase(unittest.TestCase):
    """Builds a --config pointing at an isolated temp state/games/roms tree so
    these tests never touch the real repo's run/ or config/games/."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.games_dir = self.tmp_path / "games"
        self.games_dir.mkdir(parents=True)
        self.toml_path = self.tmp_path / "docich.toml"
        self._write_config()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_config(self, extra: str = "") -> None:
        state_dir = self.tmp_path / "run"
        roms_dir = self.tmp_path / "roms"
        self.toml_path.write_text(
            "[paths]\n"
            f'state_dir = "{state_dir}"\n'
            f'games_dir = "{self.games_dir}"\n'
            f'roms_dir = "{roms_dir}"\n'
            "\n" + extra,
            encoding="utf-8",
        )

    def _write_game(self, filename: str, text: str) -> None:
        (self.games_dir / filename).write_text(text, encoding="utf-8")

    def run_main(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--config", str(self.toml_path), *args])
        return rc, out.getvalue(), err.getvalue()


class TestGamesSmoke(IsolatedConfigTestBase):
    def test_games_with_no_games_defined(self):
        rc, out, _err = self.run_main(["games"])
        self.assertEqual(rc, 0)
        self.assertIn("ゲーム定義がありません", out)

    def test_games_lists_defined_game(self):
        self._write_game(
            "nethack.toml",
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
            "[agent]\nenabled = true\n",
        )
        rc, out, _err = self.run_main(["games"])
        self.assertEqual(rc, 0)
        self.assertIn("nethack", out)
        self.assertIn("cli", out)
        self.assertIn("有効", out)

    def test_games_skips_broken_file_with_warning_and_does_not_crash(self):
        self._write_game("broken.toml", "not valid toml [[[")
        rc, _out, err = self.run_main(["games"])
        self.assertEqual(rc, 0)
        self.assertIn("broken.toml", err)


class TestDoctorSmoke(IsolatedConfigTestBase):
    def test_doctor_does_not_crash_and_returns_int_status(self):
        rc, out, _err = self.run_main(["doctor"])
        self.assertIn(rc, (0, 1))
        self.assertIn("docich doctor", out)
        self.assertIn("[コア]", out)
        self.assertIn("[adapter:retroarch]", out)
        self.assertIn("[adapter:cli]", out)
        self.assertIn("[adapter:browser]", out)

    def test_doctor_reports_broken_game_without_crashing(self):
        self._write_game("broken.toml", "not valid toml [[[")
        rc, out, _err = self.run_main(["doctor"])
        self.assertIn(rc, (0, 1))
        self.assertIn("broken", out)


class TestMainErrorHandling(IsolatedConfigTestBase):
    def test_send_to_missing_game_exits_2_with_japanese_error(self):
        rc, _out, err = self.run_main(["send", "no-such-game", '{"type":"wait","ms":1}'])
        self.assertEqual(rc, 2)
        self.assertIn("docich: エラー", err)

    def test_obs_without_current_game_exits_2(self):
        rc, _out, err = self.run_main(["obs"])
        self.assertEqual(rc, 2)
        self.assertIn("docich: エラー", err)

    def test_start_without_display_window_exits_2(self):
        # tmux 実行不要: has-session/list-windows を「セッション無し」で失敗させるように
        # procs.run をモックし、実際の tmux バイナリには依存しない。
        self._write_game("nethack.toml", '[game]\nname = "nethack"\nadapter = "cli"\n')
        no_session = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no session")
        with mock.patch("docich.tmux.procs.run", return_value=no_session):
            rc, _out, err = self.run_main(["start", "nethack"])
        self.assertEqual(rc, 2)
        self.assertIn("docich up", err)


if __name__ == "__main__":
    unittest.main()
