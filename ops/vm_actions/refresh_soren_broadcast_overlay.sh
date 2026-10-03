#!/usr/bin/env bash
# Refresh the already-open Soren91 inline broadcast rails after a reviewed
# soviet_now deploy. This is deliberately non-disruptive: the Node helper only
# attaches to loopback CDP, replaces existing overlay srcdoc frames, and detaches.
set -euo pipefail

root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ -n "${2:-}" ]] || { echo "missing --root value" >&2; exit 2; }
  root="$2"
  shift 2
fi
[[ "$#" -eq 0 ]] || { echo "unexpected arguments" >&2; exit 2; }

overlay_helper="$root/tools/refresh_inline_broadcast_overlay.mjs"
if [[ ! -f "$overlay_helper" ]]; then
  printf '{"status":"skipped","reason":"helper_missing"}\n'
  exit 0
fi
command -v node >/dev/null 2>&1 || { echo "node unavailable" >&2; exit 1; }

if [[ -f "$root/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$root/.env"
  set +a
fi

cd "$root"
timeout 15s node "$overlay_helper"
