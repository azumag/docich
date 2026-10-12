#!/usr/bin/env bash
# Optional live collection, never used for offline re-scoring or CI.
# Requires separate authorization for provider calls. Run from a prepared repo.
# Each invocation has an isolated telemetry directory and a collector case ID.
# Immutable pass records retain prior success events and their latency on retry.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$HERE/../../.." && pwd)
: "${BATCH_DIR:?Set BATCH_DIR to the directory produced by build_batches.py}"
: "${RUN_DIR:?Set RUN_DIR to a new, isolated collection directory}"
if [ -e "$RUN_DIR" ]; then
  echo "RUN_DIR must not already exist" >&2
  exit 2
fi
mkdir -p "$RUN_DIR"
RUN_DIR=$(cd -- "$RUN_DIR" && pwd)
BATCH_DIR=$(cd -- "$BATCH_DIR" && pwd)
cd "$REPO"
export COMMENT_CLASSIFIER_JEV_STATE_DIR="$RUN_DIR/state"
mkdir -p "$COMMENT_CLASSIFIER_JEV_STATE_DIR"
python3 - "$BATCH_DIR" "$RUN_DIR/expected_ids.txt" <<'PY'
import json, sys
from pathlib import Path
batches = Path(sys.argv[1])
manifest = json.loads((batches / 'manifest.json').read_text())
ids = [r['case_id'] for r in manifest]
if not ids or len(set(ids)) != len(ids) or set(ids) != {p.stem for p in batches.glob('jev-*.txt')}:
    raise ValueError('batch manifest membership/count mismatch')
for row in manifest:
    if row['batch'] != row['case_id'] + '.txt':
        raise ValueError('batch manifest identity mismatch')
Path(sys.argv[2]).write_text(''.join(i + '\n' for i in ids))
PY
cp "$RUN_DIR/expected_ids.txt" "$RUN_DIR/pending.txt"
for pass in 1 2 3 4; do
  pass_dir="$RUN_DIR/pass$pass"
  mkdir "$pass_dir"
  : >"$pass_dir/events.jsonl"
  # Gate resets affect only this run's private state directory.
  rm -f "$COMMENT_CLASSIFIER_JEV_STATE_DIR"/gate*.json
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    mkdir -p "$pass_dir/$id/metrics"
    export COMMENT_CLASSIFIER_JEV_METRICS_DIR="$pass_dir/$id/metrics"
    start=$(python3 -c 'import time; print(time.monotonic_ns() // 1000000)')
    rc=0
    ./bin/docich-comment-classify "$BATCH_DIR/$id.txt" >"$pass_dir/$id/output.json" 2>"$pass_dir/$id/stderr.txt" || rc=$?
    end=$(python3 -c 'import time; print(time.monotonic_ns() // 1000000)')
    python3 "$HERE/pipeline_records.py" record --case-id "$id" --pass-number "$pass" \
      --metrics-dir "$COMMENT_CLASSIFIER_JEV_METRICS_DIR" --rc "$rc" --latency-ms "$((end-start))" \
      --out "$pass_dir/events.jsonl"
  done <"$RUN_DIR/pending.txt"
  python3 "$HERE/pipeline_records.py" merge --expected-ids "$RUN_DIR/expected_ids.txt" \
    --pass-files "$RUN_DIR"/pass*/events.jsonl --out "$RUN_DIR/latest.jsonl" --pending "$RUN_DIR/pending.txt"
  [ -s "$RUN_DIR/pending.txt" ] || break
done
# latest.jsonl includes every case's latest event, rc, pass, and latency; pass
# files remain available for auditing. Never publish stderr/output without review.
echo "collection complete; remaining retry cases: $(wc -l < "$RUN_DIR/pending.txt")"
