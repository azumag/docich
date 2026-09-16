#!/usr/bin/env bash
set -euo pipefail
umask 077

runtime=/home/ubuntu/soren/soren91
persist=/home/ubuntu/soren-persist
runner="$runtime/daily_runtime_improve.mjs"
state="$runtime/tmp/state/improve_daily.json"
lock="$persist/.git/persist.lock"
reviewed_dir="$(pwd -P)/ops/vm_actions"
classifier="$reviewed_dir/classify_soren91_opencode_nonzero.py"
capture_shim="$reviewed_dir/soren91_opencode_capture_shim.sh"
fixed_exec_shim="$reviewed_dir/soren91_opencode_fixed_exec.sh"

[[ "$(id -un)" == "ubuntu" ]] || {
  echo 'soren91 daily improvement must run as ubuntu' >&2
  exit 77
}

export HOME=/home/ubuntu
export PATH=/usr/local/bin:/usr/bin:/bin:/snap/bin
export LANG=C.UTF-8
export AI_COMMON_AGENTS=opencode-go:deepseek-v4.1-flash
export SOREN91_IMPROVE_OPENCODE_AGENT=opencode-go:deepseek-v4.1-flash
export SOREN91_TEXT_OPENCODE_TIMEOUT=240
export SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT=240
export SOREN91_TEXT_OPENCODE_PERMISSION='{"*":"deny"}'
export OPENCODE_DISABLE_CLAUDE_CODE=true
export OPENCODE_DISABLE_PROJECT_CONFIG=true
export OPENCODE_CONFIG_CONTENT='{"permission":{"*":"deny"},"agent":{"soren-daily-improve":{"mode":"primary","permission":{"*":"deny"},"steps":1}},"share":"disabled","instructions":[]}'
export SOREN91_OPENCODE_NONZERO_CLASSIFIER="$classifier"

[[ -d "$runtime" && -f "$runtime/strategy.mjs" ]] || {
  echo 'soren91 runtime is missing' >&2
  exit 78
}
[[ -f "$runner" ]] || {
  echo 'reviewed daily runtime runner is missing' >&2
  exit 79
}
[[ -d "$persist/.git" ]] || {
  echo 'managed persist clone is missing' >&2
  exit 80
}
command -v node >/dev/null 2>&1 || {
  echo 'node is missing' >&2
  exit 81
}
command -v gh >/dev/null 2>&1 || {
  echo 'gh is missing' >&2
  exit 82
}
command -v flock >/dev/null 2>&1 || {
  echo 'flock is missing' >&2
  exit 83
}
command -v python3 >/dev/null 2>&1 || {
  echo 'python3 is missing' >&2
  exit 84
}
[[ -x /snap/bin/opencode ]] || {
  echo 'opencode is missing' >&2
  exit 87
}
[[ -f "$classifier" && ! -L "$classifier" ]] || {
  echo 'reviewed classifier is missing' >&2
  exit 88
}
[[ -f "$capture_shim" && ! -L "$capture_shim" && -f "$fixed_exec_shim" && ! -L "$fixed_exec_shim" ]] || {
  echo 'reviewed opencode shim is missing' >&2
  exit 89
}

exec 9>"$lock"
flock -w 30 9 || {
  echo 'soren91 persist clone is busy' >&2
  exit 75
}

python3 - "$runtime" "$state" <<'PY'
import json
import os
import re
import sys
import tempfile

runtime, state = sys.argv[1], sys.argv[2]

def invalid_state():
    raise SystemExit(85)

try:
    if os.path.lexists(state):
        try:
            with open(state, encoding='utf-8') as f:
                doc = json.load(f)
        except Exception:
            invalid_state()
        if not isinstance(doc, dict):
            invalid_state()
        value = doc.get('lastConsumedGame')
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            invalid_state()
        pending = doc.get('pendingPr')
        if pending is not None and not isinstance(pending, dict):
            invalid_state()
        raise SystemExit(0)

    summaries = os.path.join(runtime, 'tmp', 'summaries')
    try:
        names = os.listdir(summaries)
    except FileNotFoundError:
        raise SystemExit(0)
    games = []
    for name in names:
        match = re.fullmatch(r'game_(\d+)\.json', name)
        if match:
            games.append(int(match.group(1)))
    if not games:
        raise SystemExit(0)

    baseline = max(0, min(games) - 1)
    state_dir = os.path.dirname(state)
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.improve_daily.', dir=state_dir, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'lastConsumedGame': baseline, 'pendingPr': None}, f, separators=(',', ':'))
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, state)
        os.chmod(state, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
except SystemExit:
    raise
except Exception:
    raise SystemExit(86)
PY

opencode_shim_dir="$(mktemp -d /home/ubuntu/.soren91-opencode-shim.XXXXXX)"
/usr/bin/install -m 700 -- "$capture_shim" "$opencode_shim_dir/opencode"
/usr/bin/install -m 700 -- "$fixed_exec_shim" "$opencode_shim_dir/opencode-fixed-exec"
export PATH="$opencode_shim_dir:/usr/local/bin:/usr/bin:/bin:/snap/bin"

out="$(mktemp /home/ubuntu/.soren91-daily.XXXXXX)"
smoke_out="$(mktemp /home/ubuntu/.soren91-opencode-smoke.XXXXXX)"
cleanup() {
  rm -f "$out" "$smoke_out"
  rm -rf "$opencode_shim_dir"
}
trap cleanup EXIT INT TERM HUP

run_daily() {
  local mode="$1"
  case "$mode" in
    preflight)
      node "$runner" \
        --runtime-dir "$runtime" \
        --repo-dir "$persist" \
        --state "$state" \
        --dry-run >"$out" 2>&1
      ;;
    run)
      node "$runner" \
        --runtime-dir "$runtime" \
        --repo-dir "$persist" \
        --state "$state" >"$out" 2>&1
      ;;
    *) return 125 ;;
  esac
}

classify_private_output() {
  local fallback="$1"
  failure_rc="$fallback"
  if grep -Fq 'candidate_invalid:no decide() function found' "$out"; then failure_rc=130; return; fi
  if grep -Fq 'candidate_invalid:decide is not exported as a function' "$out"; then failure_rc=131; return; fi
  if grep -Fq 'candidate_invalid:decide() returned invalid format' "$out"; then failure_rc=132; return; fi
  if grep -Fq 'candidate_invalid:decide() returned x=' "$out"; then failure_rc=133; return; fi
  if grep -Fq 'candidate_invalid:Strategy contract: behavior no-op;' "$out"; then failure_rc=142; return; fi
  if grep -Fq 'candidate_invalid:Strategy contract: behavior replay failed' "$out"; then failure_rc=143; return; fi
  if grep -Eq 'candidate_invalid:Strategy contract: (retained-match )?behavior replay coverage was insufficient' "$out"; then failure_rc=144; return; fi
  if grep -Fq 'candidate_invalid:Strategy contract: source search consistency;' "$out"; then failure_rc=145; return; fi
  if grep -Fq 'candidate_invalid:Strategy contract: source unpositioned piece;' "$out"; then failure_rc=146; return; fi
  if grep -Fq 'candidate_invalid:Strategy contract:' "$out"; then failure_rc=134; return; fi
  if grep -Fq 'candidate_invalid:Undefined variable detected:' "$out"; then failure_rc=135; return; fi
  if grep -Eiq 'candidate_invalid:Code error:.*(Unexpected end|unterminated|string constant|template literal|unterminated comment)' "$out"; then failure_rc=137; return; fi
  if grep -Eiq 'candidate_invalid:Code error:.*(already been declared|duplicate export|duplicate declaration)' "$out"; then failure_rc=138; return; fi
  if grep -Eiq 'candidate_invalid:Code error:.*(Cannot find package|Cannot find module|module not found|ERR_MODULE_NOT_FOUND)' "$out"; then failure_rc=139; return; fi
  if grep -Eiq 'candidate_invalid:Code error:.*(is not defined|before initialization|cannot access .* before initialization)' "$out"; then failure_rc=140; return; fi
  if grep -Eiq 'candidate_invalid:Code error:.*(Unexpected token|invalid or unexpected token|SyntaxError|missing \)|missing \]|missing \}|illegal return|await is only valid|reserved word)' "$out"; then failure_rc=141; return; fi
  if grep -Fq 'candidate_invalid:Code error:' "$out"; then failure_rc=136; return; fi
  if grep -Fq 'candidate_invalid:' "$out"; then failure_rc=94; return; fi
  if grep -Fq 'model_no_candidate' "$out"; then failure_rc=93; return; fi
  if grep -Fq 'evidence_blocked:' "$out"; then failure_rc=92; return; fi
  if grep -Eq 'focus_evidence_missing:|path_escape|unsafe_evidence_root:|evidence_symlink:|unexpected_evidence_directory:|non_regular_evidence:|evidence_file_limit:|evidence_file_too_large:|history_too_large:|evidence_total_limit:' "$out"; then failure_rc=95; return; fi
  if grep -Eq 'runtime_not_current:|runtime_compat_missing:' "$out"; then failure_rc=91; return; fi
  if grep -Eq 'persist_repo_(tracked_dirty|missing)' "$out"; then failure_rc=90; return; fi
  if grep -Eq 'pending_pr_lookup_failed|unexpected_pr_state:|pr_number_parse_failed|git_diff_failed|gh .* failed' "$out"; then failure_rc=96; return; fi
  if grep -Fq 'strategy_changed_during_analysis' "$out"; then failure_rc=97; return; fi
  if grep -Eq '^\[soren91_daily_runtime\] git -C .* failed rc=' "$out"; then failure_rc=90; return; fi
  if grep -Fq 'opencode provider failure (' "$out"; then failure_rc=100; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=error_event' "$out"; then failure_rc=123; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=error_part' "$out"; then failure_rc=124; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=tool_event' "$out"; then failure_rc=125; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=unexpected_event' "$out"; then failure_rc=126; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=invalid_json' "$out"; then failure_rc=127; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=structured_nonzero' "$out"; then failure_rc=128; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=no_json' "$out"; then failure_rc=129; return; fi
  if grep -Fq 'soren91_opencode_signal=TERM' "$out"; then failure_rc=120; return; fi
  if grep -Eiq 'permission.{0,40}(denied|reject|blocked)|((tool|bash|read|glob|grep|list|webfetch|websearch).{0,40}(denied|reject|blocked))|denied.{0,40}permission|not allowed.{0,40}(tool|permission)|tool call.{0,40}(denied|reject)|PermissionDenied' "$out"; then failure_rc=114; return; fi
  if grep -Eiq 'context.{0,60}(length|window|limit|too (large|long)|exceed)|input.{0,60}(too (large|long)|limit|exceed)|prompt.{0,60}(too (large|long)|limit|exceed)|maximum context|max(imum)? input|token limit exceeded' "$out"; then failure_rc=115; return; fi
  if grep -Eiq 'max(imum)? output|output.{0,60}(too (large|long)|limit|exceed)|max_tokens|max tokens|finish_reason.{0,30}length' "$out"; then failure_rc=116; return; fi
  if grep -Eiq 'bad request|invalid request|HTTP[^0-9]*400|status[^0-9]*400|unprocessable|HTTP[^0-9]*422|status[^0-9]*422' "$out"; then failure_rc=117; return; fi
  if grep -Eiq 'content policy|safety policy|policy violation|moderation|unsafe|sensitive content' "$out"; then failure_rc=118; return; fi
  if grep -Eiq 'maxBuffer|ERR_CHILD_PROCESS_STDIO_MAXBUFFER|stdout maxBuffer length exceeded|stderr maxBuffer length exceeded' "$out"; then failure_rc=119; return; fi
  if grep -Eiq 'timed out|ETIMEDOUT|ERR_CHILD_PROCESS_TIMEOUT|signal=SIGTERM|SIGKILL' "$out"; then failure_rc=120; return; fi
  if grep -Eiq 'ECONNRESET|ENOTFOUND|EAI_AGAIN|ECONNREFUSED|socket hang up|network error|connection.{0,30}(reset|refused)|fetch failed|DNS' "$out"; then failure_rc=121; return; fi
  if grep -Eiq 'HTTP[^0-9]*5[0-9][0-9]|status[^0-9]*5[0-9][0-9]|internal server error|service unavailable|bad gateway|gateway timeout|overloaded' "$out"; then failure_rc=122; return; fi
  if grep -Eq 'model returned empty text|opencode returned no strategy code|opencode returned no text' "$out"; then failure_rc=101; return; fi
  if grep -Eq 'opencode returned invalid JSON event|opencode returned unexpected event type:|opencode returned invalid text part|opencode returned invalid text event' "$out"; then failure_rc=105; return; fi
  if grep -Eq 'opencode returned error event|opencode returned error part|opencode returned tool/error event' "$out"; then failure_rc=106; return; fi
  if grep -Eq 'opencode model failed \([^)]*\): Command failed: (script|opencode)|opencode model failed \([^)]*\): spawn (script|opencode) ' "$out"; then failure_rc=102; return; fi
  if grep -Fq 'opencode model failed (' "$out"; then failure_rc=103; return; fi
  if grep -Eq 'spawn (claude|gemini) ENOENT|claude error: code=ENOENT|gemini.*ENOENT' "$out"; then failure_rc=104; return; fi
}

classify_smoke_failure() {
  local smoke_rc="$1"
  if [[ "$smoke_rc" -eq 124 || "$smoke_rc" -eq 137 ]]; then return 113; fi
  if grep -Eiq 'ProviderModelNotFoundError|Model not found:|unknown model|model .* not found' "$smoke_out"; then return 107; fi
  if grep -Eiq 'agent not found|unknown agent' "$smoke_out"; then return 108; fi
  if grep -Eiq 'Configuration is invalid|invalid config|config.*invalid|Invalid input' "$smoke_out"; then return 109; fi
  if grep -Eiq 'not logged in|authentication|unauthorized|invalid.*token|api key|credential' "$smoke_out"; then return 110; fi
  if grep -Eiq 'rate.?limit|too many requests|429|quota|usage limit|payment required|insufficient.*credit|subscription' "$smoke_out"; then return 111; fi
  return 112
}

set +e
run_daily preflight
preflight_rc=$?
set -e
if [[ "$preflight_rc" -ne 0 ]]; then
  classify_private_output 98
  exit "$failure_rc"
fi

set +e
printf '%s\n' 'Return exactly OK.' | \
  OPENCODE_PERMISSION='{"*":"deny"}' \
  /usr/bin/timeout --kill-after=5s 20s \
  "$opencode_shim_dir/opencode" run --format json --model opencode-go/deepseek-v4.1-flash \
  >"$smoke_out" 2>&1
smoke_rc=$?
set -e
if [[ "$smoke_rc" -ne 0 ]]; then
  set +e
  classify_smoke_failure "$smoke_rc"
  smoke_category=$?
  set -e
  exit "$smoke_category"
fi

: >"$out"
set +e
run_daily run
rc=$?
set -e
if [[ "$rc" -eq 0 ]]; then
  exit 0
fi
classify_private_output 99
exit "$failure_rc"
