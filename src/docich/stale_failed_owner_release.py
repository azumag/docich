"""Owner-approved release of a stale failed corner owner with no evidence.

Deliberately separate from ``corner-rotation recover`` and from the
per-request reconcile hooks. It targets exactly one shape (#3682): a corner
state file still holds a ``failed`` owner whose release evidence left every
durable record (the ``released_requests`` pointer moved to a newer request
and the bounded audit row was evicted). Nothing then proves that owner was
released, so ``_observe`` keeps it BUSY and the whole rotation waits. This
operation re-checks the shared program slot and, only when the slot itself
proves the request left nothing behind, re-writes the release evidence and
asks the adapter to retire the owner through the normal
``settle_released_failure`` hook. It never starts a corner, never edits a
receipt, and never certifies resource release beyond the slot proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from .config import load_global
from .corner_rotation import CornerRotationManager, timestamp
from .hanjuku_admin_result import REFUSAL_CODES
from .hanjuku_manual_cancel import CancelRefused, DIGEST

ADMIN_AUDIT_LIMIT = 16


def _serialized(value):
    # Dict equality equates JSON true/1 and integer/float values. Approval
    # fingerprints must preserve those type distinctions.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(corner_id, request_id):
    """Public derivation the owner reproduces before dispatching the op.

    ``sha256(json.dumps({"corner": <id>, "request_id": <uuid>},
    sort_keys=True, separators=(",",":")))`` — only the two identity fields,
    never file contents or receipts.
    """
    return hashlib.sha256(
        _serialized({"corner": corner_id, "request_id": request_id})).hexdigest()


def _evidence_present(state, raw, corner_id, request_id):
    released = state.get("released_requests")
    pointer = released.get(corner_id) if isinstance(released, dict) else None
    if isinstance(pointer, dict) and pointer.get("request_id") == request_id:
        return True
    if any(isinstance(row, dict)
           and row.get("corner") == corner_id
           and row.get("request_id") == request_id
           and row.get("resolution") == "released-unverified"
           for row in (state.get("failed_corners") or [])):
        return True
    pin = raw.get("released_settle_pending")
    return (isinstance(pin, dict)
            and pin.get("request_id") == request_id
            and pin.get("corner_id") == corner_id)


def release(g, *, expected, apply=False, now=time.time, manager=None):
    if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
        raise CancelRefused("expected_reservation_required")
    manager = manager or CornerRotationManager(g)
    with manager.locked() as acquired:
        if not acquired:
            raise CancelRefused("busy")
        tick_now = timestamp(now())
        state = manager.load(tick_now)
        if (state.get("status") not in {"ready", "waiting"}
                or state.get("pending") is not None
                or state.get("manual_pending") is not None):
            raise CancelRefused("reservation_not_in_scope")
        # Find the unique evidence-less failed owner the fingerprint names.
        target = None
        for corner_id, adapter in manager.adapters.items():
            try:
                rows = list(adapter.observations())
            except Exception:
                raise CancelRefused("evidence_unverified") from None
            for raw in rows:
                request_id = raw.get("rotation_request_id")
                if (raw.get("status") != "failed" or not isinstance(request_id, str)
                        or _evidence_present(state, raw, corner_id, request_id)):
                    continue
                if fingerprint(corner_id, request_id) != expected:
                    continue
                if target is not None:
                    raise CancelRefused("reservation_not_in_scope")
                target = (corner_id, adapter, request_id)
        if target is None:
            raise CancelRefused("reservation_not_in_scope")
        corner_id, adapter, request_id = target
        reservation = {"request_id": request_id, "corner": corner_id}
        if not manager._slot_reusable(adapter, reservation):
            raise CancelRefused("switch_not_stable")
        if not apply:
            return {"status": "admin-eligible", "corner": corner_id}
        # Re-read under the held lock: a changed observation, ledger or plane
        # is a refusal, never something to overwrite.
        state = manager.load(tick_now)
        try:
            rows = list(adapter.observations())
        except Exception:
            raise CancelRefused("evidence_unverified") from None
        if not any(raw.get("status") == "failed"
                   and raw.get("rotation_request_id") == request_id
                   and not _evidence_present(state, raw, corner_id, request_id)
                   for raw in rows):
            raise CancelRefused("context_changed")
        if not manager._slot_reusable(adapter, reservation):
            raise CancelRefused("context_changed")
        manager._record_failed_corner(
            state, reservation, resolution="released-unverified",
            error_kind="execution-unverified", now=tick_now,
            reason_code="stale-owner-admin-released")
        manager._mark_released_request(state, corner_id, request_id, tick_now)
        admin = state.get("stale_owner_admin_releases")
        if admin is None:
            admin = []
        if not isinstance(admin, list) or any(not isinstance(row, dict) for row in admin):
            raise CancelRefused("invalid_admin_audit")
        if any(row.get("request_id") == request_id for row in admin):
            raise CancelRefused("already_admin_released")
        admin.append({"corner": corner_id, "request_id": request_id,
                      "at": tick_now,
                      "reason": "owner-approved-stale-failed-owner-release"})
        state["stale_owner_admin_releases"] = admin[-ADMIN_AUDIT_LIMIT:]
        manager.save(state)
        # Retire through the normal adapter hook; a False/None outcome keeps
        # the shared state file excluded and the same settle is retried by
        # the next tick now that the release evidence exists again.
        manager._settle_released_failure(adapter, request_id)
        manager._release_program_owner(adapter)
        return {"status": "admin-released", "corner": corner_id}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "release"))
    parser.add_argument("--expected", required=True)
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / "config/docich.soren-live.toml")
        result = release(g, expected=args.expected, apply=args.operation == "release")
    except CancelRefused as exc:
        reason = str(exc)
        result = {"status": "refused",
                  "reason": reason if reason in REFUSAL_CODES else "evidence_unverified"}
    except Exception:
        result = {"status": "refused", "reason": "evidence_unverified"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] in {"admin-eligible", "admin-released"} \
        else REFUSAL_CODES[result["reason"]]


if __name__ == "__main__":
    raise SystemExit(main())
