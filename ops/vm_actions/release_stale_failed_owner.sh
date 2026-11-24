#!/usr/bin/env bash
set -euo pipefail
(( $# == 0 )) || exit 64
mode="${STALE_FAILED_MODE:-}"
expected="${STALE_FAILED_EXPECTED:-}"
sha="${STALE_FAILED_SHA:-}"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || exit 64
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 64
case "$mode" in
  check|release) ;;
  *) exit 64 ;;
esac
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
[[ -f "$root/src/docich/stale_failed_owner_release.py" && -f "$root/config/docich.soren-live.toml" ]] || exit 25
# The gateway holds its deployment lock while executing this fixed script.
# Recheck inside that transaction, closing the gap after workflow status.
[[ "$(git -C "$root" -c core.hooksPath=/dev/null rev-parse HEAD)" == "$sha" ]] || exit 25
[[ -z "$(git -C "$root" -c core.hooksPath=/dev/null status --porcelain --untracked-files=no --ignore-submodules=all)" ]] || exit 25
export PYTHONPATH="$root/src"
python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 25)'
exec python3 -B -P -m docich.stale_failed_owner_release "$mode" --expected "$expected"
