#!/usr/bin/env bash
set -euo pipefail

# Canonical fixed recovery surface for a corner slot whose canonical
# transition failed. The service unit is resolved from the reviewed
# production unit files: the canonical name once migrated, the legacy name
# while the compatibility alias is not installed yet. Ambiguous or missing
# states fail closed.
#
# Three phase-gated steps run in order:
#   1. The retro adapter settles only a proven terminal failure: a prelaunch
#      quiesce failure whose exact terminal receipt and stable canonical owner
#      prove that Hanjuku never became active, or a post-completion restore
#      failure whose durable restore receipt is bound to the corner state
#      (#1868/#1870; the recorded restore is replayed once when canonical
#      still owns the corner game). Other games/states return noop;
#      queued/failed refuses.
#   2. corner-rotation recover commits the now-terminal adapter observation
#      without dropping or replaying the recorded reservation.
#   3. The reviewed service unit is restarted so the next normal tick can
#      resume scheduling.
# It never edits a ledger, resets a live boundary, accepts an arbitrary unit
# or command, or restarts shared Soren/display/audio/stream services.

if (( $# != 0 )); then
  printf '%s\n' 'recover corner rotation accepts no arguments' >&2
  exit 64
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
canonical="docich-corner-rotation.service"
legacy="docich-retro-corner.service"
launcher="$DOCICH_PROD_ROOT/bin/docich"
config="$DOCICH_PROD_ROOT/config/docich.soren-live.toml"

fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

mkdir -p "$unit_dir"

canonical_regular=0
legacy_regular=0
if [[ -f "$unit_dir/$canonical" && ! -L "$unit_dir/$canonical" ]]; then canonical_regular=1; fi
if [[ -f "$unit_dir/$legacy" && ! -L "$unit_dir/$legacy" ]]; then legacy_regular=1; fi
if (( canonical_regular && legacy_regular )); then
  fail "ambiguous corner rotation unit state; refusing to recover" 23
fi
if (( canonical_regular )); then
  unit="$canonical"
elif (( legacy_regular )); then
  unit="$legacy"
else
  fail "no corner rotation service unit found; refusing to recover" 24
fi

systemctl --user show "$unit" >/dev/null

if [[ ! -x "$launcher" || ! -f "$config" ]]; then
  fail "reviewed docich launcher or config missing; refusing to recover" 25
fi

# The automatic NetHack slot owns nethack_corner.json and the meriken slot owns
# soren91_corner.json, not retro_corner.json.
# The manual NetHack operator is deliberately not used for this reservation.
# Read the existing rotation ledger and select only the fixed reviewed path.
# Unreadable/missing/mixed reservation evidence must never become permission.
if ! rotation_status="$("$launcher" --config "$config" corner-rotation status 2>/dev/null)"; then
  fail "cannot verify corner rotation reservation" 26
fi
if ! recovery_owner="$(python3 -c '
import json, sys
try:
    state = json.load(sys.stdin)
    if not isinstance(state, dict) or state.get("status") != "recovery_required":
        raise ValueError("no failed reservation")
    pending = state.get("pending")
    manual = state.get("manual_pending")
    if pending is not None and manual is not None:
        raise ValueError("ambiguous reservation")
    if isinstance(pending, dict) and pending.get("corner") == "nethack":
        if manual is not None or not isinstance(pending.get("request_id"), str) or not pending["request_id"]:
            raise ValueError("invalid NetHack automatic reservation")
        print("nethack")
    elif isinstance(pending, dict) and pending.get("corner") == "meriken":
        # meriken runs on the soren91 game: its failed slot lives in
        # soren91_corner.json, which retro-corner cannot see (#1969).
        if manual is not None or not isinstance(pending.get("request_id"), str) or not pending["request_id"]:
            raise ValueError("invalid meriken automatic reservation")
        print("meriken")
    else:
        print("other")
except (TypeError, ValueError, KeyError):
    raise SystemExit(1)
' <<<"$rotation_status")"; then
  fail "failed rotation owner is unproven" 26
fi

if [[ "$recovery_owner" == "nethack" ]]; then
  # The dedicated manager checks the exact reservation, rolled-back restore
  # receipt, generation-owned terminal pane and clean retirement. It cannot
  # relaunch a new expedition or choose an arbitrary target game.
  if ! "$launcher" --config "$config" nethack-corner recover-failed-rotation >/dev/null 2>&1; then
    fail "automatic NetHack failed-slot restore refused or incomplete" 70
  fi
else
corner_cli="retro-corner"
if [[ "$recovery_owner" == "meriken" ]]; then
  corner_cli="soren91-corner"
fi
set +e
retro_json="$("$launcher" --config "$config" "$corner_cli" recover-failed 2>/dev/null)"
retro_rc=$?
set -e
if (( retro_rc != 0 )); then
  fail "retro corner recovery rejected" 70
fi
set +e
retro_status="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(1)
status = data.get("status") if isinstance(data, dict) else None
if status not in {"succeeded", "noop", "queued", "failed"}:
    raise SystemExit(1)
print(status)
' <<<"$retro_json")"
parse_rc=$?
set -e
if (( parse_rc != 0 )); then
  fail "retro corner recovery returned invalid status" 72
fi
case "$retro_status" in
  succeeded|noop) ;;
  queued) fail "retro corner recovery is not terminal yet" 70 ;;
  *) fail "retro corner recovery rejected" 71 ;;
esac
fi

if ! "$launcher" --config "$config" corner-rotation recover >/dev/null 2>&1; then
  fail "corner rotation recovery rejected" 71
fi

systemctl --user --no-block restart "$unit"
printf 'requested failed-slot recovery: %s\n' "$unit"
