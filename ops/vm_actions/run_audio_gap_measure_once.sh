#!/usr/bin/env bash
set -euo pipefail

# One-shot, reviewed wrapper around the read-only broadcast-audio gap
# measurement (docich#1811). No caller-controlled path, command, duration, or
# scenario is accepted: the fixed 92s capture mirrors the methodology the
# issue used for its before/after comparison (encoder input monitor
# soren_null.monitor, 48kHz stereo s16le), and the analysis counts
# digital-zero runs of 2..300ms that have non-silent audio on both sides.
# A capture that ends early, or a monitor that carries no audio at all, is
# rejected so a truncated or silent window can never be published as a
# measurement.
[[ "$#" -eq 0 ]] || exit 64

readonly ROOT=/home/ubuntu/docich
readonly OUT=/tmp/docich-audio-gap-latest.json
readonly MONITOR=soren_null.monitor
readonly RATE=48000
readonly CHANNELS=2
readonly SECONDS=92
[[ -d "$ROOT/.git" ]] || exit 65

export XDG_RUNTIME_DIR="/run/user/$(id -u)"

umask 077
raw="$(mktemp /tmp/docich-audio-gap.XXXXXX)"
report="$(mktemp /tmp/docich-audio-gap-report.XXXXXX)"
cleanup() { rm -f "$raw" "$report"; }
trap cleanup EXIT

status=0
timeout --signal=INT "${SECONDS}s" parec \
  -d "$MONITOR" --rate="$RATE" --channels="$CHANNELS" --format=s16le --raw \
  >"$raw" || status=$?
# 124: the fixed window elapsed and timeout delivered the signal. Any other
# non-zero status means the capture itself failed; nothing is published.
[[ "$status" -eq 0 || "$status" -eq 124 ]] || exit 66

python3 - "$raw" "$report" "$MONITOR" "$RATE" "$CHANNELS" <<'PY'
import json
import re
import sys
import time

raw_path, report_path, monitor, rate, channels = sys.argv[1:6]
rate = int(rate)
channels = int(channels)
frame = channels * 2

data = open(raw_path, "rb").read()
frames = len(data) // frame
duration_sec = frames / rate
if duration_sec < 80:
    # parec ended early (server stopped, device lost). Never publish a
    # short window as a 92s measurement.
    raise SystemExit(67)

# A frame is silent iff every channel sample is exactly zero, so every
# silent frame lies inside a maximal zero-BYTE run; the silent frames of a
# run spanning bytes [p, q) are exactly ceil(p/frame)..floor(q/frame). Scan
# runs with the regex engine instead of looping over ~4.4M frames in Python.
silent_frames = 0
runs = []  # (first_frame, last_frame_exclusive, bounded)
for match in re.finditer(rb"\x00+", data):
    start, end = match.span()
    first = -(-start // frame)
    last = end // frame
    if last <= first:
        continue
    silent_frames += last - first
    runs.append((first, last, start > 0 and end < len(data)))

nonzero_ratio = (frames - silent_frames) / frames
if nonzero_ratio < 0.005:
    # Digital silence throughout: there is no audio to measure, so this is
    # not a measurement (e.g. dispatched with no corner running).
    raise SystemExit(68)

min_gap = int(2 * rate / 1000)      # 2ms
max_gap = int(300 * rate / 1000)    # 300ms
gaps = []
long_silences = 0
for first, last, bounded in runs:
    if not bounded:
        continue  # head/tail silence has no "sounding before and after"
    span = last - first
    if min_gap <= span <= max_gap:
        gaps.append((first, span))
    elif span > max_gap:
        long_silences += 1

report = {
    "schema": "docich.audio_gap.v1",
    "meta": {
        "generated_at": int(time.time()),
        "monitor": monitor,
        "rate": rate,
        "channels": channels,
        "duration_sec": round(duration_sec, 3),
        "nonzero_frame_ratio": round(nonzero_ratio, 4),
    },
    "gaps": {
        "count": len(gaps),
        "total_ms": round(sum(span for _, span in gaps) * 1000 / rate),
        "max_ms": round(max((span for _, span in gaps), default=0) * 1000 / rate),
        "long_silences": long_silences,
        "items": [
            {"start_ms": round(start * 1000 / rate), "ms": round(span * 1000 / rate)}
            for start, span in gaps[:50]
        ],
    },
}
with open(report_path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, sort_keys=True)
PY

python3 - "$report" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
if report.get("schema") != "docich.audio_gap.v1":
    raise SystemExit(66)
meta = report.get("meta")
if not isinstance(meta, dict) or meta.get("monitor") != "soren_null.monitor":
    raise SystemExit(67)
duration = meta.get("duration_sec")
if not isinstance(duration, (int, float)) or not 80 <= duration <= 120:
    raise SystemExit(68)
gaps = report.get("gaps")
if not isinstance(gaps, dict) or not isinstance(gaps.get("count"), int):
    raise SystemExit(66)
PY

chmod 600 "$report"
mv -fT "$report" "$OUT"
trap - EXIT
rm -f "$raw"
