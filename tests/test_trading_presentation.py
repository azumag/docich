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

    def test_compact_buy_uses_reason_pair_side_format(self):
        rendered = render_notification(fill_event(), mode="compact", status=safe_status())
        self.assertIn("PAPER", rendered.overlay_event["title"])
        self.assertEqual(rendered.speech_text, "短期モメンタムの上振れを検出：BTC/JPYを買い。")
        self.assertNotIn("ペーパートレード速報", rendered.speech_text)
        self.assertNotIn("模擬投入額", rendered.speech_text)

    def test_sell_without_pnl_uses_same_brief_and_visible_failure(self):
        event = fill_event()
        event["side"] = "sell"
        event["reason_code"] = "take_profit"
        rendered = render_notification(event, mode="compact", status=safe_status())
        self.assertEqual(rendered.speech_text, "利確条件を検出：BTC/JPYを売り、損益は確認できませんでした。")
        self.assertIn("実現損益 取得失敗", rendered.overlay_event["body"])

    def test_compact_and_detailed_buy_use_identical_short_format(self):
        compact = render_notification(fill_event(), mode="compact", status=safe_status())
        detailed = render_notification(fill_event(), mode="detailed", status=safe_status())
        self.assertEqual(detailed.speech_text, compact.speech_text)
        self.assertNotIn("判断理由は", detailed.speech_text)
        self.assertNotIn("ペーパー投入額", detailed.speech_text)

    def test_detailed_sell_is_one_sentence_with_short_pnl(self):
        event = fill_event()
        event["side"] = "sell"
        event["reason_code"] = "take_profit"
        event["realized_pnl_reference"] = "125"
        rendered = render_notification(event, mode="detailed", status=safe_status())
        self.assertEqual(rendered.speech_text, "利確条件を検出：BTC/JPYを売り、損益プラス125円です。")
        self.assertNotIn("この売却で確定した", rendered.speech_text)
        self.assertNotIn("判断理由は", rendered.speech_text)

    def test_sell_negative_and_zero_pnl_are_short(self):
        event = fill_event()
        event["side"] = "sell"
        event["reason_code"] = "stop_loss"
        event["realized_pnl_reference"] = "-80"
        loss = render_notification(event, mode="compact", status=safe_status())
        self.assertEqual(loss.speech_text, "損切り条件を検出：BTC/JPYを売り、損益マイナス80円です。")
        event["realized_pnl_reference"] = "0"
        zero = render_notification(event, mode="compact", status=safe_status())
        self.assertEqual(zero.speech_text, "損切り条件を検出：BTC/JPYを売り、損益プラスマイナスゼロです。")

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
