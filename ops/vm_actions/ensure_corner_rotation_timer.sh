#!/usr/bin/env bash
set -euo pipefail

# Canonical deploy hook for the rolling-corner timer. This is intentionally
# separate from the shared display/Soren/audio services and never starts a
# game directly; the first bounded tick is scheduled by systemd itself.
#
# Stage 1 keeps the legacy unit names (docich-retro-corner.*) active in
# production. The reviewed rename to docich-corner-rotation.* is gated by
# ops/vm_actions/corner_rotation_timer_migration_epoch: while that file is
# absent this hook only reconciles the legacy units. Once a later reviewed
# change adds the epoch, the hook runs the fixed migration helper first and
# then maintains the canonical units. A production that is already migrated
# is always maintained as canonical, so a deploy never flips it back.
#
# Unit files are rendered to a temporary file and renamed into place, so a
# placement is atomic and a symlink (e.g. the legacy alias after migration)
# is never written through. No unit name or command is accepted as input and
# no shared Soren/display/audio/stream service is restarted.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
template_dir="$DOCICH_PROD_ROOT/scripts/systemd"
migration_epoch="$DOCICH_PROD_ROOT/ops/vm_actions/corner_rotation_timer_migration_epoch"
migration_helper="$DOCICH_PROD_ROOT/ops/vm_actions/migrate_corner_rotation_timer.sh"

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

render_to_temp() {
  local name="$1"
  [[ -f "$template_dir/$name" ]] || fail "missing corner rotation unit template: $template_dir/$name" 21
  TEMP_PATH="$(mktemp "$unit_dir/.corner-rotation.XXXXXX")" || fail "cannot create a temporary unit file" 40
  sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$template_dir/$name" > "$TEMP_PATH"
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

verify_timer_enabled_active() {
  local timer="$1"
  systemctl --user is-enabled --quiet "$timer" || fail "corner rotation timer is not enabled: $timer" 25
  systemctl --user is-active --quiet "$timer" || fail "corner rotation timer is not active: $timer" 25
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

legacy_aliases=0
if [[ -L "$unit_dir/$legacy_service" || -L "$unit_dir/$legacy_timer" ]]; then
  [[ -L "$unit_dir/$legacy_service" && -L "$unit_dir/$legacy_timer" ]] || \
    fail "inconsistent legacy alias state; refusing to reconcile" 23
  legacy_aliases=1
fi

if (( legacy_aliases )); then
  [[ "$(readlink "$unit_dir/$legacy_service")" == "$canonical_service" ]] || \
    fail "unexpected legacy service alias target" 23
  [[ "$(readlink "$unit_dir/$legacy_timer")" == "$canonical_timer" ]] || \
    fail "unexpected legacy timer alias target" 23
  install_regular_unit "$canonical_service"
  install_regular_unit "$canonical_timer"
  systemctl --user daemon-reload
  [[ ! -e "$unit_dir/timers.target.wants/$legacy_timer" && ! -L "$unit_dir/timers.target.wants/$legacy_timer" ]] || \
    fail "legacy timer is independently enabled" 37
  systemctl --user enable --now "$canonical_timer"
  verify_timer_enabled_active "$canonical_timer"
  verify_alias_resolution "$legacy_timer" "$canonical_timer"
  verify_alias_resolution "$legacy_service" "$canonical_service"
  printf 'enabled canonical corner rotation timer: %s\n' "$canonical_timer"
  exit 0
fi

if [[ -e "$migration_epoch" || -L "$migration_epoch" ]]; then
  [[ -f "$migration_epoch" && ! -L "$migration_epoch" ]] || \
    fail "migration epoch must be a reviewed regular file: $migration_epoch" 41
  [[ -f "$migration_helper" && ! -L "$migration_helper" ]] || \
    fail "migration helper must be a reviewed regular file: $migration_helper" 41
  bash "$migration_helper"
  install_regular_unit "$canonical_service"
  install_regular_unit "$canonical_timer"
  systemctl --user daemon-reload
  [[ ! -e "$unit_dir/timers.target.wants/$legacy_timer" && ! -L "$unit_dir/timers.target.wants/$legacy_timer" ]] || \
    fail "legacy timer is independently enabled" 37
  systemctl --user enable --now "$canonical_timer"
  verify_timer_enabled_active "$canonical_timer"
  verify_alias_resolution "$legacy_timer" "$canonical_timer"
  verify_alias_resolution "$legacy_service" "$canonical_service"
  printf 'enabled canonical corner rotation timer: %s\n' "$canonical_timer"
  exit 0
fi

# Stage 1: reconcile only the legacy regular units (current production state).
for name in "$legacy_service" "$legacy_timer"; do
  install_regular_unit "$name"
done
systemctl --user daemon-reload
systemctl --user enable --now "$legacy_timer"
verify_timer_enabled_active "$legacy_timer"
printf 'enabled legacy corner rotation timer: %s\n' "$legacy_timer"
