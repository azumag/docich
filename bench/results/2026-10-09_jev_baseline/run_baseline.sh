#!/usr/bin/env bash
# Live-Jev baseline over the public eval suite, on the Soren VM (#1263).
#
# Drives the PRODUCTION classifier (bin/docich-comment-classify) once per case
# with production's own env, but redirects the Jev cooldown-gate state and the
# telemetry directory to /home/ubuntu/jev-baseline-*, so the live chat worker's
# gate and metrics are never touched. Read-only for classification: no batch is
# ever queued for reply generation.
#
# A provider flake mid-run poisons the gate for 300 s (direct) / 300 s
# (vercel) and silently turns the rest of the suite into cooldown fallbacks.
# So: classify every case, then re-run every case whose event was not a real
# provider answer until no such case remains (bounded), with a gate reset
# between passes. Only the LAST pass's event per case is kept.
set -euo pipefail

cd /home/ubuntu/docich
set -a; . /home/ubuntu/soren/.env; set +a

STATE=/home/ubuntu/jev-baseline-state
METRICS=/home/ubuntu/jev-baseline-metrics
OUT=/home/ubuntu/jev-baseline-out
BATCH_DIR=/home/ubuntu/jev-baseline-batches/batches
export COMMENT_CLASSIFIER_JEV_STATE_DIR="$STATE"
export COMMENT_CLASSIFIER_JEV_METRICS_DIR="$METRICS"
mkdir -p "$STATE" "$METRICS" "$OUT"
rm -rf "$OUT"; mkdir -p "$OUT"

echo "backend=${COMMENT_CLASSIFIER_BACKEND} route=${DOCICH_JEV_ROUTE} timeout=${COMMENT_CLASSIFIER_JEV_TIMEOUT_MS} minconf=${COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE}"

classify_one() {  # $1 = case id
  local id="$1" start end rc
  start=$(date +%s%3N)
  if ./bin/docich-comment-classify "$BATCH_DIR/$id.txt" >"$OUT/$id.json" 2>"$OUT/$id.err"; then
    rc=0
  else
    rc=1
  fi
  end=$(date +%s%3N)
  printf '%s\t%s\t%s\n' "$id" "$rc" "$((end-start))" >>"$OUT/latency.tsv"
}

snapshot_pass() {  # copy the run's metrics aside
  local pass="$1"
  cp "$METRICS"/metrics-*.jsonl "$METRICS/pass${pass}.jsonl"
}

gate_open() {  # true when neither route is in cooldown right now
  python3 - "$STATE" <<'PY'
import json, sys, time
from pathlib import Path
state_dir = Path(sys.argv[1])
now = time.time()
for name in ("gate.json", "gate-vercel.json"):
    path = state_dir / name
    if not path.exists():
        continue
    try:
        until = json.loads(path.read_text()).get("until", 0)
    except Exception:
        until = 0
    if now < until:
        sys.exit(1)
sys.exit(0)
PY
}

PASS=1
: >"$OUT/pending.txt"
for f in "$BATCH_DIR"/jev-*.txt; do basename "$f" .txt >>"$OUT/pending.txt"; done

while :; do
  # Fresh gate for the pass: cooldowns from a previous pass must not decide
  # this pass's outcomes.
  rm -f "$STATE"/gate*.json
  rm -f "$METRICS"/metrics-*.jsonl*
  : >"$OUT/latency.tsv"
  : >"$OUT/pending_next.txt"

  echo "--- pass $PASS"
  count=0
  while read -r id; do
    [ -n "$id" ] || continue
    classify_one "$id"
    count=$((count+1))
  done <"$OUT/pending.txt"
  echo "pass $PASS classified=$count"

  # Which cases still lack a real provider answer (ok / local notification /
  # input_limit are final; cooldown / auth_error / invalid_response /
  # network_error / server_error / overloaded / rate_limited are retryable)?
  python3 - "$METRICS" "$OUT" <<'PY'
import json, sys
from pathlib import Path
metrics_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
lines = [json.loads(l) for l in (metrics_dir / "metrics-2026-10-09.jsonl").read_text().splitlines() if l.strip()]
ids = [p.split("\t")[0] for p in (out / "latency.tsv").read_text().splitlines()]
retryable = {"cooldown", "auth_error", "invalid_response", "network_error",
             "server_error", "overloaded", "rate_limited", "http_error", "timeout"}
pending = []
for case_id, event in zip(ids, lines):
    row = event["rows"][0] if event.get("rows") else {}
    if event["status"] in retryable or row.get("status") in retryable:
        pending.append(case_id)
(out / "pending_next.txt").write_text("\n".join(pending) + ("\n" if pending else ""))
print(f"retryable={len(pending)}")
PY

  mv "$OUT/pending_next.txt" "$OUT/pending.txt"
  if [ ! -s "$OUT/pending.txt" ]; then
    snapshot_pass "$PASS"
    echo "no retryable cases remain"
    break
  fi
  if [ "$PASS" -ge 4 ]; then
    snapshot_pass "$PASS"
    echo "pass budget exhausted; remaining:"; cat "$OUT/pending.txt"
    break
  fi
  PASS=$((PASS+1))
  cp "$OUT/pending.txt" "$OUT/pending_pass${PASS}.txt"
done

echo "passes=$PASS"
echo "final_metrics_lines=$(wc -l < "$METRICS"/pass*.jsonl | tail -1)"
