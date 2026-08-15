import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, stream  # noqa: E402


class StreamTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _load(self, toml_text: str) -> config.GlobalConfig:
        toml_path = self.repo_root / "docich.toml"
        toml_path.write_text(toml_text, encoding="utf-8")
        return config.load_global(self.repo_root, config_path=toml_path)


class TestDrawMouse(StreamTestBase):
    def test_draw_mouse_0_present_after_x11grab(self):
        g = self._load('[stream]\nmode = "null"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        idx = cmd.index("-f")
        self.assertEqual(cmd[idx : idx + 2], ["-f", "x11grab"])
        self.assertEqual(cmd[idx + 2 : idx + 4], ["-draw_mouse", "0"])


class TestAudioInput(StreamTestBase):
    def test_audio_enabled_uses_pulse_monitor(self):
        g = self._load('[audio]\nenabled = true\nsink_name = "docich_sink"\n\n[stream]\nmode = "null"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertIn("-f", cmd)
        self.assertIn("pulse", cmd)
        self.assertIn("docich_sink.monitor", cmd)

    def test_audio_disabled_uses_anullsrc(self):
        g = self._load('[audio]\nenabled = false\n\n[stream]\nmode = "null"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertIn("lavfi", cmd)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=44100", cmd)
        self.assertNotIn("pulse", cmd)


class TestGop(StreamTestBase):
    def test_gop_equals_framerate_times_gop_seconds(self):
        g = self._load('[stream]\nmode = "null"\nframerate = 30\ngop_seconds = 2\n')
        cmd = stream.build_ffmpeg_cmd(g)
        idx = cmd.index("-g")
        self.assertEqual(cmd[idx + 1], "60")

    def test_gop_with_custom_values(self):
        g = self._load('[stream]\nmode = "null"\nframerate = 24\ngop_seconds = 3\n')
        cmd = stream.build_ffmpeg_cmd(g)
        idx = cmd.index("-g")
        self.assertEqual(cmd[idx + 1], "72")


class TestModeNull(StreamTestBase):
    def test_mode_null_outputs_null_muxer(self):
        g = self._load('[stream]\nmode = "null"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertEqual(cmd[-2:], ["null", "-"])


class TestModeFile(StreamTestBase):
    def test_mode_file_relative_path_is_resolved_against_repo_root(self):
        # tmux window の cwd に依存しないよう、相対パスは repo_root 基準で絶対化する。
        g = self._load('[stream]\nmode = "file"\nfile_path = "run/out.flv"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        expected = str(self.repo_root / "run" / "out.flv")
        self.assertEqual(cmd[-4:], ["-y", "-f", "flv", expected])

    def test_mode_file_absolute_path_is_passed_through(self):
        abs_path = str(self.repo_root / "elsewhere" / "custom.flv")
        g = self._load(f'[stream]\nmode = "file"\nfile_path = "{abs_path}"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertEqual(cmd[-4:], ["-y", "-f", "flv", abs_path])


class TestModeRtmp(StreamTestBase):
    def setUp(self):
        super().setUp()
        self._old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        super().tearDown()

    def test_rtmp_without_key_raises(self):
        os.environ.pop("DOCICH_STREAM_KEY", None)
        g = self._load('[stream]\nmode = "rtmp"\nrtmp_url = "rtmp://example.invalid/app"\n')
        with self.assertRaises(stream.StreamKeyError):
            stream.build_ffmpeg_cmd(g)

    def test_rtmp_with_key_appends_it_to_url(self):
        os.environ["DOCICH_STREAM_KEY"] = "SECRET123"
        g = self._load('[stream]\nmode = "rtmp"\nrtmp_url = "rtmp://example.invalid/app"\n')
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertEqual(cmd[-3:], ["-f", "flv", "rtmp://example.invalid/app/SECRET123"])

    def test_rtmp_mask_key_hides_secret(self):
        os.environ["DOCICH_STREAM_KEY"] = "SECRET123"
        g = self._load('[stream]\nmode = "rtmp"\nrtmp_url = "rtmp://example.invalid/app"\n')
        cmd = stream.build_ffmpeg_cmd(g, mask_key=True)
        joined = " ".join(cmd)
        self.assertNotIn("SECRET123", joined)
        self.assertIn(stream.MASK, joined)


class TestNativeCaptions(StreamTestBase):
    def test_caption_filter_and_a53_encoder_option_are_opt_in(self):
        g = self._load(
            '[stream]\nmode = "null"\nffmpeg_bin = "/opt/docich/bin/ffmpeg"\n\n'
            '[captions]\nenabled = true\nsocket_path = "/tmp/docich/cc.sock"\n'
        )
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertEqual(cmd[0], "/opt/docich/bin/ffmpeg")
        self.assertIn("docichcc=socket=/tmp/docich/cc.sock", cmd)
        self.assertIn("-a53cc", cmd)
        self.assertEqual(cmd[cmd.index("-a53cc") + 1], "1")

    def test_disabled_captions_keep_the_original_video_path(self):
        g = self._load('[stream]\nmode = "null"\n\n[captions]\nenabled = false\n')
        cmd = stream.build_ffmpeg_cmd(g)
        self.assertNotIn("-vf", cmd)
        self.assertNotIn("-a53cc", cmd)

    def test_missing_custom_binary_fails_open_to_system_ffmpeg_without_cc(self):
        g = self._load(
            '[stream]\nmode = "null"\nffmpeg_bin = "/missing/custom-ffmpeg"\n\n'
            '[captions]\nenabled = true\nsocket_path = "/tmp/docich/cc.sock"\n'
        )

        def resolve(binary):
            return "/usr/bin/ffmpeg" if binary == "ffmpeg" else None

        with mock.patch("docich.stream._resolve_binary", side_effect=resolve):
            runtime = stream.resolve_runtime(g)
        self.assertFalse(runtime.captions_active)
        self.assertEqual(runtime.command[0], "/usr/bin/ffmpeg")
        self.assertNotIn("-vf", runtime.command)
        self.assertIn("fail-open", runtime.caption_detail)

    def test_capable_custom_binary_keeps_captions_active(self):
        g = self._load(
            '[stream]\nmode = "null"\nffmpeg_bin = "/opt/docich/bin/ffmpeg"\n\n'
            '[captions]\nenabled = true\nsocket_path = "/tmp/docich/cc.sock"\n'
        )
        with (
            mock.patch("docich.stream._resolve_binary", return_value="/opt/docich/bin/ffmpeg"),
            mock.patch(
                "docich.stream.caption_capability",
                return_value=(True, "docichcc + libx264 a53cc"),
            ),
        ):
            runtime = stream.resolve_runtime(g)
        self.assertTrue(runtime.captions_active)
        self.assertIn("docichcc=socket=/tmp/docich/cc.sock", runtime.command)

    def test_redaction_masks_stream_key_in_runtime_log_command(self):
        os.environ["DOCICH_STREAM_KEY"] = "SECRET123"
        try:
            g = self._load('[stream]\nmode = "rtmp"\n')
            command = stream.build_ffmpeg_cmd(g)
            redacted = stream.redact_stream_command(command, g)
        finally:
            os.environ.pop("DOCICH_STREAM_KEY", None)
        self.assertNotIn("SECRET123", " ".join(redacted))
        self.assertIn(stream.MASK, " ".join(redacted))

    def test_caption_socket_parent_is_created_private(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir) / "runtime" / "docich"
            stream.ensure_caption_socket_parent(str(parent / "cc.sock"))
            self.assertTrue(parent.is_dir())
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)

    def test_caption_socket_parent_rejects_group_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir) / "docich"
            parent.mkdir(mode=0o750)
            parent.chmod(0o750)
            with self.assertRaises(stream.CaptionSocketDirectoryError):
                stream.ensure_caption_socket_parent(str(parent / "cc.sock"))

    def test_caption_socket_parent_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real = root / "real"
            real.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(stream.CaptionSocketDirectoryError):
                stream.ensure_caption_socket_parent(str(link / "cc.sock"))

    def test_caption_socket_ready_requires_private_owned_unix_socket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cc.sock"
            ready, detail = stream.caption_socket_ready(str(path))
            self.assertFalse(ready)
            self.assertIn("未準備", detail)

            path.write_text("not a socket", encoding="utf-8")
            ready, detail = stream.caption_socket_ready(str(path))
            self.assertFalse(ready)
            self.assertIn("Unix socketではありません", detail)
            path.unlink()

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ready, detail = stream.caption_socket_ready(str(path))
            self.assertTrue(ready)
            self.assertEqual(detail, "caption socket ready")


if __name__ == "__main__":
    unittest.main()
