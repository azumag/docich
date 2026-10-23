#!/usr/bin/env bash
set -euo pipefail
# Canonical deploy hook for the docich#2578 host metrics ring sampler.
# It installs only its own user units, enables the minute timer, and takes one
# sample so a deploy that cannot write the ring fails here instead of leaving
# diagnostics without evidence.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
service="docich-host-metrics.service"
timer="docich-host-metrics.timer"

# The gateway exec channel may scrub or replace HOME, so both the unit tree
# and the ring path are resolved from the passwd entry exactly like
# host_metrics.state_root(). ``$HOME`` is never consulted.
command -v getent >/dev/null 2>&1 || exit 2
home="$(getent passwd "$(id -u)" | cut -d: -f6)" || exit 2
[[ "$home" == "${DOCICH_PROD_HOME:-/home/ubuntu}" ]] || exit 2
unit_dir="${XDG_CONFIG_HOME:-$home/.config}/systemd/user"
# The units must land in the same user's unit tree the ring belongs to.
[[ "$unit_dir" == "$home"/* ]] || exit 2
state_dir="$home/.local/state/docich/host-metrics"
ring="$state_dir/ring.jsonl"

mkdir -p "$unit_dir"
tmp=""
trap 'if [[ -n "$tmp" ]]; then rm -f "$tmp"; fi' EXIT
for unit in "$service" "$timer"; do
  source="$root/scripts/systemd/$unit"
  target="$unit_dir/$unit"
  [[ -f "$source" && ! -L "$source" && ! -L "$target" ]] || exit 2
  tmp="$(mktemp "$unit_dir/.host-metrics.XXXXXX")"
  sed "s#__DOCICH_ROOT__#$root#g" "$source" > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$target"
  tmp=""
done
systemctl --user daemon-reload || exit 2
systemctl --user enable --now "$timer" || exit 2
systemctl --user is-enabled --quiet "$timer" || exit 2
systemctl --user is-active --quiet "$timer" || exit 2

# `enable --now` starts the timer only; the first scheduled tick can be up to a
# minute away, so run the service once (argument-free, as in production) and
# verify the sample it leaves behind.
systemctl --user start "$service" || exit 3
[[ -f "$ring" && ! -L "$ring" ]] || exit 3
[[ -s "$ring" ]] || exit 3
[[ "$(stat -c '%a' -- "$state_dir")" == "700" ]] || exit 3
[[ "$(stat -c '%a' -- "$ring")" == "600" ]] || exit 3
