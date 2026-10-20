#!/usr/bin/env bash
set -euo pipefail

# Restart only the reviewed CLI fallback BGM service after a Soren runtime update.
# Do not touch the game process, RetroArch, PulseAudio, OBS/ffmpeg or shared stream units.
[[ $# -eq 0 ]] || { echo "no arguments accepted" >&2; exit 2; }

unit="soren-bgm.service"
expected_exec="/home/ubuntu/soren/bgm_worker.sh"
# Read-only procfs root. Overridable so the offline contract test can model a
# stray server without a live systemd cgroup; production always reads /proc.
proc_root="${SOREN_BGM_PROC_ROOT:-/proc}"

# docich #2008: a PulseAudio server must never live inside this unit's cgroup.
# An autospawned server (2026-10-11 05:55/08:05) dies with
# `systemctl restart soren-bgm`, taking the shared stream audio with it: the
# canonical socket disappears and the encoder keeps publishing video only.
# Fail closed instead of restarting over a server the deploy does not own:
# scan once before the restart and again twice after it (immediately, and at
# the point success would be reported, so a server appearing during the
# Type=simple readiness window is still caught). Any status that is not
# "scan ran and matched nothing" counts as unverifiable and fails closed.
# The fix is the canonical user units (`pulseaudio.socket` /
# `pulseaudio.service`) installed by install_soren_runtime_service.sh, which
# no longer spawns a private server here.
enumeration_failed() {
  # pgrep exit 1 means "nothing matched" and is the only healthy status.
  # Anything else (2 usage, 3 fatal, 127 missing binary, ...) means the scan
  # did not actually happen: treat it as unverifiable, never as "absent".
  case "${1:-}" in
    ''|*[!0-9]*) return 0 ;;
  esac
  [ "$1" -ne 0 ] && [ "$1" -ne 1 ]
}

# Echo the unit's own user, or fail. Empty/unreadable means the unit owner
# cannot be established, so no scan can be trusted; never fall back to the
# caller, which may be a different user than the one owning the stray server.
unit_user() {
  local owner
  owner="$(systemctl show "$unit" --property=User --value 2>/dev/null)" || owner=""
  case "$owner" in
    '')
      echo "cannot establish the $unit owner; refusing to restart over an unverifiable audio server" >&2
      return 1
      ;;
    *[!A-Za-z0-9_.-]*)
      echo "the $unit owner is not a plain user name; refusing to restart" >&2
      return 1
      ;;
  esac
  printf '%s\n' "$owner"
}

pulseaudio_in_bgm_cgroup() {
  local pid cgroup owner pids rc=0
  owner="$(unit_user)" || return 0
  # A failed enumeration must never look like "no stray server".
  pids="$(pgrep -u "$owner" -x pulseaudio 2>/dev/null)" || rc=$?
  if enumeration_failed "$rc"; then
    echo "cannot enumerate pulseaudio processes (pgrep rc=$rc); refusing to restart $unit" >&2
    return 0
  fi
  [ -n "$pids" ] || return 1
  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    cgroup="$(cat "$proc_root/$pid/cgroup" 2>/dev/null || true)"
    if [[ -z "$cgroup" ]]; then
      echo "cannot verify the cgroup of pulseaudio pid $pid; refusing to restart $unit" >&2
      return 0
    fi
    if [[ "$cgroup" == *"/$unit"* ]]; then
      echo "pulseaudio pid $pid is inside the $unit cgroup; refusing to restart over the shared audio server" >&2
      return 0
    fi
  done <<<"$pids"
  return 1
}

exec_start="$(systemctl show "$unit" --property=ExecStart --value 2>/dev/null || true)"
if [[ "$exec_start" != *"$expected_exec"* ]]; then
  echo "unexpected soren-bgm ExecStart" >&2
  exit 12
fi

if pulseaudio_in_bgm_cgroup; then
  exit 15
fi

before_pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null || true)"
case "$before_pid" in
  ''|*[!0-9]*) before_pid=0 ;;
esac

if ! sudo -n systemctl restart "$unit"; then
  echo "soren-bgm restart failed" >&2
  exit 10
fi

if pulseaudio_in_bgm_cgroup; then
  echo "the restarted $unit spawned a private PulseAudio; shared audio permanence is broken" >&2
  exit 16
fi

after_pid=0
for _ in $(seq 1 40); do
  state="$(systemctl show "$unit" --property=ActiveState --value 2>/dev/null || true)"
  pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null || true)"
  if [[ "$state" == "active" && "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]]; then
    after_pid="$pid"
    break
  fi
  sleep 0.25
done

if [[ "$after_pid" -le 0 ]]; then
  echo "soren-bgm is not active after restart" >&2
  exit 11
fi
if [[ "$before_pid" -gt 0 && "$after_pid" -eq "$before_pid" ]]; then
  echo "soren-bgm MainPID did not change" >&2
  exit 13
fi

# Re-validate at the point we would report success. Type=simple reaches
# "active" before its children settle, so a private server that appears during
# this readiness window would otherwise be missed even though it is running
# when this helper exits 0 (review P2, 2026-10-20). Scope of the guarantee:
# these are instantaneous scans at two points in time. They prove no stray
# server was present then; they cannot prove no client will ever autospawn one
# later during the unit's lifetime. The durable prevention is the pinned
# PULSE_SERVER/XDG_RUNTIME_DIR environment plus client.conf `autospawn = no`.
if pulseaudio_in_bgm_cgroup; then
  echo "the restarted $unit has a private PulseAudio after startup; shared audio permanence is broken" >&2
  exit 16
fi

# A healthy fallback BGM worker owns at most one tagged ffplay process.
# Do not kill by name here: if an unexpected duplicate remains, fail closed so
# the operator can identify its ownership instead of touching unrelated audio.
# A plain read loop keeps this helper runnable under bash 3.2 as well (mapfile
# is a bash 4 builtin, which the offline contract test on macOS does not have).
tagged=0
account_owner="$(unit_user)" || exit 14
ffplay_pids=""
ffplay_rc=0
ffplay_pids="$(pgrep -u "$account_owner" -x ffplay 2>/dev/null)" || ffplay_rc=$?
if enumeration_failed "$ffplay_rc"; then
  echo "cannot enumerate ffplay processes (pgrep rc=$ffplay_rc); refusing to report a healthy restart" >&2
  exit 14
fi
while IFS= read -r pid; do
  [[ "$pid" =~ ^[0-9]+$ ]] || continue
  cmdline="$(tr '\0' ' ' <"$proc_root/$pid/cmdline" 2>/dev/null || true)"
  if [[ "$cmdline" == *"-window_title soren-bgm-loop"* ]]; then
    tagged=$((tagged + 1))
  fi
done <<<"$ffplay_pids"
if (( tagged > 1 )); then
  echo "multiple tagged fallback BGM players remain after restart" >&2
  exit 14
fi

printf 'soren-bgm restarted: pid=%s tagged_players=%s\n' "$after_pid" "$tagged"
