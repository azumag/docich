#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only one-shot PAPER AI canary runner.
# The VM operations gateway executes this script from /home/ubuntu/docich with
# stdout/stderr captured in its private 0600 log. No arbitrary arguments,
# model IDs, URLs, commands or output paths are accepted.

[[ $# -eq 0 ]] || exit 64
readonly root="/home/ubuntu/docich"
readonly soren_root="/home/ubuntu/soren"
readonly expected_sha="${EXPECTED_SHA:-}"
[[ "$(pwd -P)" == "$root" ]] || exit 65
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || exit 66

head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
[[ "$head" == "$expected_sha" ]] || exit 67
[[ -z "$(git -C "$root" status --porcelain --untracked-files=no --ignore-submodules=all 2>/dev/null)" ]] || exit 68

env_file="$soren_root/.env"
[[ -f "$env_file" && ! -L "$env_file" ]] || exit 69
set -a
# Existing production runtime configuration is a trusted owner-managed source,
# but only the fixed Cloudflare canary capabilities below survive into docich.
# shellcheck disable=SC1090
. "$env_file"
set +a

search_account="${DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID:-}"
search_token="${DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN:-}"
direct_account="${DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID:-}"
direct_token="${CLOUDFLARE_API_TOKEN:-}"
direct_token_file="${CLOUDFLARE_API_TOKEN_FILE:-}"

exec env -i \
  PATH="/usr/local/bin:/usr/bin:/bin" \
  LANG="C.UTF-8" \
  PYTHONPATH="$root/src" \
  DOCICH_ALLOW_REAL_AI="1" \
  DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID="$search_account" \
  DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN="$search_token" \
  DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID="$direct_account" \
  CLOUDFLARE_API_TOKEN="$direct_token" \
  CLOUDFLARE_API_TOKEN_FILE="$direct_token_file" \
  "$root/bin/docich" \
  --config "$root/config/docich.soren-live.toml" \
  paper-ai-canary --execute
