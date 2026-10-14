#!/usr/bin/env python3
"""Regenerate tests/fixtures/comment_state_golden.json (#829 PR-3c).

Replays one scenario of comment-state operations against the legacy
``broadcast/comment.sh`` helpers (loaded by ``eloop_lib.sh`` as in production)
with a fixed clock (a ``date`` shim, ``TZ=UTC``), keeping the state files
between steps, and records each step's stdout / exit status plus the state
files afterwards. ``docich.comment.state`` replays the same steps. Run on
Linux like the other comment goldens (see comment_guard_golden.py).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comment_prompt_golden import bash  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests/fixtures/comment_state_golden.json"
T = 1790000000

TRACKED = [
    "tmp/.comment_queue/processed_batch_hashes.log",
    "tmp/.comment_queue/inflight_batch.log",
    "tmp/.comment_queue/processed_line_hashes.log",
    "tmp/state/comment_generation_backoff_until",
    "tmp/.comment_queue/spoken_history/.reply_hashes",
]

H1, H2 = "0f" * 16, "ab" * 16
BATCH = "alice: こんにちは\nbob: 音声が聞こえません\nalice: こんにちは\n\ncarol  : x: y"
STEPS = [
    ("hash", ["同志 alice: 今日も元気"], 0),
    ("hash", [""], 0),
    ("dedup_key", ["alice: hi: there"], 0), ("dedup_key", ["al ice : hi"], 0),
    ("dedup_key", ["no separator"], 0), ("dedup_key", ["trailing space : x"], 0),
    ("backoff_remaining", [], 0),
    ("handle_failure", ["true"], 0), ("backoff_remaining", [], 100),
    ("backoff_remaining", [], 700), ("backoff_remaining", [], 0),
    ("handle_failure", ["true"], 0), ("handle_failure", ["false"], 0), ("backoff_remaining", [], 0),
    ("backoff_set", ["abc"], 0), ("backoff_remaining", [], 1),
    ("backoff_set", ["0"], 0), ("backoff_remaining", [], 0), ("backoff_clear", [], 0),
    ("write", ["tmp/state/comment_generation_backoff_until", "12x\n"], 0), ("backoff_remaining", [], 0),
    ("recent_processed", [H1], 0), ("mark_processed", [H1], 0), ("recent_processed", [H1], 900),
    ("recent_processed", [H1], 901), ("mark_processed", [H2], 1000), ("mark_processed", [H1], 1100),
    ("write_append", ["tmp/.comment_queue/processed_batch_hashes.log", "garbage\n|\n17x|" + H2 + "\n"], 1100),
    ("recent_processed", [H2], 1100), ("mark_processed", [H2], 4000), ("recent_processed", [H1], 4000),
    ("inflight", [H1], 0), ("mark_inflight", [H1, ""], 0), ("inflight", [H1], 10), ("inflight", [H2], 10),
    ("clear_inflight", [H2], 10), ("inflight", [H1], 901), ("mark_inflight", [H1, "999999"], 1000),
    ("inflight", [H1], 1001), ("mark_inflight", [H1, "abc|def"], 1100), ("inflight", [H1], 1101),
    ("clear_inflight", [H1], 1101), ("mark_inflight", [H2, "12"], 1200), ("clear_inflight", [""], 1200),
    ("write", ["tmp/.comment_queue/inflight_batch.log", f"{T + 1300}|{H1}|"], 1300), ("inflight", [H1], 1300),
    ("filter_lines", [BATCH], 0), ("has_line", [BATCH], 0), ("record_lines", ["alice: こんにちは"], 0),
    ("filter_lines", [BATCH], 10), ("has_line", [BATCH], 10), ("has_line", ["zed: こんにちは"], 10),
    ("filter_lines", [BATCH], 1801), ("record_lines", [BATCH], 1900), ("filter_lines", [BATCH + "\ndave: new"], 1900),
    ("format_context", [BATCH], 0), ("format_context", ["  solo line  \r\nx: y"], 0), ("format_context", [""], 0),
    ("needs_thumbnail", ["今の盤面どう？"], 0), ("needs_thumbnail", ["こんにちは"], 0),
    ("needs_thumbnail", ["NEXT piece"], 0), ("needs_thumbnail", [""], 0),
    ("backlog", [], 0), ("touch", ["tmp/.comment_queue/comment_1.txt"], 0),
    ("touch", ["tmp/.comment_queue/comment_2.playing"], 0), ("touch", ["tmp/.comment_queue/other.txt"], 0),
    ("touch", ["tmp/.comment_queue/.comment_3.txt"], 0), ("backlog", [], 0),
    ("backlog_high", ["2", "total"], 0), ("backlog_high", ["2", "queued"], 0),
    ("remember", ["同志A、こんにちは。", "main"], 0), ("remember", ["同志A、こんにちは。", "main"], 1),
    ("remember", ["同志B、やあ。", "soren91"], 2), ("remember", ["", "main"], 3),
]
STEPS += [("remember", [f"返信{i}です。", "x"], 10 + i) for i in range(18)]
STEPS += [
    ("store_meta", ["tmp/.comment_queue/comment_9.txt", "main", "amd:DeepSeek", H1, "2", "57", "a", "b", ""], 0),
    ("store_meta", ["tmp/.comment_queue/comment_10.playing", "", "", "", "x", "", "", "", ""], 1),
]
SHELL = {
    "hash": '_comment_hash_text "$1"',
    "dedup_key": '_comment_dedup_key "$1"',
    "backoff_remaining": "_comment_failure_backoff_remaining",
    "backoff_set": '_comment_failure_backoff_set "$1"',
    "backoff_clear": "_comment_failure_backoff_clear",
    "handle_failure": '_comment_handle_generation_failure "$1"',
    "recent_processed": '_is_recent_comment_batch_processed "$1"',
    "mark_processed": '_mark_comment_batch_processed "$1"',
    "inflight": '_is_comment_batch_inflight "$1"',
    "mark_inflight": '_mark_comment_batch_inflight "$1" "$2"',
    "clear_inflight": '_clear_comment_batch_inflight "$1"',
    "filter_lines": '_filter_already_processed_comment_lines "$1"',
    "has_line": '_has_processed_comment_line "$1"',
    "record_lines": '_record_processed_comment_lines "$1"',
    "format_context": 'printf "%s\\n" "$1" | _format_comment_batch_context',
    "needs_thumbnail": '_comment_needs_thumbnail_context "$1"',
    "backlog": "get_comment_backlog_counts",
    "backlog_high": 'is_comment_backlog_high "$1" "$2"',
    "remember": '_remember_comment_reply_text "$1" "$2"',
    "store_meta": '_comment_store_generation_meta "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"',
}
DATE_SHIM = """#!/bin/bash
case "$1" in
  +%s) printf '%s\\n' "$GOLDEN_NOW" ;;
  +%Y%m%d_%H%M%S) exec /bin/date -u -d "@$GOLDEN_NOW" "+%Y%m%d_%H%M%S" ;;
  *) exec /bin/date "$@" ;;
esac
"""


def snapshot(sn):
    files = {rel: (sn / rel).read_text(encoding="utf-8") if (sn / rel).is_file() else None for rel in TRACKED}
    spoken = sn / "tmp/.comment_queue/spoken_history"
    files["spoken_history_txt"] = sorted(p.read_text(encoding="utf-8") for p in spoken.glob("*.txt")) \
        if spoken.is_dir() else []
    files["spoken_history_modes"] = sorted(p.name.rsplit("_", 1)[-1] for p in spoken.glob("*.txt")) \
        if spoken.is_dir() else []
    history = sn / "tmp/history/comment_generation_history.jsonl"
    files["generation_history"] = [
        {k: v for k, v in json.loads(line).items() if k != "generated_at"}
        for line in history.read_text(encoding="utf-8").splitlines()] if history.is_file() else []
    return files


def run(sn, commit, out_path):
    shim = sn / ".golden_bin"
    shim.mkdir()
    (shim / "date").write_text(DATE_SHIM)
    (shim / "date").chmod(0o755)
    results = []
    for op, args, offset in STEPS:
        now = T + offset
        env = {"GOLDEN_NOW": str(now), "TZ": "UTC", "PATH": f"{shim}:{os.environ['PATH']}"}
        if op in ("write", "write_append"):
            path = sn / args[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a" if op == "write_append" else "w", encoding="utf-8") as stream:
                stream.write(args[1])
            stdout, rc = "", 0
        elif op == "touch":
            (sn / args[0]).parent.mkdir(parents=True, exist_ok=True)
            (sn / args[0]).touch()
            stdout, rc = "", 0
        else:
            script = SHELL[op] + '; printf "\\n__RC__=%s" "$?"'
            raw = bash(script, sn, env=env, args=tuple(args))
            stdout, _, rc = raw.rpartition("\n__RC__=")
            rc = int(rc)
            if op == "store_meta":
                sidecar = sn / "tmp/.comment_queue" / (Path(args[0]).stem + ".meta.json")
                stdout = json.dumps({k: v for k, v in json.loads(sidecar.read_text()).items() if k != "generated_at"},
                                    ensure_ascii=False, sort_keys=True)
        results.append({"op": op, "args": args, "now": now, "stdout": stdout, "rc": rc, "files": snapshot(sn)})
    effective = bash('printf "%s|%s|%s" "$COMMENT_FAILURE_BACKOFF_SEC" "$COMMENT_SPOKEN_HISTORY_MAX_FILES" '
                     '"$COMMENT_GENERATION_HISTORY_KEEP"', sn).split("|")
    golden = {
        "provenance": {
            "source": "azumag/soviet_now broadcast/comment.sh state helpers as loaded by eloop_lib.sh "
                      "(see scripts/golden/comment_state_golden.py)",
            "soviet_now_commit": commit,
            "generated_by": "one scenario replayed on Linux with a fixed clock; state kept between steps",
            "purpose": "docich.comment.state must reproduce every step (#829 PR-3c)",
        },
        "effective_env": {"COMMENT_FAILURE_BACKOFF_SEC": effective[0],
                          "COMMENT_SPOKEN_HISTORY_MAX_FILES": effective[1],
                          "COMMENT_GENERATION_HISTORY_KEEP": effective[2]},
        "steps": results,
    }
    out_path.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(results)} steps) from soviet_now {commit[:8]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--soviet-now", type=Path, required=True)
    parser.add_argument("--commit", help="for a plain `git archive` export")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    checkout = args.soviet_now.resolve()
    with tempfile.TemporaryDirectory(prefix="legacy-soviet-now-") as export:
        if args.commit:
            commit = args.commit
            shutil.copytree(checkout, export, dirs_exist_ok=True)
        else:
            commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            archive = subprocess.run(["git", "-C", str(checkout), "archive", commit],
                                     check=True, capture_output=True).stdout
            subprocess.run(["tar", "-x", "-C", export], input=archive, check=True)
        run(Path(export), commit, args.out)


if __name__ == "__main__":
    main()
