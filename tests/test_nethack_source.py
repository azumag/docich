"""Pure source contracts. No game, subprocess, network, or production state."""
import datetime as dt
from copy import deepcopy
import uuid

import pytest

from docich.nethack_source import (
    SourceEvidenceError, build_post_restore_source, canonical_uuid, digest,
    new_session_context, runtime_identity, validate_session_context,
    verify_restoration, validate_restoration_summary,
)

NOW = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)
START = int(NOW.timestamp())
SOURCE = {"game": "nethack", "generation": 10, "runtime_id": "g10-0123abcd"}
TARGET = {"game": "sorengame", "generation": 12, "runtime_id": "g12-abcd0123"}


def restore_inputs():
    request = str(uuid.uuid4())
    return {
        "request_id": request, "source_runtime": deepcopy(SOURCE), "before": deepcopy(SOURCE),
        "previous_game": "sorengame",
        "receipt": {
            "schema_version": 1, "request_id": request, "status": "succeeded",
            "operation": "switch", "target": "sorengame", "generation": 12,
            "runtime_id": TARGET["runtime_id"],
            "updated_at": (NOW + dt.timedelta(seconds=100)).isoformat(),
            "result": {"status": "succeeded", "operation": "switch", "generation": 12,
                       "from_game": "nethack", "from_runtime": deepcopy(SOURCE),
                       "to_game": "sorengame", "cleanup_pending": False},
        },
        "canonical": {"schema_version": 2, "revision": 42, "phase": "ready",
                      "active": deepcopy(TARGET), "operation": None, "request_id": None,
                      "candidate": None, "previous": None, "retiring": []},
    }


def source_inputs(origin="scheduled", status="dead"):
    context = new_session_context(origin, str(uuid.uuid4()))
    session = {"session_id": context["session_id"], "corner_context": context,
               "runtime": deepcopy(SOURCE), "started_at": NOW.isoformat(),
               "ended_at": (NOW + dt.timedelta(seconds=110)).isoformat()}
    run = {"run_id": str(uuid.uuid4()), "status": status, "started_at": NOW.isoformat(),
           "birth_not_before_epoch": START,
           "terminal": {"source": "xlogfile", "identity_verified": True,
                        "starttime": START, "endtime": START + 90}}
    return run, session, verify_restoration(**restore_inputs())


@pytest.mark.parametrize("origin", ["scheduled", "rotation", "manual", "unknown"])
def test_context_never_authorizes_execution(origin):
    context = new_session_context(origin, str(uuid.uuid4()))
    assert validate_session_context(context) == context
    assert context["authorized_mode"] == "off"


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", 2), ("origin", "recovered"),
    ("origin", []), ("authorized_mode", "auto_promote"), ("secret", "sentinel"),
    ("session_id", "../escape"), ("corner_id", None), ("start_request_id", "bad"),
])
def test_context_rejects_invalid_fields(key, value):
    context = new_session_context("manual", str(uuid.uuid4()))
    context[key] = value
    with pytest.raises(SourceEvidenceError):
        validate_session_context(context)


@pytest.mark.parametrize("value", [None, "../x", "a" * 10000, 123, "A" * 36])
def test_invalid_uuid(value):
    with pytest.raises(SourceEvidenceError, match="^invalid_identity$"):
        canonical_uuid(value)


def test_context_returns_independent_copy():
    context = new_session_context("scheduled", str(uuid.uuid4()))
    copied = validate_session_context(context)
    copied["origin"] = "manual"
    assert context["origin"] == "scheduled"


def test_positive_restore_is_exactly_bound():
    result = verify_restoration(**restore_inputs())
    assert result["source_runtime"] == SOURCE
    assert result["restored_runtime"] == TARGET
    assert result["source_binding_verified"] is True
    assert result["cleanup_completed"] is True


@pytest.mark.parametrize("status", ["queued", "busy", "in_progress", "failed", "rolled_back"])
def test_pending_or_failed_is_not_restored(status):
    args = restore_inputs()
    args["receipt"]["status"] = status
    with pytest.raises(SourceEvidenceError):
        verify_restoration(**args)


@pytest.mark.parametrize("mutation", [
    "missing_source_binding", "different_source", "different_before", "wrong_request",
    "wrong_target", "target_new_generation", "pending_cleanup", "missing_cleanup",
    "retiring", "missing_retiring", "candidate", "operation", "missing_operation",
    "missing_result", "failed_result", "bool_schema", "bool_generation", "wrong_result_generation",
])
def test_ambiguous_restore_fails_closed(mutation):
    args = restore_inputs()
    r, c = args["receipt"], args["canonical"]
    if mutation == "missing_source_binding": del r["result"]["from_runtime"]
    elif mutation == "different_source": r["result"]["from_runtime"] = {**SOURCE, "runtime_id": "g10-11111111"}
    elif mutation == "different_before": args["before"] = {**SOURCE, "runtime_id": "g10-11111111"}
    elif mutation == "wrong_request": r["request_id"] = str(uuid.uuid4())
    elif mutation == "wrong_target": args["previous_game"] = "robots"
    elif mutation == "target_new_generation": c["active"] = {**TARGET, "generation": 13, "runtime_id": "g13-abcd0123"}
    elif mutation == "pending_cleanup": r["result"]["cleanup_pending"] = True
    elif mutation == "missing_cleanup": del r["result"]["cleanup_pending"]
    elif mutation == "retiring": c["retiring"] = [deepcopy(SOURCE)]
    elif mutation == "missing_retiring": del c["retiring"]
    elif mutation == "candidate": c["candidate"] = deepcopy(SOURCE)
    elif mutation == "operation": c["operation"] = "switch"
    elif mutation == "missing_operation": del c["operation"]
    elif mutation == "missing_result": del r["result"]
    elif mutation == "failed_result": r["result"]["status"] = "failed"
    elif mutation == "bool_schema": r["schema_version"] = True
    elif mutation == "bool_generation": r["generation"] = True
    elif mutation == "wrong_result_generation": r["result"]["generation"] = 15
    with pytest.raises(SourceEvidenceError):
        verify_restoration(**args)


def test_idle_restore_uses_stop_receipt():
    args = restore_inputs()
    args["previous_game"] = None
    args["receipt"].update(operation="stop", target=None)
    args["receipt"]["result"].update(operation="stop", to_game=None)
    args["canonical"].update(phase="idle", active=None)
    assert verify_restoration(**args)["restored_runtime"] is None


@pytest.mark.parametrize("origin,status,finish,expected", [
    ("scheduled", "dead", "terminal", "terminal_scheduled"),
    ("rotation", "ascended", "terminal", "terminal_scheduled"),
    ("scheduled", "dead", "stalled", "terminal_scheduled"),
    ("manual", "dead", "terminal", "manual_origin"),
    ("unknown", "dead", "terminal", "origin_unverified"),
    ("scheduled", "dead", "operator_stop", "operator_stop"),
    ("scheduled", "dead", "time_limit", "finish_reason_unverified"),
    ("scheduled", "dead", "unknown", "finish_reason_unverified"),
    ("scheduled", "suspended", "stalled", "expedition_not_terminal"),
    ("rotation", "active", "terminal", "expedition_not_terminal"),
    ("scheduled", "ended_unknown", "terminal", "terminal_unverified"),
    ("scheduled", "ended", "terminal", "unsupported_terminal"),
])
def test_eligibility_is_observation_not_permission(origin, status, finish, expected):
    run, session, restoration = source_inputs(origin, status)
    result = build_post_restore_source(run, session, restoration, finish_reason=finish)
    assert result["eligibility"] == expected
    assert result["authorized_mode"] == "off"
    assert result["slot_release_verified"] is False
    assert result["source_id"] == digest({k: v for k, v in result.items() if k != "source_id"})


@pytest.mark.parametrize("mutation", ["no_birth", "wrong_birth", "unverified", "future_terminal", "adopted", "recovered"])
def test_weak_terminal_evidence_never_eligible(mutation):
    run, session, restoration = source_inputs()
    if mutation == "no_birth": del run["birth_not_before_epoch"]
    elif mutation == "wrong_birth": run["terminal"]["starttime"] = START - 1000
    elif mutation == "unverified": run["terminal"]["identity_verified"] = False
    elif mutation == "future_terminal": run["terminal"]["endtime"] = START + 1000
    elif mutation == "adopted": run["adopted_active_runtime"] = True
    elif mutation == "recovered": run["recovered_existing_save"] = True
    assert build_post_restore_source(run, session, restoration, finish_reason="terminal")["eligibility"] == "terminal_unverified"


@pytest.mark.parametrize("mutation", ["session", "runtime", "end_order", "cleanup", "source_binding", "secret"])
def test_invalid_source_is_not_serialized(mutation):
    run, session, restoration = source_inputs()
    if mutation == "session": session["session_id"] = str(uuid.uuid4())
    elif mutation == "runtime": session["runtime"] = deepcopy(TARGET)
    elif mutation == "end_order": session["ended_at"] = NOW.isoformat()
    elif mutation == "cleanup": restoration["cleanup_completed"] = False
    elif mutation == "source_binding": restoration["source_binding_verified"] = False
    elif mutation == "secret": restoration["secret"] = "sentinel"
    with pytest.raises(SourceEvidenceError) as caught:
        build_post_restore_source(run, session, restoration, finish_reason="terminal")
    assert "sentinel" not in str(caught.value)


def test_sealed_source_does_not_share_mutable_input():
    run, session, restoration = source_inputs()
    result = build_post_restore_source(run, session, restoration, finish_reason="terminal")
    before = deepcopy(result)
    restoration["source_runtime"]["runtime_id"] = "g10-ffffffff"
    session["corner_context"]["origin"] = "manual"
    assert result == before


@pytest.mark.parametrize("value", [{"x": float("nan")}, {"x": float("inf")}, {"x": "a" * 9000}])
def test_digest_rejects_oversize_or_nonfinite(value):
    with pytest.raises(SourceEvidenceError):
        digest(value)


def test_summary_does_not_allow_raw_runtime_fields():
    _, _, restoration = source_inputs()
    restoration["source_runtime"]["argv"] = "secret-sentinel"
    with pytest.raises(SourceEvidenceError):
        validate_restoration_summary(restoration)
