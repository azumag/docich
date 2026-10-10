#!/usr/bin/env bash
set -euo pipefail
(( $# == 0 )) || exit 64
[[ "${NETHACK_ADMIN_EXPECTED:-}" =~ ^[0-9a-f]{64}$ ]] || exit 64
[[ "${NETHACK_ADMIN_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ "${NETHACK_ADMIN_EXPIRES:-}" =~ ^[1-9][0-9]{9,11}$ ]] || exit 64
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
[[ -f "$root/src/docich/nethack_admin_release.py" && -f "$root/config/docich.soren-live.toml" ]] || exit 25
[[ "$(git -C "$root" -c core.hooksPath=/dev/null rev-parse HEAD)" == "$NETHACK_ADMIN_SHA" ]] || exit 25
[[ -z "$(git -C "$root" -c core.hooksPath=/dev/null status --porcelain --untracked-files=no --ignore-submodules=all)" ]] || exit 25
[[ -z "$(git -C "$root" -c core.hooksPath=/dev/null status --porcelain --untracked-files=all -- src/docich)" ]] || exit 25
export PYTHONPATH="$root/src"
python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 25)'
# Bounded metadata operation; timeout never becomes a successful release.
exec timeout 60 python3 -B -P -X pycache_prefix=/dev/null/docich-disabled-cache -m docich.nethack_admin_release --expected "$NETHACK_ADMIN_EXPECTED" --expires "$NETHACK_ADMIN_EXPIRES" --sha "$NETHACK_ADMIN_SHA"
