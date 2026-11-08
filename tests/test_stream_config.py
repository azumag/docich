"""Pure encoder configuration contracts (ffmpeg backend only)."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_config as cfg  # noqa: E402

REPO = Path("/srv/soren")


def load(env=None, uid=1001):
    return cfg.load_stream_config(env or {}, repo_root=REPO, runtime_uid=uid)


class DefaultsTest(unittest.TestCase):
    def test_versioned_defaults(self):
        self.assertEqual(cfg.STREAM_CONFIG_CONTRACT_VERSION, 1)
        self.assertEqual(load(), {
            "backend": "ffmpeg", "display": ":99.0", "width": 1280, "height": 720,
            "fps": 30, "video_kbps": 4500, "audio_kbps": 160, "audio_delay_ms": 0,
            "pulse_source": "soren_null.monitor", "pulse_sample_rate": 48000, "pulse_channels": 2,
            "local_rtmp_url": "rtmp://127.0.0.1:1935/soren/live", "ffmpeg_bin": "ffmpeg",
            "closed_captions_enabled": False,
            "closed_captions_socket": "/run/user/1001/docich/ffmpeg-cc.sock",
            "state_dir": "/srv/soren/tmp/state/direct_stream",
            "log_file": "/srv/soren/logs/direct_stream.log",
        })

    def test_overrides_and_path_resolution(self):
        got = load({
            "SOREN_DIRECT_STREAM_SIZE": "1920X1080", "SOREN_DIRECT_STREAM_FPS": "60",
            "DISPLAY": ":98", "SOREN_DIRECT_STREAM_DISPLAY": ":99.1",
            "SOREN_DIRECT_STREAM_STATE_DIR": "/var/state", "SOREN_DIRECT_STREAM_LOG_FILE": "rel/x.log",
            "XDG_RUNTIME_DIR": "/run/user/7/", "DOCICH_CC_ENABLED": "yes",
        })
        self.assertEqual((got["width"], got["height"], got["fps"], got["display"]), (1920, 1080, 60, ":99.1"))
        self.assertEqual((got["state_dir"], got["log_file"]), ("/var/state", "/srv/soren/rel/x.log"))
        self.assertEqual(got["closed_captions_socket"], "/run/user/7/docich/ffmpeg-cc.sock")
        self.assertTrue(got["closed_captions_enabled"])
        self.assertEqual(load({"DISPLAY": ":98"})["display"], ":98")

    def test_public_config_never_contains_the_output_url(self):
        public = cfg.public_stream_config(load())
        self.assertNotIn("local_rtmp_url", public)
        self.assertNotIn("rtmp://", repr(public))
        self.assertEqual(len(public), len(load()) - 1)


class ValidationTest(unittest.TestCase):
    def test_every_rejected_value(self):
        for key, value, text in (
            ("SOREN_STREAM_BACKEND", "obs", "must be ffmpeg"),
            ("SOREN_STREAM_BACKEND", "x", "must be ffmpeg"),
            ("SOREN_DIRECT_STREAM_SIZE", "big", "WIDTHxHEIGHT"),
            ("SOREN_DIRECT_STREAM_SIZE", "100x720", "outside the supported range"),
            ("SOREN_DIRECT_STREAM_SIZE", "4000x720", "outside the supported range"),
            ("SOREN_DIRECT_STREAM_SIZE", "1281x720", "must be even"),
            ("SOREN_DIRECT_STREAM_DISPLAY", "99", "look like"),
            ("SOREN_DIRECT_STREAM_DISPLAY", ":99;rm", "look like"),
            ("SOREN_DIRECT_STREAM_PULSE_SOURCE", "a b", "unsupported characters"),
            ("SOREN_DIRECT_STREAM_PULSE_SOURCE", "", "unsupported characters"),
            ("SOREN_DIRECT_STREAM_FFMPEG_BIN", "", "is invalid"),
            ("SOREN_DIRECT_STREAM_FFMPEG_BIN", "ff\nmpeg", "is invalid"),
            ("SOREN_DIRECT_STREAM_FPS", "0", "between 1 and 60"),
            ("SOREN_DIRECT_STREAM_FPS", "61", "between 1 and 60"),
            ("SOREN_DIRECT_STREAM_VIDEO_KBPS", "499", "between 500 and 6000"),
            ("SOREN_DIRECT_STREAM_AUDIO_KBPS", "321", "between 64 and 320"),
            ("SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", "2001", "between 0 and 2000"),
            ("SOREN_DIRECT_STREAM_AUDIO_HZ", "7999", "between 8000 and 192000"),
            ("SOREN_DIRECT_STREAM_AUDIO_CHANNELS", "3", "between 1 and 2"),
            ("DOCICH_CC_ENABLED", "maybe", "must be a boolean"),
        ):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, text):
                load({key: value})

    def test_destination_and_caption_socket_safety(self):
        for url in ("rtmps://127.0.0.1/live", "rtmp://stream.example/live",
                    "rtmp://u:k@127.0.0.1/live", "rtmp://127.0.0.1/live?key=secret", "rtmp://127.0.0.1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                load({"SOREN_DIRECT_STREAM_LOCAL_URL": url})
        for socket_path in ("rel.sock", "/tmp/a,drawtext=x", "/tmp/../x", "/" + "a" * 120):
            with self.subTest(socket=socket_path), self.assertRaises(ValueError):
                load({"DOCICH_CC_SOCKET": socket_path})


class PurityTest(unittest.TestCase):
    def test_imports_and_calls_are_confined(self):
        tree = ast.parse((ROOT / "src/docich/stream_config.py").read_text())
        imported, attrs = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
        self.assertEqual(imported, {"__future__", "importlib", "pathlib", "re", "typing"})
        self.assertFalse(attrs & {"environ", "getenv", "Popen", "run", "system", "kill", "connect", "getuid", "geteuid"})

    def test_loads_by_file_path_without_package(self):
        code = (
            "import importlib.util,sys;from pathlib import Path;"
            "s=importlib.util.spec_from_file_location('x',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.load_stream_config({},repo_root=Path('/r'),runtime_uid=5)['closed_captions_socket'])"
        )
        proc = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "src/docich/stream_config.py")],
                              capture_output=True, text=True, timeout=12, check=False)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "/run/user/5/docich/ffmpeg-cc.sock"), proc.stderr)


if __name__ == "__main__":
    unittest.main()
