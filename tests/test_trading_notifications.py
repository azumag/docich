from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from time import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.events import append_public_event  # noqa: E402
from docich.trading.notifications import (  # noqa: E402
    NotificationError,
    deliver_pending_notifications,
)


def event(event_id: str, *, occurred_at: float = 1_800_000_000.0) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": "paper_fill",
        "occurred_at": occurred_at,
        "symbol": "BTC/JPY",
        "strategy_id": "momentum-v1",
        "side": "buy",
        "amount": "0.00025",
        "price": "12000000",
        "reference_notional": "3000",
        "reason_code": "momentum_breakout",
    }


def global_config(root: Path, *, speech: bool = True):
    cfg = root / "docich.toml"
    cfg.write_text(
        "[paths]\nstate_dir = \"run\"\n"
        "[trading]\nnotifications_enabled = true\n"
        f"notification_speech_enabled = {'true' if speech else 'false'}\n",
        encoding="utf-8",
    )
    return config.load_global(root, config_path=cfg)


class Sender:
    def __init__(self):
        self.calls = []
        self.fail = False

    def overlay(self, _g, payload):
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError("overlay unavailable")

    def speech(self, _g, text):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("speech unavailable")


class TestNotificationDelivery(unittest.TestCase):
    def test_first_enable_bootstraps_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root)
            source = g.state_dir / "trading" / "events.jsonl"
            append_public_event(source, event("fill:a"))
            overlay, speech = Sender(), Sender()
            result = deliver_pending_notifications(
                g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1000.0
            )
            self.assertTrue(result.bootstrapped)
            self.assertEqual(overlay.calls, [])
            self.assertEqual(speech.calls, [])
            self.assertEqual(result.overlay_pending, 0)
            self.assertEqual(result.speech_pending, 0)

    def test_destinations_ack_independently_and_retry_only_failed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root)
            source = g.state_dir / "trading" / "events.jsonl"
            overlay, speech = Sender(), Sender()
            deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1000.0)
            append_public_event(source, event("fill:b", occurred_at=1_800_000_001.0))
            overlay.fail = True
            first = deliver_pending_notifications(
                g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1001.0
            )
            self.assertEqual(first.overlay_pending, 1)
            self.assertEqual(first.speech_pending, 0)
            self.assertIn("overlay_delivery_error", first.error_codes)
            self.assertEqual(len(overlay.calls), 1)
            self.assertEqual(len(speech.calls), 1)
            overlay.fail = False
            second = deliver_pending_notifications(
                g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1002.0
            )
            self.assertEqual(second.overlay_pending, 0)
            self.assertEqual(len(overlay.calls), 2)
            self.assertEqual(len(speech.calls), 1)

    def test_speech_disabled_suppresses_history_before_later_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root, speech=False)
            source = g.state_dir / "trading" / "events.jsonl"
            overlay, speech = Sender(), Sender()
            deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1000.0)
            append_public_event(source, event("fill:c"))
            delivered = deliver_pending_notifications(
                g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1001.0
            )
            self.assertEqual(delivered.speech_pending, 0)
            self.assertEqual(speech.calls, [])
            g.trading.notification_speech_enabled = True
            deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1002.0)
            self.assertEqual(speech.calls, [])

    def test_corrupt_delivery_state_fails_closed_without_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root)
            state_dir = g.state_dir / "trading"
            state_dir.mkdir(parents=True)
            (state_dir / "notification_delivery.json").write_text("not-json", encoding="utf-8")
            append_public_event(state_dir / "events.jsonl", event("fill:d"))
            overlay, speech = Sender(), Sender()
            with self.assertRaises(NotificationError):
                deliver_pending_notifications(
                    g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1000.0
                )
            self.assertEqual(overlay.calls, [])
            self.assertEqual(speech.calls, [])

    def test_delivery_state_is_private_and_bounded_to_source_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root, speech=False)
            source = g.state_dir / "trading" / "events.jsonl"
            overlay, speech = Sender(), Sender()
            deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1000.0)
            for index in range(6):
                append_public_event(source, event(f"fill:{index}", occurred_at=1_800_000_000 + index), max_events=3)
                deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=1001 + index)
            state_path = g.state_dir / "trading" / "notification_delivery.json"
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertLessEqual(len(raw["overlay_delivered_ids"]), 3)
            self.assertLessEqual(len(raw["speech_delivered_ids"]), 3)
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(state_path.parent.stat().st_mode), 0o700)



    def test_concurrent_delivery_is_exclusive_across_state_read_and_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root, speech=False)
            source = g.state_dir / "trading" / "events.jsonl"
            deliver_pending_notifications(g, overlay_sender=Sender().overlay, speech_sender=Sender().speech, now=1000.0)
            append_public_event(source, event("fill:concurrent"))
            entered = threading.Event()
            release = threading.Event()
            calls = []
            errors = []

            def blocking_overlay(_g, payload):
                calls.append(payload)
                entered.set()
                release.wait(2.0)

            def first_delivery():
                try:
                    deliver_pending_notifications(
                        g, overlay_sender=blocking_overlay, speech_sender=Sender().speech, now=1001.0
                    )
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=first_delivery)
            thread.start()
            self.assertTrue(entered.wait(1.0))
            # A live owner must not be stolen just because its lock mtime looks old.
            lock = g.state_dir / "trading" / ".notification_delivery.lock"
            os.utime(lock, (1, 1))
            second_overlay = Sender()
            with self.assertRaises(NotificationError):
                deliver_pending_notifications(
                    g, overlay_sender=second_overlay.overlay, speech_sender=Sender().speech, now=1001.5
                )
            self.assertEqual(second_overlay.calls, [])
            release.set()
            thread.join(2.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(calls), 1)

    def test_delayed_overlay_delivery_uses_delivery_time_for_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = global_config(root, speech=False)
            source = g.state_dir / "trading" / "events.jsonl"
            overlay, speech = Sender(), Sender()
            base = time()
            deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=base)
            append_public_event(source, event("fill:late", occurred_at=base - 100.0))
            deliver_pending_notifications(g, overlay_sender=overlay.overlay, speech_sender=speech.speech, now=base + 1.0)
            self.assertEqual(overlay.calls[0]["ts"], int(base + 1.0))

if __name__ == "__main__":
    unittest.main()
