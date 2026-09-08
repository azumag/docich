from __future__ import annotations

import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.presentation import (  # noqa: E402
    PresentationError,
    read_presentation,
    render_notification,
    write_presentation,
)


def fill_event() -> dict:
    return {
        "schema_version": 1,
        "event_id": "fill:paper:opp-1",
        "event_type": "paper_fill",
        "occurred_at": 1_800_000_000.0,
        "symbol": "BTC/JPY",
        "strategy_id": "momentum-v1",
        "side": "buy",
        "amount": "0.00025",
        "price": "12000000",
        "reference_notional": "3000",
        "reason_code": "momentum_breakout",
    }


def settlement_event(*, complete: bool = True) -> dict:
    return {
        "schema_version": 1,
        "event_id": "settlement:multileg-v1:abc",
        "event_type": "multileg_settlement",
        "occurred_at": 1_800_000_001.0,
        "route_id": "JPY>BTC@BTC/JPY|BTC>ETH@ETH/BTC|ETH>JPY@ETH/JPY",
        "start_asset": "JPY",
        "start_amount": "1000",
        "complete": complete,
        "final_amount": "1005" if complete else None,
        "net_edge_bps": "50" if complete else None,
        "failed_leg_symbol": None if complete else "ETH/BTC",
        "failure_reason": None if complete else "insufficient_depth",
    }


def safe_status() -> dict:
    return {
        "schema_version": 1,
        "mode": "paper",
        "capital_reference": "10000",
        "deployed_reference": "3000",
    }


class TestNotificationConfig(unittest.TestCase):
    def test_defaults_are_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            self.assertFalse(g.trading.notifications_enabled)
            self.assertFalse(g.trading.notification_speech_enabled)

    def test_notification_flags_are_strict_booleans(self):
        for key in ("notifications_enabled", "notification_speech_enabled"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = root / "docich.toml"
                cfg.write_text(f"[trading]\n{key} = 1\n", encoding="utf-8")
                with self.assertRaises(config.ConfigError):
                    config.load_global(root, config_path=cfg)


class TestPresentationState(unittest.TestCase):
    def test_missing_state_defaults_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = read_presentation(Path(tmp) / "presentation.json")
            self.assertEqual(state.mode, "compact")
            self.assertIsNone(state.updated_at)

    def test_write_is_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "trading" / "presentation.json"
            write_presentation(path, "detailed", now=123.5)
            self.assertEqual(read_presentation(path).mode, "detailed")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_corrupt_or_invalid_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "presentation.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(PresentationError):
                read_presentation(path)
            path.write_text(json.dumps({"schema_version": 1, "mode": "live"}), encoding="utf-8")
            with self.assertRaises(PresentationError):
                read_presentation(path)


class TestNotificationRendering(unittest.TestCase):

    def test_overlay_carries_stable_source_event_id(self):
        rendered = render_notification(fill_event(), mode="compact", status=safe_status(), display_at=1_800_000_010.0)
        self.assertEqual(rendered.overlay_event["source_id"], "fill:paper:opp-1")
        self.assertEqual(rendered.overlay_event["ts"], 1_800_000_010)

    def test_compact_fill_is_paper_labeled_without_detailed_reason(self):
        rendered = render_notification(fill_event(), mode="compact", status=safe_status())
        self.assertIn("PAPER", rendered.overlay_event["title"])
        self.assertIn("BTC/JPY", rendered.overlay_event["body"])
        self.assertIn("3,000", rendered.overlay_event["body"])
        self.assertIn("ペーパー", rendered.speech_text)
        self.assertNotIn("モメンタム", rendered.speech_text)

    def test_detailed_fill_uses_stable_reason_and_paper_capital_context(self):
        rendered = render_notification(fill_event(), mode="detailed", status=safe_status())
        self.assertIn("PAPER", rendered.overlay_event["title"])
        self.assertIn("モメンタム", rendered.overlay_event["body"])
        self.assertIn("3,000", rendered.speech_text)
        self.assertIn("10,000", rendered.speech_text)
        self.assertIn("ペーパー", rendered.speech_text)

    def test_settlement_success_never_calls_edge_guaranteed_profit(self):
        rendered = render_notification(settlement_event(), mode="detailed", status=safe_status())
        self.assertIn("PAPER", rendered.overlay_event["title"])
        self.assertIn("50", rendered.overlay_event["body"])
        self.assertIn("模擬", rendered.speech_text)
        self.assertNotIn("確実", rendered.speech_text)
        self.assertEqual(rendered.overlay_event["level"], "info")

    def test_failed_settlement_is_warning(self):
        rendered = render_notification(settlement_event(complete=False), mode="detailed", status=safe_status())
        self.assertEqual(rendered.overlay_event["level"], "warn")
        self.assertIn("板不足", rendered.speech_text)


if __name__ == "__main__":
    unittest.main()
