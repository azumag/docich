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

exec 9>"$lock"
flock -w 30 9 || {
  echo 'soren91 persist clone is busy' >&2
  exit 75
}

# First-run bootstrap: the runtime retains only a bounded recent window.  If
# improve_daily.json has never existed, insisting on game #1 would falsely
# classify a healthy retained window (for example #421..#433) as a gap.  Set
# the initial cursor to one before the earliest *retained* summary, so every
# available contiguous game is still consumed and no retained evidence is
# skipped.  Once the state exists it is authoritative; malformed state fails
# closed instead of being silently reset.
python3 - "$runtime" "$state" <<'PY'
import json
import os
import re
import sys
import tempfile

runtime, state = sys.argv[1], sys.argv[2]
if os.path.lexists(state):
    try:
        with open(state, encoding='utf-8') as f:
            doc = json.load(f)
    except Exception as exc:
        raise SystemExit(f'invalid existing improve_daily state: {type(exc).__name__}')
    if not isinstance(doc, dict):
        raise SystemExit('invalid existing improve_daily state: not object')
    value = doc.get('lastConsumedGame')
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemExit('invalid existing improve_daily state: lastConsumedGame')
    pending = doc.get('pendingPr')
    if pending is not None and not isinstance(pending, dict):
        raise SystemExit('invalid existing improve_daily state: pendingPr')
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
PY

# Keep detailed model/runtime output private on the VM. The owner workflow only
# receives a fixed exit category, never raw match logs, prompts, screenshots or
# provider text. This makes failed daily runs diagnosable without weakening the
# gateway's stdout/stderr boundary.
out="$(mktemp /home/ubuntu/.soren91-daily.XXXXXX)"
cleanup() { rm -f "$out"; }
trap cleanup EXIT INT TERM HUP

set +e
node "$runner" \
  --runtime-dir "$runtime" \
  --repo-dir "$persist" \
  --state "$state" >"$out" 2>&1
rc=$?
set -e

if [[ "$rc" -eq 0 ]]; then
  exit 0
fi

# Stable failure categories. Do not print the matched line itself.
if grep -Fq 'candidate_invalid:' "$out"; then exit 94; fi
if grep -Fq 'model_no_candidate' "$out"; then exit 93; fi
if grep -Fq 'evidence_blocked:' "$out"; then exit 92; fi
if grep -Eq 'runtime_not_current:|runtime_compat_missing:' "$out"; then exit 91; fi
if grep -Eq 'persist_repo_(tracked_dirty|missing)' "$out"; then exit 90; fi
if grep -Eq 'pending_pr_lookup_failed|pr_number_parse_failed|gh .* failed' "$out"; then exit 96; fi
exit 1
