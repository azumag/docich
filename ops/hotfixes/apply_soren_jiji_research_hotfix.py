#!/usr/bin/env python3
"""Apply reviewed soviet_now#201 JIJI research routing fix to exact live preimages."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from typing import NamedTuple
from pathlib import Path

SOREN_ROOT = Path("/home/ubuntu/soren")
REVIEWED_SOVIET_NOW_COMMIT = "530f7a847c8c09d4e8469a27643d0dcc930c6105"

class PatchSpec(NamedTuple):
    relpath: str
    old_sha256: str
    new_sha256: str
    replacements: tuple[tuple[str, str], ...]

HELPERS_OLD = """\tprintf '%s' \"$1\" | grep -Eiq 'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|bad request|request_id|invalid error token|invalid token|not logged in|please run /login|unexpected error, check log file|failed to run the query|pragma wal_checkpoint|insufficient balance|no resource package|rate limit exceeded|freeusagelimiterror|degraded function cannot be invoked|function id .*degraded|providermodelnotfounderror|model not found|no such model|modelid|providerid|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|(^|[^[:alnum:]])error:[[:space:]]*gone|status[\"[:space:]]*:[[:space:]]*410|reached its end of life|is no longer available|unknownerror|unexpected server error'"""
HELPERS_NEW = """\tprintf '%s' \"$1\" | grep -Eiq 'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|bad request|request_id|invalid error token|invalid token|not logged in|please run /login|unexpected error, check log file|failed to run the query|pragma wal_checkpoint|insufficient balance|no resource package|rate limit exceeded|freeusagelimiterror|degraded function cannot be invoked|function id .*degraded|providermodelnotfounderror|model not found|no such model|modelid|providerid|agent [\"[:space:]]*[^\"[:space:]]+[\"[:space:]]* not found|free tier users do not have access to this model|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|(^|[^[:alnum:]])error:[[:space:]]*gone|status[\"[:space:]]*:[[:space:]]*410|reached its end of life|is no longer available|unknownerror|unexpected server error'"""

CORNERS_ROUTE_OLD = """\tcodex | codex:*)"""
CORNERS_ROUTE_NEW = """\tcodex | codex:* | opencode:* | opencode-go:* | vercel:* | amd:* | minimax-api:* | local | local:*)
\t\t# Provider/model specs must go through the common dispatcher.  Passing
\t\t# them to `opencode run --agent` treats the model name as an agent name,
\t\t# which can fall back to an unrelated default model and leak its error
\t\t# text into the grounding memo."""
CORNERS_PRIMARY_OLD = 'grounding_context=$(_run_opencode_jiji_research "minimax" "$research_prompt_file")'
CORNERS_PRIMARY_NEW = 'grounding_context=$(_run_opencode_jiji_research "${RADIO_MAIN_PREPASS_AGENT}" "$research_prompt_file")'

SPECS = (
    PatchSpec(
        "core/helpers.sh",
        "97ff8377bb7685da2a88975f823c48622798e06de9d21f48abc3e9269a7e746a",
        "af9954b97c0e1b09bd7c1161a672aa9909d8190763a61193fed4982a464d7850",
        ((HELPERS_OLD, HELPERS_NEW),),
    ),
    PatchSpec(
        "broadcast/radio_corners.sh",
        "80aa0e7d6e99f00706dbac67f54ee81f3cdccb6c53f6294e850f68a4e790032b",
        "846d08043c81617f8ebd1414180cec9a3d51ca32fa297be07383656c940e1fb7",
        ((CORNERS_ROUTE_OLD, CORNERS_ROUTE_NEW), (CORNERS_PRIMARY_OLD, CORNERS_PRIMARY_NEW)),
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str, spec: PatchSpec) -> str:
    out = text
    for old, new in spec.replacements:
        if out.count(old) != 1:
            raise ValueError(f"{spec.relpath}: reviewed preimage fragment mismatch")
        out = out.replace(old, new, 1)
    return out


def _atomic_replace(path: Path, data: bytes) -> None:
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, stat.S_IMODE(st.st_mode))
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def apply(root: Path = SOREN_ROOT) -> str:
    prepared: list[tuple[Path, PatchSpec, bytes]] = []
    already = 0
    for spec in SPECS:
        path = root / spec.relpath
        raw = path.read_bytes()
        current = sha256(raw)
        if current == spec.new_sha256:
            already += 1
            continue
        if current != spec.old_sha256:
            raise ValueError(f"refusing unreviewed live drift: {spec.relpath} sha256={current}")
        updated = transform(raw.decode("utf-8"), spec).encode("utf-8")
        if sha256(updated) != spec.new_sha256:
            raise ValueError(f"{spec.relpath}: output hash does not match reviewed soviet_now source")
        prepared.append((path, spec, updated))

    # Preflight every target before mutating any file. A rerun after a partial OS-level
    # write is idempotent because reviewed postimages are accepted above.
    for path, spec, updated in prepared:
        _atomic_replace(path, updated)
        if sha256(path.read_bytes()) != spec.new_sha256:
            raise RuntimeError(f"post-replace verification failed: {spec.relpath}")

    return "already_applied" if already == len(SPECS) else "applied"


def main() -> int:
    if len(sys.argv) != 1:
        print("no arguments accepted", file=sys.stderr)
        return 2
    try:
        result = apply()
    except Exception as exc:
        print(f"hotfix refused: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
