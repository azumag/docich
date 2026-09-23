#!/usr/bin/env python3
"""Regenerate tests/fixtures/comment_viewer_memory_golden.json (#829 PR-3c-2).

The viewer-memory helper itself moves byte-for-byte (drift-guarded), so this
golden pins only the ``comment.sh`` *wrappers* around it
(``_build_comment_viewer_memory_context`` / ``_stage_comment_viewer_memory`` /
``_commit_comment_viewer_memory``): the helper is replaced by a stub that
records its argv and returns a canned stdout / exit status, and each case
records the argv (or no call), the wrapper's stdout / status and whether the
sidecar survived. Legacy loaded by ``eloop_lib.sh``; run on Linux.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comment_prompt_golden import bash  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests/fixtures/comment_viewer_memory_golden.json"
TARGET = "tmp/.comment_queue/comment_5.txt"
SIDECAR = "tmp/.comment_queue/comment_5.viewer_memory.json"
STUB = """import json, os, sys
with open(os.environ["GOLDEN_STUB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
out = os.environ.get("GOLDEN_STUB_OUT")
if out is not None:
    print(out)
raise SystemExit(int(os.environ.get("GOLDEN_STUB_RC", "0")))
"""
CUSTOM = {"COMMENT_VIEWER_MEMORY_PROMPT_ITEMS": "7", "COMMENT_VIEWER_MEMORY_PROMPT_MAX_CHARS": "900",
          "COMMENT_VIEWER_MEMORY_COMMENT_MAX_CHARS": "50", "COMMENT_VIEWER_MEMORY_REPLY_MAX_CHARS": "60",
          "COMMENT_VIEWER_MEMORY_TTL_DAYS": "30", "COMMENT_VIEWER_MEMORY_EXCLUDED_USERS": "a b",
          "COMMENT_VIEWER_MEMORY_FILE": "/x/mem.json", "COMMENT_VIEWER_MEMORY_MAX_USERS": "9",
          "COMMENT_VIEWER_MEMORY_MAX_EXCHANGES": "3"}
CASES = [
    ("context", {}, "CTX", 0, False), ("context", {"COMMENT_VIEWER_MEMORY_ENABLED": "0"}, "X", 0, False),
    ("context", {}, None, 1, False), ("context", {}, "PARTIAL", 3, False), ("context", CUSTOM, "C2", 0, False),
    ("stage", {}, "2", 0, True), ("stage", {}, "0", 0, True), ("stage", {}, "abc", 0, True),
    ("stage", {}, None, 1, True), ("stage", {"COMMENT_VIEWER_MEMORY_ENABLED": "0"}, "2", 0, True),
    ("stage", CUSTOM, "1", 0, True),
    ("commit", {}, "3", 0, False), ("commit", {}, "3", 0, True), ("commit", {}, None, 2, True),
    ("commit", CUSTOM, "0", 0, True), ("commit", {"COMMENT_VIEWER_MEMORY_ENABLED": ""}, "1", 0, True),
]
SHELL = {
    "context": '_build_comment_viewer_memory_context tmp/b.txt twitch soren91',
    "stage": f'_stage_comment_viewer_memory {TARGET} tmp/b.txt tmp/r.txt youtube main h1',
    "commit": f'_commit_comment_viewer_memory {TARGET}',
}


def run(sn, commit, out_path):
    (sn / "lib/comment_viewer_memory.py").write_text(STUB, encoding="utf-8")
    results = []
    for index, (op, env, stub_out, stub_rc, with_sidecar) in enumerate(CASES):
        log = sn / f"stub_{index}.log"
        sidecar = sn / SIDECAR
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.unlink(missing_ok=True)
        if with_sidecar:
            sidecar.write_text("{}", encoding="utf-8")
        case_env = {**env, "GOLDEN_STUB_LOG": str(log), "GOLDEN_STUB_RC": str(stub_rc)}
        if stub_out is not None:
            case_env["GOLDEN_STUB_OUT"] = stub_out
        raw = bash(SHELL[op] + '; printf "\\n__RC__=%s" "$?"', sn, env=case_env)
        stdout, _, rc = raw.rpartition("\n__RC__=")
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        results.append({"op": op, "env": env, "stub_out": stub_out, "stub_rc": stub_rc,
                        "with_sidecar": with_sidecar, "calls": calls, "stdout": stdout, "rc": int(rc),
                        "sidecar_after": sidecar.exists()})
    golden = {
        "provenance": {
            "source": "azumag/soviet_now broadcast/comment.sh viewer-memory wrappers as loaded by eloop_lib.sh "
                      "(see scripts/golden/comment_viewer_memory_golden.py)",
            "soviet_now_commit": commit,
            "generated_by": "helper replaced by an argv-recording stub; Linux",
            "purpose": "docich.comment.state viewer-memory wrappers must reproduce these (#829 PR-3c-2)",
        },
        "target": TARGET, "sidecar": SIDECAR, "cases": results,
    }
    out_path.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(results)} cases) from soviet_now {commit[:8]}")


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
