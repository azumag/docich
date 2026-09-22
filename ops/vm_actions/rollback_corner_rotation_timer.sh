#!/usr/bin/env bash
set -euo pipefail

# Fixed, owner-reviewed rollback from the canonical unit names
# (docich-corner-rotation.*) back to the legacy regular units
# (docich-retro-corner.*). It accepts no unit names or commands as input.
# It stops/disables the canonical timer, refuses to kill a running canonical
# service, restores the reviewed legacy regular units, and leaves exactly the
# legacy timer enabled. It never restarts shared Soren/display/audio/stream
# services and never touches corner state, locks, pause markers or
# game-switch receipts. The canonical unit files stay on disk (disabled); a
# later deploy that still carries the reviewed migration epoch would migrate
# again, so rollback is paired with reverting that epoch in main.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
template_dir="$DOCICH_PROD_ROOT/scripts/systemd"

legacy_service="docich-retro-corner.service"
legacy_timer="docich-retro-corner.timer"
canonical_service="docich-corner-rotation.service"
canonical_timer="docich-corner-rotation.timer"

TEMP_PATH=""
cleanup() {
  if [[ -n "$TEMP_PATH" ]]; then rm -f "$TEMP_PATH"; fi
}
trap cleanup EXIT

fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

render_unit() {
  local name="$1"
  [[ -f "$template_dir/$name" ]] || fail "missing corner rotation unit template: $template_dir/$name" 21
  sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$template_dir/$name"
}

render_to_temp() {
  local name="$1"
  TEMP_PATH="$(mktemp "$unit_dir/.corner-rotation.XXXXXX")" || fail "cannot create a temporary unit file" 40
  render_unit "$name" > "$TEMP_PATH"
  printf '%s' "$TEMP_PATH"
}

install_regular_unit_if_changed() {
  local name="$1" dest="$unit_dir/$1" tmp
  [[ -L "$dest" ]] && fail "refusing to write a unit through a symlink: $dest" 22
  tmp="$(render_to_temp "$name")"
  if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    TEMP_PATH=""
    return 0
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
  TEMP_PATH=""
}

mkdir -p "$unit_dir"

# Both reviewed legacy templates must exist before anything is stopped.
for name in "$legacy_service" "$legacy_timer"; do
  [[ -f "$template_dir/$name" ]] || fail "missing corner rotation unit template: $template_dir/$name" 21
done

# Idempotency: a production already running the legacy regular units verifies
# and exits successfully.
if [[ -f "$unit_dir/$legacy_timer" && ! -L "$unit_dir/$legacy_timer" && \
      -f "$unit_dir/$legacy_service" && ! -L "$unit_dir/$legacy_service" ]]; then
  systemctl --user is-enabled --quiet "$legacy_timer" || fail "legacy timer is not enabled" 25
  systemctl --user is-active --quiet "$legacy_timer" || fail "legacy timer is not active" 25
  [[ ! -e "$unit_dir/timers.target.wants/$canonical_timer" && ! -L "$unit_dir/timers.target.wants/$canonical_timer" ]] || \
    fail "canonical timer is independently enabled" 26
  printf 'corner rotation timer is already legacy: %s\n' "$legacy_timer"
  exit 0
fi

# The canonical units must be the migrated regular files.
[[ -f "$unit_dir/$canonical_service" && ! -L "$unit_dir/$canonical_service" ]] || \
  fail "canonical service unit is missing or aliased" 24
[[ -f "$unit_dir/$canonical_timer" && ! -L "$unit_dir/$canonical_timer" ]] || \
  fail "canonical timer unit is missing or aliased" 24

# The legacy names must be the expected relative aliases (or absent).
for name in "$legacy_service" "$legacy_timer"; do
  if [[ -L "$unit_dir/$name" ]]; then
    target="$canonical_service"
    [[ "$name" == "$legacy_timer" ]] && target="$canonical_timer"
    [[ "$(readlink "$unit_dir/$name")" == "$target" ]] || \
      fail "unexpected legacy alias target: $unit_dir/$name" 23
  elif [[ -e "$unit_dir/$name" ]]; then
    fail "legacy name is not the expected alias: $unit_dir/$name" 23
  fi
done

# Never kill a running corner tick: abort and retry after it exits.
if systemctl --user is-active --quiet "$canonical_service"; then
  fail "canonical corner rotation service is active; retry after it exits (not killing it)" 33
fi

# Stop and disable the canonical timer before restoring anything legacy.
systemctl --user disable --now "$canonical_timer"
systemctl --user is-active --quiet "$canonical_timer" && fail "canonical timer is still active after disable" 34
if systemctl --user is-enabled --quiet "$canonical_timer"; then
  fail "canonical timer is still enabled after disable" 35
fi
[[ ! -e "$unit_dir/timers.target.wants/$canonical_timer" && ! -L "$unit_dir/timers.target.wants/$canonical_timer" ]] || \
  fail "canonical timer enable link remains after disable" 35

# A tick may have started while the timer was being disabled. Restore the
# canonical timer and abort instead of rolling back under a running service.
if systemctl --user is-active --quiet "$canonical_service"; then
  systemctl --user enable --now "$canonical_timer"
  fail "canonical corner rotation service started during rollback; restored the canonical timer" 33
fi

# Replace the legacy aliases with the reviewed regular units.
rm -f "$unit_dir/$legacy_service" "$unit_dir/$legacy_timer"
install_regular_unit_if_changed "$legacy_service"
install_regular_unit_if_changed "$legacy_timer"

systemctl --user daemon-reload
systemctl --user enable --now "$legacy_timer"
systemctl --user is-enabled --quiet "$legacy_timer" || fail "legacy timer is not enabled after rollback" 25
systemctl --user is-active --quiet "$legacy_timer" || fail "legacy timer is not active after rollback" 25

# Exactly one independent timer: the canonical name must not own an enable link.
[[ ! -e "$unit_dir/timers.target.wants/$canonical_timer" && ! -L "$unit_dir/timers.target.wants/$canonical_timer" ]] || \
  fail "canonical timer is independently enabled after rollback" 37

printf 'rolled back corner rotation timer: %s -> %s\n' "$canonical_timer" "$legacy_timer"
