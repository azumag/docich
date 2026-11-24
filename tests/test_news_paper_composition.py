"""N03 price/news composition tests (#779).

Verifies the A/B/C arms, the fixed held codes, event-use/cooldown/dedupe
consumption, the classifier-failure isolation of the price baseline, and
the "no sizing anywhere" contract (quantities belong to N05/N06).
"""
import dataclasses
import inspect
import unittest
from datetime import datetime, timedelta, timezone

from docich.trading.news_paper.classification import ClassificationResult, Impact
from docich.trading.news_paper.composition import (
    ARM_A,
    ARM_B,
    ARM_C,
    CLASSIFIER_OK,
    CODE_CLASSIFIER_TIMEOUT,
    CODE_CLASSIFIER_UNAVAILABLE,
    CODE_COOLDOWN_ACTIVE,
    CODE_EVENT_ALREADY_USED,
    CODE_MATERIALITY_LOW,
    CODE_NEWS_CONFLICT,
    CODE_NEWS_DUPLICATE,
    CODE_NEWS_PRICE_AGREE,
    CODE_NEWS_PRICE_CONFLICT,
    CODE_NEWS_ONLY_ELIGIBLE,
    CODE_NEWS_UNAVAILABLE,
    CODE_NO_DIRECTION,
    REEVALUATION_REASONS,
    CompositionOutcome,
    FixtureComposerPolicy,
    InMemoryEventUseStore,
    JustifiedReevaluation,
    PriceCandidate,
    _dedupe_key,
    compose,
    compose_arm_a,
    compose_arm_b,
    compose_arm_c,
    reevaluation_permitted,
)
from docich.trading.news_paper.signals import (
    CODE_INSUFFICIENT_INVENTORY,
    EvidenceRecord,
    NewsSignal,
    SignalAsset,
    SignalBuilder,
)

PUBLISHED = datetime(2026, 9, 18, 0, 10, 0, 0, tzinfo=timezone.utc)
AVAILABLE = datetime(2026, 9, 18, 0, 10, 2, 100000, tzinfo=timezone.utc)
ELIGIBLE = datetime(2026, 9, 18, 0, 25, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 18, 0, 10, 4, 0, tzinfo=timezone.utc)

STOCKS = SignalAsset(market="stocks", symbol="DEMO", venue="XTKS")
ASSET = STOCKS.asset_ref


def evidence(observation_id="news_demo_001_r1", digest="a" * 64,
             event_group="event_demo_001"):
    return EvidenceRecord(
        observation_id=observation_id,
        content_digest=digest,
        published_at=PUBLISHED,
        available_at=AVAILABLE,
        eligible_until=ELIGIBLE,
        event_group_id=event_group,
    )


def classification(direction_polarity, confidence=0.82, materiality=0.70,
                   evidence_ref="news_demo_001_r1"):
    return ClassificationResult(
        request_id="classify_demo_001",
        event_type="guidance_revision",
        impacts=(Impact(asset_ref=ASSET, polarity=direction_polarity,
                        confidence=confidence),),
        materiality=materiality,
        summary="fixture rule classification",
        uncertainty_codes=frozenset(),
        evidence_refs=(evidence_ref,),
    )


def build_signal(polarity="positive", confidence=0.82, materiality=0.70,
                 has_position=True, evidence_ref="news_demo_001_r1",
                 digest="a" * 64, event_group="event_demo_001") -> NewsSignal:
    builder = SignalBuilder()
    verdict = builder.build(
        result=classification(polarity, confidence, materiality, evidence_ref),
        evidence=[evidence(evidence_ref, digest, event_group)],
        asset=STOCKS,
        classification_ref="classification_demo_001", created_at=NOW,
        has_position=has_position)
    assert verdict.signal is not None
    return verdict.signal


def price(direction="buy", ref="price_demo_001"):
    return PriceCandidate(asset_ref=ASSET, direction=direction, ref=ref,
                          policy_version="fixture-price-v1")


class ArmATests(unittest.TestCase):
    def test_price_baseline_is_independent_of_news(self):
        for signal, status in ((None, "unavailable"), (build_signal(), "ok")):
            outcome = compose_arm_a(price(), "scenario_price_only")
            self.assertEqual(outcome.arm, ARM_A)
            self.assertEqual(outcome.status, "candidate")
            self.assertEqual(outcome.reason_codes, frozenset())
            self.assertEqual(outcome.mode, "paper")
            self.assertIs(outcome.live_eligible, False)

    def test_price_none_direction_holds(self):
        outcome = compose_arm_a(price(direction="none"), "scenario_price_only")
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes, frozenset({CODE_NO_DIRECTION}))


class ArmBTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryEventUseStore()
        self.policy = FixtureComposerPolicy()

    def compose(self, signal=None, signals=None, evidence_records=None,
                status=CLASSIFIER_OK, price_candidate=None, now=NOW):
        return compose_arm_b(
            price_candidate or price(),
            signal,
            list(signals or ([signal] if signal else [])),
            evidence_records if evidence_records is not None else [evidence()],
            self.store, "scenario_price_plus_news", now, self.policy, status)

    def test_agree_passes_without_amplification(self):
        outcome = self.compose(signal=build_signal())
        self.assertEqual(outcome.status, "candidate")
        self.assertEqual(outcome.reason_codes,
                         frozenset({CODE_NEWS_PRICE_AGREE}))
        field_names = {f.name for f in dataclasses.fields(CompositionOutcome)}
        for banned in ("quantity", "notional", "leverage", "funds", "order"):
            self.assertNotIn(banned, field_names)

    def test_opposite_direction_is_held(self):
        outcome = self.compose(signal=build_signal(polarity="negative"))
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes,
                         frozenset({CODE_NEWS_PRICE_CONFLICT}))

    def test_missing_news_is_a_separate_held_state(self):
        outcome = self.compose(signal=None, signals=[])
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes,
                         frozenset({CODE_NEWS_UNAVAILABLE}))

    def test_low_confidence_and_materiality_hold(self):
        outcome = self.compose(signal=build_signal(confidence=0.30))
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes,
                         frozenset({"CLASSIFIER_LOW_CONFIDENCE"}))
        outcome = self.compose(signal=build_signal(materiality=0.10))
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes, frozenset({CODE_MATERIALITY_LOW}))

    def test_neutral_signal_holds_with_no_direction(self):
        outcome = self.compose(signal=build_signal(polarity="uncertain"))
        self.assertEqual(outcome.status, "held")
        self.assertIn(CODE_NO_DIRECTION, outcome.reason_codes)

    def test_stock_sell_without_inventory_holds(self):
        outcome = self.compose(signal=build_signal(polarity="negative",
                                                   has_position=False),
                               price_candidate=price(direction="sell"))
        self.assertEqual(outcome.status, "held")
        self.assertIn(CODE_INSUFFICIENT_INVENTORY, outcome.reason_codes)

    def test_event_use_is_consumed_permanently(self):
        first = self.compose(signal=build_signal())
        self.assertEqual(first.status, "candidate")
        second = self.compose(signal=build_signal())
        self.assertEqual(second.status, "held")
        self.assertEqual(second.reason_codes,
                         frozenset({CODE_EVENT_ALREADY_USED}))

    def test_cooldown_blocks_same_asset_new_event(self):
        first = self.compose(signal=build_signal())
        self.assertEqual(first.status, "candidate")
        other = build_signal(evidence_ref="news_demo_002_r1", digest="b" * 64,
                             event_group="event_demo_002")
        second = self.compose(
            signal=other, signals=[other],
            evidence_records=[evidence("news_demo_002_r1", "b" * 64,
                                       "event_demo_002")])
        self.assertEqual(second.status, "held")
        self.assertEqual(second.reason_codes, frozenset({CODE_COOLDOWN_ACTIVE}))

    def test_arms_are_independent_counterfactuals(self):
        self.compose(signal=build_signal())
        # arm C runs its own scenario: arm B's cooldown must not block it.
        outcome = compose_arm_c(build_signal(), [build_signal()], [evidence()],
                                self.store, "scenario_news_only", NOW)
        self.assertEqual(outcome.status, "candidate")

    def test_redelivery_of_same_evidence_is_duplicate(self):
        signal = build_signal()
        first = self.compose(signal=signal)
        self.assertEqual(first.status, "candidate")
        fresh = InMemoryEventUseStore()  # event-use empty: dedupe fires alone
        fresh.record_dedupe_key(
            _dedupe_key("scenario_price_plus_news", signal, [evidence()]))
        outcome = compose_arm_b(price(), signal, [signal], [evidence()], fresh,
                                "scenario_price_plus_news", NOW, self.policy)
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes, frozenset({CODE_NEWS_DUPLICATE}))

    def test_contradictory_news_is_held(self):
        buy = build_signal(polarity="positive")
        sell = build_signal(polarity="negative")
        outcome = self.compose(signal=buy, signals=[buy, sell])
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes, frozenset({CODE_NEWS_CONFLICT}))

    def test_expired_signal_is_held(self):
        expired = build_signal()
        late = NOW + timedelta(minutes=30)
        outcome = self.compose(signal=expired, now=late)
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes, frozenset({"SIGNAL_EXPIRED"}))

    def test_decision_time_evidence_recheck(self):
        stale = [EvidenceRecord(
            observation_id="news_demo_001_r1",
            content_digest="a" * 64,
            published_at=PUBLISHED,
            available_at=AVAILABLE,
            eligible_until=NOW - timedelta(seconds=1),
            event_group_id="event_demo_001",
        )]
        outcome = self.compose(signal=build_signal(), evidence_records=stale)
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes,
                         frozenset({"EVIDENCE_NOT_AVAILABLE"}))


class ClassifierFailureIsolationTests(unittest.TestCase):
    """Step 9: news/classifier failures stop new news candidates but never
    touch the price baseline arm."""

    def setUp(self):
        self.store = InMemoryEventUseStore()

    def test_timeout_holds_news_arms_only(self):
        result = compose(price(), None, [], [evidence()], self.store,
                         scenario_id="scenario_price_plus_news", now=NOW,
                         classifier_status="timeout")
        arms = {outcome.arm: outcome for outcome in result.outcomes}
        self.assertEqual(arms[ARM_A].status, "candidate")
        self.assertEqual(arms[ARM_B].status, "held")
        self.assertEqual(arms[ARM_B].reason_codes,
                         frozenset({CODE_CLASSIFIER_TIMEOUT}))
        self.assertEqual(arms[ARM_C].status, "held")
        self.assertEqual(arms[ARM_C].reason_codes,
                         frozenset({CODE_CLASSIFIER_TIMEOUT}))

    def test_unavailable_holds_news_arms_only(self):
        result = compose(price(), None, [], [evidence()], self.store,
                         scenario_id="scenario_price_plus_news", now=NOW,
                         classifier_status="unavailable")
        arms = {outcome.arm: outcome for outcome in result.outcomes}
        self.assertEqual(arms[ARM_A].status, "candidate")
        self.assertEqual(arms[ARM_B].reason_codes,
                         frozenset({CODE_CLASSIFIER_UNAVAILABLE}))
        self.assertEqual(arms[ARM_C].reason_codes,
                         frozenset({CODE_CLASSIFIER_UNAVAILABLE}))


class ArmCTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryEventUseStore()

    def test_news_only_candidate_is_independent(self):
        outcome = compose_arm_c(build_signal(), [build_signal()], [evidence()],
                                self.store, "scenario_news_only", NOW)
        self.assertEqual(outcome.arm, ARM_C)
        self.assertEqual(outcome.status, "candidate")
        self.assertEqual(outcome.reason_codes,
                         frozenset({CODE_NEWS_ONLY_ELIGIBLE}))

    def test_news_only_respects_event_use(self):
        first = compose_arm_c(build_signal(), [build_signal()], [evidence()],
                              self.store, "scenario_news_only", NOW)
        second = compose_arm_c(build_signal(), [build_signal()], [evidence()],
                               self.store, "scenario_news_only", NOW)
        self.assertEqual(first.status, "candidate")
        self.assertEqual(second.status, "held")
        self.assertEqual(second.reason_codes,
                         frozenset({CODE_EVENT_ALREADY_USED}))


class ReevaluationGateTests(unittest.TestCase):
    def test_only_three_conditions_justify_reevaluation(self):
        self.assertEqual(REEVALUATION_REASONS,
                         frozenset({"new_price_observation", "new_evidence",
                                    "mapping_resolved"}))
        self.assertFalse(reevaluation_permitted(None))
        self.assertTrue(reevaluation_permitted(
            JustifiedReevaluation("new_price_observation", "quote_demo_121")))
        self.assertTrue(reevaluation_permitted(
            JustifiedReevaluation("new_evidence", "news_demo_002_r1")))
        self.assertTrue(reevaluation_permitted(
            JustifiedReevaluation("mapping_resolved", "fixture-map-v2")))
        from docich.trading.news_paper.composition import CompositionError
        with self.assertRaises(CompositionError):
            reevaluation_permitted(JustifiedReevaluation("every_cycle", "x"))

    def test_reevaluation_does_not_extend_expiry(self):
        signal = build_signal()
        original_expiry = signal.expires_at
        later = NOW + timedelta(minutes=24)
        outcome = compose_arm_b(price(direction="buy", ref="price_demo_002"),
                                signal, [signal],
                                [evidence()], InMemoryEventUseStore(),
                                "scenario_price_plus_news", later,
                                FixtureComposerPolicy())
        self.assertEqual(outcome.status, "held")
        self.assertEqual(outcome.reason_codes, frozenset({"SIGNAL_EXPIRED"}))
        self.assertEqual(signal.expires_at, original_expiry)


class NoSizingContractTests(unittest.TestCase):
    def test_composition_never_sizes(self):
        field_names = {f.name for f in dataclasses.fields(CompositionOutcome)}
        self.assertEqual(
            field_names,
            {"arm", "scenario_id", "asset_ref", "status", "reason_codes",
             "price_ref", "signal_ref", "mode", "live_eligible"})

    def test_compose_signature_has_no_volume_or_funds(self):
        signature = inspect.signature(compose)
        for banned in ("volume", "consensus", "quantity", "funds", "budget"):
            self.assertNotIn(banned, signature.parameters)

    def test_outputs_are_always_paper_and_not_live_eligible(self):
        result = compose(price(), build_signal(), [build_signal()],
                         [evidence()], InMemoryEventUseStore(),
                         scenario_id="scenario_price_plus_news", now=NOW)
        for outcome in result.outcomes:
            self.assertEqual(outcome.mode, "paper")
            self.assertIs(outcome.live_eligible, False)


if __name__ == "__main__":
    unittest.main()
