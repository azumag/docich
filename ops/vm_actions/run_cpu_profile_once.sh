#!/usr/bin/env bash
set -euo pipefail

# One-shot, reviewed wrapper around the read-only profiler (#970).
# No caller-controlled path, command, duration, or scenario is accepted.
[[ "$#" -eq 0 ]] || exit 64

readonly ROOT=/home/ubuntu/docich
readonly OUT=/tmp/docich-cpu-profile-latest.json
[[ -d "$ROOT/.git" ]] || exit 65

umask 077
tmp="$(mktemp /tmp/docich-cpu-profile.XXXXXX)"
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

cd "$ROOT"
python3 ops/vm_actions/profile_cpu.py sample \
  --scenario latest-main \
  --warmup 5 \
  --duration 60 \
  --interval 1 \
  --json >"$tmp"

python3 - "$tmp" <<'PY'
import json
import math
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
if report.get("schema") != "docich.cpu_profile.v1":
    raise SystemExit(66)
meta = report.get("meta")
if not isinstance(meta, dict) or meta.get("scenario") != "latest-main":
    raise SystemExit(67)
elapsed = meta.get("elapsed_sec")
if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 59 <= elapsed <= 120:
    raise SystemExit(68)
PY

chmod 600 "$tmp"
mv -fT "$tmp" "$OUT"
trap - EXIT
