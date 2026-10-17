from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import vrchat_osc as osc  # noqa: E402


BASE_TOML = """
[osc]
vrchat_host = "127.0.0.1"
vrchat_port = 19000
listen_host = "127.0.0.1"
listen_port = 19001
poll_interval_ms = 10
send_min_interval_ms = 0
echo_suppress_ms = 400
max_sends_per_second = 1000

[speaking]
file = "tmp/state/speaking.json"
parameter = "AI_Talking"
grace_ms = 500

[paths]
event_spool = "vrchat_osc/events.jsonl"
action_spool = "vrchat_osc/actions.jsonl"
status = "vrchat_osc/status.json"
state = "vrchat_osc/state.json"

[inbound.Mode]
action = "mode"
values = ["normal", "doushi", "capitalism"]
min_interval_ms = 500

[inbound.Alert]
action = "notify"
title = "VR同志"
body = "test alert"
min_interval_ms = 1000
"""


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def send_param(self, name: str, value: object) -> None:
        self.calls.append((name, value))

    def values(self, name: str) -> list[object]:
        return [value for sent_name, value in self.calls if sent_name == name]


class FailingSender:
    def __init__(self) -> None:
        self.calls = 0

    def send_param(self, name: str, value: object) -> None:
        self.calls += 1
        raise OSError("network unreachable")


def make_settings(tmp: str, toml_text: str = BASE_TOML):
    root = Path(tmp) / "repo"
    (root / "config").mkdir(parents=True, exist_ok=True)
    config = root / "config" / "vrchat_osc.toml"
    config.write_text(toml_text, encoding="utf-8")
    soren = root / "games" / "soviet_now"
    (soren / "tmp" / "state").mkdir(parents=True, exist_ok=True)
    settings = osc.load_settings(
        repo_root=root,
        state_dir=root / "run",
        config_path=config,
        soren_root=soren,
    )
    return settings, soren


def write_speaking(soren: Path, *, speaking: bool = True, pid: int | None = None) -> Path:
    path = soren / "tmp" / "state" / "speaking.json"
    payload = {"speaking": speaking, "since": int(time.time()), "reason": "test"}
    if pid is not None:
        payload["pid"] = pid
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_bridge(settings, *, sender=None, sinks=(), clock=lambda: 0.0):
    return osc.VrOscBridge(
        settings,
        sender=sender or RecordingSender(),
        clock=clock,
        sinks=list(sinks),
    )


def action_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestOscCodec(unittest.TestCase):
    def test_roundtrip_all_common_types(self):
        packet = osc.encode_message(
            "/avatar/parameters/AI_Emotion", (3, 1.25, "doushi", b"\x01\x02", True, False)
        )
        message = osc.decode_message(packet)
        self.assertEqual(message.address, "/avatar/parameters/AI_Emotion")
        self.assertEqual(message.args[0], 3)
        self.assertAlmostEqual(message.args[1], 1.25, places=5)
        self.assertEqual(message.args[2], "doushi")
        self.assertEqual(message.args[3], b"\x01\x02")
        # Bool is encoded as int32 0/1 (VRChat's documented representation).
        self.assertEqual(message.args[4], 1)
        self.assertEqual(message.args[5], 0)

    def test_padding_matches_osc_spec(self):
        # "/a" -> 4 bytes including NUL; ",i" -> 4 bytes
        self.assertEqual(
            osc.encode_message("/a", (1,)),
            b"/a\x00\x00,i\x00\x00\x00\x00\x00\x01",
        )

    def test_bundle_is_flattened(self):
        inner_a = osc.encode_message("/avatar/parameters/Alert", (1,))
        inner_b = osc.encode_message("/avatar/parameters/Mode", (2,))
        bundle = (
            b"#bundle\x00"
            + b"\x00" * 8
            + len(inner_a).to_bytes(4, "big")
            + inner_a
            + len(inner_b).to_bytes(4, "big")
            + inner_b
        )
        messages = osc.decode_packet(bundle)
        self.assertEqual([m.address for m in messages], [
            "/avatar/parameters/Alert",
            "/avatar/parameters/Mode",
        ])

    def test_rejects_malformed_packets(self):
        with self.assertRaises(osc.VrOscError):
            osc.decode_message(b"")
        with self.assertRaises(osc.VrOscError):
            osc.decode_message(b"/x\x00\x00" + b"i\x00\x00\x00")
        with self.assertRaises(osc.VrOscError):
            osc.decode_message(osc.encode_message("/x", (1,))[:-1])
        with self.assertRaises(osc.VrOscError):
            osc.encode_message("no-slash", ())
        with self.assertRaises(osc.VrOscError):
            osc.encode_message("/x", (float("nan"),))

    def test_type_tag_without_full_padding_is_tolerated(self):
        # ",i\0" is shorter than the padded 4 bytes; the decoder still finds
        # the tag string and the int that follows.
        packet = b"/x\x00\x00" + b",i\x00\x00" + b"\x00\x00\x00\x01"
        self.assertEqual(osc.decode_message(packet).args, (1,))

    def test_parse_assignment_and_coercion(self):
        self.assertEqual(osc.parse_assignment("AI_Emotion=2"), ("AI_Emotion", 2))
        self.assertEqual(osc.parse_assignment("Alert=true"), ("Alert", True))
        self.assertEqual(osc.parse_assignment("Alert=off"), ("Alert", False))
        with self.assertRaises(osc.VrOscError):
            osc.parse_assignment("Alert")
        self.assertIs(osc.coerce_value("Alert", "bool", 1), True)
        self.assertEqual(osc.coerce_value("AI_Emotion", "int", 2.0), 2)
        with self.assertRaises(osc.VrOscError):
            osc.coerce_value("AI_Emotion", "int", 1.5)
        with self.assertRaises(osc.VrOscError):
            osc.coerce_value("Alert", "bool", 2)


class TestSettings(unittest.TestCase):
    def test_defaults_when_config_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = osc.load_settings(repo_root=root, state_dir=root / "run")
            self.assertEqual(settings.vrchat_port, 9000)
            self.assertEqual(settings.listen_port, 9001)
            self.assertEqual(settings.params["Mode"].kind, "int")
            self.assertTrue(settings.params["Mode"].persist)
            self.assertEqual(settings.speaking_parameter, "AI_Talking")
            self.assertEqual(settings.event_spool, root / "run" / "vrchat_osc" / "events.jsonl")
            self.assertIsNone(settings.speaking_path)

    def test_explicit_missing_config_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(osc.VrOscConfigError):
                osc.load_settings(
                    repo_root=Path(tmp),
                    state_dir=Path(tmp) / "run",
                    config_path=Path(tmp) / "missing.toml",
                )

    def test_inbound_must_reference_a_parameter(self):
        toml_text = BASE_TOML + '\n[inbound.Secret]\naction = "mode"\n'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(osc.VrOscConfigError):
                make_settings(tmp, toml_text)

    def test_inbound_rejects_unknown_action(self):
        toml_text = BASE_TOML + '\n[inbound.Mode]\naction = "shell"\n'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(osc.VrOscConfigError):
                make_settings(tmp, toml_text)

    def test_ports_must_differ(self):
        toml_text = BASE_TOML.replace("listen_port = 19001", "listen_port = 19000")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(osc.VrOscConfigError):
                make_settings(tmp, toml_text)

    def test_env_overrides_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = osc.load_settings(
                repo_root=root,
                state_dir=root / "run",
                env={
                    "DOCICH_VRCHAT_OSC_HOST": "192.0.2.10",
                    "DOCICH_VRCHAT_OSC_PORT": "19010",
                    "DOCICH_VRCHAT_OSC_LISTEN_PORT": "19011",
                },
            )
            self.assertEqual(settings.vrchat_host, "192.0.2.10")
            self.assertEqual(settings.vrchat_port, 19010)
            self.assertEqual(settings.listen_port, 19011)


class TestSpool(unittest.TestCase):
    def test_tail_skips_existing_then_reads_new_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text('{"param":"Mode","value":1}\n', encoding="utf-8")
            tail = osc.JsonlTail(path)
            self.assertEqual(tail.read(), [])
            osc.append_jsonl(path, {"param": "Mode", "value": 2})
            events = tail.read()
            self.assertEqual([event["value"] for event in events], [2])

    def test_tail_handles_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            tail = osc.JsonlTail(path)
            path.write_text('{"param":"Mode","value":', encoding="utf-8")
            self.assertEqual(tail.read(), [])
            with path.open("a", encoding="utf-8") as handle:
                handle.write('3}\n')
            self.assertEqual(tail.read()[0]["value"], 3)

    def test_tail_ignores_corrupt_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            tail = osc.JsonlTail(path)
            path.write_text('not json\n{"param":"Mode","value":1}\n', encoding="utf-8")
            self.assertEqual(tail.read(), [{"param": "Mode", "value": 1}])

    def test_emit_rejects_the_derived_speaking_parameter(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            with self.assertRaises(osc.VrOscError):
                osc.append_events(
                    settings.event_spool,
                    [("AI_Talking", True)],
                    settings=settings,
                )

    def test_emit_applies_configured_and_explicit_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            count = osc.append_events(
                settings.event_spool,
                [("AI_Emotion", 2), ("Alert", True)],
                settings=settings,
                hold_ms=1500,
                now=100.0,
            )
            self.assertEqual(count, 2)
            records = action_records(settings.event_spool)
            self.assertEqual(records[0]["hold_ms"], 1500)
            self.assertEqual(records[1]["value"], True)


class TestBridgeOutbound(unittest.TestCase):
    def test_talking_follows_speaking_file_with_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, soren = make_settings(tmp)
            sender = RecordingSender()
            bridge = make_bridge(settings, sender=sender)
            path = write_speaking(soren, speaking=True, pid=os.getpid())

            bridge.tick(now=0.0)
            self.assertEqual(sender.values("AI_Talking"), [True])

            bridge.tick(now=0.2)
            self.assertEqual(sender.values("AI_Talking"), [True], "変化なしでは再送しない")

            path.unlink()
            bridge.tick(now=0.4)
            self.assertEqual(sender.values("AI_Talking"), [True], "grace 中は口を閉じない")

            bridge.tick(now=1.0)
            self.assertEqual(sender.values("AI_Talking"), [True, False])

    def test_dead_pid_means_not_talking(self):
        if os.name != "posix":
            self.skipTest("pid check is POSIX only")
        with tempfile.TemporaryDirectory() as tmp:
            settings, soren = make_settings(tmp)
            write_speaking(soren, speaking=True, pid=999999)
            self.assertFalse(osc.read_speaking(settings.speaking_path))

    def test_emotion_hold_expires_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            sender = RecordingSender()
            bridge = make_bridge(settings, sender=sender)
            osc.append_events(
                settings.event_spool,
                [("AI_Emotion", 2)],
                settings=settings,
                hold_ms=1000,
            )
            bridge.tick(now=0.0)
            self.assertEqual(sender.values("AI_Emotion"), [2])
            bridge.tick(now=0.5)
            self.assertEqual(sender.values("AI_Emotion"), [2])
            bridge.tick(now=1.1)
            self.assertEqual(sender.values("AI_Emotion"), [2, 0])

    def test_sticky_mode_is_persisted_and_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            sender = RecordingSender()
            bridge = make_bridge(settings, sender=sender)
            osc.append_events(settings.event_spool, [("Mode", 1)], settings=settings)
            bridge.tick(now=0.0)
            self.assertIn(("Mode", 1), sender.calls)
            stored = json.loads(settings.state_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["params"]["Mode"], 1)

            second = make_bridge(settings, sender=RecordingSender())
            self.assertEqual(second.values()["Mode"], 1)

    def test_alert_default_hold_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            sender = RecordingSender()
            bridge = make_bridge(settings, sender=sender)
            osc.append_events(settings.event_spool, [("Alert", True)], settings=settings, hold_ms=500)
            bridge.tick(now=0.0)
            self.assertEqual(sender.values("Alert"), [True])
            bridge.tick(now=0.6)
            self.assertEqual(sender.values("Alert"), [True, False])

    def test_send_failure_is_fail_soft_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            sender = FailingSender()
            bridge = make_bridge(settings, sender=sender)
            _, soren = make_settings(tmp)
            write_speaking(soren, speaking=True, pid=os.getpid())
            result = bridge.tick(now=0.0)
            self.assertEqual(result.sent, ())
            self.assertEqual(sender.calls, 1)
            self.assertIn("OSC 送信に失敗", bridge.status()["last_error"] or "")

    def test_status_file_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _ = make_settings(tmp)
            bridge = make_bridge(settings)
            bridge.tick(now=0.0)
            status = json.loads(settings.status_path.read_text(encoding="utf-8"))
            self.assertIn("params", status)
            self.assertEqual(status["vrchat"]["port"], settings.vrchat_port)


class TestBridgeInbound(unittest.TestCase):
    def _bridge(self, tmp):
        settings, soren = make_settings(tmp)
        sinks = [osc.ActionSpool(settings.action_spool)]
        bridge = make_bridge(settings, sinks=sinks)
        return settings, soren, bridge

    @staticmethod
    def _packet(name: str, value: object) -> bytes:
        return osc.encode_message(osc.PARAMETER_PREFIX + name, (value,))

    def test_bool_is_rising_edge_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            alert = self._packet("Alert", 1)
            bridge.tick(now=0.0, packets=[alert])
            bridge.tick(now=2.0, packets=[alert])
            records = action_records(settings.action_spool)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["action"], "notify")
            self.assertEqual(records[0]["title"], "VR同志")

            bridge.tick(now=3.0, packets=[self._packet("Alert", 0)])
            bridge.tick(now=4.0, packets=[alert])
            self.assertEqual(len(action_records(settings.action_spool)), 2)

    def test_bool_release_is_recorded_so_the_next_outbound_true_is_sent(self):
        # P2 (PR #2458 レビュー): アバターが Alert を 1->0 にした後、次の docich 側
        # Alert=true が「同期済み」と誤判定されて送信されない。受信値の同期と
        # notify の発火判定を分離し、outbound true がもう一度送られることを固定する。
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            sender = RecordingSender()
            bridge.sender = sender

            # docich -> VRChat: Alert=true を送り、同期済みとして記録する。
            osc.append_events(settings.event_spool, [("Alert", True)], settings=settings)
            bridge.tick(now=0.0)
            self.assertEqual(sender.values("Alert"), [True])

            # VRChat -> docich: アバターが Alert を解放する (1 -> 0)。
            # notify は発火しないが、受信値は同期済みとして記録される。
            bridge.tick(now=2.0, packets=[self._packet("Alert", 0)])
            self.assertIs(bridge.values()["Alert"], False, "アバターの解放を採用する")
            self.assertEqual(sender.values("Alert"), [True], "受信しただけでは送り返さない")

            # docich -> VRChat: 次の true がアバターへ届く。
            osc.append_events(settings.event_spool, [("Alert", True)], settings=settings)
            bridge.tick(now=3.0)
            self.assertEqual(sender.values("Alert"), [True, True])

    def test_int_change_requires_debounce_and_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            mode = self._packet("Mode", 1)
            bridge.tick(now=0.0, packets=[mode])
            bridge.tick(now=0.1, packets=[mode])
            bridge.tick(now=1.0, packets=[mode])
            self.assertEqual(bridge.receiver.actions, 1)

            bridge.tick(now=1.6, packets=[self._packet("Mode", 2)])
            self.assertEqual(bridge.receiver.actions, 2)
            records = action_records(settings.action_spool)
            self.assertEqual(records[-1]["name"], "capitalism")
            self.assertEqual(records[-1]["value"], 2)

    def test_mode_value_outside_allowlist_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            bridge.tick(now=0.0, packets=[self._packet("Mode", 9)])
            self.assertEqual(bridge.receiver.actions, 0)
            self.assertGreaterEqual(bridge.receiver.dropped, 1)
            self.assertEqual(action_records(settings.action_spool), [])

    def test_unknown_parameter_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            bridge.tick(now=0.0, packets=[self._packet("AI_Emotion", 3)])
            self.assertEqual(bridge.receiver.actions, 0)
            self.assertEqual(action_records(settings.action_spool), [])

    def test_inbound_mode_is_adopted_without_bouncing_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            sender = RecordingSender()
            bridge.sender = sender
            bridge.tick(now=0.0, packets=[self._packet("Mode", 1)])
            bridge.tick(now=0.1)
            self.assertEqual(bridge.values()["Mode"], 1, "VRChat側の選択をdocichも採用する")
            self.assertNotIn(("Mode", 1), sender.calls, "同じ値を送り返さない")

    def test_echo_of_our_own_send_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            osc.append_events(settings.event_spool, [("Mode", 2)], settings=settings)
            bridge.tick(now=0.0)  # sends Mode=2, _synced records it
            bridge.tick(now=0.1, packets=[self._packet("Mode", 2)])
            self.assertEqual(bridge.receiver.actions, 0, "送信直後のエコーでループしない")
            self.assertGreaterEqual(bridge.receiver.dropped, 1)

    def test_corrupt_datagram_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, _, bridge = self._bridge(tmp)
            bridge.tick(now=0.0, packets=[b"\x00\x01not-osc"])
            self.assertEqual(bridge.receiver.actions, 0)
            self.assertGreaterEqual(bridge.receiver.dropped, 1)


class TestUdpEndToEnd(unittest.TestCase):
    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_bridge_sends_and_receives_over_udp(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, soren = make_settings(tmp)
            fake_port = self._free_port()
            listen_port = self._free_port()
            while listen_port == fake_port:
                listen_port = self._free_port()
            settings = replace(
                settings,
                vrchat_port=fake_port,
                listen_port=listen_port,
                poll_interval=0.02,
            )

            fake_vrchat = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            fake_vrchat.bind(("127.0.0.1", fake_port))
            fake_vrchat.settimeout(0.05)
            sender = osc.OscSender("127.0.0.1", fake_port)
            sinks = [osc.ActionSpool(settings.action_spool)]
            bridge = osc.VrOscBridge(settings, sender=sender, sinks=sinks)
            write_speaking(soren, speaking=True, pid=os.getpid())

            thread = threading.Thread(target=lambda: bridge.run(duration=1.0), daemon=True)
            thread.start()
            try:
                time.sleep(0.15)
                injector = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    injector.sendto(
                        osc.encode_message(osc.PARAMETER_PREFIX + "Alert", (1,)),
                        ("127.0.0.1", listen_port),
                    )
                finally:
                    injector.close()
                received: list[osc.OscMessage] = []
                deadline = time.time() + 0.6
                while time.time() < deadline:
                    try:
                        data, _ = fake_vrchat.recvfrom(65535)
                    except socket.timeout:
                        continue
                    received.append(osc.decode_message(data))
                thread.join(2.0)
            finally:
                if thread.is_alive():  # pragma: no cover - safety net
                    thread.join(2.0)
                fake_vrchat.close()
                sender.close()

            addresses = {message.address for message in received}
            self.assertIn(osc.PARAMETER_PREFIX + "AI_Talking", addresses)
            records = action_records(settings.action_spool)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["action"], "notify")
            status = json.loads(settings.status_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(status["counters"]["received"], 1)


class TestCli(unittest.TestCase):
    def _root(self, tmp: str) -> tuple[Path, Path]:
        root = Path(tmp) / "repo"
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "config" / "vrchat_osc.toml").write_text(BASE_TOML, encoding="utf-8")
        soren = root / "games" / "soviet_now"
        (soren / "tmp" / "state").mkdir(parents=True, exist_ok=True)
        return root, root / "run"

    def test_emit_and_status_through_standalone_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self._root(tmp)
            code = osc.main([
                "--repo-root", str(root),
                "emit", "AI_Emotion=2", "--hold", "1.5",
            ])
            self.assertEqual(code, 0)
            events = action_records(root / "run" / "vrchat_osc" / "events.jsonl")
            self.assertEqual(events[0]["param"], "AI_Emotion")
            self.assertEqual(events[0]["hold_ms"], 1500)

            code = osc.main([
                "--repo-root", str(root),
                "status", "--json",
            ])
            self.assertEqual(code, 0)

    def test_send_dry_run_and_inject_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self._root(tmp)
            self.assertEqual(
                osc.main(["--repo-root", str(root), "send", "Mode=2", "--dry-run"]), 0
            )
            # AI_Emotion is not in [inbound], so injection is refused.
            self.assertEqual(
                osc.main(["--repo-root", str(root), "inject", "AI_Emotion=2", "--dry-run"]), 2
            )


if __name__ == "__main__":
    unittest.main()
