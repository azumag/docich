import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, config, tts  # noqa: E402


class TtsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.games_dir = self.repo_root / "config" / "games"
        self.games_dir.mkdir(parents=True)
        self.submodule = self.repo_root / "games" / "soviet_now"
        self.submodule.mkdir(parents=True)
        self.toml = self.repo_root / "docich.toml"
        self.toml.write_text(
            "[paths]\n"
            f'state_dir = "{self.repo_root / "run"}"\n'
            f'games_dir = "{self.games_dir}"\n'
            f'roms_dir = "{self.repo_root / "roms"}"\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.repo_root, config_path=self.toml)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_game(self, name="sorengame", *, submodule="games/soviet_now"):
        (self.games_dir / f"{name}.toml").write_text(
            f'[game]\nname = "{name}"\n'
            f'title = "TTS game"\nadapter = "browser"\n'
            f'submodule = "{submodule}"\n',
            encoding="utf-8",
        )
        return name

    def _write_script(self, name="say_enqueue.sh"):
        script = self.submodule / name
        script.write_text("#!/bin/bash\n\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        return script

    def _text(self, text="テスト音声。"):
        path = self.repo_root / "input.txt"
        path.write_text(text, encoding="utf-8")
        return path


class TestGameSubmodule(TtsTestBase):
    def test_defaults_to_games_under_repo_root(self):
        self._write_game("sorengame", submodule="games/soviet_now")
        self.assertEqual(tts.game_submodule(self.g, "sorengame"), self.submodule.resolve())

    def test_symlinked_or_repo_relative_path_is_allowed_once_inside_repo(self):
        self._write_game("sorengame", submodule="games/soviet_now")
        root = tts.game_submodule(self.g, "sorengame")
        self.assertTrue(root.is_relative_to(self.repo_root.resolve()))

    def test_missing_submodule_raises_clear_japanese_error(self):
        self._write_game("sorengame", submodule="games/not-present")
        with self.assertRaises(tts.TtsError) as ctx:
            tts.game_submodule(self.g, "sorengame")
        self.assertIn("submodule が見つかりません", str(ctx.exception))
        self.assertIn("git submodule update --init", str(ctx.exception))

    def test_absolute_path_outside_repo_is_rejected(self):
        self._write_game("sorengame", submodule="/tmp/outside")
        with self.assertRaises(tts.TtsError) as ctx:
            tts.game_submodule(self.g, "sorengame")
        self.assertIn("リポジトリ内に限定", str(ctx.exception))

    def test_unknown_game_is_config_error(self):
        with self.assertRaises(config.ConfigError):
            tts.game_submodule(self.g, "missing-game")

    def test_script_must_be_allowed_and_executable(self):
        self._write_game("sorengame")
        (self.submodule / "evil.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (self.submodule / "evil.sh").chmod(0o755)
        with self.assertRaises(tts.TtsError) as ctx:
            tts.resolve_script(self.g, "sorengame")
        self.assertIn("say_enqueue.sh", str(ctx.exception))


class TestBuildInvocation(TtsTestBase):
    def test_relative_path_resolution_and_cwd(self):
        self._write_game("sorengame")
        script = self._write_script()
        inv = tts.build_invocation(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
        )
        self.assertEqual(inv.script_path, script.resolve())
        self.assertEqual(inv.cwd, self.submodule.resolve())
        self.assertEqual(inv.argv[0], str(script.resolve()))
        self.assertTrue(Path(inv.argv[1]).is_absolute())
        self.assertTrue(Path(inv.argv[1]).name, "content.txt")
        self.assertEqual(Path(inv.argv[1]).read_text(encoding="utf-8"), "テスト音声。")

    def test_text_file_missing_is_rejected(self):
        self._write_game("sorengame")
        self._write_script()
        with self.assertRaises(tts.TtsError) as ctx:
            tts.build_invocation(
                self.g,
                game_name="sorengame",
                text_file=self.repo_root / "missing.txt",
                rate=120,
                pre_delay=0,
            )
        self.assertIn("テキストファイルが見つかりません", str(ctx.exception))

    def test_rate_and_pre_delay_are_embedded_as_argv_ints(self):
        self._write_game("sorengame")
        self._write_script()
        inv = tts.build_invocation(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=150,
            pre_delay=5,
        )
        self.assertEqual(inv.argv[-2:], ["150", "5"])

    def test_render_only_inserts_flag_and_output(self):
        self._write_game("sorengame")
        self._write_script()
        inv = tts.build_invocation(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
            render_only=True,
            render_output=self.repo_root / "out.wav",
        )
        self.assertEqual(inv.argv[1:3], ["--render-only", str(self.repo_root / "out.wav")])

    def test_render_only_requires_output(self):
        self._write_game("sorengame")
        self._write_script()
        with self.assertRaises(tts.TtsError):
            tts.build_invocation(
                self.g,
                game_name="sorengame",
                text_file=self._text(),
                rate=120,
                pre_delay=0,
                render_only=True,
            )

    def test_wav_playlist_requires_caption_chunks(self):
        self._write_game("sorengame")
        self._write_script()
        with self.assertRaises(tts.TtsError) as ctx:
            tts.build_invocation(
                self.g,
                game_name="sorengame",
                text_file=self._text(),
                rate=120,
                pre_delay=0,
                wav_playlist=self.repo_root / "p.txt",
            )
        self.assertIn("セットで指定", str(ctx.exception))

    def test_default_env_is_docich_sink_and_cc_off(self):
        self._write_game("sorengame")
        self._write_script()
        inv = tts.build_invocation(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
        )
        self.assertEqual(inv.env["SAY_CONTEXT_LABEL"], "docich")
        self.assertEqual(inv.env["DOCICH_CC_ENABLED"], "0")
        self.assertEqual(inv.env["PULSE_SINK"], "docich_sink")
        self.assertEqual(inv.env["SAY_AUDIO_DEVICE"], "docich_sink")
        self.assertTrue(Path(inv.env["OUTBOUND_CHAT_QUEUE_DIR"]).is_absolute())

    def test_env_overrides_are_applied(self):
        self._write_game("sorengame")
        self._write_script()
        inv = tts.build_invocation(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
            env_overrides={"SAY_CONTEXT_LABEL": "test:radio"},
        )
        self.assertEqual(inv.env["SAY_CONTEXT_LABEL"], "test:radio")

    def test_argv_is_a_plain_list_never_shell_joined(self):
        self._write_game("sorengame")
        self._write_script()
        inv = tts.build_invocation(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
        )
        self.assertIsInstance(inv.argv, list)
        self.assertTrue(all(isinstance(x, str) for x in inv.argv))


class TestRunTts(TtsTestBase):
    def test_dry_run_does_not_execute_and_returns_preview(self):
        self._write_game("sorengame")
        self._write_script()
        rc, detail = tts.run_tts(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
            dry_run=True,
        )
        self.assertEqual(rc, 0)
        self.assertIn("argv=", detail)
        self.assertIn("--render-only", tts.run_tts(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
            dry_run=True,
            render_only=True,
            render_output=self.repo_root / "out.wav",
        )[1])

    @mock.patch("docich.tts.run")
    def test_execute_passes_argv_env_and_cwd(self, fake_run):
        self._write_game("sorengame")
        self._write_script()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {"DOCICH_ALLOW_REAL_PLAYBACK": "1"}):
            rc, _detail = tts.run_tts(
                self.g,
                game_name="sorengame",
                text_file=self._text(),
                rate=120,
                pre_delay=0,
            )
        self.assertEqual(rc, 0)
        fake_run.assert_called_once()
        kwargs = fake_run.call_args.kwargs
        self.assertEqual(kwargs["cwd"], str(self.submodule.resolve()))
        self.assertEqual(kwargs["env_extra"]["DOCICH_CC_ENABLED"], "0")
        self.assertEqual(kwargs["timeout"], None)

    @mock.patch("docich.tts.run")
    def test_real_playback_requires_explicit_env(self, fake_run):
        self._write_game("sorengame")
        self._write_script()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(tts.TtsError) as cm:
                tts.run_tts(
                    self.g,
                    game_name="sorengame",
                    text_file=self._text(),
                    rate=120,
                    pre_delay=0,
                )
        self.assertIn("DOCICH_ALLOW_REAL_PLAYBACK", str(cm.exception))
        fake_run.assert_not_called()

    @mock.patch("docich.tts.run")
    def test_real_playback_allowed_with_env(self, fake_run):
        self._write_game("sorengame")
        self._write_script()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {"DOCICH_ALLOW_REAL_PLAYBACK": "1"}):
            rc, _detail = tts.run_tts(
                self.g,
                game_name="sorengame",
                text_file=self._text(),
                rate=120,
                pre_delay=0,
            )
        self.assertEqual(rc, 0)
        fake_run.assert_called_once()

    @mock.patch("docich.tts.run")
    def test_render_only_does_not_require_env(self, fake_run):
        self._write_game("sorengame")
        self._write_script()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        rc, _detail = tts.run_tts(
            self.g,
            game_name="sorengame",
            text_file=self._text(),
            rate=120,
            pre_delay=0,
            render_only=True,
            render_output=self.repo_root / "out.wav",
        )
        self.assertEqual(rc, 0)
        fake_run.assert_called_once()


class TestCliSay(TtsTestBase):
    def test_say_parse(self):
        args = cli.build_parser().parse_args(
            ["say", "sorengame", "-f", "x.txt", "--pre-delay", "0", "--dry-run"]
        )
        self.assertEqual(args.game, "sorengame")
        self.assertEqual(args.file, "x.txt")
        self.assertEqual(args.pre_delay, 0)
        self.assertTrue(args.dry_run)

    def test_say_dry_run_prints_preview(self):
        self._write_game("sorengame")
        self._write_script()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--config", str(self.toml), "say", "sorengame",
                           "-f", str(self._text()), "--pre-delay", "0", "--dry-run"])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("docich: dry-run:", out.getvalue())
        self.assertIn("say_enqueue.sh", out.getvalue())

    def test_say_unknown_game_exits_2(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--config", str(self.toml), "say", "missing"])
        self.assertEqual(rc, 2)
        self.assertIn("docich: エラー", err.getvalue())

    def test_say_text_argument_creates_temp_content(self):
        self._write_game("sorengame")
        self._write_script()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main([
                "--config", str(self.toml), "say", "sorengame",
                "こんにちは", "世界", "--pre-delay", "0", "--dry-run",
            ])
        self.assertEqual(rc, 0, err.getvalue())
        content = out.getvalue()
        self.assertIn("docich: dry-run:", content)
        self.assertIn("content.txt", content)

    def test_say_text_content_written_to_private_file(self):
        from docich import tts as tts_mod

        self._write_game("sorengame")
        self._write_script()
        captured: list[str] = []

        def fake_run_tts(*args, **kwargs):
            path = Path(kwargs["text_file"])
            captured.append(path.read_text(encoding="utf-8"))
            return 7, ""

        with mock.patch.object(tts_mod, "run_tts", side_effect=fake_run_tts):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main([
                    "--config", str(self.toml), "say", "sorengame",
                    "こんにちは", "世界", "--pre-delay", "0",
                ])
        self.assertEqual(rc, 7)
        self.assertTrue(captured)
        self.assertEqual(captured[0], "こんにちは 世界")

    def test_say_text_and_file_are_mutually_exclusive(self):
        self._write_game("sorengame")
        self._write_script()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main([
                "--config", str(self.toml), "say", "sorengame",
                "-f", str(self._text()), "テキスト", "--dry-run",
            ])
        self.assertEqual(rc, 2)
        self.assertIn("併用できません", err.getvalue())

    def test_say_without_text_or_file_exits_2(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--config", str(self.toml), "say", "sorengame"])
        self.assertEqual(rc, 2)
        self.assertIn("読み上げるテキスト", err.getvalue())
