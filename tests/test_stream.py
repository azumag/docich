import os
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
