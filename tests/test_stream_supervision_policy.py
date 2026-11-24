"""Pure reconnect / audio-watchdog policy and Pulse listing classification."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_supervision_policy as pol  # noqa: E402


def entry(number, pid=None, empty=False):
    body = "" if empty else (
        f'\tDriver: protocol-native.c\n\tProperties:\n\t\tapplication.process.id = "{pid}"\n'
        if pid is not None else "\tDriver: protocol-native.c\n\tProperties:\n\t\tmedia.name = \"x\"\n"
    )
    return f"Source Output #{number}\n{body}\n"


class ReconnectPolicyTest(unittest.TestCase):
    def test_versioned_defaults_and_overrides(self):
        self.assertEqual(pol.STREAM_SUPERVISION_POLICY_CONTRACT_VERSION, 1)
        self.assertEqual(pol.load_reconnect_policy({}), {
            "enabled": True, "twitch_channel": "", "probe_interval_sec": 60,
            "offline_threshold": 2, "backoff_sec": 10, "max_consecutive_restarts": 10,
            "reload_relay": True,
        })
        got = pol.load_reconnect_policy({
            "TWITCH_CHANNEL": "fallback", "SOREN_DIRECT_STREAM_TWITCH_CHANNEL": " my_chan ",
            "SOREN_DIRECT_STREAM_RECONNECT_ENABLED": "off",
            "SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC": "600",
            "SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD": "1",
        })
        self.assertEqual((got["enabled"], got["twitch_channel"], got["probe_interval_sec"], got["offline_threshold"]),
                         (False, "my_chan", 600, 1))
        self.assertEqual(pol.load_reconnect_policy({"TWITCH_CHANNEL": "fb"})["twitch_channel"], "fb")

    def test_invalid_values_are_refused(self):
        for key, value in (
            ("SOREN_DIRECT_STREAM_TWITCH_CHANNEL", "a b"),
            ("SOREN_DIRECT_STREAM_TWITCH_CHANNEL", 'x"){y'),
            ("SOREN_DIRECT_STREAM_TWITCH_CHANNEL", "a" * 33),
            ("SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC", "9"),
            ("SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC", "601"),
            ("SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC", "-1"),
            ("SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC", "1.5"),
            ("SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD", "0"),
            ("SOREN_DIRECT_STREAM_RECONNECT_BACKOFF_SEC", "301"),
            ("SOREN_DIRECT_STREAM_RECONNECT_MAX_CONSECUTIVE", "101"),
            ("SOREN_DIRECT_STREAM_RECONNECT_ENABLED", "maybe"),
            ("SOREN_DIRECT_STREAM_RECONNECT_RELOAD_RELAY", "2"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                pol.load_reconnect_policy({key: value})

    def test_does_not_read_the_process_environment(self):
        import os
        os.environ["SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC"] = "999999"
        try:
            self.assertEqual(pol.load_reconnect_policy({})["probe_interval_sec"], 60)
        finally:
            del os.environ["SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC"]


class AudioWatchdogPolicyTest(unittest.TestCase):
    def test_defaults_use_the_given_uid(self):
        self.assertEqual(pol.load_audio_watchdog_policy({}, uid=1001), {
            "enabled": True, "probe_interval_sec": 10, "loss_threshold": 2,
            "pulse_server": "unix:/run/user/1001/pulse/native",
        })

    def test_pulse_server_must_be_a_local_unix_socket(self):
        for value in ("tcp:host:4713", "unix:relative", "unix:/run/../etc/x", "unix:/a b", "unix:/a;b", "http://x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pol.load_audio_watchdog_policy({"PULSE_SERVER": value}, uid=1)
        self.assertEqual(
            pol.load_audio_watchdog_policy({"PULSE_SERVER": "unix:/tmp/p/native"}, uid=1)["pulse_server"],
            "unix:/tmp/p/native")

    def test_bounds(self):
        for key, value in (
            ("SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_PROBE_SEC", "4"),
            ("SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_PROBE_SEC", "121"),
            ("SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_LOSS_THRESHOLD", "0"),
            ("SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_LOSS_THRESHOLD", "11"),
            ("SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED", "x"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                pol.load_audio_watchdog_policy({key: value}, uid=1)


class ListingClassificationTest(unittest.TestCase):
    def test_ok_only_when_this_process_owns_an_output(self):
        self.assertEqual(pol.classify_source_output_listing(entry(1, 10) + entry(2, 42), 42), "ok")

    def test_missing_only_when_everything_is_attributable_to_others(self):
        self.assertEqual(pol.classify_source_output_listing("", 42), "missing")
        self.assertEqual(pol.classify_source_output_listing("  \n", 42), "missing")
        self.assertEqual(pol.classify_source_output_listing(entry(1, 10) + entry(2, 11), 42), "missing")

    def test_anything_uncertain_is_unknown_and_never_restarts(self):
        for listing in (
            "garbage output",
            entry(1, 10) + entry(2),            # one stream hides the pid
            entry(1, empty=True),                # header with no body
            entry(1, 10) + entry(2, empty=True),
        ):
            with self.subTest(listing=listing[:30]):
                self.assertEqual(pol.classify_source_output_listing(listing, 42), "unknown")
        # An attributable match wins even beside an unattributable stream.
        self.assertEqual(pol.classify_source_output_listing(entry(1) + entry(2, 42), 42), "ok")


class PurityTest(unittest.TestCase):
    def test_no_process_network_environment_or_game_access(self):
        import ast
        tree = ast.parse((ROOT / "src/docich/stream_supervision_policy.py").read_text())
        imported = set()
        attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
        self.assertEqual(imported, {"__future__", "re", "typing"})
        self.assertFalse(attrs & {"environ", "getenv", "getuid", "Popen", "run", "system"})

    def test_module_loads_by_file_path_without_package(self):
        code = (
            "import importlib.util,sys;s=importlib.util.spec_from_file_location('x',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.classify_source_output_listing('',1))"
        )
        proc = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "src/docich/stream_supervision_policy.py")],
                              capture_output=True, text=True, timeout=12, check=False)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "missing"), proc.stderr)


if __name__ == "__main__":
    unittest.main()
