"""Strict, provider-neutral data contracts for semantic decisions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


class SemanticDecisionError(ValueError):
    """A configuration, request, or provider response violated the contract."""


ROUTE_NAMES = frozenset({"direct", "vercel"})
BACKEND_NAMES = frozenset({"jev"})
DEFAULT_ROUTE = "direct"
DEFAULT_TIMEOUT_MS = 1500
MIN_TIMEOUT_MS = 50
MAX_TIMEOUT_MS = 5000
MAX_REQUEST_BYTES = 32768
MAX_RESPONSE_BYTES = 131072
MAX_PURPOSE_BYTES = 64
MAX_QUESTION_ID_BYTES = 64
MAX_CHOICE_BYTES = 128
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


@dataclass(frozen=True)
class RouteProfile:
    """Fixed provider route metadata; endpoint and model are not env-driven."""

    name: str
    endpoint: str
    requested_model: str
    credential_env: str
    timeout_ms: int = DEFAULT_TIMEOUT_MS


@dataclass(frozen=True)
class ChoiceQuestion:
    """The allowlist needed to validate one provider choice answer."""

    question_id: str
    choices: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _valid_identifier(self.question_id, MAX_QUESTION_ID_BYTES):
            raise SemanticDecisionError("invalid question id")
        if not isinstance(self.choices, tuple):
            raise SemanticDecisionError("question choices must be a tuple")
        if not self.choices or len(set(self.choices)) != len(self.choices):
            raise SemanticDecisionError("question choices must be unique and non-empty")
        for choice in self.choices:
            if not isinstance(choice, str) or not choice.strip():
                raise SemanticDecisionError("invalid question choice")
            if len(choice.encode("utf-8")) > MAX_CHOICE_BYTES:
                raise SemanticDecisionError("question choice is too large")


@dataclass(frozen=True)
class DecisionRequest:
    """An allowlisted provider payload plus its expected answer schema.

    Purpose-specific code owns the projection into ``payload``.  This generic
    layer does not accept or serialize a viewer/event object on its own.
    """

    purpose: str
    model: str
    payload: Mapping[str, Any]
    questions: tuple[ChoiceQuestion, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not _valid_identifier(
            self.purpose, MAX_PURPOSE_BYTES
        ):
            raise SemanticDecisionError("invalid purpose")
        if not isinstance(self.model, str) or not self.model.strip():
            raise SemanticDecisionError("invalid requested model")
        if not isinstance(self.payload, Mapping) or not isinstance(self.questions, tuple):
            raise SemanticDecisionError("invalid decision request")
        if self.payload.get("model") != self.model:
            raise SemanticDecisionError("request model mismatch")
        ids = [question.question_id for question in self.questions]
        if len(set(ids)) != len(ids) or not ids:
            raise SemanticDecisionError("question ids must be unique and non-empty")
        try:
            encoded = json.dumps(
                self.payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise SemanticDecisionError("request payload is not strict JSON") from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise SemanticDecisionError("request payload exceeds byte limit")

    @property
    def expected_choices(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType(
            {question.question_id: tuple(question.choices) for question in self.questions}
        )


@dataclass(frozen=True)
class TransportResult:
    """Sanitized result from the bounded transport boundary."""

    status: str
    attempted: bool
    data: Mapping[str, Any] | None = None
    retry_after: int = 0


def _valid_identifier(value: str, max_bytes: int) -> bool:
    return (
        isinstance(value, str)
        and bool(IDENTIFIER_RE.fullmatch(value))
        and len(value.encode("utf-8")) <= max_bytes
    )


def is_finite_number(value: object) -> bool:
    """Return true for JSON numbers, excluding booleans and non-finite values."""

    return type(value) in (int, float) and math.isfinite(value)
