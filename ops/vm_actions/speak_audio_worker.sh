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
debug_log=tmp/.say_queue/debug.log
outcome_wait_s=90
# The live worker may claim the item before enqueue even returns, so its
# identity must be fixed before publication rather than inferred from a later
# directory listing.  enqueue_audio_text embeds the source in the filename;
# a per-run hex nonce makes that name unique (and keeps the playback label
# "comment", since no label pattern can match hex).
nonce="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
[[ "$nonce" =~ ^[0-9a-f]{16}$ ]] || { echo 'nonce unavailable' >&2; exit 1; }
item="_owner_speak.${nonce}."
# TTL 0 disables the 120 s enqueue suppression: the owner may repeat a line.
# A non-zero return means nothing was published, so retrying is safe.
COMMENT_AUDIO_DEDUP_TTL_SEC=0 enqueue_audio_text "$text" "owner_speak.$nonce" \
  || { echo 'enqueue failed; nothing was queued' >&2; exit 1; }

# Measured outcome from the worker's own per-file record, never from the
# queue file merely disappearing (it is also removed when skipped/discarded).
item_outcome() {
  local line
  # Only _play_comment_queue's per-item verdicts count; say_enqueue also logs
  # per-chunk "再生完了" lines naming the same .playing file mid-playback.
  line="$(grep -F -- "$item" "$debug_log" 2>/dev/null \
    | grep -E '^\[_play_comment_queue [^]]*\] [^:]*(再生完了|再生失敗|重複スキップ|破棄)[^:]*: ' \
    | tail -n 1 || true)"
  case "$line" in
    *再生完了*) echo spoken ;;
    *再生失敗*) echo failed ;;
    *重複スキップ*) echo duplicate ;;
    *破棄*) echo discarded ;;
  esac
}
for _ in $(seq 1 "$outcome_wait_s"); do
  case "$(item_outcome)" in
    spoken) echo 'audio_worker played the sentence'; exit 0 ;;
    failed) echo 'audio_worker claimed the sentence but playback failed' >&2; exit 3 ;;
    duplicate) echo 'audio_worker skipped the sentence as already played' >&2; exit 3 ;;
    discarded) echo 'audio_worker discarded the sentence (mode mismatch)' >&2; exit 3 ;;
  esac
  sleep 1
done
if compgen -G "$queue_dir/comment_announce_*${item}playing" >/dev/null; then
  echo 'audio_worker is still playing the sentence; outcome not yet recorded' >&2
elif compgen -G "$queue_dir/comment_announce_*${item}txt" >/dev/null; then
  echo 'queued but not yet claimed by audio_worker (other audio ahead in queue?)' >&2
else
  echo 'queued item is gone without a recorded outcome' >&2
fi
exit 2
