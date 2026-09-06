#!/usr/bin/env python3
"""Apply the reviewed improve shared-backoff fix to the exact live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/strategy/ai.sh")
OLD_SHA256 = '179e2a0624248d5c66fa02deceaa253866a9dba6b50e2ed7fde1fce7021c726c'
NEW_SHA256 = '062f8661c5874188461470868e156e4ffea78ad7455c7ab65673bb995cddb01a'
OLD_BLOCK = 'run_ai_list() {\n\tlocal label="$1" agent_list_raw="$2"\n\tshift 2\n\tlocal agents=() _IFS_save="$IFS" agent rc saw_rate_limit=0 final_rc=1\n\tIFS=\',\' read -ra agents <<< "$agent_list_raw"\n\tIFS="$_IFS_save"\n\n\tfor agent in "${agents[@]}"; do\n\t\tagent=$(printf \'%s\' "$agent" | sed \'s/^[[:space:]]*//; s/[[:space:]]*$//\')\n\t\t[ -n "$agent" ] || continue\n\t\tlog "[$label] run_ai_list: try ${agent}"\n\t\trun_ai "$label" "$agent" "" "$@"\n\t\trc=$?\n\t\tif [ "$rc" -eq 0 ]; then\n\t\t\tlog "[$label] run_ai_list: ${agent} OK"\n\t\t\tif command -v _ai_stats_record >/dev/null 2>&1; then\n\t\t\t\t_ai_stats_record "winner" "$label" "$agent" "0" "$(_run_cmd_resolved_model "$agent")"\n\t\t\tfi\n\t\t\treturn 0\n\t\tfi\n\t\tif [ "$rc" -eq 79 ]; then\n\t\t\tsaw_rate_limit=1\n\t\t\tlog "[$label] ${agent} rate-limited → next model"\n\t\t\tcontinue\n\t\tfi\n\t\tlog "[$label] ${agent} failed (rc=$rc) → next model"\n\tdone\n\n\tif [ "$saw_rate_limit" -eq 1 ]; then\n\t\tlog "[$label] run_ai_list: all models rate-limited → caller back off"\n\t\tfinal_rc=79\n\telse\n\t\tlog "[$label] run_ai_list: all models failed (list=${agent_list_raw})"\n\t\tfinal_rc=1\n\tfi\n\tif [ "$final_rc" -eq 1 ] && command -v _ai_stats_record >/dev/null 2>&1; then\n\t\tlocal resolved_models="" candidate\n\t\tfor candidate in "${agents[@]}"; do\n\t\t\tcandidate=$(printf \'%s\' "$candidate" | sed \'s/^[[:space:]]*//; s/[[:space:]]*$//\')\n\t\t\t[ -n "$candidate" ] || continue\n\t\t\tif [ -n "$resolved_models" ]; then\n\t\t\t\tresolved_models="${resolved_models},$(_run_cmd_resolved_model "$candidate")"\n\t\t\telse\n\t\t\t\tresolved_models="$(_run_cmd_resolved_model "$candidate")"\n\t\t\tfi\n\t\tdone\n\t\t_ai_stats_record "all_failed" "$label" "" "" "$resolved_models"\n\tfi\n\treturn "$final_rc"\n}\n'
NEW_BLOCK = 'run_ai_list() {\n\tlocal label="$1" agent_list_raw="$2"\n\tshift 2\n\tlocal agents=() _IFS_save="$IFS" agent rc saw_rate_limit=0 final_rc=1\n\tlocal attempted_count=0 skipped_backoff_count=0 backoff_remaining=""\n\tIFS=\',\' read -ra agents <<< "$agent_list_raw"\n\tIFS="$_IFS_save"\n\n\tfor agent in "${agents[@]}"; do\n\t\tagent=$(printf \'%s\' "$agent" | sed \'s/^[[:space:]]*//; s/[[:space:]]*$//\')\n\t\t[ -n "$agent" ] || continue\n\t\t# lib/ai_generate.sh is sourced before strategy/ai.sh in eloop_lib.sh.\n\t\t# Reuse its agent-scoped backoff so improve does not spend its wall-time\n\t\t# budget retrying a provider already parked by radio/comment generation.\n\t\tif command -v _ai_backoff_check >/dev/null 2>&1 && ! _ai_backoff_check "$agent"; then\n\t\t\tbackoff_remaining="unknown"\n\t\t\tif command -v _ai_backoff_remaining >/dev/null 2>&1; then\n\t\t\t\tbackoff_remaining=$(_ai_backoff_remaining "$agent")\n\t\t\tfi\n\t\t\tlog "[$label] ${agent} shared backoff skip (${backoff_remaining}s remaining)"\n\t\t\tskipped_backoff_count=$((skipped_backoff_count + 1))\n\t\t\tcontinue\n\t\tfi\n\t\tattempted_count=$((attempted_count + 1))\n\t\tlog "[$label] run_ai_list: try ${agent}"\n\t\trun_ai "$label" "$agent" "" "$@"\n\t\trc=$?\n\t\tif [ "$rc" -eq 0 ]; then\n\t\t\tlog "[$label] run_ai_list: ${agent} OK"\n\t\t\tif command -v _ai_stats_record >/dev/null 2>&1; then\n\t\t\t\t_ai_stats_record "winner" "$label" "$agent" "0" "$(_run_cmd_resolved_model "$agent")"\n\t\t\tfi\n\t\t\treturn 0\n\t\tfi\n\t\tif [ "$rc" -eq 79 ]; then\n\t\t\tsaw_rate_limit=1\n\t\t\tlog "[$label] ${agent} rate-limited → next model"\n\t\t\tcontinue\n\t\tfi\n\t\tlog "[$label] ${agent} failed (rc=$rc) → next model"\n\tdone\n\n\tif [ "$attempted_count" -eq 0 ] && [ "$skipped_backoff_count" -gt 0 ]; then\n\t\tlog "[$label] run_ai_list: all models are in shared backoff → caller back off"\n\t\tfinal_rc=79\n\telif [ "$saw_rate_limit" -eq 1 ]; then\n\t\tlog "[$label] run_ai_list: all models rate-limited → caller back off"\n\t\tfinal_rc=79\n\telse\n\t\tlog "[$label] run_ai_list: all models failed (list=${agent_list_raw})"\n\t\tfinal_rc=1\n\tfi\n\tif [ "$final_rc" -eq 1 ] && command -v _ai_stats_record >/dev/null 2>&1; then\n\t\tlocal resolved_models="" candidate\n\t\tfor candidate in "${agents[@]}"; do\n\t\t\tcandidate=$(printf \'%s\' "$candidate" | sed \'s/^[[:space:]]*//; s/[[:space:]]*$//\')\n\t\t\t[ -n "$candidate" ] || continue\n\t\t\tif [ -n "$resolved_models" ]; then\n\t\t\t\tresolved_models="${resolved_models},$(_run_cmd_resolved_model "$candidate")"\n\t\t\telse\n\t\t\t\tresolved_models="$(_run_cmd_resolved_model "$candidate")"\n\t\t\tfi\n\t\tdone\n\t\t_ai_stats_record "all_failed" "$label" "" "" "$resolved_models"\n\tfi\n\treturn "$final_rc"\n}\n'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    if text.count(OLD_BLOCK) != 1:
        raise ValueError("reviewed hotfix preimage block mismatch")
    return text.replace(OLD_BLOCK, NEW_BLOCK, 1)


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
