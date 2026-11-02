#!/usr/bin/env bash
set -euo pipefail
(( $# == 0 )) || exit 64
mode="${RESUME_RECOVERY_MODE:-}"
expected="${RESUME_RECOVERY_EXPECTED:-}"
sha="${RESUME_RECOVERY_SHA:-}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 64
case "$mode" in
  check|recover) [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || exit 64 ;;
  retry) ;;
  *) exit 64 ;;
esac
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
[[ -f "$root/src/docich/hanjuku_resume_recovery.py" && -f "$root/config/docich.soren-live.toml" ]] || exit 25
# The canonical gateway holds the deployment lock; bind the fixed helper to
# the current protected-main deployment and refuse uncommitted tracked drift.
[[ "$(git -C "$root" -c core.hooksPath=/dev/null rev-parse HEAD)" == "$sha" ]] || exit 25
[[ -z "$(git -C "$root" -c core.hooksPath=/dev/null status --porcelain --untracked-files=no --ignore-submodules=all)" ]] || exit 25
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PYTHONPATH="$root/src"
python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 25)'
exec python3 -B -P -m docich.hanjuku_resume_recovery "$mode" --expected "$expected"
