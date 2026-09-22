"""Fail-closed JSON parsing and choice-answer validation."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

if __package__:
    from .contracts import DecisionRequest, SemanticDecisionError, is_finite_number
else:  # pragma: no cover - used by the isolated transport child.
    from contracts import (  # type: ignore[no-redef]
        DecisionRequest,
        SemanticDecisionError,
        is_finite_number,
    )


_TOP_LEVEL_KEYS = frozenset({"model", "answers", "usage"})
_ANSWER_KEYS = frozenset({"type", "choice", "probabilities", "confidence"})
_USAGE_KEYS = frozenset({"input_tokens", "output_tokens"})
_MAX_TOKENS = 1_000_000_000


def strict_loads(raw: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SemanticDecisionError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise SemanticDecisionError("non-finite JSON number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except SemanticDecisionError:
        raise
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticDecisionError("invalid JSON") from exc


def validate_response(data: Mapping[str, Any], request: DecisionRequest) -> dict[str, Any]:
    """Return only the schema fields needed by the caller.

    Provider-specific metadata, reasoning, headers, and arbitrary echoed input
    are rejected instead of being carried into the docich runtime.
    """

    if not isinstance(data, Mapping) or set(data) != _TOP_LEVEL_KEYS:
        raise SemanticDecisionError("invalid response envelope")
    model = data.get("model")
    if not isinstance(model, str) or model != request.model:
        raise SemanticDecisionError("resolved model mismatch")

    answers = data.get("answers")
    expected = request.expected_choices
    if not isinstance(answers, Mapping) or set(answers) != set(expected):
        raise SemanticDecisionError("answer ids do not match request")
    clean_answers: dict[str, dict[str, Any]] = {}
    for question_id, choices in expected.items():
        answer = answers[question_id]
        if not isinstance(answer, Mapping) or set(answer) != _ANSWER_KEYS:
            raise SemanticDecisionError("invalid answer schema")
        if answer.get("type") != "choice":
            raise SemanticDecisionError("answer type must be choice")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in choices:
            raise SemanticDecisionError("answer choice is outside its allowlist")
        confidence = answer.get("confidence")
        if not _bounded_number(confidence):
            raise SemanticDecisionError("invalid confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, Mapping) or set(probabilities) != set(choices):
            raise SemanticDecisionError("invalid probability keys")
        if not all(_bounded_number(value) for value in probabilities.values()):
            raise SemanticDecisionError("invalid probability value")
        if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-5):
            raise SemanticDecisionError("probabilities must sum to one")
        if probabilities[choice] + 1e-7 < max(probabilities.values()):
            raise SemanticDecisionError("choice is not the highest probability")
        clean_answers[question_id] = {
            "type": "choice",
            "choice": choice,
            "confidence": confidence,
            "probabilities": {key: probabilities[key] for key in choices},
        }

    usage = data.get("usage")
    if not isinstance(usage, Mapping) or set(usage) != _USAGE_KEYS:
        raise SemanticDecisionError("invalid usage schema")
    if any(
        type(usage.get(key)) is not int
        or not 0 <= usage[key] <= _MAX_TOKENS
        for key in _USAGE_KEYS
    ):
        raise SemanticDecisionError("invalid usage value")
    return {
        "model": model,
        "answers": clean_answers,
        "usage": {key: usage[key] for key in ("input_tokens", "output_tokens")},
    }


def _bounded_number(value: object) -> bool:
    return is_finite_number(value) and 0 <= value <= 1
