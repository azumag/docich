"""NewsSignal builder for the news-linked PAPER lane (N03, #779).

Contract sources (design frozen in #775 as v2 + v3 + v4):

* N00 v2 §1 — ``signal.expires_at = min(all evidence eligible_until,
  event TTL from published_at, available_at-anchored cap, policy cap)``.
  The host computes the expiry; a classifier never sets it, and a
  re-classification at a later time must not extend it (every cap is
  anchored to immutable evidence timestamps, never to the build clock).
* N00 v2 §2.4 — NewsSignal v1 key set, invariants and reject conditions.
* Parent #769 D4 (NewsSignal), B3 (stock sell = reduce-only, explicit FX
  base/quote direction, no automatic inversion of ambiguous material).

FX direction needs an explicit currency attribution from the pre-verified
mapping layer (out-of-band from the strict classification schema, see
``classification.RuleClassification.currency_mentions``).  Polarity alone is
never interpreted for FX: a missing or ambiguous currency mention yields
``NO_DIRECTION`` (fail-closed), and a quote-currency positive (円材料) maps to
selling the base of e.g. USD_JPY — never to buying it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .classification import (
    _fraction,
    _hex64,
    _id,
    _int,
    _text,
    _unique_id_list,
    _version,
    canonical,
    format_ts,
    parse_ts,
)
from .execution import LIVE_ELIGIBLE, MODE

SCHEMA_VERSION = 1
SIGNAL_POLICY_VERSION = "fixture-signal-policy-v1"

CODE_NON_PAPER_MODE = "NON_PAPER_MODE"
CODE_LIVE_ELIGIBILITY_FORBIDDEN = "LIVE_ELIGIBILITY_FORBIDDEN"
CODE_VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
CODE_TYPE_INVALID = "TYPE_INVALID"
CODE_UNKNOWN_KEY = "UNKNOWN_KEY"
CODE_PAYLOAD_LIMIT = "PAYLOAD_LIMIT"
CODE_NO_DIRECTION = "NO_DIRECTION"
CODE_INSUFFICIENT_INVENTORY = "INSUFFICIENT_INVENTORY"
CODE_EVIDENCE_NOT_AVAILABLE = "EVIDENCE_NOT_AVAILABLE"
CODE_EVIDENCE_REVOKED = "EVIDENCE_REVOKED"
CODE_ASSET_UNMAPPED = "ASSET_UNMAPPED"
CODE_SIGNAL_EXPIRED = "SIGNAL_EXPIRED"

DIRECTIONS = frozenset({"buy", "sell", "neutral"})
MARKETS = frozenset({"stocks", "fx"})
MAX_NO_TRADE_REASONS = 16
MAX_EVIDENCE_REFS = 20
REASON_MAX_CHARS = 240

FX_PAIRS: Mapping[str, tuple[str, str]] = {
    "USD_JPY": ("USD", "JPY"),  # (base, quote)
}

SIGNAL_KEYS = frozenset({
    "schema_version", "signal_id", "mode", "live_eligible", "asset",
    "direction", "confidence", "materiality", "event_type",
    "expected_horizon", "created_at", "available_at", "expires_at",
    "event_group_id", "evidence_refs", "classification_ref", "policy_version",
    "reason", "no_trade_reasons",
})
ASSET_KEYS = frozenset({"market", "symbol", "venue"})
HORIZON_KEYS = frozenset({"unit", "value"})

_SYMBOL_RE = re.compile(r"\A[A-Z0-9][A-Z0-9_]{0,15}\Z")


class SignalError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# fixture signal policy (fixture-only values; approved values stay unset)
# ---------------------------------------------------------------------------

FIXTURE_EVENT_TTL_S: Mapping[str, int] = {
    "guidance_revision": 900,
    "earnings": 1800,
    "monetary_policy": 1800,
    "macro_release": 1800,
    "capital_action": 3600,
    "regulatory": 3600,
    "operational_incident": 900,
    "other": 900,
    "unknown": 600,
}


@dataclass(frozen=True)
class FixtureSignalPolicy:
    policy_version: str = SIGNAL_POLICY_VERSION
    horizon_s: int = 900
    available_cap_s: int = 3600   # available_at-anchored cap
    policy_cap_s: int = 86400    # absolute ceiling, also available-anchored

    def event_ttl_s(self, event_type: str) -> int:
        return FIXTURE_EVENT_TTL_S.get(event_type, FIXTURE_EVENT_TTL_S["unknown"])


# ---------------------------------------------------------------------------
# evidence input (NewsObservation projection; N02 owns the full record)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRecord:
    observation_id: str
    content_digest: str
    published_at: datetime
    available_at: datetime
    eligible_until: datetime
    event_group_id: str

    def validate(self) -> None:
        _id(self.observation_id, "observation_id")
        _hex64(self.content_digest, "content_digest")
        _id(self.event_group_id, "event_group_id")
        if not (self.published_at <= self.available_at <= self.eligible_until):
            raise SignalError(CODE_VALUE_OUT_OF_RANGE)


@dataclass(frozen=True)
class SignalAsset:
    market: str
    symbol: str
    venue: str

    @property
    def asset_ref(self) -> str:
        return f"{self.market}:{self.venue}:{self.symbol}"

    def validate(self) -> None:
        if self.market not in MARKETS:
            raise SignalError(CODE_VALUE_OUT_OF_RANGE)
        if not _SYMBOL_RE.match(self.symbol) or not _SYMBOL_RE.match(self.venue):
            raise SignalError(CODE_VALUE_OUT_OF_RANGE)


# ---------------------------------------------------------------------------
# direction rules (host-side fixed rules; never classifier output)
# ---------------------------------------------------------------------------


def stock_direction(polarity: str) -> str:
    if polarity == "positive":
        return "buy"
    if polarity == "negative":
        return "sell"
    return "neutral"  # uncertain / neutral


def fx_direction(pair_symbol: str, polarity: str,
                 currency_mention: str | None) -> str:
    """Explicit base/quote mapping. Ambiguity fails closed to ``neutral``.

    A positive quote-currency material (円材料) means *sell the base* of the
    pair (e.g. sell USD_JPY); a positive base-currency material means buy.
    No mention, an unknown mention, or an ambiguous mention never auto-flips.
    """
    pair = FX_PAIRS.get(pair_symbol)
    if pair is None or currency_mention is None or polarity not in ("positive", "negative"):
        return "neutral"
    base, quote = pair
    if currency_mention == base:
        forward = "buy"
    elif currency_mention == quote:
        forward = "sell"  # buying the quote currency = selling the base
    else:
        return "neutral"
    if polarity == "positive":
        return forward
    return "sell" if forward == "buy" else "buy"


def stock_sell_gate(direction: str, market: str, has_position: bool) -> tuple[str, ...]:
    """Stock sell is reduce-only (parent B3): no position -> no new order."""
    if market == "stocks" and direction == "sell" and not has_position:
        return (CODE_INSUFFICIENT_INVENTORY,)
    return ()


# ---------------------------------------------------------------------------
# expiry (N00 v2 §1: min formula, all caps evidence-anchored)
# ---------------------------------------------------------------------------


def compute_expires_at(evidence: Sequence[EvidenceRecord], event_type: str,
                        policy: FixtureSignalPolicy) -> datetime:
    """min(eligible_until, published+TTL, available+cap, available+policy cap).

    Anchored to immutable evidence timestamps only: a re-classification at a
    later time produces the exact same expiry.
    """
    if not evidence:
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    eligible_min = min(item.eligible_until for item in evidence)
    published_min = min(item.published_at for item in evidence)
    available_min = min(item.available_at for item in evidence)
    caps = [
        eligible_min,
        published_min + timedelta(seconds=policy.event_ttl_s(event_type)),
        available_min + timedelta(seconds=policy.available_cap_s),
        available_min + timedelta(seconds=policy.policy_cap_s),
    ]
    return min(caps)


# ---------------------------------------------------------------------------
# NewsSignal v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsSignal:
    signal_id: str
    asset: SignalAsset
    direction: str
    confidence: float
    materiality: float
    event_type: str
    horizon_s: int
    created_at: datetime
    available_at: datetime
    expires_at: datetime
    event_group_id: str
    evidence_refs: tuple[str, ...]
    classification_ref: str
    policy_version: str
    reason: str
    no_trade_reasons: frozenset[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "signal_id": self.signal_id,
            "mode": MODE,
            "live_eligible": LIVE_ELIGIBLE,
            "asset": {
                "market": self.asset.market,
                "symbol": self.asset.symbol,
                "venue": self.asset.venue,
            },
            "direction": self.direction,
            "confidence": self.confidence,
            "materiality": self.materiality,
            "event_type": self.event_type,
            "expected_horizon": {"unit": "seconds", "value": self.horizon_s},
            "created_at": format_ts(self.created_at),
            "available_at": format_ts(self.available_at),
            "expires_at": format_ts(self.expires_at),
            "event_group_id": self.event_group_id,
            "evidence_refs": list(self.evidence_refs),
            "classification_ref": self.classification_ref,
            "policy_version": self.policy_version,
            "reason": self.reason,
            "no_trade_reasons": sorted(self.no_trade_reasons),
        }


def decode_news_signal(payload: Mapping[str, Any]) -> NewsSignal:
    obj = dict(payload)
    expected = set(SIGNAL_KEYS)
    actual = set(obj)
    if actual != expected:
        if actual - expected:
            raise SignalError(CODE_UNKNOWN_KEY)
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    if obj["mode"] != MODE:
        raise SignalError(CODE_NON_PAPER_MODE)
    if obj["live_eligible"] is not True and obj["live_eligible"] is not False:
        raise SignalError(CODE_TYPE_INVALID)
    if obj["live_eligible"] is not LIVE_ELIGIBLE:
        raise SignalError(CODE_LIVE_ELIGIBILITY_FORBIDDEN)
    if _int(obj["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    raw_asset = obj["asset"]
    if not isinstance(raw_asset, dict) or set(raw_asset) != set(ASSET_KEYS):
        raise SignalError(CODE_UNKNOWN_KEY)
    asset = SignalAsset(
        market=raw_asset["market"],
        symbol=raw_asset["symbol"],
        venue=raw_asset["venue"],
    )
    asset.validate()
    direction = obj["direction"]
    if direction not in DIRECTIONS:
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    confidence = _fraction(obj["confidence"], "confidence")
    materiality = _fraction(obj["materiality"], "materiality")
    raw_horizon = obj["expected_horizon"]
    if not isinstance(raw_horizon, dict) or set(raw_horizon) != set(HORIZON_KEYS):
        raise SignalError(CODE_UNKNOWN_KEY)
    if raw_horizon["unit"] != "seconds":
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    horizon_s = _int(raw_horizon["value"], "value")
    if not (1 <= horizon_s <= 86400):
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    created_at = parse_ts(obj["created_at"], "created_at")
    available_at = parse_ts(obj["available_at"], "available_at")
    expires_at = parse_ts(obj["expires_at"], "expires_at")
    if not (available_at <= created_at < expires_at):
        raise SignalError(CODE_SIGNAL_EXPIRED)
    if created_at + timedelta(seconds=horizon_s) > expires_at:
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    reasons = obj["no_trade_reasons"]
    if not isinstance(reasons, list) or len(reasons) > MAX_NO_TRADE_REASONS:
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    for reason in reasons:
        if not isinstance(reason, str) or not reason or len(reason) > 64:
            raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    if len(set(reasons)) != len(reasons):
        raise SignalError(CODE_VALUE_OUT_OF_RANGE)
    reason_text = _text(obj["reason"], "reason", max_len=REASON_MAX_CHARS)
    return NewsSignal(
        signal_id=_id(obj["signal_id"], "signal_id"),
        asset=asset,
        direction=direction,
        confidence=confidence,
        materiality=materiality,
        event_type=obj["event_type"],
        horizon_s=horizon_s,
        created_at=created_at,
        available_at=available_at,
        expires_at=expires_at,
        event_group_id=_id(obj["event_group_id"], "event_group_id"),
        evidence_refs=_unique_id_list(obj["evidence_refs"], "evidence_refs",
                                      minimum=1, maximum=MAX_EVIDENCE_REFS),
        classification_ref=_id(obj["classification_ref"], "classification_ref"),
        policy_version=_version(obj["policy_version"], "policy_version"),
        reason=reason_text,
        no_trade_reasons=frozenset(reasons),
    )


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalBuildVerdict:
    signal: NewsSignal | None
    code: str | None          # fixed code when no signal was built
    no_trade_reasons: frozenset[str]


def _signal_id(binding: Mapping[str, Any]) -> str:
    return "sig_" + hashlib.sha256(canonical(binding)).hexdigest()[:16]


def _host_reason(event_type: str, direction: str, evidence_count: int,
                 policy_version: str) -> str:
    return (f"fixture signal policy {policy_version}: {event_type} {direction} "
            f"from {evidence_count} validated evidence item(s); "
            f"expiry=min(evidence eligible_until, ttl caps)")


class SignalBuilder:
    """Host-side signal assembly (parent #769 D4 + N00 v2 §1)."""

    def __init__(self, policy: FixtureSignalPolicy | None = None) -> None:
        self._policy = policy or FixtureSignalPolicy()

    @property
    def policy(self) -> FixtureSignalPolicy:
        return self._policy

    def build(self, *, result: Any, evidence: Sequence[EvidenceRecord],
              asset: SignalAsset, classification_ref: str, created_at: datetime,
              currency_mention: str | None = None,
              has_position: bool = False) -> SignalBuildVerdict:
        """Assemble one NewsSignal for one target asset.

        ``result`` is a validated :class:`ClassificationResult`; the impact
        row for ``asset`` provides polarity/confidence. Never amplifies,
        never sizes, never extends evidence expiry.
        """
        asset.validate()
        impact = None
        for candidate in result.impacts:
            if candidate.asset_ref == asset.asset_ref:
                impact = candidate
                break
        if impact is None:
            return SignalBuildVerdict(None, CODE_ASSET_UNMAPPED, frozenset())
        records = {item.observation_id: item for item in evidence}
        for ref in result.evidence_refs:
            if ref not in records:
                return SignalBuildVerdict(None, CODE_EVIDENCE_NOT_AVAILABLE,
                                          frozenset())
            records[ref].validate()
        if any(item.eligible_until <= created_at for item in evidence):
            return SignalBuildVerdict(None, CODE_EVIDENCE_NOT_AVAILABLE,
                                      frozenset())
        expires_at = compute_expires_at(evidence, result.event_type, self._policy)
        if expires_at <= created_at:
            return SignalBuildVerdict(None, CODE_SIGNAL_EXPIRED, frozenset())
        if asset.market == "stocks":
            direction = stock_direction(impact.polarity)
        else:
            direction = fx_direction(asset.symbol, impact.polarity,
                                     currency_mention)
        reasons: list[str] = []
        if direction == "neutral":
            reasons.append(CODE_NO_DIRECTION)
        reasons.extend(stock_sell_gate(direction, asset.market, has_position))
        window_s = int((expires_at - created_at).total_seconds())
        horizon_s = min(self._policy.horizon_s, window_s)
        if horizon_s < 1:
            return SignalBuildVerdict(None, CODE_SIGNAL_EXPIRED, frozenset())
        unique_reasons = tuple(dict.fromkeys(reasons))
        if len(unique_reasons) > MAX_NO_TRADE_REASONS:
            raise SignalError(CODE_VALUE_OUT_OF_RANGE)
        binding = {
            "evidence": [
                {"id": item.observation_id, "digest": item.content_digest}
                for item in sorted(evidence, key=lambda x: x.observation_id)
            ],
            "classification": {
                "request_id": result.request_id,
                "event_type": result.event_type,
                "materiality": result.materiality,
            },
            "policy_version": self._policy.policy_version,
            "asset_ref": asset.asset_ref,
            "event_group_id": evidence[0].event_group_id,
        }
        signal = NewsSignal(
            signal_id=_signal_id(binding),
            asset=asset,
            direction=direction,
            confidence=impact.confidence,
            materiality=result.materiality,
            event_type=result.event_type,
            horizon_s=horizon_s,
            created_at=created_at,
            available_at=created_at,
            expires_at=expires_at,
            event_group_id=evidence[0].event_group_id,
            evidence_refs=tuple(result.evidence_refs),
            classification_ref=classification_ref,
            policy_version=self._policy.policy_version,
            reason=_host_reason(result.event_type, direction, len(evidence),
                                self._policy.policy_version),
            no_trade_reasons=frozenset(unique_reasons),
        )
        return SignalBuildVerdict(signal, None, frozenset(unique_reasons))


__all__ = [
    "CODE_ASSET_UNMAPPED", "CODE_EVIDENCE_NOT_AVAILABLE", "CODE_EVIDENCE_REVOKED",
    "CODE_INSUFFICIENT_INVENTORY", "CODE_LIVE_ELIGIBILITY_FORBIDDEN",
    "CODE_NO_DIRECTION", "CODE_NON_PAPER_MODE", "CODE_PAYLOAD_LIMIT",
    "CODE_SIGNAL_EXPIRED", "CODE_TYPE_INVALID", "CODE_UNKNOWN_KEY",
    "CODE_VALUE_OUT_OF_RANGE", "DIRECTIONS", "EvidenceRecord",
    "FIXTURE_EVENT_TTL_S", "FX_PAIRS", "FixtureSignalPolicy", "MARKETS",
    "MAX_EVIDENCE_REFS", "MAX_NO_TRADE_REASONS", "MODE", "LIVE_ELIGIBLE",
    "NewsSignal", "REASON_MAX_CHARS", "SIGNAL_KEYS", "SIGNAL_POLICY_VERSION",
    "SCHEMA_VERSION", "SignalAsset", "SignalBuildVerdict", "SignalBuilder",
    "SignalError", "compute_expires_at", "decode_news_signal", "fx_direction",
    "stock_direction", "stock_sell_gate",
]
