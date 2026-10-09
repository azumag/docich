#!/usr/bin/env python3
"""Build one production-shaped batch per case for the live-Jev baseline (#1263).

Read-only over the eval suite. For every case it writes the exact batch file
the live chat worker feeds to ``bin/docich-comment-classify``: one
``user: comment`` line. The username is a neutral probe handle (the suite
projects every real handle to ``[user]`` and keeps no raw handle), so neither a
real handle nor a raw log line leaves the host.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

PROBE_USER = "azumagbanjo"


def build(suite_path: Path, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    with suite_path.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    for case in rows:
        case_id = case["case_id"]
        comment = case["input"]["comment"]
        # Card notifications contain ": " inside their own text; the heuristic
        # classifies the raw line, so keep the line raw like production does.
        line = f"{PROBE_USER}: {comment}"
        batch = out_dir / f"{case_id}.txt"
        batch.write_text(line + "\n", encoding="utf-8")
        manifest.append({"case_id": case_id, "batch": str(batch),
                         "expected_category": case["expected"]["category"],
                         "intent_family": case["expected"]["intent_family"],
                         "tags": list(case.get("tags") or [])})
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: build_batches.py <public_cases.jsonl> <out_dir>", file=sys.stderr)
        return 2
    manifest = build(Path(argv[0]), Path(argv[1]))
    print(f"batches={len(manifest)} out={argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
