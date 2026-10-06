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

result="$(
  env -i \
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
)"

receipt_dir="$HOME/.config/docich"
receipt="$receipt_dir/paper-ai-canary.json"
umask 077
mkdir -p "$receipt_dir"
chmod 0700 "$receipt_dir"
tmp="$(mktemp "$receipt_dir/.paper-ai-canary.XXXXXX")"
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

CANARY_RESULT="$result" python3 - "$expected_sha" "$tmp" <<'PY'
import json, os, pathlib, re, sys, time
sha, path = sys.argv[1], pathlib.Path(sys.argv[2])
if not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit(70)
try:
    data = json.loads(os.environ["CANARY_RESULT"])
except (KeyError, ValueError):
    raise SystemExit(71)
required = {
    "status", "research_backend", "source_count", "asset_background",
    "direct_agent", "output_chars", "output_sha256", "publishing",
    "production_state_written",
}
if set(data) != required or data.get("status") != "ok":
    raise SystemExit(72)
if data.get("publishing") is not False or data.get("production_state_written") is not False:
    raise SystemExit(73)
if data.get("research_backend") != "websearch_verified_body":
    raise SystemExit(74)
if type(data.get("source_count")) is not int or data["source_count"] < 0:
    raise SystemExit(75)
if type(data.get("asset_background")) is not bool:
    raise SystemExit(76)
if type(data.get("output_chars")) is not int or not 1 <= data["output_chars"] <= 600:
    raise SystemExit(77)
if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("output_sha256", ""))):
    raise SystemExit(78)
receipt = {
    "schema_version": 1,
    "status": "ok",
    "sha": sha,
    "recorded_at": int(time.time()),
    "research_backend": data["research_backend"],
    "source_count": data["source_count"],
    "asset_background": data["asset_background"],
    "direct_agent": data["direct_agent"],
    "output_chars": data["output_chars"],
    "output_sha256": data["output_sha256"],
    "publishing": False,
}
path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
chmod 0600 "$tmp"
mv -f "$tmp" "$receipt"
trap - EXIT
printf '%s\n' "$result"
