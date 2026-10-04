"""Owner-approved administrative release of one reservation with unknown history.

This is deliberately separate from receipt-based cancellation. It never
certifies non-dispatch or resource release, and never edits an execution receipt.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import time

from .config import load_global
from .corner_rotation import timestamp
from .game_switch import GameSwitchStore, atomic_write_json, validate_request_id, validate_state
from .hanjuku_manual_cancel import (
    CancelRefused, DIGEST, OWNER_FILES, TARGET, TERMINAL_OWNER, _existing_lock, _object,
)
from .trading.soren_output import resolve_soren_root


def release(g, *, expected, apply=False, now=time.time):
    """Compare the approved reservation and recheck current owners under locks.

    The owner's separate administrative approval accepts historical uncertainty.
    Only the rotation ledger changes; all other observations are read-only.
    """
    if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
        raise CancelRefused("expected_reservation_required")
    state_dir = Path(g.state_dir)
    program = resolve_soren_root(g) / "tmp/state"
    store = GameSwitchStore(state_dir)
    snapshots = {}

    def observe(path, *, optional=False):
        value = _object(path, optional=optional)
        if path in snapshots:
            # A registry can refer to an owner already read above. Preserve
            # its first observation, including absence, and reject drift now.
            if snapshots[path][0] != value:
                raise CancelRefused("context_changed")
        else:
            snapshots[path] = (value, optional)
        return value

    with ExitStack() as held:
        for path in (
            state_dir / "locks/corner-rotation.lock",
            state_dir / "locks/retro-corner.lock",
            state_dir / "locks/retro-corner-manual.lock",
            program / "docich_program.lock",
            state_dir / "locks/game-switch.lock",
        ):
            held.enter_context(_existing_lock(path))
        ledger_path = state_dir / "corner_rotation.json"
        state = observe(ledger_path)
        request = state.get("manual_pending")
        if (state.get("schema_version") != 1 or state.get("status") != "waiting"
                or state.get("reason") != "manual-request-needs-resume-or-recovery"
                or state.get("pending") is not None or not isinstance(request, dict)
                or request.get("corner") != TARGET
                or request.get("state_file") != "retro_corner_manual.json"
                or not isinstance(state.get("queued_manual"), dict)
                or state["queued_manual"].get("corner") != "weather"):
            raise CancelRefused("reservation_not_in_scope")
        request_id = validate_request_id(request.get("request_id"))
        selected = timestamp(request.get("selected_at"))
        validate_request_id(state["queued_manual"].get("request_id"))
        timestamp(state["queued_manual"].get("selected_at"))
        released_at = timestamp(now())
        if released_at < selected:
            raise CancelRefused("clock_regressed")
        fingerprint = hashlib.sha256(json.dumps(
            request, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        # Includes selected_at and every reservation field, not just its UUID.
        if fingerprint != expected:
            raise CancelRefused("reservation_changed")
        for name in OWNER_FILES:
            queue = observe(program / "docich_program_queue" / name, optional=True)
            if queue is not None and queue.get("status") not in {"done", "expired", "cancelled"}:
                raise CancelRefused("program_queue_unverified")
            owner = observe(state_dir / name, optional=True)
            if owner is not None:
                if owner.get("status") not in TERMINAL_OWNER:
                    raise CancelRefused("owner_not_terminal")
                if owner.get("rotation_request_id") == request_id:
                    raise CancelRefused("owner_requires_reconciliation")
        registry = observe(program / "docich_program_active.json", optional=True)
        if registry is not None:
            files = (*OWNER_FILES, "paper_corner.json", "paper_corner_manual.json",
                     "nethack_corner.json", "nethack_corner_manual.json",
                     "soren91_corner.json", "soren91_corner_manual.json", "weather_corner.json")
            registered = registry.get("owner_state")
            if registered not in {str(state_dir / name) for name in files}:
                raise CancelRefused("program_owner_unverified")
            owner = observe(Path(registered))
            if (owner.get("rotation_request_id") == request_id
                    or (owner.get("game") == TARGET and owner.get("status") not in TERMINAL_OWNER)):
                raise CancelRefused("program_owner_unverified")
        canonical = observe(store.canonical.path)
        validate_state(canonical)
        if (canonical.get("phase") not in {"idle", "ready"}
                or canonical.get("request_id") == request_id
                or canonical.get("candidate") is not None
                or canonical.get("previous") is not None or canonical.get("retiring")):
            raise CancelRefused("switch_not_stable")
        active = canonical.get("active")
        if active and active.get("game") == TARGET:
            raise CancelRefused("target_active")
        # A new receipt changes the approval premise. Never reinterpret it or
        # route through cancellation, even when it would be terminal/failed.
        if observe(store.receipts._path(request_id), optional=True) is not None:
            raise CancelRefused("receipt_now_present")
        audit = state.get("manual_admin_releases", [])
        if not isinstance(audit, list) or any(not isinstance(row, dict) for row in audit):
            raise CancelRefused("invalid_admin_audit")
        if any(row.get("reservation", {}).get("request_id") == request_id
               for row in audit if isinstance(row.get("reservation"), dict)):
            raise CancelRefused("already_admin_released")
        # Recheck every observation immediately before the one atomic write.
        # Cooperating writers use the locks above; changes from another writer
        # still refuse instead of overwriting another reservation or owner.
        for path, (value, optional) in snapshots.items():
            if _object(path, optional=optional) != value:
                raise CancelRefused("context_changed")
        result = {"status": "admin-released" if apply else "admin-eligible",
                  "corner": TARGET, "weather_queue_preserved": True,
                  "request_generation_coverage": "unknown",
                  "all_resources_released": None, "cancellation_authority": False}
        if not apply:
            return result
        updated = dict(state)
        updated["manual_admin_releases"] = [*audit, {
            "reservation": request, "at": released_at,
            "reason": "owner-approved-admin-release-with-unknown-history",
            "receipt_present": False, "request_generation_coverage": "unknown",
            "resource_attribution_unknown": True, "all_resources_released": None,
            "cancellation_authority": False,
        }]
        updated.update(manual_pending=None, status="waiting",
                       reason="manual-request-admin-released", error_kind=None)
        if len(json.dumps(updated, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode()) + 1 > 1024 * 1024:
            raise CancelRefused("admin_audit_limit")
        atomic_write_json(ledger_path, updated)
        return result


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
        result = {"status": "refused", "reason": str(exc)}
    except Exception:
        result = {"status": "refused", "reason": "evidence_unverified"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] in {"admin-eligible", "admin-released"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
