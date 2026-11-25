#!/usr/bin/env bash
set -euo pipefail

# Fixed, owner-reviewed consolidation of the corner-rotation owner.
#
# The production host can end up with two rotation workers: the canonical
# user timer (docich-corner-rotation.timer) and a hand-installed system-scope
# legacy unit (docich-soren91-corner.timer/.service) that starts
# `soren91-corner tick`. Both take the same corner-rotation lock, so only the
# first one to start owns the rolling rotation and the canonical timer exits
# with `already-running` forever. This helper makes the canonical user timer
# the only owner.
#
# It never accepts a unit name or a command as input. It stops the legacy
# system timer first, then the running legacy worker, masks both legacy units
# (reversible with unmask), and proves that the canonical user worker took the
# rotation lock and the heartbeat inside the fresh-heartbeat window
# (HEARTBEAT_FRESH_S = 180s in src/docich/corner_rotation.py). A running corner
# is parked rather than quarantined: the heartbeat written by the legacy
# process is still fresh when the canonical tick starts.
#
# It never touches the display/audio/stream/encoder services, the game
# runtime, the rotation ledger, the locks or the game-switch receipts.
#
# ROTATION_OWNER_MODE:
#   check (default) - read-only projection; no systemctl mutation
#   apply           - disable/stop/mask the legacy system units, then verify
#
# The workflow layer prefixes this variable on stdin; no other input is read.

if (( $# != 0 )); then
  printf '%s\n' 'consolidate corner rotation owner accepts no arguments' >&2
  exit 64
fi

mode="${ROTATION_OWNER_MODE:-check}"
case "$mode" in
  check|apply) ;;
  *) printf '%s\n' 'invalid ROTATION_OWNER_MODE' >&2; exit 64 ;;
esac

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
# The gateway can run with a scrubbed HOME. Resolve the deployed user's unit
# root from the reviewed production checkout, exactly like the read-only
# diagnostics collector does, so a scrubbed HOME cannot look like a missing
# canonical timer.
unit_dir="$(dirname "$DOCICH_PROD_ROOT")/.config/systemd/user"

canonical_service="docich-corner-rotation.service"
canonical_timer="docich-corner-rotation.timer"
legacy_service="docich-soren91-corner.service"
legacy_timer="docich-soren91-corner.timer"
expected_legacy_exec="soren91-corner tick"
heartbeat_fresh_s=120
takeover_tries=24
takeover_sleep_s=5

fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

unit_present() {
  systemctl cat "$1" >/dev/null 2>&1
}

unit_active() {
  local value
  value="$(systemctl is-active "$1" 2>/dev/null || true)"
  case "$value" in
    active|activating|reloading) printf 'true\n' ;;
    inactive|failed|deactivating|unknown|'') printf 'false\n' ;;
    *) printf 'null\n' ;;
  esac
}

unit_enabled() {
  local value
  value="$(systemctl is-enabled "$1" 2>/dev/null || true)"
  case "$value" in
    enabled|enabled-runtime|static|alias|linked|linked-runtime) printf 'true\n' ;;
    disabled|masked|masked-runtime|not-found|bad|'') printf 'false\n' ;;
    *) printf 'null\n' ;;
  esac
}

user_unit_active() {
  local value
  value="$(systemctl --user is-active "$1" 2>/dev/null || true)"
  case "$value" in
    active|activating|reloading) printf 'true\n' ;;
    *) printf 'false\n' ;;
  esac
}

user_unit_enabled() {
  local value
  value="$(systemctl --user is-enabled "$1" 2>/dev/null || true)"
  case "$value" in
    enabled|enabled-runtime|alias|linked|linked-runtime) printf 'true\n' ;;
    *) printf 'false\n' ;;
  esac
}

# One bounded, sanitized projection of the rotation ledger, the heartbeat and
# the current lock holder. Only fixed enums, booleans, ids and ages leave the
# collector; the seed, request payload, paths and unit bodies never do.
rotation_projection() {
  python3 - "$DOCICH_PROD_ROOT" <<'PY'
import json
import os
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
try:
    config = tomllib.loads((root / "config" / "docich.soren-live.toml").read_text())
    raw_state = (config.get("paths") or {}).get("state_dir") or "run-soren-live"
    state_dir = Path(raw_state)
    if not state_dir.is_absolute():
        state_dir = root / state_dir
except Exception:
    state_dir = root / "run-soren-live"

def read_json(path):
    try:
        value = json.loads(path.read_text())
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}

state = read_json(state_dir / "corner_rotation.json")
heartbeat = read_json(state_dir / "corner_rotation_heartbeat.json")
pending = state.get("pending") if isinstance(state.get("pending"), dict) else None
manual = state.get("manual_pending") if isinstance(state.get("manual_pending"), dict) else None
request = (pending or {}).get("request_id") or (manual or {}).get("request_id")
status = state.get("status")
if status not in {"ready", "waiting", "running", "recovery_required"}:
    status = "unknown"
hold = state.get("clock_hold_until")
clock_hold = isinstance(hold, (int, float)) and not isinstance(hold, bool) and float(hold) > 0

at = heartbeat.get("at")
if type(at) not in (int, float) or isinstance(at, bool):
    at = None
age = None
if at is not None:
    age = int(max(0.0, __import__("time").time() - float(at)))
pid = heartbeat.get("pid")
if type(pid) is not int:
    pid = None

lock_pid = None
lock_owner = "unknown"
try:
    inode = (state_dir / "locks" / "corner-rotation.lock").stat().st_ino
except OSError:
    inode = None
if inode is not None:
    try:
        for line in Path("/proc/locks").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 6 and parts[1] == "FLOCK" and parts[5].endswith(":" + str(inode)):
                lock_pid = int(parts[4])
                break
    except Exception:
        lock_pid = None
if lock_pid is not None:
    try:
        cmdline = Path(f"/proc/{lock_pid}/cmdline").read_bytes()
    except Exception:
        cmdline = b""
    if b"corner-rotation\x00tick" in cmdline:
        lock_owner = "canonical"
    elif b"soren91-corner\x00tick" in cmdline:
        lock_owner = "legacy"
    else:
        lock_owner = "other"

print(json.dumps({
    "status": status,
    "pending_phase": (pending or {}).get("phase") if (pending or {}).get("phase") in {"selected", "dispatched"} else None,
    "has_request": isinstance(request, str) and bool(request),
    "clock_hold": clock_hold,
    "heartbeat_age_sec": age,
    "heartbeat_pid": pid,
    "heartbeat_request_present": isinstance(heartbeat.get("request_id"), str) and bool(heartbeat.get("request_id")),
    "lock_pid": lock_pid,
    "lock_owner": lock_owner,
}, sort_keys=True))
PY
}

projection_field() {
  python3 -c 'import json,sys
value = json.loads(sys.argv[1]).get(sys.argv[2])
if value is None:
    print("")
elif value is True:
    print("true")
elif value is False:
    print("false")
else:
    print(value)' "$rotation" "$1"
}

system_stream_pid() {
  systemctl show -p MainPID --value docich-stream.service 2>/dev/null || true
}

encoder_pids() {
  pgrep -f 'docich\.twica_(ffmpeg|encoder)|twica_ffmpeg|twica_encoder' 2>/dev/null | sort -n | tr '\n' ',' || true
}

legacy_main_pid() {
  systemctl show -p MainPID --value "$legacy_service" 2>/dev/null || true
}

rotation="$(rotation_projection)" || fail 'cannot read the rotation ledger' 30

hb_pid="$(projection_field heartbeat_pid)"
hb_age="$(projection_field heartbeat_age_sec)"

canonical_service_present=false
canonical_timer_present=false
[[ -f "$unit_dir/$canonical_service" && ! -L "$unit_dir/$canonical_service" ]] && canonical_service_present=true
[[ -f "$unit_dir/$canonical_timer" && ! -L "$unit_dir/$canonical_timer" ]] && canonical_timer_present=true
canonical_timer_active="$(user_unit_active "$canonical_timer")"
canonical_timer_enabled="$(user_unit_enabled "$canonical_timer")"

legacy_service_present=false
legacy_timer_present=false
unit_present "$legacy_service" && legacy_service_present=true
unit_present "$legacy_timer" && legacy_timer_present=true
legacy_service_active="$(unit_active "$legacy_service")"
legacy_service_enabled="$(unit_enabled "$legacy_service")"
legacy_timer_active="$(unit_active "$legacy_timer")"
legacy_timer_enabled="$(unit_enabled "$legacy_timer")"
legacy_exec_reviewed=false
if [[ "$legacy_service_present" == true ]]; then
  legacy_exec="$(systemctl show -p ExecStart --value "$legacy_service" 2>/dev/null || true)"
  case "$legacy_exec" in
    *"$expected_legacy_exec"*) legacy_exec_reviewed=true ;;
  esac
fi
main_pid="$(legacy_main_pid)"
lock_owner="$(projection_field lock_owner)"
status="$(projection_field status)"
pending_phase="$(projection_field pending_phase)"
has_request="$(projection_field has_request)"
clock_hold="$(projection_field clock_hold)"
heartbeat_owner="missing"
if [[ "$hb_pid" =~ ^[0-9]+$ ]]; then
  if [[ "$main_pid" =~ ^[0-9]+$ && "$hb_pid" == "$main_pid" ]]; then
    heartbeat_owner="legacy"
  elif [[ "$lock_owner" == "canonical" ]]; then
    heartbeat_owner="canonical"
  else
    heartbeat_owner="other"
  fi
fi

already_consolidated=false
if [[ "$legacy_service_active" != true && "$legacy_timer_active" != true \
      && "$legacy_timer_enabled" != true && "$canonical_timer_active" == true \
      && "$heartbeat_owner" != legacy ]]; then
  already_consolidated=true
fi

duplicate_owner=false
if [[ "$canonical_timer_active" == true \
      && ( "$legacy_service_active" == true || "$legacy_timer_active" == true ) ]]; then
  duplicate_owner=true
fi

preflight_refusal=""
if [[ "$canonical_service_present" != true || "$canonical_timer_present" != true ]]; then
  preflight_refusal="canonical_unit_missing"
elif [[ "$canonical_timer_enabled" != true || "$canonical_timer_active" != true ]]; then
  preflight_refusal="canonical_timer_not_ready"
elif [[ "$legacy_service_present" == true && "$legacy_exec_reviewed" != true ]]; then
  preflight_refusal="legacy_unit_unreviewed"
elif [[ "$status" != running && "$status" != waiting && "$status" != ready ]]; then
  preflight_refusal="rotation_not_consolidatable"
elif [[ "$clock_hold" == true ]]; then
  preflight_refusal="rotation_clock_hold"
elif [[ "$has_request" != true ]]; then
  preflight_refusal="rotation_without_request"
elif [[ "$duplicate_owner" == true ]]; then
  if [[ "$heartbeat_owner" != legacy ]]; then
    preflight_refusal="heartbeat_owner_not_legacy"
  elif [[ ! "$hb_age" =~ ^[0-9]+$ || "$hb_age" -gt "$heartbeat_fresh_s" ]]; then
    preflight_refusal="heartbeat_not_fresh"
  fi
fi

emit() {
  printf '{"schema":1,"mode":"%s","action":"%s","refusal":"%s",' "$mode" "$1" "${2:-}"
  printf '"canonical":{"service_present":%s,"timer_present":%s,"timer_active":%s,"timer_enabled":%s},' \
    "$canonical_service_present" "$canonical_timer_present" "$canonical_timer_active" "$canonical_timer_enabled"
  printf '"legacy":{"service_present":%s,"service_active":%s,"service_enabled":%s,"timer_present":%s,"timer_active":%s,"timer_enabled":%s,"exec_reviewed":%s},' \
    "$legacy_service_present" "$legacy_service_active" "$legacy_service_enabled" "$legacy_timer_present" "$legacy_timer_active" "$legacy_timer_enabled" "$legacy_exec_reviewed"
  printf '"rotation":{"status":"%s","pending_phase":%s,"clock_hold":%s},' \
    "$status" "$([[ -n "$pending_phase" ]] && printf '"%s"' "$pending_phase" || printf 'null')" "$clock_hold"
  printf '"heartbeat":{"owner":"%s","age_sec":%s},' \
    "$heartbeat_owner" "$([[ -n "$hb_age" ]] && printf '%s' "$hb_age" || printf 'null')"
  printf '"duplicate_owner":%s,"lock_owner":"%s"}\n' "$duplicate_owner" "$lock_owner"
}

if [[ "$mode" == check ]]; then
  if [[ "$already_consolidated" == true ]]; then
    emit already_consolidated
  elif [[ -n "$preflight_refusal" ]]; then
    emit refuse "$preflight_refusal"
    exit 2
  else
    emit would_consolidate
  fi
  exit 0
fi

[[ -n "$preflight_refusal" ]] && fail "refusing consolidation: $preflight_refusal" 20
command -v sudo >/dev/null 2>&1 || fail 'sudo is not available for the system-scope units' 21
sudo -n true 2>/dev/null || fail 'passwordless sudo is not available for the system-scope units' 21

pre_stream_pid="$(system_stream_pid)"
pre_encoder_pids="$(encoder_pids)"
pre_status="$status"
pre_phase="$pending_phase"

if [[ "$already_consolidated" == true ]]; then
  if [[ "$legacy_service_present" == true || "$legacy_timer_present" == true ]]; then
    [[ "$legacy_service_present" == true ]] && sudo -n systemctl disable "$legacy_service" >/dev/null 2>&1 || true
    [[ "$legacy_timer_present" == true ]] && sudo -n systemctl disable "$legacy_timer" >/dev/null 2>&1 || true
    sudo -n systemctl mask "$legacy_service" "$legacy_timer" >/dev/null 2>&1 || true
    sudo -n systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  emit already_consolidated
  exit 0
fi

rollback_legacy_owner() {
  sudo -n systemctl unmask "$legacy_service" "$legacy_timer" >/dev/null 2>&1 || true
  sudo -n systemctl enable "$legacy_timer" >/dev/null 2>&1 || true
  sudo -n systemctl start --no-block "$legacy_timer" >/dev/null 2>&1 || true
  sudo -n systemctl start --no-block "$legacy_service" >/dev/null 2>&1 || true
  sudo -n systemctl daemon-reload >/dev/null 2>&1 || true
  local restored=""
  local i age pid main_now
  for i in $(seq 1 12); do
    sleep 5
    rotation="$(rotation_projection)" || true
    age="$(projection_field heartbeat_age_sec)"
    pid="$(projection_field heartbeat_pid)"
    main_now="$(legacy_main_pid)"
    if [[ "$age" =~ ^[0-9]+$ ]] && (( age <= 60 )) \
        && [[ "$pid" =~ ^[0-9]+$ && "$main_now" =~ ^[0-9]+$ && "$pid" == "$main_now" ]]; then
      restored=true
      break
    fi
  done
  if [[ -z "$restored" ]]; then
    fail 'consolidation failed and the legacy owner did not resume within 60s' 42
  fi
  fail 'consolidation failed; the legacy owner was restored' 41
}

if [[ "$legacy_timer_present" == true ]]; then
  sudo -n systemctl disable --now "$legacy_timer" >/dev/null 2>&1 || \
    fail 'could not disable the legacy system timer' 22
fi
if [[ "$legacy_service_present" == true ]]; then
  sudo -n systemctl stop --no-block "$legacy_service" >/dev/null 2>&1 || \
    fail 'could not stop the legacy system service' 23
  stopped=false
  for _ in $(seq 1 15); do
    if [[ "$(unit_active "$legacy_service")" != true ]]; then
      stopped=true
      break
    fi
    sleep 2
  done
  [[ "$stopped" == true ]] || fail 'the legacy system service did not stop; left it running' 24
  sudo -n systemctl disable "$legacy_service" >/dev/null 2>&1 || true
fi
sudo -n systemctl mask "$legacy_service" "$legacy_timer" >/dev/null 2>&1 || \
  rollback_legacy_owner
sudo -n systemctl daemon-reload >/dev/null 2>&1 || true

legacy_pid="$main_pid"
took_over=false
for _ in $(seq 1 "$takeover_tries"); do
  systemctl --user start --no-block "$canonical_service" >/dev/null 2>&1 || true
  sleep "$takeover_sleep_s"
  rotation="$(rotation_projection)" || continue
  hb_pid="$(projection_field heartbeat_pid)"
  hb_age="$(projection_field heartbeat_age_sec)"
  lock_owner="$(projection_field lock_owner)"
  if [[ "$hb_pid" =~ ^[0-9]+$ && "$hb_pid" != "$legacy_pid" \
        && -n "$hb_age" && "$hb_age" -le 60 ]]; then
    took_over=true
    break
  fi
done

if [[ "$took_over" != true ]]; then
  rollback_legacy_owner
fi

post_stream_pid="$(system_stream_pid)"
post_encoder_pids="$(encoder_pids)"
rotation="$(rotation_projection)" || fail 'cannot re-read the rotation ledger' 31
status="$(projection_field status)"
pending_phase="$(projection_field pending_phase)"
clock_hold="$(projection_field clock_hold)"
legacy_timer_active="$(unit_active "$legacy_timer")"
legacy_service_active="$(unit_active "$legacy_service")"
legacy_timer_enabled="$(unit_enabled "$legacy_timer")"
canonical_timer_active="$(user_unit_active "$canonical_timer")"
lock_owner="$(projection_field lock_owner)"
hb_pid="$(projection_field heartbeat_pid)"
heartbeat_owner="other"
if [[ "$hb_pid" =~ ^[0-9]+$ ]]; then
  if [[ "$hb_pid" == "$legacy_pid" ]]; then
    heartbeat_owner="legacy"
  else
    heartbeat_owner="canonical"
  fi
fi
duplicate_owner=false
if [[ "$canonical_timer_active" == true \
      && ( "$legacy_service_active" == true || "$legacy_timer_active" == true ) ]]; then
  duplicate_owner=true
fi

invariant_ok=true
[[ "$status" == "$pre_status" && "$pending_phase" == "$pre_phase" && "$clock_hold" == false ]] || invariant_ok=false
[[ "$post_stream_pid" == "$pre_stream_pid" ]] || invariant_ok=false
[[ "$post_encoder_pids" == "$pre_encoder_pids" ]] || invariant_ok=false
[[ "$legacy_timer_active" != true && "$legacy_service_active" != true && "$legacy_timer_enabled" != true ]] || invariant_ok=false
[[ "$canonical_timer_active" == true ]] || invariant_ok=false
[[ "$lock_owner" == "unknown" || "$lock_owner" == "canonical" ]] || invariant_ok=false

if [[ "$invariant_ok" != true ]]; then
  # The canonical owner already holds the rotation. Do not flap the legacy
  # units back in: that would create a second owner again. Report the mismatch.
  emit invariant_mismatch
  exit 43
fi

emit consolidated
exit 0
