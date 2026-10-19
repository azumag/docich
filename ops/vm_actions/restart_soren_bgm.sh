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
# Detect that condition before and after the restart and fail closed instead of
# restarting over a server the deploy does not own. The fix is the canonical
# user units (`systemctl --user start pulseaudio.socket pulseaudio.service`)
# installed by install_soren_runtime_service.sh, which no longer spawns here.
pulseaudio_in_bgm_cgroup() {
  local pid cgroup
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
  done < <(pgrep -u "$(id -un)" -x pulseaudio 2>/dev/null || true)
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

# A healthy fallback BGM worker owns at most one tagged ffplay process.
# Do not kill by name here: if an unexpected duplicate remains, fail closed so
# the operator can identify its ownership instead of touching unrelated audio.
# A plain read loop keeps this helper runnable under bash 3.2 as well (mapfile
# is a bash 4 builtin, which the offline contract test on macOS does not have).
tagged=0
while IFS= read -r pid; do
  [[ "$pid" =~ ^[0-9]+$ ]] || continue
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  if [[ "$cmdline" == *"-window_title soren-bgm-loop"* ]]; then
    tagged=$((tagged + 1))
  fi
done < <(pgrep -u "$(id -un)" -x ffplay 2>/dev/null || true)
if (( tagged > 1 )); then
  echo "multiple tagged fallback BGM players remain after restart" >&2
  exit 14
fi

printf 'soren-bgm restarted: pid=%s tagged_players=%s\n' "$after_pid" "$tagged"
