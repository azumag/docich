#!/usr/bin/env bash
set -euo pipefail

# Restart only the reviewed CLI fallback BGM service after a Soren runtime update.
# Do not touch the game process, RetroArch, PulseAudio, OBS/ffmpeg or shared stream units.
[[ $# -eq 0 ]] || { echo "no arguments accepted" >&2; exit 2; }

unit="soren-bgm.service"
expected_exec="/home/ubuntu/soren/bgm_worker.sh"

exec_start="$(systemctl show "$unit" --property=ExecStart --value 2>/dev/null || true)"
if [[ "$exec_start" != *"$expected_exec"* ]]; then
  echo "unexpected soren-bgm ExecStart" >&2
  exit 12
fi

before_pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null || true)"
case "$before_pid" in
  ''|*[!0-9]*) before_pid=0 ;;
esac

if ! sudo -n systemctl restart "$unit"; then
  echo "soren-bgm restart failed" >&2
  exit 10
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
tagged=0
mapfile -t ffplay_pids < <(pgrep -u "$(id -un)" -x ffplay 2>/dev/null || true)
for pid in "${ffplay_pids[@]}"; do
  [[ "$pid" =~ ^[0-9]+$ ]] || continue
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  if [[ "$cmdline" == *"-window_title soren-bgm-loop"* ]]; then
    tagged=$((tagged + 1))
  fi
done
if (( tagged > 1 )); then
  echo "multiple tagged fallback BGM players remain after restart" >&2
  exit 14
fi

printf 'soren-bgm restarted: pid=%s tagged_players=%s\n' "$after_pid" "$tagged"
