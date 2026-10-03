#!/usr/bin/env bash
# Fixed reference-run bridge: no provider, model, queue or backoff implementation.
# The consumer supplies private paths and a shorter per-event validity budget.
set -uo pipefail
[ "$#" -eq 7 ] || exit 2
scene_root="$1"; scene_prompt="$2"; scene_output="$3"
scene_agent="$4"; scene_failure="$5"; scene_meta="$6"; scene_budget="$7"
case "$scene_budget" in ''|*[!0-9]*) exit 2 ;; esac
[ "$scene_budget" -ge 1 ] && [ "$scene_budget" -le 15 ] || exit 2
[ "${DOCICH_ALLOW_REAL_AI:-}" = 1 ] || exit 2
cd "$scene_root" || exit 2
[ -f ./eloop_lib.sh ] || exit 2
# The canonical bootstrap loads the existing operator config and all dispatcher
# policy overlays. Its incidental stdout/stderr never becomes model output.
source ./eloop_lib.sh >/dev/null 2>&1 || { printf 'unavailable\n' >"$scene_failure"; exit 2; }
if [ "${EXPLORE_MODE:-0}" = 1 ] || [ "${STREAMING_ENABLED:-1}" != 1 ] \
   || [ -f tmp/stop ] || [ -f tmp/state/radio_worker.paused ] \
   || [ -f tmp/state/audio_worker.paused ]; then
    printf 'disabled\n' >"$scene_failure"; exit 3
fi
declare -F ai_generate_list >/dev/null || { printf 'unavailable\n' >"$scene_failure"; exit 2; }
scene_role=BATCH_COMMENTARY_AGENTS
scene_agents="${BATCH_COMMENTARY_AGENTS:-}"
if [ -z "$scene_agents" ]; then scene_role=RADIO_AGENTS; scene_agents="${RADIO_AGENTS:-}"; fi
if [ -z "$scene_agents" ]; then scene_role=AI_COMMON_AGENTS; scene_agents="${AI_COMMON_AGENTS:-}"; fi
[ -n "$scene_agents" ] || { printf 'unavailable\n' >"$scene_failure"; exit 2; }
printf '{"role":"%s"}\n' "$scene_role" >"$scene_meta"

_hanjuku_scene_output_present() { [ -n "${1:-}" ]; }
_hanjuku_scene_cap() {
    # Queue/provider lock zero means unlimited; improve-gate zero means no wait.
    local existing="${1:-}" zero_is_limit="${2:-0}"
    case "$existing" in ''|*[!0-9]*) printf '%s' "$scene_budget"; return ;; esac
    if { [ "$existing" -gt 0 ] || [ "$zero_is_limit" = 1 ]; } && [ "$existing" -lt "$scene_budget" ]; then
        printf '%s' "$existing"
    else
        printf '%s' "$scene_budget"
    fi
}
_hanjuku_scene_call() {
    # Only reduce waits for this short-lived request. Do not disable a gate,
    # change a provider/model chain, claim another slot or reset shared backoff.
    local queue_cap="${AI_GENERATION_QUEUE_MAX_WAIT_SEC:-0}" deadline=$(( $(date +%s) + scene_budget ))
    if declare -F _ai_generation_queue_max_wait_sec >/dev/null; then
        queue_cap=$(_ai_generation_queue_max_wait_sec 'RADIO:hanjuku-commentary')
    fi
    case "${AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH:-}" in
        ''|*[!0-9]*) ;;
        *) [ "$AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH" -ge "$deadline" ] || deadline="$AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH" ;;
    esac
    local -x AI_GENERATION_QUEUE_MAX_WAIT_SEC="$(_hanjuku_scene_cap "$queue_cap")"
    local -x AI_GENERATION_QUEUE_MAX_WAIT_SEC_HARD_CAP=1
    # Canonical queue metadata names this short-lived job, so its existing
    # dead-owner reaper can recover a slot after bounded process cleanup.
    local -x AI_GENERATION_QUEUE_OWNER_PID="${BASHPID:-$$}"
    local -x AI_RADIO_IMPROVE_WAIT_MAX_SEC="$(_hanjuku_scene_cap "${AI_RADIO_IMPROVE_WAIT_MAX_SEC:-}" 1)"
    local -x OPENCODE_RUN_LOCK_MAX_WAIT_SEC="$(_hanjuku_scene_cap "${OPENCODE_RUN_LOCK_MAX_WAIT_SEC:-}")"
    local -x AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH="$deadline"
    ai_generate_list 'RADIO:hanjuku-commentary' "$scene_prompt" "$scene_agents" \
        "$scene_budget" _hanjuku_scene_output_present "$scene_agent" "$scene_failure"
}
_hanjuku_scene_call >"$scene_output" 2>/dev/null
scene_rc=$?
[ "$scene_rc" -eq 0 ] || [ -s "$scene_failure" ] || printf 'generation_failed\n' >"$scene_failure"
exit "$scene_rc"
