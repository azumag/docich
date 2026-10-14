#!/usr/bin/env python3
"""Apply reviewed Soren AI queue fixes to the exact observed live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/lib/ai_generate.sh")
OLD_SHA256 = "bca120948577e27cb0aad5f718fa5ead504f2d7d4aa35f998a15a0399d21559b"
NEW_SHA256 = "8b43d7ddc305d5ec80e8c78aa1c35ade5f392979078564a06611b0bceb2fbd4a"

REPLACEMENTS = (
    (
        '\tlocal stale_sec="${AI_GENERATION_QUEUE_STALE_SEC:-900}"\n'
        '\tlocal waited=0 token now mt age owner_summary=""\n'
        '\tlock_dir=$(_ai_generation_queue_lock_dir "$label")\n'
        '\n'
        '\tcase "$wait_sec" in\n'
        '\t\'\' | *[!0-9]*) wait_sec=2 ;;\n'
        '\tesac\n'
        '\t[ "$wait_sec" -lt 1 ] && wait_sec=1\n'
        '\tcase "$stale_sec" in\n'
        '\t\'\' | *[!0-9]*) stale_sec=900 ;;\n'
        '\tesac\n'
        '\t[ "$stale_sec" -lt 60 ] && stale_sec=60\n'
        '\n'
        '\tmkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true\n'
        '\ttoken="${BASHPID:-$$}:$RANDOM:$(date +%s)"\n'
        '\n'
        '\twhile ! mkdir "$lock_dir" 2>/dev/null; do\n'
        '\t\tnow=$(date +%s)\n',
        '\tlocal stale_sec="${AI_GENERATION_QUEUE_STALE_SEC:-900}"\n'
        '\tlocal waited=0 token now mt age owner_summary="" owner_pid=""\n'
        '\tlock_dir=$(_ai_generation_queue_lock_dir "$label")\n'
        '\n'
        '\tcase "$wait_sec" in\n'
        '\t\'\' | *[!0-9]*) wait_sec=2 ;;\n'
        '\tesac\n'
        '\t[ "$wait_sec" -lt 1 ] && wait_sec=1\n'
        '\tcase "$stale_sec" in\n'
        '\t\'\' | *[!0-9]*) stale_sec=900 ;;\n'
        '\tesac\n'
        '\t[ "$stale_sec" -lt 60 ] && stale_sec=60\n'
        '\n'
        '\tmkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true\n'
        '\ttoken="${BASHPID:-$$}:$RANDOM:$(date +%s)"\n'
        '\n'
        '\twhile ! mkdir "$lock_dir" 2>/dev/null; do\n'
        '\t\towner_pid=$(sed -n \'s/^pid=//p\' "$lock_dir/owner" 2>/dev/null | head -n 1)\n'
        '\t\tcase "$owner_pid" in\n'
        '\t\t\'\' | *[!0-9]*) ;;\n'
        '\t\t*)\n'
        '\t\t\tif ! kill -0 "$owner_pid" 2>/dev/null; then\n'
        '\t\t\t\tlog "[AIQ:${label}] dead generation lock owner cleared (pid=${owner_pid})" >&2\n'
        '\t\t\t\trm -rf "$lock_dir" 2>/dev/null || true\n'
        '\t\t\t\tcontinue\n'
        '\t\t\tfi\n'
        '\t\t\t;;\n'
        '\t\tesac\n'
        '\t\tnow=$(date +%s)\n',
    ),
    (
        '\t\toutput=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override")\n'
        '\t\trc=$?\n'
        '\t\tAI_DISPATCH_VALIDATOR=""\n',
        '\t\toutput=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override")\n'
        '\t\trc=$?\n'
        '\t\t# 改善ゲートの打ち切りはモデル非依存で、実際のprovider呼び出しも発生していない。\n'
        '\t\t# fallbackを続けると全候補へ偽のfail streak/backoffを付けるため、その場で伝播する。\n'
        '\t\tif [ "$rc" -eq "$AI_GATE_GIVEUP_RC" ]; then\n'
        '\t\t\t[ -n "$failure_kind_file" ] && printf \'gate_giveup\\n\' >"$failure_kind_file"\n'
        '\t\t\tAI_DISPATCH_VALIDATOR="$saved_validator"\n'
        '\t\t\treturn "$AI_GATE_GIVEUP_RC"\n'
        '\t\tfi\n'
        '\t\tAI_DISPATCH_VALIDATOR=""\n',
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise ValueError("reviewed hotfix preimage fragment mismatch")
        text = text.replace(old, new, 1)
    return text


def apply(path: Path = TARGET) -> str:
    raw = path.read_bytes()
    current = sha256(raw)
    if current == NEW_SHA256:
        return "already_applied"
    if current != OLD_SHA256:
        raise ValueError(f"refusing unreviewed live drift: sha256={current}")
    updated = transform(raw.decode("utf-8")).encode("utf-8")
    if sha256(updated) != NEW_SHA256:
        raise ValueError("hotfix output hash does not match reviewed soviet_now main")
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(updated)
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
    if sha256(path.read_bytes()) != NEW_SHA256:
        raise RuntimeError("post-replace verification failed")
    return "applied"


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
