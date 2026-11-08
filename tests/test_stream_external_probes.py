"""Twitch probe, relay reload and Pulse capture probe run against fakes."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_external_probes as ep  # noqa: E402
from docich.stream_host_checks import RuntimeCheckError  # noqa: E402


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()


def opener(body=None, error=None, seen=None):
    def urlopen(request, timeout):
        if seen is not None:
            seen.append((request, timeout))
        if error is not None:
            raise error
        return Response(body)
    return urlopen


class TwitchProbeTest(unittest.TestCase):
    def test_live_offline_and_unknown(self):
        self.assertIs(ep.twitch_channel_is_live("chan", urlopen=opener({"data": {"user": {"stream": {"id": "1"}}}})), True)
        self.assertIs(ep.twitch_channel_is_live("chan", urlopen=opener({"data": {"user": {"stream": None}}})), False)
        for label, kwargs in (
            ("url error", {"error": urllib.error.URLError("down")}),
            ("os error", {"error": OSError("reset")}),
            ("bad json", {"body": b"<html>"}),
            ("no data", {"body": {"errors": []}}),
            ("null user", {"body": {"data": {"user": None}}}),
            ("non-dict", {"body": [1, 2]}),
        ):
            with self.subTest(label):
                self.assertIsNone(ep.twitch_channel_is_live("chan", urlopen=opener(**kwargs)))

    def test_request_is_credential_less_and_channel_is_validated(self):
        seen = []
        ep.twitch_channel_is_live("my_chan", timeout=7, urlopen=opener({"data": {"user": {"stream": None}}}, seen=seen))
        request, timeout = seen[0]
        self.assertEqual(timeout, 7)
        self.assertEqual(request.full_url, ep.TWITCH_GQL_URL)
        self.assertEqual(request.get_method(), "POST")
        header_names = {name.lower() for name in dict(request.header_items())}
        self.assertEqual(header_names, {"client-id", "content-type", "accept"})
        self.assertEqual(json.loads(request.data), {"query": 'query { user(login: "my_chan") { stream { id } } }'})
        for bad in ("", 'x"){y', "a b", "a" * 33, "chan;drop"):
            with self.subTest(channel=bad):
                seen.clear()
                self.assertIsNone(ep.twitch_channel_is_live(bad, urlopen=opener({"data": {}}, seen=seen)))
                self.assertEqual(seen, [], "an invalid channel must never reach the network")


class ReloadRelayTest(unittest.TestCase):
    def test_first_success_stops_the_chain(self):
        calls = []
        def run(command, **kw):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        self.assertTrue(ep.reload_relay(run=run, log=lambda t: None))
        self.assertEqual(calls, [list(ep.RELAY_RELOAD_COMMANDS[0])])

    def test_failures_fall_through_in_order_and_are_logged(self):
        calls, logs = [], []
        def run(command, **kw):
            calls.append(tuple(command))
            if len(calls) == 1:
                raise OSError("no sudo")
            if len(calls) == 2:
                raise subprocess.TimeoutExpired("sudo", 10)
            return subprocess.CompletedProcess(command, 1, "", " denied \n")
        self.assertFalse(ep.reload_relay(run=run, log=logs.append))
        self.assertEqual(tuple(calls), ep.RELAY_RELOAD_COMMANDS)
        self.assertIn("skipped (no sudo)", logs[0])
        self.assertIn("rc=1 denied cmd=nginx -s reload", logs[2])

    def test_only_the_three_fixed_commands_exist(self):
        self.assertEqual(ep.RELAY_RELOAD_COMMANDS, (
            ("sudo", "-n", "systemctl", "reload", "soren-rtmp-relay.service"),
            ("sudo", "-n", "nginx", "-s", "reload", "-c", "/etc/soren-rtmp/nginx.conf"),
            ("nginx", "-s", "reload", "-c", "/etc/soren-rtmp/nginx.conf"),
        ))


class AudioProbeTest(unittest.TestCase):
    def probe(self, outputs):
        envs = []
        def run(command, *, env):
            envs.append(env)
            value = outputs[tuple(command)]
            if isinstance(value, Exception):
                raise value
            return value
        state = ep.probe_audio_capture_state("unix:/run/user/1/pulse/native", 42, base_env={"PATH": "/bin"}, run=run)
        return state, envs

    def test_states(self):
        entry = 'Source Output #3\n\t\tapplication.process.id = "42"\n'
        other = 'Source Output #3\n\t\tapplication.process.id = "7"\n'
        info = ("pactl", "info")
        listing = ("pactl", "list", "source-outputs")
        self.assertEqual(self.probe({info: "ok", listing: entry})[0], "ok")
        self.assertEqual(self.probe({info: "ok", listing: other})[0], "missing")
        self.assertEqual(self.probe({info: "ok", listing: "garbage"})[0], "unknown")
        self.assertEqual(self.probe({info: RuntimeCheckError("down"), listing: ""})[0], "unreachable")
        self.assertEqual(self.probe({info: "ok", listing: RuntimeCheckError("x")})[0], "unknown")

    def test_pulse_server_is_pinned_without_mutating_the_caller_env(self):
        base = {"PATH": "/bin", "PULSE_SERVER": "tcp:evil:4713"}
        envs = []
        ep.probe_audio_capture_state("unix:/run/user/1/pulse/native", 1, base_env=base,
                                     run=lambda command, *, env: envs.append(env) or "")
        self.assertEqual({e["PULSE_SERVER"] for e in envs}, {"unix:/run/user/1/pulse/native"})
        self.assertEqual(base["PULSE_SERVER"], "tcp:evil:4713")


class PurityTest(unittest.TestCase):
    def test_no_signals_locks_or_shell(self):
        tree = ast.parse((ROOT / "src/docich/stream_external_probes.py").read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(attrs & {"kill", "send_signal", "flock", "Popen", "system", "environ", "write_text", "mkdir"})
        shell = [k for n in ast.walk(tree) if isinstance(n, ast.keyword) for k in [n.arg] if k == "shell"]
        self.assertEqual(shell, [])


if __name__ == "__main__":
    unittest.main()
