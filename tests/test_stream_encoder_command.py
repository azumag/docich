"""Pure FFmpeg argv/progress/exit contracts for the single direct encoder."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_encoder_command as enc  # noqa: E402

BASE = dict(
    ffmpeg_bin="ffmpeg", display=":99", size="1280x720", fps=30,
    video_kbps=2500, audio_kbps=128, audio_delay_ms=0,
    pulse_source="soren_null.monitor", pulse_sample_rate=44100, pulse_channels=2,
    local_rtmp_url="rtmp://127.0.0.1:1935/soren/live",
    closed_captions_socket="/tmp/docich/cc.sock",
)

GOLDEN_LIVE = [
    "ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats_period", "1",
    "-thread_queue_size", "1024", "-f", "x11grab", "-draw_mouse", "0",
    "-framerate", "30", "-video_size", "1280x720", "-i", ":99+0,0",
    "-thread_queue_size", "2048", "-isync", "0", "-f", "pulse",
    "-sample_rate", "44100", "-channels", "2", "-i", "soren_null.monitor",
    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
    "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-r", "30", "-fps_mode", "cfr",
    "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
    "-b:v", "2500k", "-minrate", "2500k", "-maxrate", "2500k", "-bufsize", "5000k",
    "-af", "adelay=0:all=1,aresample=async=1:first_pts=0",
    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
    "-progress", "pipe:1",
    "-flvflags", "no_duration_filesize", "-f", "flv", "rtmp://127.0.0.1:1935/soren/live",
]


def build(**override):
    args = {**BASE, "mode": "live", "closed_captions_active": False, **override}
    return enc.build_direct_stream_ffmpeg_command(**args)


class BuildCommandTest(unittest.TestCase):
    def test_versioned_and_live_golden_argv(self):
        self.assertEqual(enc.STREAM_ENCODER_COMMAND_CONTRACT_VERSION, 1)
        self.assertEqual(build(), GOLDEN_LIVE)

    def test_captions_insert_before_video_codec_and_preset(self):
        command = build(closed_captions_active=True)
        video = command.index("-c:v")
        self.assertEqual(command[video - 2:video], ["-vf", "docichcc=socket=/tmp/docich/cc.sock"])
        preset = command.index("-preset")
        self.assertEqual(command[preset - 2:preset], ["-a53cc", "1"])
        # Removing exactly the caption options restores the baseline argv.
        stripped = command[:video - 2] + command[video:preset - 2] + command[preset:]
        self.assertEqual(stripped, GOLDEN_LIVE)

    def test_record_mode_and_duration_ordering(self):
        command = build(mode="record", output_path="/tmp/out.mkv", duration_sec=10)
        self.assertEqual(command[-6:], ["-t", "10", "-y", "-f", "matroska", "/tmp/out.mkv"])
        self.assertNotIn("flv", command)
        live = build(duration_sec=5)
        self.assertEqual(live[-7:], ["-t", "5", "-flvflags", "no_duration_filesize", "-f", "flv", BASE["local_rtmp_url"]])

    def test_record_mode_does_not_need_or_publish_a_network_url(self):
        command = build(mode="record", output_path="/tmp/out.mkv", local_rtmp_url="rtmp://stream.example/live")
        self.assertNotIn("rtmp://stream.example/live", command)

    def test_invalid_modes_and_durations_are_refused(self):
        for kwargs, text in (
            ({"mode": "x"}, "mode must be live or record"),
            ({"mode": "record"}, "requires an output path"),
            ({"duration_sec": 0}, "between 1 and 86400"),
            ({"duration_sec": 86401}, "between 1 and 86400"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, text):
                build(**kwargs)

    def test_live_output_can_only_be_loopback_without_credentials(self):
        for url in (
            "rtmps://127.0.0.1/live", "rtmp://stream.example/live",
            "rtmp://user:key@127.0.0.1/live", "rtmp://127.0.0.1/live?key=secret",
            "rtmp://127.0.0.1", "",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                build(local_rtmp_url=url)

    def test_caption_socket_cannot_inject_filter_options(self):
        for socket_path in (
            "", "relative.sock", "/tmp/a,drawtext=x", "/tmp/a;b", "/tmp/a b",
            "/tmp/../etc/x", "/tmp/a\nb", "/" + "a" * 120, "/tmp/a'b",
        ):
            with self.subTest(socket=socket_path), self.assertRaises(ValueError):
                build(closed_captions_active=True, closed_captions_socket=socket_path)
        # An unused socket value is not interpreted when captions are off.
        self.assertEqual(build(closed_captions_socket="/tmp/a,b"), GOLDEN_LIVE)


class FileLoadTest(unittest.TestCase):
    def test_module_works_when_loaded_by_file_path_without_package(self):
        # A legacy caller loads staged docich modules by path, not as a package.
        code = (
            "import importlib.util,sys;p=sys.argv[1];"
            "s=importlib.util.spec_from_file_location('x',p);m=importlib.util.module_from_spec(s);"
            "s.loader.exec_module(m);"
            "print(m.build_direct_stream_ffmpeg_command(ffmpeg_bin='f',display=':1',size='1x1',fps=1,"
            "video_kbps=1,audio_kbps=1,audio_delay_ms=0,pulse_source='s',pulse_sample_rate=1,"
            "pulse_channels=1,local_rtmp_url='rtmp://localhost/a',mode='live',closed_captions_active=False)[-1])"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT / "src/docich/stream_encoder_command.py")],
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "rtmp://localhost/a"), proc.stderr)

    def test_module_has_no_process_network_or_game_dependencies(self):
        source = (ROOT / "src/docich/stream_encoder_command.py").read_text()
        for forbidden in ("import subprocess", "import socket", "soviet_now", "os.system", "Popen", "environ"):
            self.assertNotIn(forbidden, source)


class ProgressAndExitTest(unittest.TestCase):
    def test_progress_parsing(self):
        parsed = enc.parse_progress_lines([
            "frame=12\n", "fps=29.97", "speed=1.01x", "total_size=N/A", "out_time_us=1000000.0",
            "progress=continue", "noequals", "", "drop_frames=abc", "speed=N/A",
        ])
        self.assertEqual(parsed, {
            "frame": 12, "fps": 29.97, "speed": "N/A", "total_size": "N/A",
            "out_time_us": 1000000, "progress": "continue", "drop_frames": "abc",
        })
        self.assertEqual(enc.parse_progress_lines(["speed=1.01x", "fps=30"]), {"speed": 1.01, "fps": 30.0})

    def test_requested_stop_is_not_a_failure(self):
        self.assertEqual(enc.classify_ffmpeg_exit(255, stopping=True), (0, "stopped"))
        self.assertEqual(enc.classify_ffmpeg_exit(0, stopping=False), (0, "completed"))
        self.assertEqual(enc.classify_ffmpeg_exit(1, stopping=False), (1, "failed"))
        self.assertEqual(enc.classify_ffmpeg_exit(255, stopping=False), (255, "failed"))


if __name__ == "__main__":
    unittest.main()
