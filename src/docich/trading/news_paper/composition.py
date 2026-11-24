"""Price/news composition for the news-linked PAPER lane (N03, #779).

Contract sources (design frozen in #775 as v2 + v3 + v4):

* N00 v2 §1.2/§1.3 — decision-time re-verification of every evidence item
  (expiry / revocation) before a candidate may pass.
* N00 v2 §3.4/§3.6 — COMPOSE/DECIDE held codes, the fixed check order and
  the only three re-evaluation conditions (new eligible price, new
  in-window evidence, mapping resolved).
* Parent #769 B3 — A = price-only baseline (fully independent of news),
  B = price + news filter (never amplifies), C = news-only arm; conflict,
  contradiction, low confidence/materiality and missing news are held with
  fixed codes; RSS/LLM failures stop new news candidates but never touch
  the price baseline arm (step 9).

This module never sizes: no quantity, notional, leverage, funds or order
field exists in any output.  Final quantities, reservations, acceptance
and fills belong to N05/N06.  Volume/consensus values are not inputs at
all, so a missing volume can never be fabricated as zero or backfilled.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Protocol, Sequence

from .execution import LIVE_ELIGIBLE, MODE
from .signals import EvidenceRecord, NewsSignal

SCHEMA_VERSION = 1
COMPOSER_POLICY_VERSION = "fixture-composer-v1"
RISK_POLICY_VERSION = "fixture-risk-v1"

CODE_NEWS_PRICE_AGREE = "NEWS_PRICE_AGREE"
CODE_NEWS_ONLY_ELIGIBLE = "NEWS_ONLY_ELIGIBLE"
CODE_NEWS_PRICE_CONFLICT = "NEWS_PRICE_CONFLICT"
CODE_NEWS_CONFLICT = "NEWS_CONFLICT"
CODE_NEWS_DUPLICATE = "NEWS_DUPLICATE"
CODE_EVENT_ALREADY_USED = "EVENT_ALREADY_USED"
CODE_COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
CODE_NEWS_UNAVAILABLE = "NEWS_UNAVAILABLE"
CODE_NO_DIRECTION = "NO_DIRECTION"
CODE_SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
CODE_EVIDENCE_NOT_AVAILABLE = "EVIDENCE_NOT_AVAILABLE"
CODE_CLASSIFIER_TIMEOUT = "CLASSIFIER_TIMEOUT"
CODE_CLASSIFIER_UNAVAILABLE = "CLASSIFIER_UNAVAILABLE"
CODE_CLASSIFIER_OUTPUT_INVALID = "CLASSIFIER_OUTPUT_INVALID"
CODE_CLASSIFIER_LOW_CONFIDENCE = "CLASSIFIER_LOW_CONFIDENCE"
CODE_MATERIALITY_LOW = "MATERIALITY_LOW"

ARM_A = "A"  # price-only baseline
ARM_B = "B"  # price + news filter
ARM_C = "C"  # news-only (independent policy)

CLASSIFIER_OK = "ok"

# N00 v2 §3.6: the only three permitted re-evaluation justifications.
REEVALUATION_REASONS = frozenset({
    "new_price_observation", "new_evidence", "mapping_resolved",
})


class CompositionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FixtureComposerPolicy:
    """Fixture-only thresholds; no approved production value is used."""

    policy_version: str = COMPOSER_POLICY_VERSION
    min_confidence: float = 0.60
    min_materiality: float = 0.50
    cooldown_s: int = 1800


@dataclass(frozen=True)
class PriceCandidate:
    """Price-arm proposal (fixture input). Direction only — never a size."""

    asset_ref: str
    direction: str  # "buy" | "sell" | "none"
    ref: str
    policy_version: str

    def validate(self) -> None:
        if self.direction not in ("buy", "sell", "none"):
            raise CompositionError("VALUE_OUT_OF_RANGE")
        if not self.asset_ref or not self.ref or not self.policy_version:
            raise CompositionError("VALUE_OUT_OF_RANGE")


@dataclass(frozen=True)
class CompositionOutcome:
    """One arm's outcome for one asset. Contains no sizing fields at all."""

    arm: str
    scenario_id: str
    asset_ref: str
    status: str  # "candidate" | "held"
    reason_codes: frozenset[str]
    price_ref: str | None
    signal_ref: str | None
    mode: str
    live_eligible: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "arm": self.arm,
            "scenario_id": self.scenario_id,
            "asset_ref": self.asset_ref,
            "status": self.status,
            "reason_codes": sorted(self.reason_codes),
            "price_ref": self.price_ref,
            "signal_ref": self.signal_ref,
            "mode": self.mode,
            "live_eligible": self.live_eligible,
        }


@dataclass(frozen=True)
class CompositionResult:
    outcomes: tuple[CompositionOutcome, ...]

    def to_json(self) -> dict[str, Any]:
        return {"outcomes": [o.to_json() for o in self.outcomes]}


class EventUseStore(Protocol):
    """``event_use_id = (scenario, event_group, asset)`` consumption store.

    Cooldown and dedupe keys are scenario-scoped as well: the A/B/C arms are
    independent counterfactuals evaluated on the same inputs, so one arm's
    candidate must never block another arm (parent B3/E: arms do not steal
    liquidity or state from each other). Durable persistence is N05's job.
    """

    def used(self, key: tuple[str, str, str]) -> bool: ...
    def record(self, key: tuple[str, str, str]) -> None: ...
    def cooldown_active(self, scenario_id: str, asset_ref: str,
                        now: datetime) -> bool: ...
    def record_cooldown(self, scenario_id: str, asset_ref: str,
                        now: datetime) -> None: ...
    def seen_dedupe_key(self, key: str) -> bool: ...
    def record_dedupe_key(self, key: str) -> None: ...


class InMemoryEventUseStore:
    """Fixture store. Durable persistence is N05's ledger, not this module."""

    def __init__(self, cooldown_s: int = 1800) -> None:
        self._used: set[tuple[str, str, str]] = set()
        self._cooldown: dict[tuple[str, str], datetime] = {}
        self._dedupe: set[str] = set()
        self._cooldown_s = cooldown_s

    def used(self, key: tuple[str, str, str]) -> bool:
        return key in self._used

    def record(self, key: tuple[str, str, str]) -> None:
        self._used.add(key)

    def cooldown_active(self, scenario_id: str, asset_ref: str,
                        now: datetime) -> bool:
        until = self._cooldown.get((scenario_id, asset_ref))
        return until is not None and now < until

    def record_cooldown(self, scenario_id: str, asset_ref: str,
                        now: datetime) -> None:
        self._cooldown[(scenario_id, asset_ref)] = now + timedelta(
            seconds=self._cooldown_s)

    def seen_dedupe_key(self, key: str) -> bool:
        return key in self._dedupe

    def record_dedupe_key(self, key: str) -> None:
        self._dedupe.add(key)


@dataclass(frozen=True)
class JustifiedReevaluation:
    """N00 v2 §3.6 — one of the three fixed conditions, with the new ref."""

    reason: str  # one of REEVALUATION_REASONS
    ref: str     # new price observation id / new evidence id / mapping version

    def validate(self) -> None:
        if self.reason not in REEVALUATION_REASONS or not self.ref:
            raise CompositionError("VALUE_OUT_OF_RANGE")


def reevaluation_permitted(reevaluation: JustifiedReevaluation | None) -> bool:
    """Unconditional per-cycle re-evaluation is prohibited."""
    if reevaluation is None:
        return False
    reevaluation.validate()
    return True


def _dedupe_key(scenario_id: str, signal: NewsSignal,
                evidence: Sequence[EvidenceRecord]) -> str:
    """Scenario-scoped: identical evidence re-delivered inside one scenario."""
    digests = sorted(item.content_digest for item in evidence)
    return hashlib.sha256(
        f"{scenario_id}:{':'.join(digests)}".encode("utf-8")).hexdigest()


def _verify_evidence_at_decision(signal: NewsSignal,
                                  evidence: Sequence[EvidenceRecord],
                                  now: datetime) -> str | None:
    """Decision-time re-verification (N00 v2 §1.2): every item still valid."""
    records = {item.observation_id: item for item in evidence}
    for ref in signal.evidence_refs:
        record = records.get(ref)
        if record is None or record.event_group_id != signal.event_group_id:
            return CODE_EVIDENCE_NOT_AVAILABLE
        if record.eligible_until <= now:
            return CODE_EVIDENCE_NOT_AVAILABLE
    return None


def _news_gates(signal: NewsSignal | None,
                signals: Sequence[NewsSignal],
                evidence: Sequence[EvidenceRecord],
                now: datetime) -> frozenset[str] | None:
    """Shared news-side gates for arms B and C. None means the gates passed."""
    directions = {item.direction for item in signals if item.direction != "neutral"}
    if len(directions) > 1:
        return frozenset({CODE_NEWS_CONFLICT})
    if signal is None:
        return frozenset({CODE_NEWS_UNAVAILABLE})
    if signal.expires_at <= now:
        return frozenset({CODE_SIGNAL_EXPIRED})
    failure = _verify_evidence_at_decision(signal, evidence, now)
    if failure is not None:
        return frozenset({failure})
    if signal.direction == "neutral" or signal.no_trade_reasons:
        return frozenset({CODE_NO_DIRECTION} | set(signal.no_trade_reasons))
    return None


def _threshold_gates(signal: NewsSignal,
                     policy: FixtureComposerPolicy) -> frozenset[str] | None:
    codes: set[str] = set()
    if signal.confidence < policy.min_confidence:
        codes.add(CODE_CLASSIFIER_LOW_CONFIDENCE)
    if signal.materiality < policy.min_materiality:
        codes.add(CODE_MATERIALITY_LOW)
    return frozenset(codes) or None


def _use_gates(signal: NewsSignal, evidence: Sequence[EvidenceRecord],
               store: EventUseStore, scenario_id: str, now: datetime,
               ) -> frozenset[str] | None:
    """Fixed check order: event-use first (permanent), then the asset
    cooldown inside this scenario, then scenario-scoped dedupe."""
    key = (scenario_id, signal.event_group_id, _asset_of(signal))
    if store.used(key):
        return frozenset({CODE_EVENT_ALREADY_USED})
    if store.cooldown_active(scenario_id, _asset_of(signal), now):
        return frozenset({CODE_COOLDOWN_ACTIVE})
    if store.seen_dedupe_key(_dedupe_key(scenario_id, signal, evidence)):
        return frozenset({CODE_NEWS_DUPLICATE})
    return None


def _asset_of(signal: NewsSignal) -> str:
    return signal.asset.asset_ref


def compose_arm_a(price: PriceCandidate, scenario_id: str) -> CompositionOutcome:
    """Arm A: price-only baseline. Completely independent of news state."""
    price.validate()
    if price.direction == "none":
        return CompositionOutcome(
            arm=ARM_A, scenario_id=scenario_id, asset_ref=price.asset_ref,
            status="held", reason_codes=frozenset({CODE_NO_DIRECTION}),
            price_ref=price.ref, signal_ref=None, mode=MODE,
            live_eligible=LIVE_ELIGIBLE,
        )
    return CompositionOutcome(
        arm=ARM_A, scenario_id=scenario_id, asset_ref=price.asset_ref,
        status="candidate", reason_codes=frozenset(),
        price_ref=price.ref, signal_ref=None, mode=MODE,
        live_eligible=LIVE_ELIGIBLE,
    )


def compose_arm_b(price: PriceCandidate, signal: NewsSignal | None,
                  signals: Sequence[NewsSignal],
                  evidence: Sequence[EvidenceRecord],
                  store: EventUseStore, scenario_id: str, now: datetime,
                  policy: FixtureComposerPolicy | None = None,
                  classifier_status: str = CLASSIFIER_OK,
                  ) -> CompositionOutcome:
    """Arm B: price baseline filtered by news. Never amplifies: the price
    candidate's size (which this module never sees) passes through or the
    entry is held — there is no third outcome."""
    price.validate()
    effective = policy or FixtureComposerPolicy()
    if classifier_status != CLASSIFIER_OK:
        code = {
            "timeout": CODE_CLASSIFIER_TIMEOUT,
            "unavailable": CODE_CLASSIFIER_UNAVAILABLE,
        }.get(classifier_status, CODE_CLASSIFIER_OUTPUT_INVALID)
        return _held(ARM_B, scenario_id, price.asset_ref, price.ref, signal, code)
    gates = _news_gates(signal, signals, evidence, now)
    if gates is not None:
        return _held(ARM_B, scenario_id, price.asset_ref, price.ref, signal, gates)
    assert signal is not None
    if price.direction == "none":
        return _held(ARM_B, scenario_id, price.asset_ref, price.ref, signal,
                     CODE_NO_DIRECTION)
    if signal.direction != price.direction:
        return _held(ARM_B, scenario_id, price.asset_ref, price.ref, signal,
                      CODE_NEWS_PRICE_CONFLICT)
    thresholds = _threshold_gates(signal, effective)
    if thresholds is not None:
        return _held(ARM_B, scenario_id, price.asset_ref, price.ref, signal,
                     thresholds)
    uses = _use_gates(signal, evidence, store, scenario_id, now)
    if uses is not None:
        return _held(ARM_B, scenario_id, price.asset_ref, price.ref, signal, uses)
    store.record((scenario_id, signal.event_group_id, _asset_of(signal)))
    store.record_cooldown(scenario_id, _asset_of(signal), now)
    store.record_dedupe_key(_dedupe_key(scenario_id, signal, evidence))
    return CompositionOutcome(
        arm=ARM_B, scenario_id=scenario_id, asset_ref=price.asset_ref,
        status="candidate",
        reason_codes=frozenset({CODE_NEWS_PRICE_AGREE}),
        price_ref=price.ref, signal_ref=signal.signal_id, mode=MODE,
        live_eligible=LIVE_ELIGIBLE,
    )


def compose_arm_c(signal: NewsSignal | None, signals: Sequence[NewsSignal],
                  evidence: Sequence[EvidenceRecord],
                  store: EventUseStore, scenario_id: str, now: datetime,
                  policy: FixtureComposerPolicy | None = None,
                  classifier_status: str = CLASSIFIER_OK,
                  ) -> CompositionOutcome:
    """Arm C: news-only candidate under its own independent policy."""
    asset_ref = signal.asset.asset_ref if signal is not None else _common_asset(signals)
    effective = policy or FixtureComposerPolicy()
    if classifier_status != CLASSIFIER_OK:
        code = {
            "timeout": CODE_CLASSIFIER_TIMEOUT,
            "unavailable": CODE_CLASSIFIER_UNAVAILABLE,
        }.get(classifier_status, CODE_CLASSIFIER_OUTPUT_INVALID)
        return _held(ARM_C, scenario_id, asset_ref, None, signal, code)
    gates = _news_gates(signal, signals, evidence, now)
    if gates is not None:
        return _held(ARM_C, scenario_id, asset_ref, None, signal, gates)
    assert signal is not None
    thresholds = _threshold_gates(signal, effective)
    if thresholds is not None:
        return _held(ARM_C, scenario_id, asset_ref, None, signal, thresholds)
    uses = _use_gates(signal, evidence, store, scenario_id, now)
    if uses is not None:
        return _held(ARM_C, scenario_id, asset_ref, None, signal, uses)
    store.record((scenario_id, signal.event_group_id, _asset_of(signal)))
    store.record_cooldown(scenario_id, _asset_of(signal), now)
    store.record_dedupe_key(_dedupe_key(scenario_id, signal, evidence))
    return CompositionOutcome(
        arm=ARM_C, scenario_id=scenario_id, asset_ref=asset_ref,
        status="candidate",
        reason_codes=frozenset({CODE_NEWS_ONLY_ELIGIBLE}),
        price_ref=None, signal_ref=signal.signal_id, mode=MODE,
        live_eligible=LIVE_ELIGIBLE,
    )


def _common_asset(signals: Sequence[NewsSignal]) -> str:
    for item in signals:
        return item.asset.asset_ref
    return "unknown"


def _held(arm: str, scenario_id: str, asset_ref: str, price_ref: str | None,
          signal: NewsSignal | None,
          codes: Iterable[str] | str) -> CompositionOutcome:
    if isinstance(codes, str):
        codes = (codes,)
    return CompositionOutcome(
        arm=arm, scenario_id=scenario_id, asset_ref=asset_ref,
        status="held", reason_codes=frozenset(codes),
        price_ref=price_ref, signal_ref=signal.signal_id if signal else None,
        mode=MODE, live_eligible=LIVE_ELIGIBLE,
    )


def compose(price: PriceCandidate,
            signal: NewsSignal | None,
            signals: Sequence[NewsSignal] | None,
            evidence: Sequence[EvidenceRecord],
            store: EventUseStore,
            *, scenario_id: str, now: datetime,
            policy: FixtureComposerPolicy | None = None,
            classifier_status: str = CLASSIFIER_OK) -> CompositionResult:
    """Evaluate arms A/B/C for one price candidate in one pass.

    News failures (timeout/unavailable/invalid) hold B and C but never
    change arm A. A held re-evaluation needs one of the three fixed
    justifications (see :func:`reevaluation_permitted`) and never extends
    the original signal expiry.
    """
    all_signals: list[NewsSignal] = list(signals or [])
    if signal is not None and signal not in all_signals:
        all_signals = [signal] + all_signals
    outcomes = (
        compose_arm_a(price, scenario_id),
        compose_arm_b(price, signal, all_signals, evidence, store,
                       scenario_id, now, policy, classifier_status),
        compose_arm_c(signal, all_signals, evidence, store,
                      scenario_id, now, policy, classifier_status),
    )
    return CompositionResult(tuple(outcomes))


__all__ = [
    "ARM_A", "ARM_B", "ARM_C", "CLASSIFIER_OK", "CODE_CLASSIFIER_LOW_CONFIDENCE",
    "CODE_CLASSIFIER_OUTPUT_INVALID", "CODE_CLASSIFIER_TIMEOUT",
    "CODE_CLASSIFIER_UNAVAILABLE", "CODE_COOLDOWN_ACTIVE",
    "CODE_EVENT_ALREADY_USED", "CODE_EVIDENCE_NOT_AVAILABLE",
    "CODE_MATERIALITY_LOW", "CODE_NEWS_CONFLICT", "CODE_NEWS_DUPLICATE",
    "CODE_NEWS_ONLY_ELIGIBLE", "CODE_NEWS_PRICE_AGREE",
    "CODE_NEWS_PRICE_CONFLICT", "CODE_NEWS_UNAVAILABLE", "CODE_NO_DIRECTION",
    "CODE_SIGNAL_EXPIRED", "CompositionError", "CompositionOutcome",
    "CompositionResult", "FixtureComposerPolicy", "InMemoryEventUseStore",
    "JustifiedReevaluation", "PriceCandidate", "REEVALUATION_REASONS",
    "reevaluation_permitted", "compose", "compose_arm_a", "compose_arm_b",
    "compose_arm_c", "COMPOSER_POLICY_VERSION", "RISK_POLICY_VERSION",
    "SCHEMA_VERSION",
]
