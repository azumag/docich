#!/usr/bin/env bash
set -euo pipefail
if (( $# != 0 )); then
  printf '%s\n' 'manual cancellation accepts no arguments' >&2
  exit 64
fi
mode="${CANCEL_MODE:-check}"
expected="${CANCEL_EXPECTED:-}"
case "$mode" in
  check) [[ -z "$expected" ]] || exit 64; args=(check) ;;
  apply) [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || exit 64; args=(apply --expected "$expected") ;;
  *) exit 64 ;;
esac
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
[[ -f "$root/src/docich/hanjuku_manual_cancel.py" && -f "$root/config/docich.soren-live.toml" ]] || exit 25
export PYTHONPATH="$root/src"
exec python3 -B -m docich.hanjuku_manual_cancel "${args[@]}"
