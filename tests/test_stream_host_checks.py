"""Host prerequisite checks run against injected fakes (no ffmpeg/X11/Pulse)."""
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_host_checks as hc  # noqa: E402

DEVICES = " D  x11grab       X11 screen capture\n D  pulse         Pulse audio input\n"
ENCODERS = " V....D libx264        H.264\n"
FILTERS = " ... docichcc        V->V   closed captions\n"
ENCODER_HELP = "libx264 AVOptions:\n  -a53cc  <boolean> Use A53 Caption\n"
XDPY = "  dimensions:    1920x1080 pixels (508x285 millimeters)\n"
SOURCES = "0\tsoren_null.monitor\tmodule-null-sink.c\ts16le 2ch 44100Hz\tIDLE\n"


def config(**override):
    base = {
        "backend": "ffmpeg", "ffmpeg_bin": "ffmpeg", "display": ":99.0", "width": 1280, "height": 720,
        "pulse_source": "soren_null.monitor", "local_rtmp_url": "rtmp://127.0.0.1:1935/soren/live",
        "closed_captions_enabled": False, "closed_captions_socket": "/tmp/unused/cc.sock",
    }
    base.update(override)
    return base


def fake_check(**responses):
    table = {
        ("ffmpeg", "-hide_banner", "-devices"): DEVICES,
        ("ffmpeg", "-hide_banner", "-encoders"): ENCODERS,
        ("ffmpeg", "-hide_banner", "-filters"): FILTERS,
        ("ffmpeg", "-hide_banner", "-h", "encoder=libx264"): ENCODER_HELP,
        ("xdpyinfo", "-display", ":99.0"): XDPY,
        ("pactl", "list", "short", "sources"): SOURCES,
    }
    for key, value in responses.items():
        table[tuple(key.split("|"))] = value

    def check(command, **_kw):
        value = table[tuple(command)]
        if isinstance(value, Exception):
            raise value
        return value
    return check


def validate(cfg=None, mode="live", check=None, **kw):
    options = dict(
        check=check or fake_check(), platform="linux", which=lambda name: f"/usr/bin/{name}",
        is_file=lambda value: False, connect=lambda *a, **k: type("C", (), {"close": lambda self: None})(),
        run=lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""), log=lambda text: None,
    )
    options.update(kw)
    return hc.validate_runtime(cfg or config(), mode=mode, **options)


class ValidateRuntimeTest(unittest.TestCase):
    def test_healthy_host_passes_and_reports_caption_state(self):
        self.assertIs(validate(), False)
        self.assertIs(validate(config(closed_captions_enabled=True)), True)
        self.assertEqual(hc.STREAM_HOST_CHECKS_CONTRACT_VERSION, 1)

    def test_every_missing_prerequisite_is_a_runtime_check_error(self):
        for label, kwargs, text in (
            ("platform", {"platform": "darwin"}, "Linux-only"),
            ("ffmpeg missing", {"which": lambda n: None if n == "ffmpeg" else "/x"}, "FFmpeg executable was not found"),
            ("no x11grab", {"check": fake_check(**{"ffmpeg|-hide_banner|-devices": " D pulse x\n"})}, "x11grab"),
            ("no pulse", {"check": fake_check(**{"ffmpeg|-hide_banner|-devices": " D x11grab x\n"})}, "PulseAudio input"),
            ("no libx264", {"check": fake_check(**{"ffmpeg|-hide_banner|-encoders": " V h264_nvenc\n"})}, "libx264"),
            ("no xdpyinfo", {"which": lambda n: None if n == "xdpyinfo" else "/x"}, "xdpyinfo was not found"),
            ("no dimensions", {"check": fake_check(**{"xdpyinfo|-display|:99.0": "nothing"})}, "X11 display dimensions"),
            ("small display", {"check": fake_check(**{"xdpyinfo|-display|:99.0": "dimensions: 800x600 pixels"})}, "smaller than requested"),
            ("no pactl", {"which": lambda n: None if n == "pactl" else "/x"}, "pactl was not found"),
            ("no source", {"check": fake_check(**{"pactl|list|short|sources": "0\tother\tm\n"})}, "PulseAudio source was not found"),
            ("check failure", {"check": fake_check(**{"xdpyinfo|-display|:99.0": hc.RuntimeCheckError("boom")})}, "boom"),
        ):
            with self.subTest(label), self.assertRaisesRegex(hc.RuntimeCheckError, text):
                validate(**kwargs)

    def test_live_requires_the_ffmpeg_backend_but_record_does_not_need_the_relay(self):
        with self.assertRaisesRegex(hc.RuntimeCheckError, "requires SOREN_STREAM_BACKEND=ffmpeg"):
            validate(config(backend="obs"))
        def refuse(*a, **k):
            raise OSError("no relay")
        self.assertIs(validate(mode="record", connect=refuse), False)
        with self.assertRaisesRegex(hc.RuntimeCheckError, "not reachable"):
            validate(connect=refuse)

    def test_ffmpeg_given_as_a_file_path_is_accepted(self):
        base = fake_check()

        def check(command, **_kw):
            return base(["ffmpeg", *command[1:]] if command[0] == "/opt/ff/ffmpeg" else command)

        self.assertIs(validate(config(ffmpeg_bin="/opt/ff/ffmpeg"), check=check,
                               which=lambda n: None if n == "/opt/ff/ffmpeg" else "/x",
                               is_file=lambda value: value == "/opt/ff/ffmpeg"), False)
        with self.assertRaisesRegex(hc.RuntimeCheckError, "FFmpeg executable was not found"):
            validate(config(ffmpeg_bin="/opt/ff/ffmpeg"), check=check,
                     which=lambda n: None if n == "/opt/ff/ffmpeg" else "/x", is_file=lambda value: False)


class RelayTest(unittest.TestCase):
    def test_pgrep_fallback_accepts_a_manual_nginx_master(self):
        def refuse(*a, **k):
            raise OSError("down")
        ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "1234\n", "")
        hc.validate_local_relay("rtmp://127.0.0.1/a/b", connect=refuse, run=ok)
        for result in (subprocess.CompletedProcess([], 0, "  \n", ""), subprocess.CompletedProcess([], 1, "", "")):
            with self.assertRaisesRegex(hc.RuntimeCheckError, "not reachable"):
                hc.validate_local_relay("rtmp://127.0.0.1/a/b", connect=refuse, run=lambda *a, **k: result)
        def timeout(*a, **k):
            raise subprocess.TimeoutExpired("pgrep", 2)
        with self.assertRaisesRegex(hc.RuntimeCheckError, "not reachable"):
            hc.validate_local_relay("rtmp://127.0.0.1/a/b", connect=refuse, run=timeout)

    def test_host_missing_and_default_port(self):
        with self.assertRaisesRegex(hc.RuntimeCheckError, "host is missing"):
            hc.validate_local_relay("rtmp:///x")
        seen = []
        hc.validate_local_relay("rtmp://localhost/app", connect=lambda addr, timeout: seen.append((addr, timeout)) or
                                type("C", (), {"close": lambda self: None})())
        self.assertEqual(seen, [(("localhost", 1935), 2)])


class CaptionTest(unittest.TestCase):
    def test_disabled_and_missing_capabilities_fail_open(self):
        logs = []
        self.assertFalse(hc.caption_capabilities_available(config(), check=fake_check(), log=logs.append))
        self.assertEqual(logs, [])
        on = config(closed_captions_enabled=True)
        for key, value, text in (
            ("ffmpeg|-hide_banner|-filters", " ... other\n", "docichcc filter is unavailable"),
            ("ffmpeg|-hide_banner|-h|encoder=libx264", "no options", "a53cc is unavailable"),
            ("ffmpeg|-hide_banner|-filters", hc.RuntimeCheckError("probe failed"), "probe failed"),
        ):
            logs.clear()
            with self.subTest(text):
                self.assertFalse(hc.caption_capabilities_available(on, check=fake_check(**{key: value}), log=logs.append))
                self.assertIn(text, logs[0])

    def test_socket_directory_is_private_real_and_owned(self):
        with tempfile.TemporaryDirectory(prefix="host-checks-") as tmp:
            root = Path(tmp)
            logs = []
            cfg = config(closed_captions_enabled=True, closed_captions_socket=str(root / "a" / "b" / "cc.sock"))
            self.assertFalse(hc.prepare_caption_runtime(config(), log=logs.append))
            self.assertTrue(hc.prepare_caption_runtime(cfg, log=logs.append))
            self.assertEqual(stat.S_IMODE((root / "a" / "b").stat().st_mode), 0o700)
            os.chmod(root / "a" / "b", 0o775)
            self.assertTrue(hc.prepare_caption_runtime(cfg, log=logs.append))
            self.assertEqual(stat.S_IMODE((root / "a" / "b").stat().st_mode), 0o700)
            link = root / "link"
            os.symlink(root / "a" / "b", link)
            self.assertFalse(hc.prepare_caption_runtime(
                config(closed_captions_enabled=True, closed_captions_socket=str(link / "cc.sock")), log=logs.append))
            self.assertIn("not a real directory", logs[-1])
            self.assertFalse(hc.prepare_caption_runtime(cfg, geteuid=lambda: os.geteuid() + 1, log=logs.append))
            self.assertIn("not owned by this user", logs[-1])
            blocker = root / "file"
            blocker.write_text("x")
            self.assertFalse(hc.prepare_caption_runtime(
                config(closed_captions_enabled=True, closed_captions_socket=str(blocker / "cc.sock")), log=logs.append))


class CheckedTest(unittest.TestCase):
    def test_maps_failures_and_joins_output(self):
        ok = lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "out", "err")
        self.assertEqual(hc.checked(["x"], run=ok), "outerr")
        bad = lambda cmd, **k: subprocess.CompletedProcess(cmd, 3, "", "")
        with self.assertRaisesRegex(hc.RuntimeCheckError, "check failed: x"):
            hc.checked(["x"], run=bad)
        def missing(cmd, **k):
            raise FileNotFoundError(cmd[0])
        with self.assertRaisesRegex(hc.RuntimeCheckError, "failed to run prerequisite check: x"):
            hc.checked(["x"], run=missing)


if __name__ == "__main__":
    unittest.main()
