#!/usr/bin/env bash
set -euo pipefail
umask 077

# Production entry point for the external Soren91 daily improvement loop.
# The live player tree is evidence-only: the Node runner copies a strict,
# bounded allowlist into a private tmp directory and writes strategy candidates
# only in /home/ubuntu/soren-persist before opening a PR.
#
# Serialize with soviet_now/strategy/persist.sh, which uses the same flock.
# This prevents the shared managed clone from being checked out by two PR
# producers at once.

runtime=/home/ubuntu/soren/soren91
persist=/home/ubuntu/soren-persist
runner="$runtime/daily_runtime_improve.mjs"
state="$runtime/tmp/state/improve_daily.json"
lock="$persist/.git/persist.lock"

# install_vm_gateway.sh installs the forced-command key for the named SSH user
# (production default: ubuntu). Numeric UIDs are host-specific and must not be
# treated as part of the security contract.
[[ "$(id -un)" == "ubuntu" ]] || {
  echo 'soren91 daily improvement must run as ubuntu' >&2
  exit 77
}

# gateway.py deliberately supplies a minimal PATH. OpenCode is installed from
# snap on this host, so add only its fixed system path; keep HOME explicit so
# gh/opencode use the ubuntu-owned credentials/state rather than caller input.
export HOME=/home/ubuntu
export PATH=/usr/local/bin:/usr/bin:/bin:/snap/bin
export LANG=C.UTF-8

# The default text chain starts with two free opencode models that were already
# measured hanging for ~45 s each on this VM. Daily improvement is a bounded
# offline job, so use the known working opencode-go coding model first for both
# the optional PNG attachment attempt and the text fallback. This is an
# execution choice only; the reviewed strategy/evidence/validation contracts
# stay unchanged.
export AI_COMMON_AGENTS=opencode-go:deepseek-v4.1-flash
export SOREN91_IMPROVE_OPENCODE_AGENT=opencode-go:deepseek-v4.1-flash
export SOREN91_IMPROVE_OPENCODE_TIMEOUT=90

# Match the exact configuration pattern already proven by docich self-repair:
# deny every permission, use one explicitly named primary one-step text agent,
# disable sharing and project instructions. text_ai.mjs also sets the
# OPENCODE_PERMISSION env var for each model attempt, so force its daily-only
# source to the same deny-all policy; otherwise its broader comment defaults
# can override the intended no-tool daily execution boundary.
export SOREN91_TEXT_OPENCODE_PERMISSION='{"*":"deny"}'
export OPENCODE_DISABLE_CLAUDE_CODE=true
export OPENCODE_DISABLE_PROJECT_CONFIG=true
export OPENCODE_CONFIG_CONTENT='{"permission":{"*":"deny"},"agent":{"soren-daily-improve":{"mode":"primary","permission":{"*":"deny"},"steps":1}},"share":"disabled","instructions":[]}'

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

exec 9>"$lock"
flock -w 30 9 || {
  echo 'soren91 persist clone is busy' >&2
  exit 75
}

# First-run bootstrap: the runtime retains only a bounded recent window. If
# improve_daily.json has never existed, insisting on game #1 would falsely
# classify a healthy retained window (for example #421..#433) as a gap. Set
# the initial cursor to one before the earliest retained summary. Existing
# malformed state gets a fixed exit code; its contents are never emitted.
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

# Upstream text_ai.mjs intentionally invokes the generic executable name
# `opencode`. For the production daily job only, resolve that name through a
# private reviewed shim which accepts exactly the argv shape emitted by the
# direct JSON transport and inserts the explicit known-good text-only agent.
# It cannot execute arbitrary operations, flags, models with shell metacharacters,
# or caller-controlled commands. On a non-zero OpenCode exit the shim inspects
# stdout JSON privately and appends only one fixed diagnostic marker to stderr;
# raw stdout/stderr still remain inside the VM-side private runner log.
opencode_shim_dir="$(mktemp -d /home/ubuntu/.soren91-opencode-shim.XXXXXX)"
cat >"$opencode_shim_dir/opencode" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$#" -eq 5 ]] || exit 64
[[ "$1" == "run" ]] || exit 64
[[ "$2" == "--format" && "$3" == "json" ]] || exit 64
[[ "$4" == "--model" ]] || exit 64
[[ "$5" =~ ^[A-Za-z0-9_./:-]{1,160}$ ]] || exit 64

child_out="$(mktemp /home/ubuntu/.soren91-opencode-child-out.XXXXXX)"
child_err="$(mktemp /home/ubuntu/.soren91-opencode-child-err.XXXXXX)"
cleanup_child() { rm -f "$child_out" "$child_err"; }
trap cleanup_child EXIT INT TERM HUP

set +e
/snap/bin/opencode run --format json --agent soren-daily-improve --model "$5" >"$child_out" 2>"$child_err"
rc=$?
set -e

cat "$child_out"
cat "$child_err" >&2

if [[ "$rc" -ne 0 ]]; then
  category="$(python3 - "$child_out" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines(2 * 1024 * 1024 + 1)
except Exception:
    print('no_json')
    raise SystemExit(0)

saw = False
for line in lines:
    if not line.strip():
        continue
    try:
        event = json.loads(line)
    except Exception:
        print('invalid_json')
        raise SystemExit(0)
    if not isinstance(event, dict) or event.get('error'):
        print('error_event')
        raise SystemExit(0)
    saw = True
    event_type = event.get('type')
    if event_type not in ('step_start', 'step_finish', 'text'):
        print('unexpected_event')
        raise SystemExit(0)
    part = event.get('part') or {}
    if not isinstance(part, dict) or part.get('error'):
        print('error_part')
        raise SystemExit(0)
    if part.get('reason') in ('tool-calls', 'tool_calls', 'error') or any(
        key in part for key in ('tool', 'toolCallID', 'tool_calls')
    ):
        print('tool_event')
        raise SystemExit(0)
print('structured_nonzero' if saw else 'no_json')
PY
)"
  case "$category" in
    error_event|error_part|tool_event|unexpected_event|invalid_json|structured_nonzero|no_json) ;;
    *) category=structured_nonzero ;;
  esac
  printf 'soren91_opencode_nonzero_json=%s\n' "$category" >&2
fi
exit "$rc"
SH
chmod 700 "$opencode_shim_dir/opencode"
export PATH="$opencode_shim_dir:/usr/local/bin:/usr/bin:/bin:/snap/bin"

# Keep detailed model/runtime output private on the VM. The owner workflow only
# receives a fixed exit category, never raw match logs, prompts, screenshots or
# provider text. This makes failed daily runs diagnosable without weakening the
# gateway's stdout/stderr boundary.
out="$(mktemp /home/ubuntu/.soren91-daily.XXXXXX)"
smoke_out="$(mktemp /home/ubuntu/.soren91-opencode-smoke.XXXXXX)"
cleanup() {
  rm -f "$out" "$smoke_out"
  rm -rf "$opencode_shim_dir"
}
trap cleanup EXIT INT TERM HUP

# Do not forward caller-controlled argv through this production wrapper. Only
# these two reviewed invocations are permitted.
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
    *)
      return 125
      ;;
  esac
}

classify_private_output() {
  local fallback="$1"
  failure_rc="$fallback"
  if grep -Fq 'candidate_invalid:' "$out"; then failure_rc=94; return; fi
  if grep -Fq 'model_no_candidate' "$out"; then failure_rc=93; return; fi
  if grep -Fq 'evidence_blocked:' "$out"; then failure_rc=92; return; fi
  if grep -Eq 'focus_evidence_missing:|path_escape|unsafe_evidence_root:|evidence_symlink:|unexpected_evidence_directory:|non_regular_evidence:|evidence_file_limit:|evidence_file_too_large:|history_too_large:|evidence_total_limit:' "$out"; then failure_rc=95; return; fi
  if grep -Eq 'runtime_not_current:|runtime_compat_missing:' "$out"; then failure_rc=91; return; fi
  if grep -Eq 'persist_repo_(tracked_dirty|missing)' "$out"; then failure_rc=90; return; fi
  if grep -Eq 'pending_pr_lookup_failed|unexpected_pr_state:|pr_number_parse_failed|git_diff_failed|gh .* failed' "$out"; then failure_rc=96; return; fi
  if grep -Fq 'strategy_changed_during_analysis' "$out"; then failure_rc=97; return; fi
  if grep -Eq '^\[soren91_daily_runtime\] git -C .* failed rc=' "$out"; then failure_rc=90; return; fi

  # Model diagnostics remain private. Match only stable, non-secret reason
  # fragments and turn them into fixed exit categories for the owner workflow.
  # Prefer a specific full-prompt cause over the generic child-process error.
  if grep -Fq 'opencode provider failure (' "$out"; then failure_rc=100; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=error_event' "$out"; then failure_rc=123; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=error_part' "$out"; then failure_rc=124; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=tool_event' "$out"; then failure_rc=125; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=unexpected_event' "$out"; then failure_rc=126; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=invalid_json' "$out"; then failure_rc=127; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=structured_nonzero' "$out"; then failure_rc=128; return; fi
  if grep -Fq 'soren91_opencode_nonzero_json=no_json' "$out"; then failure_rc=129; return; fi
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

# Preflight uses the reviewed runner's --dry-run path. It exercises persist
# checkout, runtime compatibility, pending-PR reconciliation, bounded evidence
# copy, and evidence continuity without calling a model or opening a PR.
set +e
run_daily preflight
preflight_rc=$?
set -e
if [[ "$preflight_rc" -ne 0 ]]; then
  classify_private_output 98
  exit "$failure_rc"
fi

# A fixed, bounded model smoke distinguishes OpenCode/model/config/auth startup
# failures from failures that only occur with the full retained-evidence prompt.
# Mirror the exact deny-all OPENCODE_PERMISSION used by the Node text path so
# this smoke also covers the per-attempt permission merge. Output stays private.
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

# The real run gets a fresh private log so a successful preflight can never
# influence the failure classifier below.
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