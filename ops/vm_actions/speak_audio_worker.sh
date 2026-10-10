#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only operator action: queue one sentence for the VM audio-worker.
# The workflow pipes this file to the production gateway with the validated
# sentence prepended as a single `TEXT_B64=<base64>` assignment line.  The
# sentence is only ever handled as data: decoded, re-validated, written to the
# queue with printf, never evaluated or used as a command or path.
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
queue_dir="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
queued_files() { compgen -G "$queue_dir/comment_announce_*_owner_speak.txt" || true; }
before="$(queued_files)"
# TTL 0 disables the 120 s duplicate suppression: the owner may repeat a line.
COMMENT_AUDIO_DEDUP_TTL_SEC=0 enqueue_audio_text "$text" owner_speak
mine="$(comm -13 <(printf '%s\n' "$before" | sort) <(queued_files | sort))"
[[ -n "$mine" && "$(printf '%s\n' "$mine" | wc -l)" -eq 1 ]] \
  || { echo 'enqueue did not produce exactly one queue file' >&2; exit 1; }

# Measured acknowledgement: the worker claims (renames/removes) the .txt.
for _ in $(seq 1 90); do
  if [[ ! -e "$mine" ]]; then
    echo 'audio_worker consumed the queued sentence'
    exit 0
  fi
  sleep 1
done
echo 'queued but not yet consumed by audio_worker (other audio ahead in queue?)' >&2
exit 2
