"""Shared choice-schema validation, independent of comment rubrics or policy."""
from __future__ import annotations

import json
import math

from .routes import RouteProfile

MAX_REQUEST_BYTES = 32768
MAX_RESPONSE_BYTES = 131072


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("nonfinite_number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def number(value, low=0, high=1) -> bool:
    return type(value) in (int, float) and low <= value <= high and math.isfinite(value)


def validate_request(request, profile: RouteProfile) -> None:
    """Reject unsupported contracts; projection belongs to each reviewed purpose."""
    if (type(request) is not dict or set(request) != {"model", "state", "questions"}
            or request["model"] != profile.requested_model
            or not isinstance(request["state"], (dict, list, str))):
        raise ValueError("invalid_request")
    questions = request["questions"]
    if type(questions) is not dict or not questions:
        raise ValueError("invalid_request")
    for key, question in questions.items():
        if (type(key) is not str or not key or type(question) is not dict
                or set(question) != {"type", "instructions", "criteria"}
                or question["type"] != "choice"
                or type(question["instructions"]) is not str):
            raise ValueError("invalid_request")
        criteria = question["criteria"]
        if (type(criteria) is not dict or not criteria
                or any(type(label) is not str or not label
                       or type(description) is not str
                       for label, description in criteria.items())):
            raise ValueError("invalid_request")


def encode_request(request, profile: RouteProfile) -> bytes:
    validate_request(request, profile)
    raw = dumps(request).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("input_limit")
    return raw


def validate_response(data, request, profile: RouteProfile) -> dict:
    """All-or-nothing and idempotent. Never retain model-echoed context fields.

    Keep the validated type discriminator: the parent and a legacy consumer may
    validate the same answer again without treating sanitisation as a new schema.
    """
    validate_request(request, profile)
    if type(data) is not dict or data.get("model") not in profile.resolved_models:
        raise ValueError("invalid_response")
    answers = data.get("answers")
    if type(answers) is not dict or set(answers) != set(request["questions"]):
        raise ValueError("invalid_response")
    clean = {}
    for key, question in request["questions"].items():
        labels = question["criteria"]
        answer = answers[key]
        if type(answer) is not dict or answer.get("type") != "choice":
            raise ValueError("invalid_response")
        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        confidence = answer.get("confidence")
        if type(choice) is not str or choice not in labels or not number(confidence):
            raise ValueError("invalid_response")
        if type(probabilities) is not dict or set(probabilities) != set(labels):
            raise ValueError("invalid_response")
        if (not all(number(p) for p in probabilities.values())
                or not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-5)
                or probabilities[choice] + 1e-7 < max(probabilities.values())):
            raise ValueError("invalid_response")
        clean[key] = {"type": "choice", "choice": choice, "confidence": confidence,
                      "probabilities": {label: probabilities[label] for label in labels}}
    usage = data.get("usage")
    if (type(usage) is not dict
            or any(type(usage.get(k)) is not int or not 0 <= usage[k] <= 10**9
                   for k in ("input_tokens", "output_tokens"))):
        raise ValueError("invalid_response")
    return {"model": data["model"], "answers": clean,
            "usage": {k: usage[k] for k in ("input_tokens", "output_tokens")}}
