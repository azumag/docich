import io
import os
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docich import chat, cli, config  # noqa: E402
from test_tts import TtsTestBase  # noqa: E402


class ChatTestBase(TtsTestBase):
    def _write_broadcast(self, name="sorengame"):
        self._write_game(name)
        bcast = self.submodule / "broadcast"
        bcast.mkdir(parents=True, exist_ok=True)
        (bcast / "comment.sh").write_text(
            "#!/usr/bin/env bash\n"
            "generate_comment_response() { echo comment; }\n",
            encoding="utf-8",
        )
        (bcast / "radio_engine.sh").write_text(
            "#!/usr/bin/env bash\n"
            "_radio_generate_and_play() { echo radio; }\n",
            encoding="utf-8",
        )
        (self.submodule / "eloop_lib.sh").write_text(
            "#!/usr/bin/env bash\n"
            'echo "eloop loaded" >/dev/null\n',
            encoding="utf-8",
        )


class TestBuildComment(ChatTestBase):
    def test_comment_invocation(self):
        self._write_broadcast()
        inv = chat.build_comment_invocation(self.g, game_name="sorengame")
        self.assertEqual(inv.cwd, self.submodule.resolve())
        self.assertIn("generate_comment_response", inv.argv)
        self.assertIn("twitch", inv.argv)
        self.assertEqual(inv.argv[0], "bash")
        self.assertIn("broadcast_ref.sh", inv.argv[1])
        self.assertEqual(inv.env["DOCICH_CC_ENABLED"], "0")
        self.assertTrue(Path(inv.env["OUTBOUND_CHAT_QUEUE_DIR"]).is_absolute())

    def test_comment_source_youtube(self):
        self._write_broadcast()
        inv = chat.build_comment_invocation(
            self.g, game_name="sorengame", source="youtube"
        )
        self.assertIn("youtube", inv.argv)

    def test_comment_source_allowlist(self):
        self._write_broadcast()
        with self.assertRaises(chat.ChatError):
            chat.build_comment_invocation(
                self.g, game_name="sorengame", source="$(rm -rf /)"
            )

    def test_comment_missing_broadcast_is_rejected(self):
        self._write_game("sorengame")
        with self.assertRaises(chat.ChatError):
            chat.build_comment_invocation(self.g, game_name="sorengame")

    def test_comment_dry_run_repro(self):
        self._write_broadcast()
        rc, detail = chat.run_comment(
            self.g, game_name="sorengame", source="twitch", dry_run=True
        )
        self.assertEqual(rc, 0)
        self.assertIn("function=generate_comment_response", detail)
        self.assertIn("argv=", detail)

    @mock.patch("docich.chat.run")
    def test_comment_real_run_requires_env(self, fake_run):
        self._write_broadcast()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(chat.ChatError) as cm:
                chat.run_comment(self.g, game_name="sorengame")
        self.assertIn("DOCICH_ALLOW_REAL_COMMENT", str(cm.exception))
        fake_run.assert_not_called()

    @mock.patch("docich.chat.run")
    def test_comment_real_run_with_env(self, fake_run):
        self._write_broadcast()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {"DOCICH_ALLOW_REAL_COMMENT": "1"}):
            rc, _detail = chat.run_comment(self.g, game_name="sorengame")
        self.assertEqual(rc, 0)
        fake_run.assert_called_once()


class TestBuildRadio(ChatTestBase):
    def test_radio_with_topic(self):
        self._write_broadcast()
        inv = chat.build_radio_invocation(
            self.g, game_name="sorengame", topic="今日の話題"
        )
        self.assertIn("_radio_generate_and_play", inv.argv)
        prompt = Path(inv.argv[4])
        self.assertEqual(prompt.read_text(encoding="utf-8"), "今日の話題\n")

    def test_radio_requires_prompt_or_topic(self):
        self._write_broadcast()
        with self.assertRaises(chat.ChatError) as cm:
            chat.build_radio_invocation(self.g, game_name="sorengame")
        self.assertIn("どちらか一方だけ", str(cm.exception))
        with self.assertRaises(chat.ChatError):
            chat.build_radio_invocation(
                self.g,
                game_name="sorengame",
                topic="x",
                prompt_file=Path("/tmp/x.txt"),
            )

    def test_radio_corner_safety(self):
        self._write_broadcast()
        with self.assertRaises(chat.ChatError):
            chat.build_radio_invocation(
                self.g, game_name="sorengame", topic="x", corner="a;rm -rf /"
            )

    def test_radio_dry_run_repro(self):
        self._write_broadcast()
        rc, detail = chat.run_radio(
            self.g, game_name="sorengame", topic="x", dry_run=True
        )
        self.assertEqual(rc, 0)
        self.assertIn("function=_radio_generate_and_play", detail)

    @mock.patch("docich.chat.run")
    def test_radio_real_run_requires_env(self, fake_run):
        self._write_broadcast()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(chat.ChatError) as cm:
                chat.run_radio(self.g, game_name="sorengame", topic="x")
        self.assertIn("DOCICH_ALLOW_REAL_RADIO", str(cm.exception))
        fake_run.assert_not_called()


class TestCliChat(ChatTestBase):
    def test_chat_parse(self):
        args = cli.build_parser().parse_args(
            ["chat", "sorengame", "--source", "youtube", "--dry-run"]
        )
        self.assertEqual(args.source, "youtube")
        self.assertTrue(args.dry_run)

    def test_chat_dry_run_prints_preview(self):
        self._write_broadcast()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(
                ["--config", str(self.toml), "chat", "sorengame", "--dry-run"]
            )
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("docich: chat dry-run:", out.getvalue())
        self.assertIn("generate_comment_response", out.getvalue())

    def test_radio_parse(self):
        args = cli.build_parser().parse_args(
            ["radio", "sorengame", "--topic", "x", "--corner", "news", "--dry-run"]
        )
        self.assertEqual(args.corner, "news")
        self.assertEqual(args.topic, "x")

    def test_radio_dry_run_prints_preview(self):
        self._write_broadcast()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(
                ["--config", str(self.toml), "radio", "sorengame",
                 "--topic", "今日の話題", "--dry-run"]
            )
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("docich: radio dry-run:", out.getvalue())
        self.assertIn("_radio_generate_and_play", out.getvalue())

    def test_radio_failure_prints_stderr(self):
        from docich import chat as chat_mod

        self._write_broadcast()
        with mock.patch.object(
            chat_mod, "run_radio", return_value=(1, "ADVICE: エラーメッセージXYZ")
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(
                    ["--config", str(self.toml), "radio", "sorengame",
                     "--topic", "x"]
                )
        self.assertEqual(rc, 1)
        self.assertIn("エラー終了しました", out.getvalue())
        self.assertIn("エラーメッセージXYZ", err.getvalue())

    def test_chat_failure_prints_stderr(self):
        from docich import chat as chat_mod

        self._write_broadcast()
        with mock.patch.object(
            chat_mod, "run_comment", return_value=(1, "CHAT: エラー詳細ABC")
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(
                    ["--config", str(self.toml), "chat", "sorengame"]
                )
        self.assertEqual(rc, 1)
        self.assertIn("エラー終了しました", out.getvalue())
        self.assertIn("エラー詳細ABC", err.getvalue())


if __name__ == "__main__":
    unittest.main()
