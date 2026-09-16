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
command -v opencode >/dev/null 2>&1 || {
  echo 'opencode is missing' >&2
  exit 87
}
command -v script >/dev/null 2>&1 || {
  echo 'script is missing' >&2
  exit 88
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

# Keep detailed model/runtime output private on the VM. The owner workflow only
# receives a fixed exit category, never raw match logs, prompts, screenshots or
# provider text. This makes failed daily runs diagnosable without weakening the
# gateway's stdout/stderr boundary.
out="$(mktemp /home/ubuntu/.soren91-daily.XXXXXX)"
cleanup() { rm -f "$out"; }
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
  # Prefer the underlying OpenCode failure over the later missing legacy CLI.
  if grep -Fq 'opencode provider failure (' "$out"; then failure_rc=100; return; fi
  if grep -Eq 'model returned empty text|opencode returned no strategy code' "$out"; then failure_rc=101; return; fi
  if grep -Eq 'opencode model failed \([^)]*\): Command failed: script|opencode model failed \([^)]*\): spawn script ' "$out"; then failure_rc=102; return; fi
  if grep -Fq 'opencode model failed (' "$out"; then failure_rc=103; return; fi
  if grep -Eq 'spawn (claude|gemini) ENOENT|claude error: code=ENOENT|gemini.*ENOENT' "$out"; then failure_rc=104; return; fi
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
