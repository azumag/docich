"""Fixed administrative refusal codes; never publish private helper output."""
import json
import re
import sys

# Stable wire codes carried by the existing gateway exit_code envelope.
# Do not reuse shell preflight codes (25/64) or SSH transport code (255).
REFUSAL_CODES = {
    "expected_reservation_required": 81,
    "reservation_not_in_scope": 82,
    "clock_regressed": 83,
    "reservation_changed": 84,
    "context_changed": 85,
    "program_queue_unverified": 86,
    "owner_not_terminal": 87,
    "owner_requires_reconciliation": 88,
    "program_owner_unverified": 89,
    "switch_not_stable": 90,
    "target_active": 91,
    "receipt_now_present": 92,
    "invalid_admin_audit": 93,
    "already_admin_released": 94,
    "admin_audit_limit": 95,
    "unsafe_state": 96,
    "missing_evidence": 97,
    "unreadable_evidence": 98,
    "invalid_evidence": 99,
    "unsafe_lock": 100,
    "busy": 101,
    "lock_unavailable": 102,
    "evidence_unverified": 103,
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def public_result(raw, mode, sha, gateway_rc):
    """Accept only a matching execution envelope, including transport status."""
    reason = "gateway_result_unverified"
    try:
        result = json.loads(raw, object_pairs_hook=_unique_object)
        code = result.get("exit_code")
        if (mode not in {"check", "release"} or not re.fullmatch(r"[0-9a-f]{40}", sha)
                or not isinstance(result, dict) or result.get("status") != "executed"
                or result.get("sha") != sha or type(code) is not int
                or type(gateway_rc) is not int or not 0 <= code <= 254
                or gateway_rc != code or result.get("output") != "withheld"):
            raise ValueError("unverified envelope")
        if code == 0:
            return {"status": "admin-eligible" if mode == "check" else "admin-released",
                    "corner": "hanjuku-hero", "cancellation_authority": False}, 0
        reason = {value: key for key, value in REFUSAL_CODES.items()}.get(code, {
            25: "helper_preflight_failed", 64: "helper_arguments_invalid",
        }.get(code, "helper_failed"))
    except (ValueError, TypeError, AttributeError, RecursionError):
        pass
    return {"status": "refused", "reason": reason,
            "corner": "hanjuku-hero", "cancellation_authority": False}, 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        raw, mode, sha, status = args
        gateway_rc = int(status)
    except (ValueError, TypeError):
        raw, mode, sha, gateway_rc = "", "", "", -1
    result, rc = public_result(raw, mode, sha, gateway_rc)
    print(json.dumps(result, separators=(",", ":")))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
