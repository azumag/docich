#!/usr/bin/env bash
set -euo pipefail

# Fixed, owner-reviewed migration from the legacy unit names
# (docich-retro-corner.*) to the canonical names (docich-corner-rotation.*).
# This script accepts no unit names or commands as input. It is fail-closed:
# the legacy timer is stopped/disabled first, a running legacy service aborts
# the migration without killing it, legacy units that do not match the
# reviewed templates are never overwritten, and the legacy names become
# relative aliases of the canonical units so exactly one timer can be enabled.
# It never restarts shared Soren/display/audio/stream services and never
# touches corner state, locks, pause markers or game-switch receipts.

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

install_regular_unit() {
  local name="$1" dest="$unit_dir/$1" tmp
  [[ -L "$dest" ]] && fail "refusing to write a unit through a symlink: $dest" 22
  tmp="$(render_to_temp "$name")"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
  TEMP_PATH=""
}

install_canonical_unit() {
  local name="$1" dest="$unit_dir/$1" tmp
  [[ -L "$dest" ]] && fail "refusing to write a unit through a symlink: $dest" 22
  tmp="$(render_to_temp "$name")"
  if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    TEMP_PATH=""
    return 0
  fi
  if [[ -e "$dest" ]]; then
    rm -f "$tmp"
    TEMP_PATH=""
    fail "canonical unit exists with unexpected content: $dest" 42
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
  TEMP_PATH=""
}

verify_canonical_unit_installable() {
  local name="$1" dest="$unit_dir/$1" tmp
  [[ -L "$dest" ]] && fail "refusing to write a unit through a symlink: $dest" 22
  tmp="$(render_to_temp "$name")"
  if [[ -e "$dest" ]] && ! cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    TEMP_PATH=""
    fail "canonical unit exists with unexpected content: $dest" 42
  fi
  rm -f "$tmp"
  TEMP_PATH=""
}

verify_legacy_unit_matches_reviewed() {
  local name="$1" dest="$unit_dir/$1" tmp
  [[ -f "$dest" && ! -L "$dest" ]] || fail "legacy unit is missing or not a regular file: $dest" 31
  tmp="$(render_to_temp "$name")"
  if ! cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    TEMP_PATH=""
    fail "legacy unit does not match the reviewed template: $dest" 32
  fi
  rm -f "$tmp"
  TEMP_PATH=""
}

make_relative_alias() {
  local name="$1" target="$2" dest="$unit_dir/$1" tmp
  [[ -L "$dest" ]] && fail "refusing to replace an unexpected symlink: $dest" 36
  tmp="$unit_dir/.${name}.alias.$$"
  rm -f "$tmp"
  ln -s "$target" "$tmp"
  mv -f "$tmp" "$dest"
}

verify_alias_resolution() {
  local name="$1" expected="$2" info id names
  info="$(systemctl --user show -p Id -p Names "$name" 2>/dev/null)" || \
    fail "could not query the legacy unit: $name" 38
  id="$(printf '%s\n' "$info" | sed -n 's/^Id=//p')"
  names="$(printf '%s\n' "$info" | sed -n 's/^Names=//p')"
  if [[ "$id" != "$expected" && " $names " != *" $expected "* ]]; then
    fail "legacy name did not resolve to the canonical unit: $name -> ${id:-unknown}" 38
  fi
}

mkdir -p "$unit_dir"

# Idempotency: an already migrated production verifies and exits successfully.
if [[ -L "$unit_dir/$legacy_service" || -L "$unit_dir/$legacy_timer" ]]; then
  [[ -L "$unit_dir/$legacy_service" && -L "$unit_dir/$legacy_timer" ]] || \
    fail "inconsistent legacy alias state; refusing to migrate" 23
  [[ "$(readlink "$unit_dir/$legacy_service")" == "$canonical_service" ]] || \
    fail "unexpected legacy service alias target" 23
  [[ "$(readlink "$unit_dir/$legacy_timer")" == "$canonical_timer" ]] || \
    fail "unexpected legacy timer alias target" 23
  [[ -f "$unit_dir/$canonical_service" && ! -L "$unit_dir/$canonical_service" ]] || \
    fail "canonical service unit is missing or aliased" 24
  [[ -f "$unit_dir/$canonical_timer" && ! -L "$unit_dir/$canonical_timer" ]] || \
    fail "canonical timer unit is missing or aliased" 24
  systemctl --user is-enabled --quiet "$canonical_timer" || fail "canonical timer is not enabled" 25
  systemctl --user is-active --quiet "$canonical_timer" || fail "canonical timer is not active" 25
  [[ ! -e "$unit_dir/timers.target.wants/$legacy_timer" && ! -L "$unit_dir/timers.target.wants/$legacy_timer" ]] || \
    fail "legacy timer is independently enabled" 26
  verify_alias_resolution "$legacy_timer" "$canonical_timer"
  verify_alias_resolution "$legacy_service" "$canonical_service"
  printf 'corner rotation timer is already canonical: %s\n' "$canonical_timer"
  exit 0
fi

# The legacy units must be the reviewed regular files before any switch, and
# the canonical destinations must be installable, so an unexpected drift
# aborts before the legacy timer is touched.
verify_legacy_unit_matches_reviewed "$legacy_service"
verify_legacy_unit_matches_reviewed "$legacy_timer"
verify_canonical_unit_installable "$canonical_service"
verify_canonical_unit_installable "$canonical_timer"

# Never kill a running corner tick: abort and retry after it exits.
if systemctl --user is-active --quiet "$legacy_service"; then
  fail "legacy corner rotation service is active; retry after it exits (not killing it)" 33
fi

# Stop and disable the legacy timer before installing anything canonical.
systemctl --user disable --now "$legacy_timer"
systemctl --user is-active --quiet "$legacy_timer" && fail "legacy timer is still active after disable" 34
if systemctl --user is-enabled --quiet "$legacy_timer"; then
  fail "legacy timer is still enabled after disable" 35
fi
[[ ! -e "$unit_dir/timers.target.wants/$legacy_timer" && ! -L "$unit_dir/timers.target.wants/$legacy_timer" ]] || \
  fail "legacy timer enable link remains after disable" 35

# A tick may have started while the timer was being disabled. Restore the
# legacy timer and abort instead of switching under a running service.
if systemctl --user is-active --quiet "$legacy_service"; then
  systemctl --user enable --now "$legacy_timer"
  fail "legacy corner rotation service started during migration; restored the legacy timer" 33
fi

# Install the canonical units atomically, then make the legacy names aliases.
install_canonical_unit "$canonical_service"
install_canonical_unit "$canonical_timer"
make_relative_alias "$legacy_service" "$canonical_service"
make_relative_alias "$legacy_timer" "$canonical_timer"

systemctl --user daemon-reload
systemctl --user enable --now "$canonical_timer"
systemctl --user is-enabled --quiet "$canonical_timer" || fail "canonical timer is not enabled after migration" 25
systemctl --user is-active --quiet "$canonical_timer" || fail "canonical timer is not active after migration" 25

# Exactly one independent timer: the legacy name must not own an enable link.
[[ ! -e "$unit_dir/timers.target.wants/$legacy_timer" && ! -L "$unit_dir/timers.target.wants/$legacy_timer" ]] || \
  fail "legacy timer is independently enabled after migration" 37

verify_alias_resolution "$legacy_timer" "$canonical_timer"
verify_alias_resolution "$legacy_service" "$canonical_service"

printf 'migrated corner rotation timer: %s -> %s\n' "$legacy_timer" "$canonical_timer"
