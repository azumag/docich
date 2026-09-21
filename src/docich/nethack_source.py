"""Evidence-only contracts for post-restoration NetHack improvement (#859).

This module cannot enqueue work, authorize an LLM, publish a catalog, or act on
any game. A source is a bounded projection of trusted lifecycle observations;
it is not a new canonical game state or permission to run an improvement job.
"""
from __future__ import annotations

import datetime as dt
from copy import deepcopy
import hashlib
import json
import re
import uuid
from collections.abc import Mapping

SOURCE_SCHEMA_VERSION = 1
CONTEXT_KEYS = frozenset({
    "schema_version", "corner_id", "session_id", "origin", "start_request_id",
    "authorized_mode",
})
ORIGINS = frozenset({"scheduled", "rotation", "manual", "unknown"})
FINISH_REASONS = frozenset({"terminal", "stalled", "operator_stop", "time_limit", "unknown"})
_GAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RUNTIME_RE = re.compile(r"^g([1-9][0-9]*)-[0-9a-f]{8}$")
MAX_SOURCE_BYTES = 8192


class SourceEvidenceError(ValueError):
    """A fixed reason code; never embeds untrusted input or exception text."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SourceEvidenceError(reason)


def canonical_uuid(value: object) -> str:
    _require(isinstance(value, str) and len(value) == 36, "invalid_identity")
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise SourceEvidenceError("invalid_identity") from None
    _require(parsed == value, "invalid_identity")
    return parsed


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**63 - 1


def _time(value: object) -> dt.datetime:
    _require(isinstance(value, str) and 1 <= len(value) <= 40, "invalid_timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value)
        _require(parsed.tzinfo is not None, "invalid_timestamp")
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        raise SourceEvidenceError("invalid_timestamp") from None


def digest(value: object) -> str:
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SourceEvidenceError("invalid_evidence") from None
    _require(len(data) <= MAX_SOURCE_BYTES, "evidence_too_large")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def new_session_context(origin: str, start_request_id: str) -> dict[str, object]:
    return validate_session_context({
        "schema_version": SOURCE_SCHEMA_VERSION,
        "corner_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "origin": origin,
        "start_request_id": start_request_id,
        # PR1 never grants future execution permission, even to scheduled runs.
        "authorized_mode": "off",
    })


def validate_session_context(value: object) -> dict[str, object]:
    _require(type(value) is dict and set(value) == CONTEXT_KEYS, "invalid_session_context")
    _require(type(value["schema_version"]) is int
             and value["schema_version"] == SOURCE_SCHEMA_VERSION, "invalid_session_context")
    for key in ("corner_id", "session_id", "start_request_id"):
        canonical_uuid(value[key])
    _require(type(value["origin"]) is str and value["origin"] in ORIGINS,
             "invalid_origin")
    _require(value["authorized_mode"] == "off", "execution_not_authorized")
    return dict(value)


def runtime_identity(value: object) -> dict[str, object]:
    _require(isinstance(value, Mapping), "runtime_unverified")
    game, generation, runtime_id = value.get("game"), value.get("generation"), value.get("runtime_id")
    _require(isinstance(game, str) and _GAME_RE.fullmatch(game) is not None,
             "runtime_unverified")
    _require(_integer(generation, 1), "runtime_unverified")
    _require(isinstance(runtime_id, str) and len(runtime_id) <= 40, "runtime_unverified")
    match = _RUNTIME_RE.fullmatch(runtime_id)
    _require(match is not None and int(match.group(1)) == generation, "runtime_unverified")
    return {"game": game, "generation": generation, "runtime_id": runtime_id}


def verify_restoration(
    *, request_id: str, source_runtime: object, before: object,
    receipt: object, canonical: object, previous_game: object,
) -> dict[str, object]:
    """Join the specific restore request to pre/post canonical observations.

    Caller reads receipt and post-state in one nonblocking shared-lock scope.
    Missing metadata fails closed. An empty retiring list is explicit cleanup
    evidence; it is not inferred from the mere absence of a worker error.
    """
    canonical_uuid(request_id)
    source = runtime_identity(source_runtime)
    _require(source["game"] == "nethack", "source_runtime_mismatch")
    _require(runtime_identity(before) == source, "source_runtime_mismatch")
    _require(isinstance(receipt, Mapping) and isinstance(canonical, Mapping),
             "restore_unverified")
    _require(type(receipt.get("schema_version")) is int
             and receipt["schema_version"] == 1 and receipt.get("request_id") == request_id,
             "restore_request_mismatch")
    _require(receipt.get("status") == "succeeded", "restore_not_succeeded")
    result = receipt.get("result")
    _require(isinstance(result, Mapping), "restore_unverified")
    _require(result.get("status") == "succeeded"
             and result.get("operation") == receipt.get("operation"), "restore_not_succeeded")
    _require(result.get("from_game") == "nethack", "restore_source_mismatch")
    # A pre-call observation cannot prove what a queued switch eventually
    # stopped. This binding must be emitted by the coordinator inside its
    # transition, never inferred from adjacent generation numbers or time.
    # Legacy receipts do not contain it: those remain explicitly unverified.
    _require(isinstance(result.get("from_runtime"), Mapping), "restore_source_unverified")
    _require(runtime_identity(result["from_runtime"]) == source, "restore_source_mismatch")
    _require(type(canonical.get("schema_version")) is int
             and canonical["schema_version"] == 2, "restore_unverified")
    _require({"operation", "request_id", "active", "candidate", "previous", "retiring"}
             <= set(canonical), "restore_unverified")
    _require(canonical.get("operation") is None and canonical.get("request_id") is None,
             "restore_not_stable")
    _require(canonical.get("candidate") is None and canonical.get("previous") is None
             and canonical.get("retiring") == [], "cleanup_unverified")
    _require(result.get("cleanup_pending") is False, "cleanup_unverified")
    _require(_integer(canonical.get("revision"), 1), "restore_unverified")
    _require(_integer(receipt.get("generation"), 1)
             and receipt["generation"] > source["generation"]
             and type(result.get("generation")) is int
             and result["generation"] == receipt["generation"], "restore_generation_mismatch")
    if previous_game is None:
        _require(receipt.get("operation") == "stop" and receipt.get("target") is None
                 and result.get("to_game") is None, "restore_target_mismatch")
        _require(canonical.get("phase") == "idle" and canonical.get("active") is None,
                 "restore_target_mismatch")
        restored = None
    else:
        _require(isinstance(previous_game, str) and previous_game != "nethack"
                 and _GAME_RE.fullmatch(previous_game) is not None, "restore_target_mismatch")
        _require(receipt.get("operation") == "switch" and receipt.get("target") == previous_game
                 and result.get("to_game") == previous_game, "restore_target_mismatch")
        _require(canonical.get("phase") == "ready", "restore_not_stable")
        restored = runtime_identity(canonical.get("active"))
        _require(restored["game"] == previous_game
                 and restored["generation"] == receipt["generation"]
                 and restored["runtime_id"] == receipt.get("runtime_id"),
                 "restore_generation_mismatch")
    completed_at = _time(receipt.get("updated_at")).isoformat()
    return {
        "request_id": request_id,
        "source_runtime": source,
        "restored_runtime": restored,
        "restore_generation": receipt["generation"],
        "canonical_revision": canonical["revision"],
        "completed_at": completed_at,
        "cleanup_completed": True,
        "source_binding_verified": True,
    }


def validate_restoration_summary(value: object) -> dict[str, object]:
    """Validate the fixed, secret-free projection at the persistence boundary."""
    keys = {"request_id", "source_runtime", "restored_runtime", "restore_generation",
            "canonical_revision", "completed_at", "cleanup_completed", "source_binding_verified"}
    _require(type(value) is dict and set(value) == keys, "restore_unverified")
    canonical_uuid(value["request_id"])
    _require(value["cleanup_completed"] is True
             and value["source_binding_verified"] is True, "restore_unverified")
    source = runtime_identity(value["source_runtime"])
    _require(type(value["source_runtime"]) is dict
             and set(value["source_runtime"]) == set(source)
             and source["game"] == "nethack", "source_runtime_mismatch")
    _require(_integer(value["restore_generation"], 1)
             and value["restore_generation"] > source["generation"]
             and _integer(value["canonical_revision"], 1), "restore_generation_mismatch")
    if value["restored_runtime"] is not None:
        restored = runtime_identity(value["restored_runtime"])
        _require(type(value["restored_runtime"]) is dict
                 and set(value["restored_runtime"]) == set(restored)
                 and restored["game"] != "nethack"
                 and restored["generation"] == value["restore_generation"], "restore_target_mismatch")
    _time(value["completed_at"])
    digest(value)
    return deepcopy(value)


def build_post_restore_source(
    run: Mapping[str, object], session: Mapping[str, object],
    restoration: Mapping[str, object], *, finish_reason: str,
) -> dict[str, object]:
    """Build a sealed observation, never an executable job or paid permission."""
    context = validate_session_context(session.get("corner_context"))
    run_id = canonical_uuid(run.get("run_id"))
    _require(session.get("session_id") == context["session_id"], "session_identity_mismatch")
    _require(type(finish_reason) is str and finish_reason in FINISH_REASONS,
             "invalid_finish_reason")
    restoration = validate_restoration_summary(restoration)
    canonical_uuid(restoration.get("request_id"))
    _require(runtime_identity(session.get("runtime")) == restoration.get("source_runtime"),
             "source_runtime_mismatch")
    _require(_time(session.get("started_at")) <= _time(restoration.get("completed_at"))
             <= _time(session.get("ended_at")), "invalid_evidence_order")
    terminal = run.get("terminal")
    terminal_id = None
    if run.get("status") not in {"dead", "ascended", "ended"}:
        eligibility = "expedition_not_terminal" if run.get("status") in {"active", "suspended"} else "terminal_unverified"
    elif not isinstance(terminal, Mapping) or terminal.get("source") != "xlogfile":
        eligibility = "terminal_unverified"
    else:
        birth_floor = run.get("birth_not_before_epoch")
        start, end = terminal.get("starttime"), terminal.get("endtime")
        confirmed = (
            terminal.get("identity_verified") is True
            and _integer(birth_floor) and _integer(start) and _integer(end)
            and birth_floor <= start <= int(_time(run.get("started_at")).timestamp())
            and start <= end <= int(_time(session.get("ended_at")).timestamp())
            and end >= int(_time(session.get("started_at")).timestamp())
            and not run.get("recovered_existing_save") and not run.get("adopted_active_runtime")
        )
        if not confirmed:
            eligibility = "terminal_unverified"
        else:
            terminal_id = digest({"run_id": run_id, "terminal": dict(terminal)})
            if context["origin"] not in {"scheduled", "rotation"}:
                eligibility = "manual_origin" if context["origin"] == "manual" else "origin_unverified"
            elif finish_reason == "operator_stop":
                eligibility = "operator_stop"
            elif finish_reason not in {"terminal", "stalled"}:
                eligibility = "finish_reason_unverified"
            elif run.get("status") not in {"dead", "ascended"}:
                eligibility = "unsupported_terminal"
            else:
                eligibility = "terminal_scheduled"
    source = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "scope": "expedition",
        "run_id": run_id,
        "session_id": context["session_id"],
        "corner_id": context["corner_id"],
        "origin": context["origin"],
        "authorized_mode": "off",
        "start_request_id": context["start_request_id"],
        "finish_reason": finish_reason,
        "run_status": run.get("status"),
        "terminal_event_id": terminal_id,
        "restoration": deepcopy(restoration),
        "recorded_at": _time(session.get("ended_at")).isoformat(),
        "eligibility": eligibility,
        # The enclosing corner/program slot has not yet been released here.
        "slot_release_verified": False,
    }
    source["source_id"] = digest(source)
    return source
