import io
import json
import os
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

    def test_caption_send_reset(self):
        args = self.parser.parse_args(["caption", "send", "reset"])
        self.assertEqual(args.command, "caption")
        self.assertEqual(args.caption_action, "send")
        self.assertEqual(args.op, "reset")

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

    def test_doctor_returns_nonzero_when_requested_native_cc_is_unavailable(self):
        self._write_config(
            "[audio]\n"
            "enabled = false\n\n"
            "[captions]\n"
            "enabled = true\n"
            'socket_path = "/tmp/docich-test/cc.sock"\n'
        )

        def available(name: str) -> str:
            return f"/usr/bin/{name}"

        with (
            mock.patch("docich.cli.procs.which", side_effect=available),
            mock.patch(
                "docich.cli.caption_capability",
                return_value=(False, "docichcc filterがありません"),
            ),
        ):
            rc, out, _err = self.run_main(["doctor"])
        self.assertEqual(rc, 1)
        self.assertIn("[NG] native CC", out)


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

    def test_snap_wraps_called_process_error_as_cli_error(self):
        # xkit.screenshot は procs.run(check=True) 経由で CalledProcessError を投げうる
        # (ディスプレイ未起動時の ffmpeg 失敗など)。CliError に変換され docich: エラー で終わること。
        with mock.patch(
            "docich.cli.XKit.screenshot",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ):
            rc, _out, err = self.run_main(["snap"])
        self.assertEqual(rc, 2)
        self.assertIn("docich: エラー", err)
        self.assertIn("スクリーンショットに失敗しました", err)
        self.assertIn("docich up", err)


class TestCaptionCli(IsolatedConfigTestBase):
    def test_status_requires_live_stream_socket_before_reporting_active(self):
        self._write_config(
            "[stream]\n"
            'mode = "file"\n\n'
            "[captions]\n"
            "enabled = true\n"
            f'socket_path = "{self.tmp_path / "cc" / "cc.sock"}"\n'
        )
        g = cli.load_global(self.tmp_path, config_path=self.toml_path)
        runtime = cli.StreamRuntime(
            command=["/opt/docich/ffmpeg", "-f", "null", "-"],
            captions_active=True,
            caption_detail="docichcc + libx264 a53cc",
        )
        with (
            mock.patch("docich.cli.Tmux") as tmux_class,
            mock.patch("docich.cli.XKit") as xkit_class,
            mock.patch("docich.cli.resolve_runtime", return_value=runtime),
            mock.patch(
                "docich.cli.caption_socket_ready",
                return_value=(False, "caption socketは未準備です"),
            ),
        ):
            tmux_class.return_value.has_session.return_value = True
            tmux_class.return_value.has_window.side_effect = lambda name: name == "stream"
            xkit_class.return_value.display_ready.return_value = True
            output = io.StringIO()
            with redirect_stdout(output):
                rc = cli.cmd_status(g)
        self.assertEqual(rc, 0)
        self.assertIn("captions.active: いいえ", output.getvalue())
        self.assertIn("caption socketは未準備です", output.getvalue())

    def test_supervised_stream_pins_config_and_caption_environment(self):
        self._write_config(
            "[stream]\n"
            'ffmpeg_bin = "/opt/docich/bin/ffmpeg"\n'
            'mode = "file"\n\n'
            "[captions]\n"
            "enabled = true\n"
            f'socket_path = "{self.tmp_path / "cc" / "cc.sock"}"\n'
        )
        g = cli.load_global(self.tmp_path, config_path=self.toml_path)
        argv = cli._run_argv(g, "stream")
        self.assertEqual(argv[1:3], ["--config", str(self.toml_path.resolve())])
        with mock.patch.dict(os.environ, {"DOCICH_STREAM_KEY": "test-key"}):
            env = cli._stream_window_env(g)
        self.assertEqual(env["DOCICH_FFMPEG_BIN"], "/opt/docich/bin/ffmpeg")
        self.assertEqual(env["DOCICH_CC_ENABLED"], "1")
        self.assertEqual(env["DOCICH_CC_SOCKET"], str(self.tmp_path / "cc" / "cc.sock"))
        self.assertEqual(env["DOCICH_STREAM_KEY"], "test-key")

    def test_caption_plan_with_fixture_writes_aligned_private_plan(self):
        chunks = self.tmp_path / "chunks.txt"
        translations = self.tmp_path / "translations.json"
        output = self.tmp_path / "plan.json"
        chunks.write_text("一つ目。\n二つ目。\n", encoding="utf-8")
        translations.write_text('["First.","Second."]', encoding="utf-8")

        rc, _out, err = self.run_main([
            "caption", "plan",
            "--chunks-file", str(chunks),
            "--translations-file", str(translations),
            "--execution-id", "test-speech-1",
            "--output", str(output),
        ])
        self.assertEqual(rc, 0, err)
        plan = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(plan["executionId"], "test-speech-1")
        self.assertEqual([chunk["enText"] for chunk in plan["chunks"]], ["First.", "Second."])

    def test_stream_fails_open_when_caption_socket_directory_is_unsafe(self):
        self._write_config(
            "[stream]\n"
            'mode = "file"\n'
            f'file_path = "{self.tmp_path / "out.flv"}"\n\n'
            "[captions]\n"
            "enabled = true\n"
            f'socket_path = "{self.tmp_path / "cc" / "cc.sock"}"\n'
        )
        observed: list[str] = []

        def run_once(_component, _g, build, **_kwargs):
            command, _env = build()
            observed.extend(command)

        requested = cli.StreamRuntime(
            command=["/opt/docich/ffmpeg", "-vf", "docichcc=socket=/tmp/cc.sock"],
            captions_active=True,
            caption_detail="docichcc + libx264 a53cc",
        )
        with (
            mock.patch("docich.cli.resolve_runtime", return_value=requested),
            mock.patch(
                "docich.cli.ensure_caption_socket_parent",
                side_effect=cli.CaptionSocketDirectoryError("unsafe directory"),
            ),
            mock.patch("docich.cli.run_loop", side_effect=run_once),
        ):
            rc, out, err = self.run_main(["run", "stream"])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("-vf", observed)
        self.assertNotIn("-a53cc", observed)
        self.assertIn("字幕を無効化して配信を継続", out)


if __name__ == "__main__":
    unittest.main()
