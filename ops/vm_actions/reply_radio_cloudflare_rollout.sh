#!/usr/bin/env bash
set -euo pipefail

# One-time owner-approved production rollout for verified-Web comment research
# and the opt-in native RADIO consumer. This script is sent through the existing
# fixed VM exec gateway only after the exact protected-main SHA is deployed.
#
# Safety:
# - fixed production roots, no caller arguments or arbitrary commands
# - real Web/JEV/direct-LLM canaries run before any .env mutation
# - only an owned marked block is appended/replaced in Soren .env
# - radio/chat/kick are the only workers restarted
# - failure after mutation restores the exact previous .env and best-effort
#   replaces those workers again

[[ $# -eq 0 ]] || exit 64
readonly docich_root="/home/ubuntu/docich"
readonly soren_root="/home/ubuntu/soren"
readonly env_file="$soren_root/.env"
readonly expected_sha="${EXPECTED_SHA:-}"
readonly canary_agent="cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8"
readonly begin_marker="# BEGIN DOCICH VERIFIED WEB ROLLOUT v1"
readonly end_marker="# END DOCICH VERIFIED WEB ROLLOUT v1"

[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || exit 65
[[ -d "$docich_root/.git" ]] || exit 66
[[ "$(git -C "$docich_root" rev-parse HEAD 2>/dev/null || true)" == "$expected_sha" ]] || exit 67
[[ -z "$(git -C "$docich_root" status --porcelain --untracked-files=no --ignore-submodules=all 2>/dev/null)" ]] || exit 68
[[ -f "$env_file" && ! -L "$env_file" ]] || exit 69
[[ -f "$docich_root/ops/vm_actions/restart_radio_worker.sh" ]] || exit 70
[[ -f "$docich_root/ops/vm_actions/restart_chat_kick_workers.sh" ]] || exit 70

# Load the existing owner-managed production capabilities. Do not echo them.
set +e +u
set -a
# shellcheck disable=SC1090
. "$env_file" >/dev/null 2>&1
source_rc=$?
set +a
set -u -e
[[ "$source_rc" -eq 0 ]] || exit 71

# Preserve existing global AI/JEV policy rather than broadening it here.
[[ "${DOCICH_ALLOW_REAL_AI:-0}" == "1" ]] || exit 72
[[ "${DOCICH_REPLY_ROUTING_ENABLED:-0}" == "1" ]] || exit 72
[[ "${COMMENT_CLASSIFIER_BACKEND:-}" == "jev" ]] || exit 72
[[ "${DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID:-}" =~ ^[A-Fa-f0-9]{32}$ ]] || exit 73
[[ -n "${DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN:-}" ]] || exit 73
[[ "${DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID:-}" =~ ^[A-Fa-f0-9]{32}$ ]] || exit 73
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_API_TOKEN_FILE:-}" ]] \
   || [[ -z "${CLOUDFLARE_API_TOKEN:-}" && -z "${CLOUDFLARE_API_TOKEN_FILE:-}" ]]; then
  exit 73
fi
if [[ -n "${CLOUDFLARE_API_TOKEN_FILE:-}" ]]; then
  [[ "$CLOUDFLARE_API_TOKEN_FILE" == /* && -f "$CLOUDFLARE_API_TOKEN_FILE" && ! -L "$CLOUDFLARE_API_TOKEN_FILE" ]] || exit 73
fi

# Canary-only flags. The persistent block is written only after both real calls
# succeed. Existing JEV route/provider credentials remain unchanged.
export DOCICH_REPLY_WEB_SEARCH_ENABLED=1
export DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare
export DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_PROVIDER=ceramic
export DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_GATEWAY_ID=default
export DOCICH_REPLY_WEB_DIRECT_ENABLED=1
export DOCICH_REPLY_RESEARCH_ENABLED=1
export DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED=1
export DOCICH_RADIO_SCRIPT_DIRECT_ENABLED=1
export DOCICH_RADIO_RESEARCH_ROUTING_ENABLED=1

# Real comment Web path canary: Cloudflare Web Search -> bounded WebBroker fetch
# -> verified receipt/coverage. No OpenCode or final reply model is involved.
if ! PYTHONPATH="$docich_root/src" python3 - <<'PY'
import os
from docich.comment_classifier import web_batch

turns = [{"role": "user", "content": "Cloudflare Workers Web Search の現在公開されている公式仕様を確認する"}]
result = web_batch.research(
    turns,
    "web",
    env=dict(os.environ),
    timeout_sec=30.0,
    comment_scopes=("web",),
)
if not result.ok or result.covered_questions != (1,) or not result.sources:
    raise SystemExit(1)
PY
then
  exit 74
fi

# Real RADIO canary: existing production JEV route -> Cloudflare Web Search /
# verified body -> fixed direct Cloudflare model -> native parser/guard.
request_file="$(mktemp /tmp/docich-radio-rollout-request.XXXXXX)"
result_file="$(mktemp /tmp/docich-radio-rollout-result.XXXXXX)"
backup_file=""
cleanup_files() {
  rm -f "$request_file" "$result_file" 2>/dev/null || true
  [[ -z "$backup_file" ]] || rm -f "$backup_file" 2>/dev/null || true
}
trap cleanup_files EXIT
python3 - "$canary_agent" >"$request_file" <<'PY'
import json, sys
agent = sys.argv[1]
topic = "Cloudflare Workers Web Search の現在公開されている公式仕様と制限を外部資料で確認して紹介する"
json.dump({"topic": topic, "queries": [topic], "agents": agent}, sys.stdout, ensure_ascii=False)
PY
if ! PYTHONPATH="$docich_root/src" python3 -P -m docich.radio.consumer <"$request_file" >"$result_file"; then
  exit 75
fi
if ! python3 - "$result_file" <<'PY'
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, ValueError):
    raise SystemExit(1)
if data.get("status") not in {"ok", "partial"} or data.get("scope") != "web":
    raise SystemExit(1)
body = data.get("body")
summary = data.get("summary")
if not isinstance(body, str) or len(body) < 100 or not isinstance(summary, str) or not summary:
    raise SystemExit(1)
PY
then
  exit 75
fi

umask 077
backup_file="$(mktemp "$soren_root/.env.docich-rollout-backup.XXXXXX")"
cp -p -- "$env_file" "$backup_file"

rollback_needed=1
rollback() {
  status=$?
  if [[ "$rollback_needed" -eq 1 && -n "$backup_file" && -f "$backup_file" ]]; then
    cp -p -- "$backup_file" "$env_file" 2>/dev/null || true
    "$docich_root/ops/vm_actions/restart_radio_worker.sh" >/dev/null 2>&1 || true
    "$docich_root/ops/vm_actions/restart_chat_kick_workers.sh" >/dev/null 2>&1 || true
  fi
  cleanup_files
  exit "$status"
}
trap rollback EXIT

# Atomically replace only this rollout's marked block; all pre-existing owner
# configuration, credentials and comments stay byte-for-byte unchanged.
if ! python3 - "$env_file" "$begin_marker" "$end_marker" <<'PY'
import os, pathlib, stat, sys, tempfile
path = pathlib.Path(sys.argv[1])
begin, end = sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
if text.count(begin) != text.count(end) or text.count(begin) > 1:
    raise SystemExit(1)
if begin in text:
    start = text.index(begin)
    finish = text.index(end, start) + len(end)
    prefix = text[:start].rstrip("\n")
    suffix = text[finish:].lstrip("\n")
    text = prefix + ("\n" if prefix else "") + (suffix + ("\n" if suffix and not suffix.endswith("\n") else ""))
block = "\n".join([
    begin,
    "DOCICH_REPLY_WEB_SEARCH_ENABLED=1",
    "DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare",
    "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_PROVIDER=ceramic",
    "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_GATEWAY_ID=default",
    "DOCICH_REPLY_WEB_DIRECT_ENABLED=1",
    "DOCICH_REPLY_RESEARCH_ENABLED=1",
    "DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED=1",
    "DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=1",
    "DOCICH_RADIO_NATIVE_CORNERS=ai_knowledge",
    "DOCICH_RADIO_NATIVE_ROOT=/home/ubuntu/docich",
    "DOCICH_RADIO_NATIVE_AGENTS=cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8",
    "DOCICH_RADIO_SCRIPT_DIRECT_ENABLED=1",
    "DOCICH_RADIO_RESEARCH_ROUTING_ENABLED=1",
    end,
]) + "\n"
base = text.rstrip("\n")
new_text = (base + "\n\n" if base else "") + block
mode = stat.S_IMODE(path.stat().st_mode)
fd, tmp_name = tempfile.mkstemp(prefix=".env.docich-rollout.", dir=str(path.parent), text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp_name, mode)
    os.replace(tmp_name, path)
finally:
    try:
        os.unlink(tmp_name)
    except FileNotFoundError:
        pass
PY
then
  exit 76
fi

# Full replacement is required because these long-lived bash workers source
# .env only at startup. The helpers fail closed unless the supervisor produces
# a new reviewed PID.
"$docich_root/ops/vm_actions/restart_radio_worker.sh" || exit 77
"$docich_root/ops/vm_actions/restart_chat_kick_workers.sh" || exit 78

worker_pid() {
  local name="$1" file="$soren_root/tmp/state/${name}.pid" pid="" cmd=""
  [[ -r "$file" ]] || return 1
  pid="$(tr -d '[:space:]' <"$file")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmd="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmd" == *"workers/${name}.sh"* ]] || return 1
  printf '%s\n' "$pid"
}

verify_env() {
  local name="$1"; shift
  if [[ -e "$soren_root/tmp/state/${name}.paused" ]]; then
    return 0
  fi
  local pid env_text expected
  pid="$(worker_pid "$name")" || return 1
  env_text="$(tr '\0' '\n' <"/proc/$pid/environ")"
  for expected in "$@"; do
    grep -Fxq -- "$expected" <<<"$env_text" || return 1
  done
}

common=(
  "DOCICH_REPLY_WEB_SEARCH_ENABLED=1"
  "DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare"
  "DOCICH_REPLY_WEB_DIRECT_ENABLED=1"
  "DOCICH_REPLY_RESEARCH_ENABLED=1"
)
verify_env radio_worker "${common[@]}" \
  "DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=1" \
  "DOCICH_RADIO_NATIVE_CORNERS=ai_knowledge" \
  "DOCICH_RADIO_NATIVE_ROOT=/home/ubuntu/docich" \
  "DOCICH_RADIO_NATIVE_AGENTS=$canary_agent" \
  "DOCICH_RADIO_SCRIPT_DIRECT_ENABLED=1" \
  "DOCICH_RADIO_RESEARCH_ROUTING_ENABLED=1" || exit 79
for name in chat_worker kick_worker; do
  verify_env "$name" "${common[@]}" \
    "DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED=1" \
    "DOCICH_REPLY_ROUTING_ENABLED=1" \
    "COMMENT_CLASSIFIER_BACKEND=jev" || exit 79
done

rollback_needed=0
trap - EXIT
cleanup_files
printf 'verified_web_rollout_enabled sha=%s radio_corner=ai_knowledge\n' "$expected_sha"
