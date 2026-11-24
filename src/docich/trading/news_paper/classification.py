"""Bounded rule classification for the news-linked PAPER lane (N03, #779).

Contract sources (design frozen in #775 as v2 + v3 + v4):

* N00 v2 §2.2/§2.3 — ClassificationRequest v1 / ClassificationResult v1
  allowlists, bounded materials, fixed enums, host envelope.
* N00 v3 §2 + v4 §2 — mandatory ``request_id`` on responses and the
  terminal-state priority rules (completed+identical is a no-op; every other
  post-terminal response is ``CLASSIFIER_OUTPUT_INVALID``; retry needs a new
  ``request_id``).
* Parent #769 D3 (bounded inputs, no accounts/risk/persona/URLs in the
  request) and D10 (fixed diagnostic codes only).

The classifier is the Phase-1 "rule baseline": a fixed fixture rule table
(``fixture-classifier-v1``).  LLM adoption is NOT approved, therefore no LLM
or network call path exists in this module: the disabled gateway raises the
fixed code ``FEATURE_DISABLED`` and the LLM call count is structurally zero.
Unapproved judgment-table values never appear here; the only numeric policy
values live in :class:`FixtureClassifierPolicy` and are fixture-only.

Classification summaries are host templates.  Material text is never echoed
into a classification output, so injected instructions in news text cannot
change the output shape (they simply do not match the fixed rule table).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
CLASSIFIER_POLICY_VERSION = "fixture-classifier-v1"
CLASSIFIER_KIND = "rule"
LLM_ENABLED = False  # LLM adoption is not approved: call count must stay 0.

RESPONSE_MAX_BYTES = 16384
RESPONSE_MAX_DEPTH = 16
MAX_EVIDENCE = 20
MAX_MATERIALS = 20
MAX_IMPACTS = 20
MAX_UNCERTAINTY = 8

# Fixed diagnostic codes (parent #769 D10). Host code never puts free-form
# exception text into a code field.
CODE_INVALID_JSON = "INVALID_JSON"
CODE_DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
CODE_UNKNOWN_KEY = "UNKNOWN_KEY"
CODE_SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
CODE_TYPE_INVALID = "TYPE_INVALID"
CODE_NONFINITE_VALUE = "NONFINITE_VALUE"
CODE_VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
CODE_PAYLOAD_LIMIT = "PAYLOAD_LIMIT"
CODE_CLASSIFIER_OUTPUT_INVALID = "CLASSIFIER_OUTPUT_INVALID"
CODE_CLASSIFIER_TIMEOUT = "CLASSIFIER_TIMEOUT"
CODE_CLASSIFIER_UNAVAILABLE = "CLASSIFIER_UNAVAILABLE"
CODE_FEATURE_DISABLED = "FEATURE_DISABLED"

EVENT_TYPES = frozenset({
    "earnings", "guidance_revision", "capital_action", "regulatory",
    "monetary_policy", "macro_release", "operational_incident", "other",
    "unknown",
})
POLARITIES = frozenset({"positive", "negative", "uncertain", "neutral"})
UNCERTAINTY_CODES = frozenset({
    "PUBLISHED_AT_UNKNOWN", "TIMEZONE_ASSUMED", "SOURCE_UNVERIFIED",
    "DUPLICATE_CANDIDATE", "REVISED_AFTER", "AMBIGUOUS_ASSET_MAPPING",
    "CONFLICTING_SOURCES", "TRANSLATED_CONTENT", "INSUFFICIENT_CONTEXT",
    "UNKNOWN_EVENT_TYPE",
})

REQUEST_KEYS = frozenset({
    "schema_version", "request_id", "evidence_refs", "bounded_materials",
    "mapping_context", "cutoff_at", "classifier_policy_version",
})
RESULT_KEYS = frozenset({
    "schema_version", "request_id", "event_type", "impacts", "materiality",
    "summary", "uncertainty_codes", "evidence_refs",
})
MATERIAL_KEYS = frozenset({"evidence_ref", "title", "excerpt", "content_digest"})
MAPPING_KEYS = frozenset({"asset_ref", "mapping_version"})
IMPACT_KEYS = frozenset({"asset_ref", "polarity", "confidence"})

_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}\Z")
_VER_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}\Z")
_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_TS_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_ASSET_REF_RE = re.compile(
    r"\A(stocks|fx):[A-Z0-9][A-Z0-9_]{0,15}:[A-Z0-9][A-Z0-9_]{0,15}\Z")


class ClassifierError(ValueError):
    """Contract violation; carries a fixed diagnostic code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# primitive validators (strict; no coercion anywhere)
# ---------------------------------------------------------------------------


def _text(value: Any, name: str, *, max_len: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ClassifierError(CODE_TYPE_INVALID)
    try:
        value.encode("utf-8")  # rejects lone surrogates
    except UnicodeEncodeError:
        raise ClassifierError(CODE_TYPE_INVALID) from None
    if any(ord(ch) < 0x20 for ch in value):
        raise ClassifierError(CODE_TYPE_INVALID)
    if not value and not allow_empty:
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    if len(value) > max_len:
        raise ClassifierError(CODE_PAYLOAD_LIMIT)
    return value


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    return value


def _version(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _VER_RE.match(value):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    return value


def _hex64(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HEX64_RE.match(value):
        raise ClassifierError(CODE_TYPE_INVALID)
    return value


def parse_ts(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not _TS_RE.match(value):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    return moment.replace(tzinfo=timezone.utc)


def format_ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _fraction(value: Any, name: str) -> float:
    """confidence/materiality: strict JSON number, finite, 0..1, <=8 decimals."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClassifierError(CODE_TYPE_INVALID)
    if not math.isfinite(value):
        raise ClassifierError(CODE_NONFINITE_VALUE)
    try:
        dec = Decimal(str(value))
    except InvalidOperation:
        raise ClassifierError(CODE_NONFINITE_VALUE) from None
    if dec < 0 or dec > 1:
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    exponent = dec.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > 8:
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    return float(value)


def canonical(obj: Any) -> bytes:
    """Canonical byte form used for digests and identity comparisons."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def _reject_constant(token: str) -> Any:
    raise ClassifierError(CODE_NONFINITE_VALUE)


def _pairs_hook(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ClassifierError(CODE_DUPLICATE_JSON_KEY)
        seen[key] = value
    return seen


def _depth(obj: Any) -> int:
    if isinstance(obj, dict):
        return 1 + max((_depth(v) for v in obj.values()), default=0)
    if isinstance(obj, (list, tuple)):
        return 1 + max((_depth(v) for v in obj), default=0)
    return 1


def _reject_nonfinite(obj: Any) -> None:
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ClassifierError(CODE_NONFINITE_VALUE)
    if isinstance(obj, dict):
        for value in obj.values():
            _reject_nonfinite(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _reject_nonfinite(value)


def strict_json_loads(text: Any, *, max_bytes: int, max_depth: int) -> Any:
    """Strict JSON gate: size, duplicate keys, NaN/Infinity, depth, nonfinite."""
    if not isinstance(text, str):
        raise ClassifierError(CODE_TYPE_INVALID)
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ClassifierError(CODE_TYPE_INVALID) from None
    if len(raw) > max_bytes:
        raise ClassifierError(CODE_PAYLOAD_LIMIT)
    try:
        obj = json.loads(text, parse_constant=_reject_constant,
                         object_pairs_hook=_pairs_hook)
    except ClassifierError:
        raise
    except (json.JSONDecodeError, ValueError):
        raise ClassifierError(CODE_INVALID_JSON) from None
    if _depth(obj) > max_depth:
        raise ClassifierError(CODE_PAYLOAD_LIMIT)
    _reject_nonfinite(obj)
    return obj


def _exact_keys(obj: Any, keys: frozenset[str], *, code: str = CODE_UNKNOWN_KEY) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ClassifierError(CODE_TYPE_INVALID)
    actual = set(obj)
    if actual != set(keys):
        unknown = actual - set(keys)
        if unknown:
            raise ClassifierError(CODE_UNKNOWN_KEY)
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)  # missing key
    return obj


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClassifierError(CODE_TYPE_INVALID)
    return value


def _unique_id_list(value: Any, name: str, *, minimum: int, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not (minimum <= len(value) <= maximum):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    ids = tuple(_id(item, name) for item in value)
    if len(set(ids)) != len(ids):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    return ids


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundedMaterial:
    evidence_ref: str
    title: str
    excerpt: str
    content_digest: str


@dataclass(frozen=True)
class MappingEntry:
    asset_ref: str
    mapping_version: str

    @property
    def market(self) -> str:
        return self.asset_ref.split(":", 1)[0]


@dataclass(frozen=True)
class Impact:
    asset_ref: str
    polarity: str
    confidence: float


@dataclass(frozen=True)
class ClassificationRequest:
    request_id: str
    evidence_refs: tuple[str, ...]
    materials: tuple[BoundedMaterial, ...]
    mapping_context: tuple[MappingEntry, ...]
    cutoff_at: datetime
    classifier_policy_version: str

    def material_for(self, evidence_ref: str) -> BoundedMaterial:
        for material in self.materials:
            if material.evidence_ref == evidence_ref:
                return material
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)

    def mapped_assets(self) -> frozenset[str]:
        return frozenset(entry.asset_ref for entry in self.mapping_context)


@dataclass(frozen=True)
class ClassificationResult:
    request_id: str
    event_type: str
    impacts: tuple[Impact, ...]
    materiality: float
    summary: str
    uncertainty_codes: frozenset[str]
    evidence_refs: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": self.request_id,
            "event_type": self.event_type,
            "impacts": [
                {
                    "asset_ref": impact.asset_ref,
                    "polarity": impact.polarity,
                    "confidence": impact.confidence,
                }
                for impact in self.impacts
            ],
            "materiality": self.materiality,
            "summary": self.summary,
            "uncertainty_codes": sorted(self.uncertainty_codes),
            "evidence_refs": list(self.evidence_refs),
        }


def decode_classification_request(payload: Any) -> ClassificationRequest:
    obj = payload if isinstance(payload, dict) else _exact_keys(payload, REQUEST_KEYS)
    _exact_keys(obj, REQUEST_KEYS)
    if _int(obj["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise ClassifierError(CODE_SCHEMA_VERSION_UNSUPPORTED)
    request_id = _id(obj["request_id"], "request_id")
    evidence_refs = _unique_id_list(obj["evidence_refs"], "evidence_refs",
                                    minimum=1, maximum=MAX_EVIDENCE)
    raw_materials = obj["bounded_materials"]
    if not isinstance(raw_materials, list) or not (
            1 <= len(raw_materials) <= MAX_MATERIALS):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    materials: list[BoundedMaterial] = []
    for raw in raw_materials:
        item = _exact_keys(raw, MATERIAL_KEYS)
        material = BoundedMaterial(
            evidence_ref=_id(item["evidence_ref"], "evidence_ref"),
            title=_text(item["title"], "title", max_len=300),
            excerpt=_text(item["excerpt"], "excerpt", max_len=2000),
            content_digest=_hex64(item["content_digest"], "content_digest"),
        )
        materials.append(material)
    if {m.evidence_ref for m in materials} != set(evidence_refs):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)  # 1:1 with evidence_refs
    raw_mappings = obj["mapping_context"]
    if not isinstance(raw_mappings, list) or not (
            1 <= len(raw_mappings) <= MAX_MATERIALS):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    mappings: list[MappingEntry] = []
    for raw in raw_mappings:
        item = _exact_keys(raw, MAPPING_KEYS)
        asset_ref = item["asset_ref"]
        if not isinstance(asset_ref, str) or not _ASSET_REF_RE.match(asset_ref):
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
        mappings.append(MappingEntry(
            asset_ref=asset_ref,
            mapping_version=_version(item["mapping_version"], "mapping_version"),
        ))
    if len({m.asset_ref for m in mappings}) != len(mappings):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    return ClassificationRequest(
        request_id=request_id,
        evidence_refs=evidence_refs,
        materials=tuple(materials),
        mapping_context=tuple(mappings),
        cutoff_at=parse_ts(obj["cutoff_at"], "cutoff_at"),
        classifier_policy_version=_version(
            obj["classifier_policy_version"], "classifier_policy_version"),
    )


def decode_classification_result(payload: Any, request: ClassificationRequest) -> ClassificationResult:
    """Strict response gate (parent D3 + #775 v3/v4 request_id contract)."""
    obj = payload
    if not isinstance(obj, dict):
        raise ClassifierError(CODE_TYPE_INVALID)
    # v4 §2 evaluation order step 1: request_id extraction happens before
    # any other schema evaluation; missing or non-ID -> OUTPUT_INVALID.
    if "request_id" not in obj or not isinstance(obj["request_id"], str) \
            or not _ID_RE.match(obj["request_id"]):
        raise ClassifierError(CODE_CLASSIFIER_OUTPUT_INVALID)
    _exact_keys(obj, RESULT_KEYS)
    if _int(obj["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise ClassifierError(CODE_SCHEMA_VERSION_UNSUPPORTED)
    if obj["request_id"] != request.request_id:
        raise ClassifierError(CODE_CLASSIFIER_OUTPUT_INVALID)  # v4 step 1-2
    event_type = obj["event_type"]
    if event_type not in EVENT_TYPES:
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    raw_impacts = obj["impacts"]
    if not isinstance(raw_impacts, list) or len(raw_impacts) > MAX_IMPACTS:
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    mapped = request.mapped_assets()
    impacts: list[Impact] = []
    for raw in raw_impacts:
        item = _exact_keys(raw, IMPACT_KEYS)
        asset_ref = item["asset_ref"]
        if not isinstance(asset_ref, str) or asset_ref not in mapped:
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
        polarity = item["polarity"]
        if polarity not in POLARITIES:
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
        impacts.append(Impact(
            asset_ref=asset_ref,
            polarity=polarity,
            confidence=_fraction(item["confidence"], "confidence"),
        ))
    materiality = _fraction(obj["materiality"], "materiality")
    summary = _text(obj["summary"], "summary", max_len=2000)
    raw_codes = obj["uncertainty_codes"]
    if not isinstance(raw_codes, list) or len(raw_codes) > MAX_UNCERTAINTY:
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
    codes: list[str] = []
    for raw in raw_codes:
        if not isinstance(raw, str) or raw not in UNCERTAINTY_CODES:
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
        if raw in codes:
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
        codes.append(raw)
    refs = _unique_id_list(obj["evidence_refs"], "evidence_refs",
                           minimum=1, maximum=MAX_EVIDENCE)
    if not set(refs) <= set(request.evidence_refs):
        raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)  # non-empty subset only
    return ClassificationResult(
        request_id=request.request_id,
        event_type=event_type,
        impacts=tuple(impacts),
        materiality=materiality,
        summary=summary,
        uncertainty_codes=frozenset(codes),
        evidence_refs=refs,
    )


# ---------------------------------------------------------------------------
# host envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostEnvelope:
    envelope_id: str
    request_id: str
    classifier_kind: str
    policy_version: str
    provider: str | None
    model: str | None
    model_version: str | None
    prompt_version: str | None
    schema_version_str: str
    input_digest: str
    received_at: datetime
    validated_at: datetime | None
    cost: None
    status: str

    def to_json(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "request_id": self.request_id,
            "classifier_kind": self.classifier_kind,
            "policy_version": self.policy_version,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version_str,
            "input_digest": self.input_digest,
            "received_at": format_ts(self.received_at),
            "validated_at": format_ts(self.validated_at) if self.validated_at else None,
            "cost": self.cost,
            "status": self.status,
        }


def build_envelope(request: ClassificationRequest, *, envelope_id: str,
                   received_at: datetime, validated_at: datetime | None,
                   status: str) -> HostEnvelope:
    """Host-side envelope. Raw material/response text is never stored."""
    return HostEnvelope(
        envelope_id=_id(envelope_id, "envelope_id"),
        request_id=request.request_id,
        classifier_kind=CLASSIFIER_KIND,
        policy_version=request.classifier_policy_version,
        provider=None,   # rule classifier: no LLM version fields, no cost
        model=None,
        model_version=None,
        prompt_version=None,
        schema_version_str=f"news-paper-classification-v{SCHEMA_VERSION}",
        input_digest=digest({
            "request_id": request.request_id,
            "evidence_refs": list(request.evidence_refs),
            "materials": [
                {"evidence_ref": m.evidence_ref, "content_digest": m.content_digest}
                for m in request.materials
            ],
            "mapping_context": [m.asset_ref for m in request.mapping_context],
            "cutoff_at": format_ts(request.cutoff_at),
            "classifier_policy_version": request.classifier_policy_version,
        }),
        received_at=received_at,
        validated_at=validated_at,
        cost=None,
        status=status,
    )


# ---------------------------------------------------------------------------
# disabled LLM gateway (LLM adoption not approved -> call count 0)
# ---------------------------------------------------------------------------


class LLMClassificationGateway:
    """Tools-less LLM gateway placeholder.

    LLM adoption is NOT approved (judgment table "LLM/ルール" row is
    undecided), so this gateway refuses to exist as a call path: every call
    raises the fixed code ``FEATURE_DISABLED``. No provider, model, unit
    price, budget or network code is defined here. When an LLM path is
    separately approved it must be implemented as a new, reviewed gateway —
    never by editing this class into an enabled state.
    """

    calls: int = 0

    def classify(self, request: ClassificationRequest) -> Any:  # pragma: no cover
        type(self).calls += 1
        raise ClassifierError(CODE_FEATURE_DISABLED)


# ---------------------------------------------------------------------------
# fixture rule baseline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureRule:
    rule_id: str
    event_type: str
    keywords: tuple[str, ...]
    polarity: str
    confidence: float
    materiality: float
    currency: str | None = None  # FX only: explicit base/quote attribution


FIXTURE_RULES: tuple[FixtureRule, ...] = (
    FixtureRule("fixture-rule-guidance-up", "guidance_revision",
                ("業績予想", "上方修正"), "positive", 0.82, 0.70),
    FixtureRule("fixture-rule-guidance-down", "guidance_revision",
                ("業績予想", "下方修正"), "negative", 0.78, 0.70),
    FixtureRule("fixture-rule-earnings-beat", "earnings",
                ("決算", "上振れ"), "positive", 0.74, 0.60),
    FixtureRule("fixture-rule-earnings-miss", "earnings",
                ("決算", "下振れ"), "negative", 0.74, 0.60),
    FixtureRule("fixture-rule-incident", "operational_incident",
                ("設備", "停止"), "negative", 0.66, 0.55),
    FixtureRule("fixture-rule-boj-jpy", "monetary_policy",
                ("日銀", "利上げ"), "negative", 0.71, 0.65, currency="JPY"),
    FixtureRule("fixture-rule-fed-usd", "monetary_policy",
                ("FRB", "利上げ"), "positive", 0.71, 0.65, currency="USD"),
)


@dataclass(frozen=True)
class FixtureClassifierPolicy:
    """Fixture-only adoption thresholds (no approved production value)."""

    policy_version: str = CLASSIFIER_POLICY_VERSION
    min_confidence: float = 0.60
    min_materiality: float = 0.50


@dataclass(frozen=True)
class RuleClassification:
    """Rule output plus out-of-band metadata (never part of the result schema)."""

    result: ClassificationResult
    status: str  # "ok" | "low_confidence" | "low_materiality"
    matched_rule_ids: tuple[str, ...]
    currency_mentions: Mapping[str, str | None] = field(default_factory=dict)


class RuleClassifier:
    """Deterministic fixture rule table. No LLM, no network, no secrets."""

    def __init__(self, rules: Sequence[FixtureRule] = FIXTURE_RULES,
                 policy: FixtureClassifierPolicy | None = None) -> None:
        self._rules = tuple(rules)
        self._policy = policy or FixtureClassifierPolicy()

    @property
    def policy(self) -> FixtureClassifierPolicy:
        return self._policy

    def classify(self, request: ClassificationRequest) -> RuleClassification:
        if request.classifier_policy_version != self._policy.policy_version:
            raise ClassifierError(CODE_CLASSIFIER_UNAVAILABLE)
        matched: list[tuple[str, FixtureRule, str]] = []
        for evidence_ref in request.evidence_refs:
            material = request.material_for(evidence_ref)
            haystack = material.title + "\n" + material.excerpt
            for rule in self._rules:
                if all(keyword in haystack for keyword in rule.keywords):
                    matched.append((evidence_ref, rule, material.content_digest))
                    break
        evidence_list = list(request.evidence_refs)
        if not matched:
            result = ClassificationResult(
                request_id=request.request_id,
                event_type="unknown",
                impacts=(),
                materiality=0.0,
                summary=_host_summary("no fixture rule matched", "unknown", 0),
                uncertainty_codes=frozenset({"UNKNOWN_EVENT_TYPE"}),
                evidence_refs=tuple(evidence_list),
            )
            return RuleClassification(result, "low_confidence", (), {})
        distinct = {(rule.event_type, rule.polarity) for _, rule, _ in matched}
        conflicting = len(distinct) > 1
        if conflicting:
            primary = matched[0][1]
            result = ClassificationResult(
                request_id=request.request_id,
                event_type="other",
                impacts=(),
                materiality=0.0,
                summary=_host_summary("conflicting fixture rules", "other", 0),
                uncertainty_codes=frozenset({"CONFLICTING_SOURCES"}),
                evidence_refs=tuple(evidence_list),
            )
            return RuleClassification(result, "low_confidence",
                                      tuple(f"{r.rule_id}" for _, r, _ in matched), {})
        primary = matched[0][1]
        impacts: list[Impact] = []
        mentions: dict[str, str | None] = {}
        for entry in request.mapping_context:
            impacts.append(Impact(
                asset_ref=entry.asset_ref,
                polarity=primary.polarity,
                confidence=primary.confidence,
            ))
            mentions[entry.asset_ref] = primary.currency
        result = ClassificationResult(
            request_id=request.request_id,
            event_type=primary.event_type,
            impacts=tuple(impacts),
            materiality=primary.materiality,
            summary=_host_summary(f"fixture rule {primary.rule_id}",
                                  primary.event_type, len(matched)),
            uncertainty_codes=frozenset(),
            evidence_refs=tuple(evidence_list),
        )
        if primary.confidence < self._policy.min_confidence:
            status = "low_confidence"
        elif primary.materiality < self._policy.min_materiality:
            status = "low_materiality"
        else:
            status = "ok"
        return RuleClassification(
            result, status,
            tuple(rule.rule_id for _, rule, _ in matched),
            mentions,
        )


def _host_summary(subject: str, event_type: str, matches: int) -> str:
    """Fixed host template. Material text is never copied into outputs."""
    return (f"fixture rule classification: {subject}; event={event_type}; "
            f"matched={matches}; policy={CLASSIFIER_POLICY_VERSION}")


# ---------------------------------------------------------------------------
# request lifecycle (N00 v4 §2: terminal-state priority rules)
# ---------------------------------------------------------------------------

STATE_IN_FLIGHT = "in_flight"
STATE_COMPLETED = "completed"
STATE_TIMEOUT = "timeout"
STATE_UNAVAILABLE = "unavailable"
STATE_CANCEL = "cancel"
STATE_OUTPUT_INVALID = "output_invalid"
STATE_LOW_CONFIDENCE = "low_confidence"
STATE_LOW_MATERIALITY = "low_materiality"
_TERMINAL = frozenset({
    STATE_COMPLETED, STATE_TIMEOUT, STATE_UNAVAILABLE, STATE_CANCEL,
    STATE_OUTPUT_INVALID, STATE_LOW_CONFIDENCE, STATE_LOW_MATERIALITY,
})


@dataclass(frozen=True)
class SubmissionVerdict:
    duplicate: bool
    state: str


@dataclass(frozen=True)
class ResponseVerdict:
    adopted: bool
    code: str | None
    state: str
    changed: bool
    classification_available_at: datetime | None = None


@dataclass
class _RequestState:
    request: ClassificationRequest
    status: str = STATE_IN_FLIGHT
    adopted_canonical: bytes | None = None
    classification_available_at: datetime | None = None


class RequestTracker:
    """v4 §2 evaluation order: request_id -> match -> state-based verdict.

    A retry always needs a new ``request_id``; re-submitting a known id never
    starts a new classification. Identical re-response after normal
    completion is a no-op with zero state change.
    """

    def __init__(self) -> None:
        self._states: dict[str, _RequestState] = {}

    def submit(self, request: ClassificationRequest) -> SubmissionVerdict:
        existing = self._states.get(request.request_id)
        if existing is None:
            self._states[request.request_id] = _RequestState(request)
            return SubmissionVerdict(False, STATE_IN_FLIGHT)
        return SubmissionVerdict(True, existing.status)

    def state_of(self, request_id: str) -> str | None:
        entry = self._states.get(request_id)
        return entry.status if entry else None

    def _mark(self, request_id: str, status: str) -> None:
        entry = self._states.get(request_id)
        if entry is None:
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)
        if entry.status != STATE_IN_FLIGHT:
            raise ClassifierError(CODE_VALUE_OUT_OF_RANGE)  # terminal is final
        entry.status = status

    def mark_timeout(self, request_id: str) -> None:
        self._mark(request_id, STATE_TIMEOUT)

    def mark_unavailable(self, request_id: str) -> None:
        self._mark(request_id, STATE_UNAVAILABLE)

    def mark_cancel(self, request_id: str) -> None:
        self._mark(request_id, STATE_CANCEL)

    def evaluate(self, request_id: str, response: ClassificationResult,
                 raw_canonical: bytes, *, validated_at: datetime,
                 policy: FixtureClassifierPolicy | None = None) -> ResponseVerdict:
        """Apply the fixed evaluation order and adopt at most one response."""
        entry = self._states.get(request_id)
        if entry is None:
            return ResponseVerdict(False, CODE_CLASSIFIER_OUTPUT_INVALID,
                                   STATE_UNAVAILABLE, False)
        state = entry.status
        if state != STATE_IN_FLIGHT:
            if state == STATE_COMPLETED and entry.adopted_canonical == raw_canonical:
                return ResponseVerdict(False, None, state, False)  # no-op
            return ResponseVerdict(False, CODE_CLASSIFIER_OUTPUT_INVALID,
                                   state, False)
        effective = policy or FixtureClassifierPolicy()
        impact_confident = all(
            impact.confidence >= effective.min_confidence
            for impact in response.impacts
        )
        if not response.impacts:
            entry.status = STATE_LOW_CONFIDENCE
            return ResponseVerdict(False, None, entry.status, True)
        if not impact_confident:
            entry.status = STATE_LOW_CONFIDENCE
            return ResponseVerdict(False, None, entry.status, True)
        if response.materiality < effective.min_materiality:
            entry.status = STATE_LOW_MATERIALITY
            return ResponseVerdict(False, None, entry.status, True)
        entry.status = STATE_COMPLETED
        entry.adopted_canonical = raw_canonical
        entry.classification_available_at = validated_at
        return ResponseVerdict(True, None, entry.status, True, validated_at)


__all__ = [
    "CLASSIFIER_KIND", "CLASSIFIER_POLICY_VERSION", "CODE_CLASSIFIER_OUTPUT_INVALID",
    "CODE_CLASSIFIER_TIMEOUT", "CODE_CLASSIFIER_UNAVAILABLE", "CODE_FEATURE_DISABLED",
    "CODE_DUPLICATE_JSON_KEY", "CODE_INVALID_JSON", "CODE_NONFINITE_VALUE",
    "CODE_PAYLOAD_LIMIT", "CODE_SCHEMA_VERSION_UNSUPPORTED", "CODE_TYPE_INVALID",
    "CODE_UNKNOWN_KEY", "CODE_VALUE_OUT_OF_RANGE", "EVENT_TYPES", "FixtureClassifierPolicy",
    "FixtureRule", "FIXTURE_RULES", "HostEnvelope", "IMPACT_KEYS", "Impact", "LLM_ENABLED",
    "LLMClassificationGateway", "MAPPING_KEYS", "MappingEntry", "MATERIAL_KEYS",
    "MAX_EVIDENCE", "MAX_IMPACTS", "MAX_MATERIALS", "MAX_UNCERTAINTY", "POLARITIES",
    "REQUEST_KEYS", "RESPONSE_MAX_BYTES", "RESPONSE_MAX_DEPTH", "RESULT_KEYS",
    "RequestTracker", "ResponseVerdict", "RuleClassification", "RuleClassifier",
    "STATE_CANCEL", "STATE_COMPLETED", "STATE_IN_FLIGHT", "STATE_LOW_CONFIDENCE",
    "STATE_LOW_MATERIALITY", "STATE_OUTPUT_INVALID", "STATE_TIMEOUT", "STATE_UNAVAILABLE",
    "SubmissionVerdict", "UNCERTAINTY_CODES", "BoundedMaterial", "ClassificationRequest",
    "ClassificationResult", "ClassifierError", "SCHEMA_VERSION",
    "build_envelope", "canonical", "decode_classification_request",
    "decode_classification_result", "digest", "format_ts", "parse_ts",
    "strict_json_loads",
]
