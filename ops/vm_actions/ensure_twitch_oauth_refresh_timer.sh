#!/usr/bin/env bash
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
tmp=""
trap 'if [[ -n "$tmp" ]]; then rm -f "$tmp"; fi' EXIT
for unit in docich-twitch-oauth-refresh.service docich-twitch-oauth-refresh.timer; do
  source="$root/scripts/systemd/$unit"
  target="$unit_dir/$unit"
  [[ -f "$source" && ! -L "$source" && ! -L "$target" ]] || exit 2
  tmp="$(mktemp "$unit_dir/.twitch-oauth-refresh.XXXXXX")"
  sed "s#__DOCICH_ROOT__#$root#g" "$source" > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$target"
  tmp=""
done
systemctl --user daemon-reload
systemctl --user enable --now docich-twitch-oauth-refresh.timer
systemctl --user is-enabled --quiet docich-twitch-oauth-refresh.timer
systemctl --user is-active --quiet docich-twitch-oauth-refresh.timer
