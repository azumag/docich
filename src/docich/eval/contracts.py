"""Versioned offline-eval contracts for the comment hillclimb base (#1308).

This is the PR-1 foundation slice of issue #1308: the schema, split
vocabulary, grader registry and deterministic hashing that every later slice
shares. It is pure stdlib, never contacts a provider or production state, and
is the single place that decides what a valid case / suite manifest looks
like. ``evals/comment/v1`` is loaded through these functions, and a campaign
records the hashes computed here so a score can never be compared across
suite or grader revisions by accident.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

SUITE_SCHEMA = "docich.eval.suite.v1"
CASE_SCHEMA = "docich.eval.case.v1"
SCHEMA_VERSION = 1

SPLIT_TRAIN = "train"
SPLIT_VALIDATION = "validation"
SPLIT_SEALED = "sealed_test"
SPLITS = (SPLIT_TRAIN, SPLIT_VALIDATION, SPLIT_SEALED)
# The default 60/20/20 split from issue #1308 section 1. Critical fixtures are
# a separate always-on bucket, never folded into these ratios.
DEFAULT_RATIOS = {SPLIT_TRAIN: 0.6, SPLIT_VALIDATION: 0.2, SPLIT_SEALED: 0.2}
DEFAULT_SEED = 13080

HOST_MODES = ("main", "soren91")
INTENT_FAMILIES = (
    "game", "question", "advice", "stream_ops", "notification", "chitchat", "other",
)
SCREEN_NEED_LABELS = ("required", "not_required", "uncertain")

# Versioned grader identities. A campaign stores these next to the rubric so a
# score is only ever compared against the same grader revision.
GRADER_VERSIONS = {
    "deterministic": "deterministic-v1",
    "classifier": "classifier-v1",
    "reply_semantic": "reply-semantic-v1",
}

MAX_CASE_BYTES = 4096
MAX_COMMENT_CHARS = 500
MAX_TAGS = 32
MAX_REQUIREMENTS = 32

_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_WS_RE = re.compile(r"\s+")

# Public corpora must never carry production secrets. These patterns gate what
# may be committed under evals/ (issue #1308 section 2 / section 10 PR-1).
SECRET_PATTERNS = (
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("api_key", re.compile(r"(?i)\b(?:sk|ghp|gho|xox[baprs]|AKIA)[-_A-Za-z0-9]{12,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("assigned_secret", re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:token|secret|password|passwd|api[_-]?key|access[_-]?key)"
        r"(?![A-Za-z0-9])\s*[:=]\s*\S{6,}")),
    ("private_path", re.compile(r"(?:/(?:home|Users)/[^\s]+|[A-Za-z]:\\\\[^\s]+)")),
    ("private_ip", re.compile(r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_USER_AT_RE = re.compile(r"@[A-Za-z0-9_]{2,32}")
_USER_PREFIX_RE = re.compile(r"(?m)^\s*(?:user|viewer|from)\s*[:=]\s*\S+")


class ContractError(ValueError):
    """A case or manifest violates the eval contract (never a transient error)."""


# --------------------------------------------------------------------- hashing

def canonical(value) -> str:
    """Canonical JSON: stable key order, no NaN, no ASCII escaping."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False)


def digest(value) -> str:
    """``sha256:<hex>`` of the canonical JSON encoding of ``value``."""
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- normalisation

def normalize_comment(text: str) -> str:
    """NFKC, whitespace-collapsed body used for fingerprints and overlap tests."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def looks_private(text: str) -> tuple[str, ...]:
    """Names of the secret/PII patterns matched by ``text`` (empty when clean)."""
    return tuple(name for name, pattern in SECRET_PATTERNS if pattern.search(text))


def case_fingerprint(case: dict) -> str:
    """Group-stable identity: two near-identical bodies in one thread collide."""
    body = normalize_comment(case["input"]["comment"])
    return hash_text(case["group_id"] + "\x00" + body)[: len("sha256:") + 24]


# ------------------------------------------------------------------ validation

def _id(raw, field):
    if not isinstance(raw, str) or not _ID_RE.match(raw):
        raise ContractError(f"invalid_{field}")
    return raw


def _comment(raw):
    if not isinstance(raw, str):
        raise ContractError("invalid_comment")
    text = normalize_comment(raw)
    if not text:
        raise ContractError("empty_comment")
    if len(text) > MAX_COMMENT_CHARS:
        raise ContractError("comment_too_long")
    if len(text.encode("utf-8")) > MAX_CASE_BYTES:
        raise ContractError("case_too_large")
    return text


def _bool(raw, field, default=False):
    if raw is None:
        return default
    if type(raw) is not bool:
        raise ContractError(f"invalid_{field}")
    return raw


def _optional_label(raw, field, allowed):
    if raw is None:
        return None
    if allowed is None:
        return _id(raw, field)
    if raw not in allowed:
        raise ContractError(f"invalid_{field}")
    return raw


def _string_list(raw, field, limit):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > limit:
        raise ContractError(f"invalid_{field}")
    out = []
    for item in raw:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_COMMENT_CHARS:
            raise ContractError(f"invalid_{field}")
        out.append(item.strip())
    return out


def validate_case(raw: dict) -> dict:
    """Return a normalised, freshly-built case or raise :class:`ContractError`."""
    if not isinstance(raw, dict):
        raise ContractError("invalid_case")
    if raw.get("schema") not in (None, CASE_SCHEMA):
        raise ContractError("invalid_case_schema")
    source = raw.get("input")
    if not isinstance(source, dict):
        raise ContractError("invalid_input")
    expected = raw.get("expected") or {}
    if not isinstance(expected, dict):
        raise ContractError("invalid_expected")
    host_mode = source.get("host_mode", HOST_MODES[0])
    if host_mode not in HOST_MODES:
        raise ContractError("invalid_host_mode")
    hint = source.get("active_game_hint")
    if hint is not None:
        hint = _id(hint, "active_game_hint")
    case = {
        "schema": CASE_SCHEMA,
        "case_id": _id(raw.get("case_id"), "case_id"),
        "group_id": _id(raw.get("group_id"), "group_id"),
        "input": {
            "comment": _comment(source.get("comment")),
            "active_game_hint": hint,
            "host_mode": host_mode,
            "image_attached": _bool(source.get("image_attached"), "image_attached"),
        },
        "expected": {
            "intent_family": _optional_label(expected.get("intent_family"),
                                             "intent_family", INTENT_FAMILIES),
            "category": _optional_label(expected.get("category"), "category", None),
            "screen_need": _optional_label(expected.get("screen_need"),
                                           "screen_need", SCREEN_NEED_LABELS),
            "must_use_game_context": _bool(expected.get("must_use_game_context"),
                                           "must_use_game_context"),
            "must_not_claim_screen_seen_without_image": _bool(
                expected.get("must_not_claim_screen_seen_without_image"),
                "must_not_claim_screen_seen_without_image"),
            "response_must_include": _string_list(expected.get("response_must_include"),
                                                  "response_must_include", MAX_REQUIREMENTS),
            "response_must_not_include": _string_list(expected.get("response_must_not_include"),
                                                      "response_must_not_include", MAX_REQUIREMENTS),
        },
        "tags": [t for t in _string_list(raw.get("tags"), "tags", MAX_TAGS)],
    }
    return case


def case_digest(case: dict) -> str:
    return digest(case)


def assert_public_safe(case: dict) -> None:
    """Reject a public fixture that carries a production secret or PII pattern."""
    found = looks_private(canonical(case))
    if found:
        raise ContractError("unsafe_public_fixture:" + ",".join(found))


# -------------------------------------------------------------------- manifest

def validate_manifest(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ContractError("invalid_manifest")
    if raw.get("schema") != SUITE_SCHEMA:
        raise ContractError("invalid_manifest_schema")
    suite = _id(raw.get("suite"), "suite")
    ratios = raw.get("ratios") or dict(DEFAULT_RATIOS)
    if not isinstance(ratios, dict) or set(ratios) != set(SPLITS):
        raise ContractError("invalid_ratios")
    for value in ratios.values():
        if type(value) not in (int, float) or value < 0:
            raise ContractError("invalid_ratios")
    if abs(sum(ratios.values()) - 1.0) > 1e-9:
        raise ContractError("ratios_must_sum_to_one")
    seed = raw.get("seed", DEFAULT_SEED)
    if type(seed) is not int:
        raise ContractError("invalid_seed")
    return {
        "schema": SUITE_SCHEMA,
        "suite": suite,
        "seed": seed,
        "ratios": {key: float(value) for key, value in ratios.items()},
        "rubric_version": _id(raw.get("rubric_version", "comment-reply-rubric-v1"), "rubric_version"),
        "grader_versions": dict(raw.get("grader_versions") or GRADER_VERSIONS),
        "public_cases": raw.get("public_cases", "public_cases.jsonl"),
        "critical_cases": raw.get("critical_cases", "critical_cases.jsonl"),
    }
