#!/usr/bin/env bash
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
tmp=""
trap 'if [[ -n "$tmp" ]]; then rm -f "$tmp"; fi' EXIT
for unit in docich-opencode-retention.service docich-opencode-retention.timer; do
  source="$root/scripts/systemd/$unit"
  target="$unit_dir/$unit"
  [[ -f "$source" && ! -L "$source" && ! -L "$target" ]] || exit 2
  tmp="$(mktemp "$unit_dir/.opencode-retention.XXXXXX")"
  sed "s#__DOCICH_ROOT__#$root#g" "$source" > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$target"
  tmp=""
done
systemctl --user daemon-reload
systemctl --user enable --now docich-opencode-retention.timer
systemctl --user is-enabled --quiet docich-opencode-retention.timer
systemctl --user is-active --quiet docich-opencode-retention.timer
