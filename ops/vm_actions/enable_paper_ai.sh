#!/usr/bin/env bash
set -euo pipefail

# Enable the reviewed PAPER Web Search + direct-AI path for future corner ticks.
# Requires a fresh successful one-shot canary for this exact deployed SHA.
# No service is restarted and no running corner is interrupted.

[[ $# -eq 0 ]] || exit 64
readonly root="/home/ubuntu/docich"
readonly soren_root="/home/ubuntu/soren"
readonly expected_sha="${EXPECTED_SHA:-}"
readonly config_dir="$HOME/.config/docich"
readonly receipt="$config_dir/paper-ai-canary.json"
readonly target="$config_dir/paper-ai.env"

[[ "$(pwd -P)" == "$root" ]] || exit 65
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || exit 66
head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
[[ "$head" == "$expected_sha" ]] || exit 67
[[ -z "$(git -C "$root" status --porcelain --untracked-files=no --ignore-submodules=all 2>/dev/null)" ]] || exit 68
[[ -f "$receipt" && ! -L "$receipt" ]] || exit 69

python3 - "$receipt" "$expected_sha" <<'PY'
import json, pathlib, re, sys, time
path, sha = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(70)
expected = {
    "schema_version", "status", "sha", "recorded_at", "research_backend",
    "source_count", "asset_background", "direct_agent", "output_chars",
    "output_sha256", "publishing",
}
if set(data) != expected or data.get("schema_version") != 1 or data.get("status") != "ok":
    raise SystemExit(71)
if data.get("sha") != sha or not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit(72)
stamp = data.get("recorded_at")
if type(stamp) is not int or not 0 <= int(time.time()) - stamp <= 86400:
    raise SystemExit(73)
if data.get("research_backend") != "websearch_verified_body" or data.get("publishing") is not False:
    raise SystemExit(74)
if type(data.get("source_count")) is not int or data["source_count"] < 0:
    raise SystemExit(75)
if type(data.get("asset_background")) is not bool:
    raise SystemExit(76)
if type(data.get("output_chars")) is not int or not 1 <= data["output_chars"] <= 600:
    raise SystemExit(77)
if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("output_sha256", ""))):
    raise SystemExit(78)
PY

source_env="$soren_root/.env"
[[ -f "$source_env" && ! -L "$source_env" ]] || exit 79
set -a
# shellcheck disable=SC1090
. "$source_env"
set +a

search_account="${DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID:-}"
search_token="${DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN:-}"
direct_account="${DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID:-}"
direct_token="${CLOUDFLARE_API_TOKEN:-}"
direct_token_file="${CLOUDFLARE_API_TOKEN_FILE:-}"

[[ "$search_account" =~ ^[A-Fa-f0-9]{32}$ ]] || exit 80
[[ "$direct_account" =~ ^[A-Fa-f0-9]{32}$ ]] || exit 81
[[ -n "$search_token" ]] || exit 82
if [[ -n "$direct_token" && -n "$direct_token_file" ]] || [[ -z "$direct_token" && -z "$direct_token_file" ]]; then
  exit 83
fi

safe_value() {
  local value="$1"
  [[ "${#value}" -le 4096 ]] || return 1
  [[ "$value" =~ ^[A-Za-z0-9._~+/:=-]+$ ]]
}
safe_value "$search_token" || exit 84
if [[ -n "$direct_token" ]]; then
  safe_value "$direct_token" || exit 85
else
  [[ "$direct_token_file" == /* ]] || exit 86
  safe_value "$direct_token_file" || exit 86
  [[ -f "$direct_token_file" && ! -L "$direct_token_file" ]] || exit 86
fi

umask 077
mkdir -p "$config_dir"
chmod 0700 "$config_dir"
tmp="$(mktemp "$config_dir/.paper-ai.env.XXXXXX")"
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT
{
  printf '%s\n' \
    'DOCICH_PAPER_RESEARCH_BACKEND=websearch' \
    'DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare' \
    'DOCICH_REPLY_WEB_SEARCH_ENABLED=1' \
    'DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_PROVIDER=ceramic' \
    'DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_GATEWAY_ID=default' \
    "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID=$search_account" \
    "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN=$search_token" \
    'DOCICH_PAPER_SCRIPT_DIRECT_ENABLED=1' \
    'DOCICH_PAPER_IMPROVE_DIRECT_ENABLED=1' \
    "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID=$direct_account"
  if [[ -n "$direct_token" ]]; then
    printf 'CLOUDFLARE_API_TOKEN=%s\n' "$direct_token"
  else
    printf 'CLOUDFLARE_API_TOKEN_FILE=%s\n' "$direct_token_file"
  fi
} > "$tmp"
chmod 0600 "$tmp"
mv -f "$tmp" "$target"
trap - EXIT

# No restart: future oneshot rotation/PAPER invocations load the optional file.
printf 'paper_ai_enabled sha=%s\n' "$expected_sha"
