#!/usr/bin/env python3
"""Apply the reviewed improve provider-failure backoff fix to the exact live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/strategy/ai.sh")
OLD_SHA256 = "062f8661c5874188461470868e156e4ffea78ad7455c7ab65673bb995cddb01a"
NEW_SHA256 = "d4f7f508791c80410118e15d278f33299783d6f5f5a77179cb10f164cd883f58"

OLD_MARKER = '''\treturn 0
}

run_ai() {
'''
NEW_MARKER = '''\treturn 0
}

_run_ai_mark_shared_failure_backoff() {
\tlocal agent="${1:-}" label="${2:-AI}" rc="${3:-1}"
\tlocal backoff_sec streak shift streak_max
\t[ -n "$agent" ] || return 0
\t[ "$rc" -ne 0 ] 2>/dev/null || return 0
\tcommand -v _ai_backoff_set >/dev/null 2>&1 || return 0

\tif [ "$rc" -eq 79 ] && command -v _ai_backoff_sec_for_agent >/dev/null 2>&1; then
\t\tbackoff_sec=$(_ai_backoff_sec_for_agent "$agent" "$label")
\telse
\t\tbackoff_sec="${AI_BACKOFF_FAILURE_SEC:-300}"
\t\tcase "$backoff_sec" in
\t\t'' | *[!0-9]*) backoff_sec=300 ;;
\t\tesac
\t\t[ "$backoff_sec" -lt 1 ] && backoff_sec=300
\t\tif command -v _ai_fail_streak_record >/dev/null 2>&1; then
\t\t\tstreak=$(_ai_fail_streak_record "$agent")
\t\t\tcase "$streak" in '' | *[!0-9]*) streak=1 ;; esac
\t\t\tstreak_max="${AI_FAILURE_STREAK_MAX_BACKOFF_SEC:-3600}"
\t\t\tcase "$streak_max" in '' | *[!0-9]*) streak_max=3600 ;; esac
\t\t\tif [ "$streak" -gt 1 ]; then
\t\t\t\tshift=$((streak - 1))
\t\t\t\t[ "$shift" -gt 3 ] && shift=3
\t\t\t\tbackoff_sec=$((backoff_sec * (1 << shift)))
\t\t\t\t[ "$backoff_sec" -gt "$streak_max" ] && backoff_sec="$streak_max"
\t\t\tfi
\t\tfi
\tfi
\t_ai_backoff_set "$agent" "$backoff_sec"
\tlog "[$label] ${agent} provider/CLI failure (rc=${rc}) → shared backoff ${backoff_sec}s"
}

run_ai() {
'''

OLD_PRIMARY = '''\t\trun_cmd "$primary" "$attempt_prompt" "$expect" "$expect_snapshot" "$expect_was_present"
\t\tprimary_ret=$?
\t\tlog "[$label] run_cmd returned rc=$primary_ret (attempt ${attempt}/${primary_attempts})"
\t\t# トークン超過 or 空応答: セッションが汚染されている → primary ループ打ち切り
'''
NEW_PRIMARY = '''\t\trun_cmd "$primary" "$attempt_prompt" "$expect" "$expect_snapshot" "$expect_was_present"
\t\tprimary_ret=$?
\t\tlog "[$label] run_cmd returned rc=$primary_ret (attempt ${attempt}/${primary_attempts})"
\t\tif [ "${RUN_AI_SHARED_FAILURE_BACKOFF:-0}" = "1" ] && [ "$primary_ret" -ne 0 ]; then
\t\t\t_run_ai_mark_shared_failure_backoff "$primary" "$label" "$primary_ret"
\t\t\tlog "[$label] provider/CLI failure entered shared backoff → skip remaining primary attempts"
\t\t\tbreak
\t\tfi
\t\t# トークン超過 or 空応答: セッションが汚染されている → primary ループ打ち切り
'''

OLD_LIST_CALL = '''\t\tlog "[$label] run_ai_list: try ${agent}"
\t\trun_ai "$label" "$agent" "" "$@"
\t\trc=$?
\t\tif [ "$rc" -eq 0 ]; then
'''
NEW_LIST_CALL = '''\t\tlog "[$label] run_ai_list: try ${agent}"
\t\tlocal _prev_shared_failure_backoff="${RUN_AI_SHARED_FAILURE_BACKOFF-}"
\t\tRUN_AI_SHARED_FAILURE_BACKOFF=1
\t\trun_ai "$label" "$agent" "" "$@"
\t\trc=$?
\t\tif [ -n "$_prev_shared_failure_backoff" ]; then
\t\t\tRUN_AI_SHARED_FAILURE_BACKOFF="$_prev_shared_failure_backoff"
\t\telse
\t\t\tunset RUN_AI_SHARED_FAILURE_BACKOFF
\t\tfi
\t\tif [ "$rc" -eq 0 ]; then
'''


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    replacements = (
        (OLD_MARKER, NEW_MARKER, "helper marker"),
        (OLD_PRIMARY, NEW_PRIMARY, "primary failure block"),
        (OLD_LIST_CALL, NEW_LIST_CALL, "run_ai_list call block"),
    )
    for old, new, label in replacements:
        if text.count(old) != 1:
            raise ValueError(f"reviewed {label} preimage mismatch")
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
