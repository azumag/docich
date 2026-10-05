#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only one-shot PAPER AI canary runner.
# The VM operations gateway executes this script from /home/ubuntu/docich with
# stdout/stderr captured in its private 0600 log. No arbitrary arguments,
# model IDs, URLs, commands or output paths are accepted.

[[ $# -eq 0 ]] || exit 64
root="/home/ubuntu/docich"
soren_root="/home/ubuntu/soren"
[[ "$(pwd -P)" == "$root" ]] || exit 65
[[ "${EXPECTED_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || exit 66

head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
[[ "$head" == "$EXPECTED_SHA" ]] || exit 67
[[ -z "$(git -C "$root" status --porcelain --untracked-files=no --ignore-submodules=all 2>/dev/null)" ]] || exit 68

env_file="$soren_root/.env"
[[ -f "$env_file" && ! -L "$env_file" ]] || exit 69
set -a
# Existing production runtime configuration. The canary itself projects only
# its fixed Cloudflare search/direct capabilities into child workers.
# shellcheck disable=SC1090
. "$env_file"
set +a

export DOCICH_ALLOW_REAL_AI=1
export PYTHONPATH="$root/src"

exec "$root/bin/docich" \
  --config "$root/config/docich.soren-live.toml" \
  paper-ai-canary --execute
