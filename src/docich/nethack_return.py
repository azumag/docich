"""Read-only proof for the owner-approved legacy NetHack return contract.

Only old corners missing previous_game qualify. Two request-bound process-exit
acknowledgements substitute for the missing historical source lease; the live
target still requires its complete canonical identity and snapshot. This module
never changes a receipt, runtime, reservation, save, or process.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat

from .game_switch import validate_receipt, validate_request_id, validate_state
from .naming import runtime_id_generation, validate_runtime_id
from .retro_corner import RetroCornerManager

RECORD_KEY = "legacy_return_reconciliation"


class ReturnUnproven(ValueError):
    """Fixed refusal, with no operational values in the message."""


def _require(condition):
    if not condition:
        raise ReturnUnproven("legacy return evidence unproven")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _directory(root: Path, parts=()):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_record(root: Path, parts, *, optional=False):
    """Bound every component, reject links/devices/duplicates/oversized JSON."""
    parent = _directory(root, parts[:-1])
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            if optional:
                return None
            raise
        with os.fdopen(fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            _require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 65536)
            raw = handle.read(65537)
        _require(len(raw) <= 65536)
        value = json.loads(raw, object_pairs_hook=_unique_object)
        _require(isinstance(value, dict))
        return value
    finally:
        os.close(parent)


def _instant(value):
    _require(isinstance(value, str))
    parsed = dt.datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None and parsed.timestamp() >= 0)
    return parsed


def _digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def _receipt(root, request_id, status):
    validate_request_id(request_id)
    value = read_record(root, ("game-switch", "requests", f"{request_id}.json"))
    _require(type(value.get("schema_version")) is int and value["schema_version"] == 1)
    validate_receipt(value, root, expected_request_id=request_id)
    result = value.get("result")
    _require(value.get("status") == status and value.get("operation") == "switch"
             and value.get("target") == "sorengame" and isinstance(result, dict))
    _require(result.get("request_id") == request_id and result.get("operation") == "switch"
             and result.get("status") == status and result.get("from_game") == "nethack"
             and result.get("to_game") == "sorengame"
             and type(result.get("generation")) is int
             and result["generation"] == value["generation"])
    _require(_instant(value["created_at"]) <= _instant(value["updated_at"]))
    return value


def _boundary(root, runtime_id, request_id, player, receipt):
    validate_runtime_id(runtime_id)
    value = read_record(root, ("runtimes", runtime_id, "nethack_boundary.json"))
    _require(type(value.get("schema_version")) is int and value["schema_version"] == 1
             and type(value.get("generation")) is int
             and value["generation"] == runtime_id_generation(runtime_id)
             and value.get("runtime_id") == runtime_id and value.get("game") == "nethack"
             and value.get("request_id") == request_id and value.get("outcome") == "ended"
             and value.get("player_name") == player)
    _require(_instant(receipt["created_at"]) <= _instant(value.get("recorded_at"))
             <= _instant(receipt["updated_at"]))
    return value


def _restored_runtime(root, generation):
    directory = _directory(root, ("runtimes",))
    try:
        matches = []
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                _require(index < 8192)
                if entry.name.startswith(f"g{generation}-"):
                    matches.append(entry.name)
                    _require(len(matches) == 1)
        _require(len(matches) == 1)
        runtime_id = validate_runtime_id(matches[0])
        _require(runtime_id_generation(runtime_id) == generation)
        return runtime_id
    finally:
        os.close(directory)


def legacy_return_proof(root: Path, owner, ledger, *, player, now):
    """Caller holds rotation, tick, corner, and shared canonical locks."""
    from .corner_rotation import timestamp

    _require(type(owner.get("schema_version")) is int and owner["schema_version"] == 1
             and "previous_game" not in owner and owner.get("game") == "nethack"
             and owner.get("status") in {"failed", "interrupted"}
             and owner.get("finish_reason") == "terminal"
             and all(key not in owner for key in
                     ("restore_recovery", "restore_cleanup", "restore_cleanup_attempt")))
    _require(_digest(read_record(root, ("nethack_corner.json",))) == _digest(owner))
    _require(_digest(read_record(root, ("corner_rotation.json",))) == _digest(ledger)
             and type(ledger.get("schema_version")) is int and ledger["schema_version"] == 1)
    reservation = owner.get("rotation_request_id")
    restore_id = owner.get("switch_request_id")
    validate_request_id(reservation)
    validate_request_id(restore_id)
    original_runtime = validate_runtime_id(owner.get("rotation_runtime_id"))
    pending = ledger.get("pending")
    _require(ledger.get("status") == "recovery_required"
             and ledger.get("manual_pending") is None and isinstance(pending, dict)
             and pending.get("corner") == "nethack" and pending.get("phase") == "dispatched"
             and pending.get("request_id") == reservation)
    manual = read_record(root, ("nethack_corner_manual.json",), optional=True)
    if manual is not None:
        _require(type(manual.get("schema_version")) is int and manual["schema_version"] == 1
                 and manual.get("status") in {"idle", "completed", "interrupted", "expired"})
    canonical = read_record(root, ("game_switch.json",))
    _require(type(canonical.get("schema_version")) is int and canonical["schema_version"] == 2)
    validate_state(canonical)
    _require(RetroCornerManager._restore_canonical_clean(canonical)
             and canonical.get("deadline_at") is None
             and _instant(canonical.get("updated_at")) <= now)
    last = canonical.get("last_result")
    _require(isinstance(last, dict))
    return_id = validate_request_id(last.get("request_id"))
    _require(len({reservation, restore_id, return_id}) == 3)
    original = _receipt(root, restore_id, "rolled_back")
    landed = _receipt(root, return_id, "succeeded")
    result = original["result"]
    # This exception is only for old replace-mode rollbacks lacking durable
    # source identities, never a modern proof or an in-place lease renewal.
    _require("source_runtime" not in result and "restored_runtime" not in result
             and "source_runtime" not in landed["result"])
    restored_generation = result.get("restored_generation")
    _require(type(restored_generation) is int
             and runtime_id_generation(original_runtime) < original["generation"]
             < restored_generation < landed["generation"])
    _require(result.get("cleanup_pending") is None or type(result.get("cleanup_pending")) is bool)
    _require(landed["result"].get("cleanup_pending") is None
             or landed["result"].get("cleanup_pending") is False)
    if owner.get("last_error_code") is not None:
        _require(owner["last_error_code"] == result.get("error_code"))
    started = _instant(owner.get("started_at"))
    completed = _instant(owner.get("completed_at"))
    _require(timestamp(ledger.get("last_seen_at")) <= now.timestamp()
             and timestamp(pending.get("selected_at")) <= started.timestamp())
    _require(started <= completed <= _instant(original["created_at"])
             <= _instant(original["updated_at"]) <= _instant(landed["created_at"])
             <= _instant(landed["updated_at"]) <= now)
    first = _boundary(root, original_runtime, restore_id, player, original)
    restored_runtime = _restored_runtime(root, restored_generation)
    second = _boundary(root, restored_runtime, return_id, player, landed)
    active = RetroCornerManager._restore_source_identity(canonical.get("active"))
    _require(active is not None and canonical["active"].get("adapter") == "soren"
             and RetroCornerManager._succeeded_start_receipt_matches_active(
                 canonical, landed, request_id=return_id, target="sorengame")
             and last == landed["result"])
    # Capture the complete owner input, not just the fields used above. A new
    # run/history mutation cannot inherit an old reconciliation authorization.
    owner_input = {key: value for key, value in owner.items()
                   if key not in {RECORD_KEY, "status", "switch_status"}}
    return {
        "schema_version": 1, "rotation_request_id": reservation,
        "restore_request_id": restore_id, "return_request_id": return_id,
        "original_source": {"game": "nethack", "runtime_id": original_runtime,
                            "generation": runtime_id_generation(original_runtime)},
        "rollback_source": {"game": "nethack", "runtime_id": restored_runtime,
                            "generation": restored_generation},
        "active_runtime": active, "canonical_revision": canonical["revision"],
        "return_completed_at": landed["updated_at"],
        "canonical_sha256": _digest(canonical),
        "evidence_sha256": _digest([owner_input, ledger, original, landed, first, second, manual]),
    }


def saved_record_matches(record, proof, *, now):
    _require(isinstance(record, dict)
             and set(record) == set(proof) | {"phase", "prepared_at"}
             and type(record.get("schema_version")) is int
             and record.get("phase") in {"prepared", "committed"}
             and _instant(proof["return_completed_at"]) <= _instant(record.get("prepared_at")) <= now)
    # Equality alone accepts bool/float generations; compare canonical JSON too.
    _require(_digest({key: record[key] for key in proof}) == _digest(proof))
    return record["phase"]
