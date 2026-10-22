"""N03 NewsSignal builder tests (#779).

Offline fixtures only (parent #769 D2/D4 example timestamps). The expiry
min-formula, the explicit FX base/quote mapping, the stock sell reduce-only
gate and the re-classification no-extension rule are all verified here.
"""
import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from docich.trading.news_paper.classification import ClassificationResult, Impact
from docich.trading.news_paper.signals import (
    CODE_ASSET_UNMAPPED,
    CODE_EVIDENCE_NOT_AVAILABLE,
    CODE_INSUFFICIENT_INVENTORY,
    CODE_NO_DIRECTION,
    CODE_SIGNAL_EXPIRED,
    DIRECTIONS,
    EvidenceRecord,
    FixtureSignalPolicy,
    NewsSignal,
    SignalAsset,
    SignalBuilder,
    SignalError,
    compute_expires_at,
    decode_news_signal,
    fx_direction,
    stock_direction,
    stock_sell_gate,
)

PUBLISHED = datetime(2026, 9, 18, 0, 10, 0, 0, tzinfo=timezone.utc)
AVAILABLE = datetime(2026, 9, 18, 0, 10, 2, 100000, tzinfo=timezone.utc)
ELIGIBLE = datetime(2026, 9, 18, 0, 25, 0, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 9, 18, 0, 10, 3, 0, tzinfo=timezone.utc)

DIGEST = "a" * 64
STOCKS = SignalAsset(market="stocks", symbol="DEMO", venue="XTKS")
FX = SignalAsset(market="fx", symbol="USD_JPY", venue="FXP")


def evidence(**overrides):
    record = EvidenceRecord(
        observation_id="news_demo_001_r1",
        content_digest=DIGEST,
        published_at=PUBLISHED,
        available_at=AVAILABLE,
        eligible_until=ELIGIBLE,
        event_group_id="event_demo_001",
    )
    for key, value in overrides.items():
        record = dataclasses.replace(record, **{key: value})
    return record


def result_for(asset_ref, polarity, confidence=0.82, materiality=0.70,
               event_type="guidance_revision"):
    return ClassificationResult(
        request_id="classify_demo_001",
        event_type=event_type,
        impacts=(Impact(asset_ref=asset_ref, polarity=polarity,
                        confidence=confidence),),
        materiality=materiality,
        summary="fixture rule classification",
        uncertainty_codes=frozenset(),
        evidence_refs=("news_demo_001_r1",),
    )


class ExpiryMinFormulaTests(unittest.TestCase):
    """N00 v2 §1: expires_at = min(eligible_until, published+TTL,
    available+cap, available+policy cap). Every cap is evidence-anchored."""

    def test_parent_d4_fixture_expiry(self):
        expires = compute_expires_at([evidence()], "guidance_revision",
                                     FixtureSignalPolicy())
        self.assertEqual(expires, datetime(2026, 9, 18, 0, 25, 0, 0,
                                           tzinfo=timezone.utc))

    def test_event_ttl_can_be_the_minimum(self):
        late = evidence(eligible_until=PUBLISHED + timedelta(hours=2))
        expires = compute_expires_at([late], "guidance_revision",
                                     FixtureSignalPolicy())
        self.assertEqual(expires, PUBLISHED + timedelta(seconds=900))

    def test_available_cap_can_be_the_minimum(self):
        policy = FixtureSignalPolicy(available_cap_s=60)
        late = evidence(eligible_until=PUBLISHED + timedelta(hours=2))
        expires = compute_expires_at([late], "guidance_revision", policy)
        self.assertEqual(expires, AVAILABLE + timedelta(seconds=60))

    def test_policy_cap_can_be_the_minimum(self):
        policy = FixtureSignalPolicy(policy_cap_s=30)
        late = evidence(eligible_until=PUBLISHED + timedelta(hours=2))
        expires = compute_expires_at([late], "guidance_revision", policy)
        self.assertEqual(expires, AVAILABLE + timedelta(seconds=30))

    def test_minimum_over_all_evidence_eligible_until(self):
        early = evidence(eligible_until=PUBLISHED + timedelta(seconds=120))
        expires = compute_expires_at([evidence(), early], "guidance_revision",
                                     FixtureSignalPolicy())
        self.assertEqual(expires, PUBLISHED + timedelta(seconds=120))


class DirectionRuleTests(unittest.TestCase):
    def test_stock_directions(self):
        self.assertEqual(stock_direction("positive"), "buy")
        self.assertEqual(stock_direction("negative"), "sell")
        self.assertEqual(stock_direction("uncertain"), "neutral")
        self.assertEqual(stock_direction("neutral"), "neutral")

    def test_fx_explicit_base_quote(self):
        # 円の好材料 is NOT interpreted as buying USD_JPY.
        self.assertEqual(fx_direction("USD_JPY", "positive", "JPY"), "sell")
        self.assertEqual(fx_direction("USD_JPY", "positive", "USD"), "buy")
        self.assertEqual(fx_direction("USD_JPY", "negative", "JPY"), "buy")
        self.assertEqual(fx_direction("USD_JPY", "negative", "USD"), "sell")

    def test_fx_ambiguity_fails_closed(self):
        self.assertEqual(fx_direction("USD_JPY", "positive", None), "neutral")
        self.assertEqual(fx_direction("USD_JPY", "positive", "EUR"), "neutral")
        self.assertEqual(fx_direction("EUR_USD", "positive", "JPY"), "neutral")

    def test_stock_sell_reduce_only(self):
        self.assertEqual(stock_sell_gate("sell", "stocks", False),
                         (CODE_INSUFFICIENT_INVENTORY,))
        self.assertEqual(stock_sell_gate("sell", "stocks", True), ())
        self.assertEqual(stock_sell_gate("buy", "stocks", False), ())
        self.assertEqual(stock_sell_gate("sell", "fx", False), ())


class SignalBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = SignalBuilder()

    def build(self, *, result, asset=STOCKS, created_at=CREATED, **kwargs):
        return self.builder.build(
            result=result, evidence=[evidence()], asset=asset,
            classification_ref="classification_demo_001",
            created_at=created_at, **kwargs)

    def test_positive_stock_fixture_matches_parent_d4(self):
        verdict = self.build(result=result_for(STOCKS.asset_ref, "positive"))
        self.assertIsNotNone(verdict.signal)
        signal = verdict.signal
        self.assertEqual(signal.direction, "buy")
        self.assertEqual(signal.event_type, "guidance_revision")
        self.assertEqual(signal.expires_at,
                         datetime(2026, 9, 18, 0, 25, 0, 0, tzinfo=timezone.utc))
        # horizon is capped by the remaining window (created 00:10:03)
        self.assertEqual(signal.horizon_s, 897)
        self.assertLessEqual(signal.created_at + timedelta(seconds=signal.horizon_s),
                             signal.expires_at)
        self.assertEqual(signal.signal_id[:4], "sig_")
        self.assertFalse(signal.no_trade_reasons)

    def test_signal_id_is_deterministic_and_bound(self):
        first = self.build(result=result_for(STOCKS.asset_ref, "positive")).signal
        second = self.build(result=result_for(STOCKS.asset_ref, "positive")).signal
        self.assertEqual(first.signal_id, second.signal_id)
        other_asset = self.build(
            result=result_for("fx:FXP:USD_JPY", "positive"), asset=FX,
            currency_mention="USD").signal
        self.assertIsNotNone(other_asset)
        self.assertNotEqual(first.signal_id, other_asset.signal_id)

    def test_reclassification_never_extends_expiry(self):
        early = self.build(result=result_for(STOCKS.asset_ref, "positive"),
                          created_at=CREATED).signal
        late = self.build(result=result_for(STOCKS.asset_ref, "positive"),
                          created_at=CREATED + timedelta(minutes=5)).signal
        self.assertEqual(early.expires_at, late.expires_at)
        self.assertEqual(early.available_at, CREATED)
        self.assertEqual(late.available_at, CREATED + timedelta(minutes=5))
        self.assertLessEqual(late.horizon_s, early.horizon_s)

    def test_stock_sell_without_position_is_rejected_as_no_trade(self):
        verdict = self.build(result=result_for(STOCKS.asset_ref, "negative"),
                             has_position=False)
        self.assertEqual(verdict.signal.direction, "sell")
        self.assertIn(CODE_INSUFFICIENT_INVENTORY, verdict.signal.no_trade_reasons)

    def test_stock_sell_with_position_passes(self):
        verdict = self.build(result=result_for(STOCKS.asset_ref, "negative"),
                             has_position=True)
        self.assertEqual(verdict.signal.direction, "sell")
        self.assertFalse(verdict.signal.no_trade_reasons)

    def test_fx_jpy_positive_maps_to_sell(self):
        verdict = self.build(result=result_for(FX.asset_ref, "positive"),
                             asset=FX, currency_mention="JPY")
        self.assertEqual(verdict.signal.direction, "sell")
        self.assertFalse(verdict.signal.no_trade_reasons)

    def test_fx_without_currency_mention_has_no_direction(self):
        verdict = self.build(result=result_for(FX.asset_ref, "positive"),
                             asset=FX, currency_mention=None)
        self.assertEqual(verdict.signal.direction, "neutral")
        self.assertIn(CODE_NO_DIRECTION, verdict.signal.no_trade_reasons)

    def test_uncertain_polarity_is_neutral_no_trade(self):
        verdict = self.build(result=result_for(STOCKS.asset_ref, "uncertain"))
        self.assertEqual(verdict.signal.direction, "neutral")
        self.assertIn(CODE_NO_DIRECTION, verdict.signal.no_trade_reasons)

    def test_unmapped_asset_fails_closed(self):
        verdict = self.build(result=result_for("stocks:XTKS:OTHER", "positive"))
        self.assertIsNone(verdict.signal)
        self.assertEqual(verdict.code, CODE_ASSET_UNMAPPED)

    def test_expired_evidence_is_not_available(self):
        verdict = self.build(
            result=result_for(STOCKS.asset_ref, "positive"),
            created_at=ELIGIBLE + timedelta(seconds=1))
        self.assertIsNone(verdict.signal)
        self.assertEqual(verdict.code, CODE_EVIDENCE_NOT_AVAILABLE)

    def test_window_shorter_than_a_second_expires(self):
        policy = FixtureSignalPolicy(policy_cap_s=1)
        builder = SignalBuilder(policy)
        verdict = builder.build(result=result_for(STOCKS.asset_ref, "positive"),
                                evidence=[evidence()], asset=STOCKS,
                                classification_ref="classification_demo_001",
                                created_at=AVAILABLE + timedelta(microseconds=500))
        self.assertIsNone(verdict.signal)
        self.assertEqual(verdict.code, CODE_SIGNAL_EXPIRED)

    def test_reason_is_fixed_host_template(self):
        verdict = self.build(result=result_for(STOCKS.asset_ref, "positive"))
        self.assertLessEqual(len(verdict.signal.reason), 240)
        self.assertIn("fixture signal policy", verdict.signal.reason)
        self.assertNotIn("架空企業", verdict.signal.reason)


class NewsSignalContractTests(unittest.TestCase):
    def setUp(self):
        self.builder = SignalBuilder()
        self.signal = self.builder.build(
            result=result_for(STOCKS.asset_ref, "positive"),
            evidence=[evidence()], asset=STOCKS,
            classification_ref="classification_demo_001",
            created_at=CREATED).signal
        self.assertIsNotNone(self.signal)

    def test_roundtrip_decode(self):
        decoded = decode_news_signal(self.signal.to_json())
        self.assertEqual(decoded.to_json(), self.signal.to_json())
        self.assertEqual(decoded.signal_id, self.signal.signal_id)

    def test_unknown_key_rejected(self):
        payload = self.signal.to_json()
        payload["quantity"] = "100"
        with self.assertRaises(SignalError) as caught:
            decode_news_signal(payload)
        self.assertEqual(caught.exception.code, "UNKNOWN_KEY")

    def test_live_mode_rejected(self):
        payload = self.signal.to_json()
        payload["mode"] = "live"
        with self.assertRaises(SignalError) as caught:
            decode_news_signal(payload)
        self.assertEqual(caught.exception.code, "NON_PAPER_MODE")

    def test_live_eligible_true_rejected(self):
        payload = self.signal.to_json()
        payload["live_eligible"] = True
        with self.assertRaises(SignalError) as caught:
            decode_news_signal(payload)
        self.assertEqual(caught.exception.code,
                         "LIVE_ELIGIBILITY_FORBIDDEN")

    def test_horizon_bounds(self):
        payload = self.signal.to_json()
        payload["expected_horizon"] = {"unit": "seconds", "value": 86401}
        with self.assertRaises(SignalError):
            decode_news_signal(payload)
        payload["expected_horizon"] = {"unit": "minutes", "value": 900}
        with self.assertRaises(SignalError):
            decode_news_signal(payload)

    def test_horizon_must_fit_window(self):
        payload = self.signal.to_json()
        payload["expected_horizon"] = {"unit": "seconds", "value": 900}
        with self.assertRaises(SignalError) as caught:
            decode_news_signal(payload)
        self.assertEqual(caught.exception.code, "VALUE_OUT_OF_RANGE")

    def test_expired_signal_rejected(self):
        payload = self.signal.to_json()
        payload["expires_at"] = payload["available_at"]
        with self.assertRaises(SignalError) as caught:
            decode_news_signal(payload)
        self.assertEqual(caught.exception.code, CODE_SIGNAL_EXPIRED)

    def test_duplicate_no_trade_reasons_rejected(self):
        payload = self.signal.to_json()
        payload["no_trade_reasons"] = ["NO_DIRECTION", "NO_DIRECTION"]
        with self.assertRaises(SignalError):
            decode_news_signal(payload)

    def test_directions_are_the_frozen_enum(self):
        self.assertEqual(DIRECTIONS, frozenset({"buy", "sell", "neutral"}))


if __name__ == "__main__":
    unittest.main()
