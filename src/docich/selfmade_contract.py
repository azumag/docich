"""Pure preparation contracts for #380; no runtime or OS attestation.

Only a future trusted host collector may supply preflight data. A matching
report, or a matching IPC proposal, never authorizes generated execution.
The existing prototype's start_generated remains unconditionally closed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .selfmade_prototype import (
    BUTTONS, MAX_TICKS, State, _fields, _integer, _json, _require,
    check_proposal,
)

CONTRACT_VERSION = 1
MAX_INPUT_BYTES = 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
CPU_MILLICORES = 1000
MEMORY_BYTES = 256 * 1024 * 1024
PROCESS_LIMIT = 16
_GUARANTEES = (
    "os_isolation", "non_root", "network_disabled", "no_capabilities",
    "bundle_read_only", "host_home_hidden", "credentials_hidden",
    "docker_socket_hidden", "no_host_fallback",
)


@dataclass(frozen=True)
class PreparedProfile:
    """Validated report data, deliberately not an executable sandbox handle."""
    version: int
    image_digest: str
    tmp_bytes: int


def validate_preflight(report, *, expected_image_digest: str) -> PreparedProfile:
    """Validate fixed reported guarantees; does not inspect or change the OS.

    The collector and the real negative isolation/resource probes remain
    unimplemented. Untrusted game/provider assertions are not evidence.
    No particular sandbox vendor, installation, or tmp size is selected here.
    """
    _require(type(expected_image_digest) is str and re.fullmatch(
        r"sha256:[0-9a-f]{64}", expected_image_digest), "invalid_image_digest")
    fields = ("version", "image_digest", "cpu_millicores", "memory_bytes",
              "process_limit", "output_bytes", "tmp_bytes", *_GUARANTEES)
    _fields(report, fields)
    _require(type(report["version"]) is int and
             report["version"] == CONTRACT_VERSION, "contract_version_mismatch")
    _require(type(report["image_digest"]) is str and
             report["image_digest"] == expected_image_digest, "image_digest_mismatch")
    for field in _GUARANTEES:
        _require(report[field] is True, "preflight_incomplete")
    for field, expected in (("cpu_millicores", CPU_MILLICORES),
                            ("memory_bytes", MEMORY_BYTES),
                            ("process_limit", PROCESS_LIMIT),
                            ("output_bytes", MAX_OUTPUT_BYTES)):
        _require(type(report[field]) is int and report[field] == expected,
                 "resource_contract_mismatch")
    # The public contract says limited tmp but does not select its size. Require
    # a positive declared limit without inventing a runtime default/upper bound.
    _require(type(report["tmp_bytes"]) is int and report["tmp_bytes"] > 0,
             "tmp_limit_required")
    return PreparedProfile(CONTRACT_VERSION, expected_image_digest, report["tmp_bytes"])


@dataclass(frozen=True)
class SolverInput:
    frame_id: str
    seq: int
    button: str | None
    ticks: int


def parse_input(raw: bytes, *, frame_id: str, previous_seq: int) -> SolverInput:
    """Existing four-field solver input, validated without advancing any state."""
    _require(type(raw) is bytes and len(raw) <= MAX_INPUT_BYTES, "input_limit")
    value = _json(raw)
    _fields(value, ("frame_id", "seq", "buttons", "ticks"))
    _require(type(value["frame_id"]) is str and value["frame_id"] == frame_id,
             "stale_frame")
    _integer(value["seq"], 1, MAX_TICKS)
    _require(type(previous_seq) is int and 0 <= previous_seq < MAX_TICKS and
             value["seq"] == previous_seq + 1, "invalid_seq")
    _integer(value["ticks"], 1, 60)
    buttons = value["buttons"]
    _require(type(buttons) is list and len(buttons) <= 1 and all(
        type(button) is str and button in BUTTONS for button in buttons), "invalid_buttons")
    return SolverInput(value["frame_id"], value["seq"],
                       buttons[0] if buttons else None, value["ticks"])


class OutputBudget:
    """Session aggregate budget for every byte on all generated output streams.

    A future transport must charge stdout, stderr, diagnostics, framing and
    drawing bytes, including malformed/rejected messages, before buffering.
    This helper itself performs no stream I/O and cannot stop a process.
    """
    def __init__(self):
        self.used = 0
        self.exhausted = False

    def consume(self, raw: bytes) -> None:
        _require(type(raw) is bytes, "invalid_output_bytes")
        if self.exhausted or len(raw) > MAX_OUTPUT_BYTES - self.used:
            self.exhausted = True
            _require(False, "output_limit")
        self.used += len(raw)


def parse_proposal(raw: bytes, *, expected_seq: int, expected_state: State,
                   budget: OutputBudget, max_message_bytes: int) -> State:
    """Internal v1 prep envelope: version/seq/tick/state, not a live wire ABI.

    seq identifies the already accepted solver batch and may repeat for its
    successive ticks. The trusted controller supplies the current expected seq
    and independently computes the expected transition. No proposal can update
    it. A per-message limit is mandatory but explicitly caller-selected: the
    public design fixes total output, not this wire limit. Channel identity,
    framing and the actual runtime choice of per-message limit are future work.
    """
    budget.consume(raw)
    _integer(max_message_bytes, 1, MAX_OUTPUT_BYTES)
    _require(len(raw) <= max_message_bytes, "message_limit")
    value = _json(raw)
    _fields(value, ("version", "seq", "tick", "state"))
    _require(type(value["version"]) is int and
             value["version"] == CONTRACT_VERSION, "contract_version_mismatch")
    _integer(expected_seq, 1, MAX_TICKS)
    _integer(value["seq"], 1, MAX_TICKS)
    _require(value["seq"] == expected_seq, "invalid_seq")
    _integer(value["tick"], 1, MAX_TICKS)
    _require(value["tick"] == expected_state.tick, "invalid_tick")
    check_proposal(expected_state, value["state"])
    # Return the trusted immutable value, never references from untrusted JSON.
    return expected_state
