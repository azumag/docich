#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only operator action: queue one merged-PR report sentence for the
# VM audio-worker.  Unlike speak_audio_worker.sh this returns as soon as the
# queue write is published.  A merge report is fire-and-forget: a backlogged
# audio queue must not turn a successful enqueue into a failed workflow run.
TEXT_B64="${TEXT_B64:-}"
[[ "$TEXT_B64" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || { echo 'invalid text encoding' >&2; exit 64; }
(( ${#TEXT_B64} <= 4096 )) || { echo 'text too long' >&2; exit 64; }

soren_root=/home/ubuntu/soren
[[ -r "$soren_root/lib/outbound_queue.sh" && -r "$soren_root/workers/audio_worker.sh" ]] \
  || { echo 'soren audio runtime missing' >&2; exit 65; }

# Re-validate on the VM: decodes as UTF-8, one line, 1..240 chars, no
# control/invisible characters.  Prints the text only to the pipe below.
text="$(printf '%s' "$TEXT_B64" | python3 -c '
import base64, sys, unicodedata
t = base64.b64decode(sys.stdin.read(), validate=True).decode("utf-8")
ok = 0 < len(t) <= 240 and t == t.strip() and not any(unicodedata.category(c)[0] == "C" for c in t)
sys.stdout.write(t) if ok else sys.exit(1)
')" || { echo 'invalid text' >&2; exit 64; }

# Fail closed when no audio_worker is alive: a queued file nobody consumes
# would otherwise be spoken unexpectedly later.
pid_file="$soren_root/tmp/state/audio_worker.pid"
pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
[[ "$pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$pid" 2>/dev/null \
  && [[ "$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" == *workers/audio_worker.sh* ]] \
  || { echo 'audio_worker is not running' >&2; exit 66; }

cd "$soren_root"
set +u
# shellcheck source=/dev/null
source lib/outbound_queue.sh
set -u
nonce="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
[[ "$nonce" =~ ^[0-9a-f]{16}$ ]] || { echo 'nonce unavailable' >&2; exit 1; }
# Source becomes a filename component; the fixed prefix identifies automatic
# merge reports.  TTL 0 lets a deliberate re-run speak the same line again.
COMMENT_AUDIO_DEDUP_TTL_SEC=0 enqueue_audio_text "$text" "pr_merge_report.$nonce" \
  || { echo 'enqueue failed; nothing was queued' >&2; exit 1; }
echo 'merge report queued for audio_worker'
