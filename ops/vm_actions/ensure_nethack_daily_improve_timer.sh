#!/usr/bin/env bash
set -euo pipefail

# Canonical deploy hook for the once-daily NetHack strategy candidate job.
# It installs/enables only its own user units and never starts or switches a game.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DOCICH_PROD_ROOT="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
template_dir="$DOCICH_PROD_ROOT/scripts/systemd"
service="docich-nethack-daily-improve.service"
timer="docich-nethack-daily-improve.timer"
temporary_paths=()

cleanup() {
  local path
  for path in "${temporary_paths[@]}"; do
    [[ -z "$path" ]] || rm -f -- "$path"
  done
}
trap cleanup EXIT

[[ -d "$DOCICH_PROD_ROOT/.git" || -f "$DOCICH_PROD_ROOT/.git" ]]
[[ "$(git -C "$DOCICH_PROD_ROOT" rev-parse --is-inside-work-tree 2>/dev/null)" == true ]]
command -v systemctl >/dev/null
mkdir -p "$unit_dir"

install_regular_unit() {
  local name="$1" destination="$unit_dir/$1" temporary
  [[ -f "$template_dir/$name" && ! -L "$template_dir/$name" ]]
  [[ ! -L "$destination" ]] || {
    echo "refusing to write a unit through a symlink: $destination" >&2
    exit 22
  }
  temporary="$(mktemp "$unit_dir/.nethack-daily-improve.XXXXXX")"
  temporary_paths+=("$temporary")
  if [[ "$name" == "$service" ]]; then
    sed "s#__DOCICH_ROOT__#$DOCICH_PROD_ROOT#g" "$template_dir/$name" >"$temporary"
  else
    cat "$template_dir/$name" >"$temporary"
  fi
  chmod 0644 "$temporary"
  mv -f "$temporary" "$destination"
}

install_regular_unit "$service"
install_regular_unit "$timer"
systemctl --user daemon-reload
systemctl --user enable --now "$timer"
systemctl --user is-enabled --quiet "$timer"
systemctl --user is-active --quiet "$timer"

echo "enabled NetHack daily retrospective timer: $timer"
