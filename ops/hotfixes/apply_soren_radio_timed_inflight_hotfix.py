#!/usr/bin/env python3
"""Port soviet_now#195 timed-corner inflight recovery onto the exact live scheduler."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/broadcast/scheduler.sh")
SOURCE_FIX_SHA = "16ba1026af1d0aad2d2297003d7e8f8d9210b352"
OLD_SHA256 = "4e1f4278747ed04c344c79ad1c6e7f39c0020dc1057b6a928ba196d084547a79"
NEW_SHA256 = "918ebfd5f76511ca5c376eaa3f713f2511a5729c6aad68a22a63fb614367b041"

OLD_FRAGMENT = '\t_try_timed_corner() {\n\t\tlocal marker_key="$1" target_hh="$2" target_mm="$3"\n\t\tlocal marker="$TMP_MARKERS_DIR/.timed_corner_done_${today}_${marker_key}"\n\t\tlocal inflight="$TMP_MARKERS_DIR/.timed_corner_inflight_${today}_${marker_key}"\n\t\t[ -f "$marker" ] && return 1\n\t\tif ! mkdir "$inflight" 2>/dev/null; then\n\t\t\treturn 1 # another scheduler beat us\n\t\tfi\n\t\tlocal target=$((10#$target_hh * 60 + 10#$target_mm))\n\t\tlocal now=$((10#$current_hour * 60 + 10#$current_min))\n\t\tlocal diff=$((now - target))\n\t\t[ "$diff" -lt 0 ] && diff=$((-diff))\n\t\t[ "$diff" -le 15 ] || {\n\t\t\trmdir "$inflight" 2>/dev/null\n\t\t\treturn 1\n\t\t}\n\t\treturn 0\n\t}\n\n\t# 成功マーカーを作成するラッパー (バックグラウンドジョブ内で使用)\n\t_run_timed_corner() {\n\t\tlocal marker_key="$1" func="$2"\n\t\tshift 2\n\t\t"$func" "$@" &\n\t\tlocal _bg_pid=$!\n\t\twait "$_bg_pid"\n\t\tlocal _exit_code=$?\n\t\tif [ "$_exit_code" -eq 0 ]; then\n\t\t\ttouch "$TMP_MARKERS_DIR/.timed_corner_done_${today}_${marker_key}"\n\t\tfi\n\t\trmdir "$TMP_MARKERS_DIR/.timed_corner_inflight_${today}_${marker_key}" 2>/dev/null\n\t}\n\n\t# stale inflight marker クリーンアップ (前日以前を一掃)\n\tlocal _yesterday_marker_inf=$TMP_MARKERS_DIR/.timed_corner_inflight_$(date -d yesterday +%Y%m%d)_*\n\trm -f $_yesterday_marker_inf 2>/dev/null\n\t# 無日付の legacy marker のみ削除 (日付付き marker は保護)\n\tfor _f in "$TMP_MARKERS_DIR"/.timed_corner_inflight_*; do\n\t\t[ -e "$_f" ] || continue\n\t\tcase "$(basename "$_f")" in\n\t\t.timed_corner_inflight_[0-9]*) ;;\n\t\t*) rm -f "$_f" ;;\n\t\tesac\n\tdone\n'
NEW_FRAGMENT = '\t_timed_corner_release_inflight() {\n\t\tlocal inflight="$1"\n\t\t[ -d "$inflight" ] || return 0\n\t\trm -f "$inflight/owner" 2>/dev/null || return 1\n\t\trmdir "$inflight" 2>/dev/null\n\t}\n\n\t_timed_corner_write_owner() {\n\t\tlocal inflight="$1"\n\t\tprintf \'%s\\n\' "${BASHPID:-$$}" >"$inflight/owner" 2>/dev/null\n\t}\n\n\t_timed_corner_claim_inflight() {\n\t\tlocal inflight="$1" owner_pid=""\n\t\tif mkdir "$inflight" 2>/dev/null; then\n\t\t\t_timed_corner_write_owner "$inflight" || {\n\t\t\t\t_timed_corner_release_inflight "$inflight" 2>/dev/null || true\n\t\t\t\treturn 1\n\t\t\t}\n\t\t\treturn 0\n\t\tfi\n\n\t\t# A reload/crash may terminate the background timed-corner before its\n\t\t# normal rmdir runs.  Owner-aware markers let the next scheduler tick\n\t\t# reclaim only a marker whose exact job process is no longer alive.\n\t\towner_pid=$(cat "$inflight/owner" 2>/dev/null || true)\n\t\tcase "$owner_pid" in\n\t\t\'\' | *[!0-9]*) return 1 ;; # legacy/unknown marker: fail closed\n\t\tesac\n\t\tif kill -0 "$owner_pid" 2>/dev/null; then\n\t\t\treturn 1\n\t\tfi\n\t\t_timed_corner_release_inflight "$inflight" 2>/dev/null || return 1\n\t\tmkdir "$inflight" 2>/dev/null || return 1\n\t\t_timed_corner_write_owner "$inflight" || {\n\t\t\t_timed_corner_release_inflight "$inflight" 2>/dev/null || true\n\t\t\treturn 1\n\t\t}\n\t\treturn 0\n\t}\n\n\t_try_timed_corner() {\n\t\tlocal marker_key="$1" target_hh="$2" target_mm="$3"\n\t\tlocal marker="$TMP_MARKERS_DIR/.timed_corner_done_${today}_${marker_key}"\n\t\tlocal inflight="$TMP_MARKERS_DIR/.timed_corner_inflight_${today}_${marker_key}"\n\t\t[ -f "$marker" ] && return 1\n\t\t_timed_corner_claim_inflight "$inflight" || return 1\n\t\tlocal target=$((10#$target_hh * 60 + 10#$target_mm))\n\t\tlocal now=$((10#$current_hour * 60 + 10#$current_min))\n\t\tlocal diff=$((now - target))\n\t\t[ "$diff" -lt 0 ] && diff=$((-diff))\n\t\t[ "$diff" -le 15 ] || {\n\t\t\t_timed_corner_release_inflight "$inflight" 2>/dev/null || true\n\t\t\treturn 1\n\t\t}\n\t\treturn 0\n\t}\n\n\t# 成功マーカーを作成するラッパー (バックグラウンドジョブ内で使用)\n\t_run_timed_corner() {\n\t\tlocal marker_key="$1" func="$2"\n\t\tlocal inflight="$TMP_MARKERS_DIR/.timed_corner_inflight_${today}_${marker_key}"\n\t\tshift 2\n\t\t# Background functions have a distinct BASHPID while $$ remains the\n\t\t# long-lived radio worker.  Persist BASHPID so reload-killed jobs are\n\t\t# distinguishable from a still-running owner.\n\t\t_timed_corner_write_owner "$inflight" || {\n\t\t\t_timed_corner_release_inflight "$inflight" 2>/dev/null || true\n\t\t\treturn 1\n\t\t}\n\t\t"$func" "$@" &\n\t\tlocal _bg_pid=$!\n\t\twait "$_bg_pid"\n\t\tlocal _exit_code=$?\n\t\tif [ "$_exit_code" -eq 0 ]; then\n\t\t\ttouch "$TMP_MARKERS_DIR/.timed_corner_done_${today}_${marker_key}"\n\t\tfi\n\t\t_timed_corner_release_inflight "$inflight" 2>/dev/null || true\n\t}\n\n\t# 前日以前のdated markerは現在日のslotを排他しないため安全に除去できる。\n\t# 旧実装はdirectoryに rm -f を使っていたため一度も消えず蓄積していた。\n\tlocal _f _base _marker_date\n\tfor _f in "$TMP_MARKERS_DIR"/.timed_corner_inflight_*; do\n\t\t[ -e "$_f" ] || continue\n\t\t_base=$(basename "$_f")\n\t\tcase "$_base" in\n\t\t.timed_corner_inflight_[0-9]*)\n\t\t\t_marker_date=${_base#.timed_corner_inflight_}\n\t\t\t_marker_date=${_marker_date%%_*}\n\t\t\tif [[ "$_marker_date" =~ ^[0-9]{8}$ ]] && [[ "$_marker_date" < "$today" ]]; then\n\t\t\t\t_timed_corner_release_inflight "$_f" 2>/dev/null || true\n\t\t\tfi\n\t\t\t;;\n\t\t*)\n\t\t\tif [ -d "$_f" ]; then rmdir "$_f" 2>/dev/null || true; else rm -f "$_f" 2>/dev/null || true; fi\n\t\t\t;;\n\t\tesac\n\tdone\n'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    if text.count(OLD_FRAGMENT) != 1:
        raise ValueError("reviewed hotfix preimage fragment mismatch")
    return text.replace(OLD_FRAGMENT, NEW_FRAGMENT, 1)


def apply(path: Path = TARGET) -> str:
    raw = path.read_bytes()
    current = sha256(raw)
    if current == NEW_SHA256:
        return "already_applied"
    if current != OLD_SHA256:
        raise ValueError(f"refusing unreviewed live drift: sha256={current}")
    updated = transform(raw.decode("utf-8")).encode("utf-8")
    if sha256(updated) != NEW_SHA256:
        raise ValueError("hotfix output hash does not match reviewed production transform")
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
