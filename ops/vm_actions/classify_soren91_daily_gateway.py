#!/usr/bin/env python3
"""Classify the fixed VM-gateway result for Soren91 daily improvement.

The production gateway deliberately withholds command stdout/stderr and returns
only a fixed JSON envelope. This helper accepts that envelope on stdin,
validates its exact shape, and emits one fixed category. It never prints the
raw gateway response, operation id, match evidence, prompts, or provider text.
"""
from __future__ import annotations

import json
import re
import sys

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
OPERATION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
EXPECTED_KEYS = {"status", "sha", "exit_code", "output", "operation_id"}

EXIT_CATEGORIES = {
    0: "success",
    75: "persist_busy",
    77: "wrong_user",
    78: "runtime_missing",
    79: "runner_missing",
    80: "persist_clone_missing",
    81: "node_missing",
    82: "gh_missing",
    83: "flock_missing",
    84: "python_missing",
    85: "state_invalid",
    86: "state_bootstrap_failure",
    87: "opencode_missing",
    88: "script_missing",
    90: "persist_repo",
    91: "runtime_compatibility",
    92: "evidence_blocked",
    93: "model_no_candidate",
    94: "candidate_invalid",
    95: "evidence_invalid",
    96: "pr_failure",
    97: "strategy_race",
    98: "preflight_other",
    99: "runtime_other",
    100: "opencode_provider_failure",
    101: "opencode_output_invalid",
    102: "opencode_cli_failure",
    103: "opencode_failure_other",
    104: "legacy_cli_missing",
    105: "opencode_json_invalid",
    106: "opencode_tool_or_error_event",
    107: "opencode_smoke_model_unavailable",
    108: "opencode_smoke_agent_unavailable",
    109: "opencode_smoke_config_invalid",
    110: "opencode_smoke_auth_failure",
    111: "opencode_smoke_provider_limit",
    112: "opencode_smoke_cli_other",
    113: "opencode_smoke_timeout",
    114: "opencode_full_permission_or_tool",
    115: "opencode_full_context_limit",
    116: "opencode_full_output_limit",
    117: "opencode_full_request_invalid",
    118: "opencode_full_safety_reject",
    119: "opencode_full_maxbuffer",
    120: "opencode_full_timeout",
    121: "opencode_full_network",
    122: "opencode_full_provider_unavailable",
    123: "opencode_nonzero_error_event",
    124: "opencode_nonzero_error_part",
    125: "opencode_nonzero_tool_event",
    126: "opencode_nonzero_unexpected_event",
    127: "opencode_nonzero_invalid_json",
    128: "opencode_nonzero_structured",
    129: "opencode_nonzero_no_json",
    130: "candidate_missing_decide",
    131: "candidate_decide_not_function",
    132: "candidate_return_invalid",
    133: "candidate_x_out_of_range",
    134: "candidate_behavior_contract",
    135: "candidate_undefined_variable",
    136: "candidate_code_error",
}


def classify_gateway_result(raw: str, expected_sha: str, ssh_rc: int) -> str:
    if not SHA_RE.fullmatch(expected_sha or ""):
        return "gateway_response_invalid"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "gateway_response_invalid"
    if not isinstance(data, dict) or set(data) != EXPECTED_KEYS:
        return "gateway_response_invalid"
    if data.get("status") != "executed" or data.get("sha") != expected_sha:
        return "gateway_response_invalid"
    if data.get("output") != "withheld":
        return "gateway_response_invalid"
    operation_id = data.get("operation_id")
    if not isinstance(operation_id, str) or not OPERATION_ID_RE.fullmatch(operation_id):
        return "gateway_response_invalid"
    exit_code = data.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not 0 <= exit_code <= 254:
        return "gateway_response_invalid"
    if isinstance(ssh_rc, bool) or not isinstance(ssh_rc, int) or not 0 <= ssh_rc <= 255:
        return "gateway_response_invalid"
    if ssh_rc != exit_code:
        return "gateway_exit_mismatch"
    return EXIT_CATEGORIES.get(exit_code, "other")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("gateway_response_invalid")
        return 0
    expected_sha = argv[1]
    try:
        ssh_rc = int(argv[2], 10)
    except ValueError:
        print("gateway_response_invalid")
        return 0
    raw = sys.stdin.read(4097)
    if len(raw) > 4096:
        print("gateway_response_invalid")
        return 0
    print(classify_gateway_result(raw, expected_sha, ssh_rc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
