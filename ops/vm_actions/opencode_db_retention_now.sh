#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only production helper for an immediate OpenCode DB retention pass.
# The actual mutation contract lives in the deployed, reviewed soviet_now
# lib/opencode_db_retention.sh: shared producer flock, exclusive retention flock,
# transactional prune, then byte-preserving zero-block reclaim. This helper invokes
# contract on the two fixed production DB paths.
#
# No restart, sudo, arbitrary path, prompt, model output, or credential data is
# touched. Output is retained only in the VM-private exec log by the gateway.

root="/home/ubuntu/soren"
default_db="/home/ubuntu/.local/share/opencode/opencode.db"
worker_db="$root/tmp/state/xdg_data/opencode/opencode.db"
retention_days=1
case "${1:-}" in
  "") ;; # One-shot recovery after this reviewed epoch.
  --scheduled) retention_days=1 ;;
  *) exit 2 ;;
esac
export OPENCODE_ROTATION_GATE_WAIT_SEC=600
# Use a private tmpfs copy only under disk pressure and with >=4 GiB RAM reserved.
export OPENCODE_RETENTION_MEMORY_COMPACTION=1
# Approved sparse recovery avoids an unfittable compact-image writeback WAL.
# The reviewed Python helper holds SQLite EXCLUSIVE throughout and verifies
# unchanged bytes; the existing cutoff, gate, reserve and deadline remain.
export OPENCODE_RETENTION_SPARSE_RECLAIM=1
export OPENCODE_RETENTION_DEADLINE_EPOCH=$(( $(date +%s) + 780 ))
export OPENCODE_RETENTION_STATE_DIR="$root/tmp/state"
env_file="$root/.env"

[[ -d "$root" ]] || { echo "soren root missing" >&2; exit 2; }
[[ -f "$root/lib/opencode_db_retention.sh" && ! -L "$root/lib/opencode_db_retention.sh" ]] || {
  echo "reviewed retention shell helper missing" >&2
  exit 2
}
[[ -f "$root/lib/opencode_db_retention.py" && ! -L "$root/lib/opencode_db_retention.py" ]] || {
  echo "reviewed retention python helper missing" >&2
  exit 2
}

# Respect the production emergency opt-out without sourcing .env (which can
# contain secrets and shell syntax). Read only this one boolean and emit only
# 0/1. Invalid values fail closed instead of silently forcing retention on.
if [[ -f "$env_file" ]]; then
  opt="$(
    python3 - "$env_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
seen = ""
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != "OPENCODE_DEFAULT_DB_RETENTION_ENABLED":
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    if value not in {"0", "1"}:
        raise SystemExit(3)
    seen = value
print(seen)
PY
  )"
  if [[ "$opt" == "0" ]]; then
    export OPENCODE_DEFAULT_DB_RETENTION_ENABLED=0
  elif [[ "$opt" == "1" ]]; then
    export OPENCODE_DEFAULT_DB_RETENTION_ENABLED=1
  fi
fi

export HOME="/home/ubuntu"
export ELOOP_LIB_DIR="$root"
cd "$root"

# shellcheck disable=SC1091
source "$root/lib/opencode_db_retention.sh"
command -v _opencode_db_retention_rotate >/dev/null 2>&1 || {
  echo "retention function unavailable" >&2
  exit 2
}

# Stage 1: opencode.db retention. Result is kept separate from stage 2 so a
# session-file failure cannot mask a DB pass (or the reverse).
db_status=0
if _opencode_db_retention_rotate "$retention_days" "$worker_db" "$default_db"; then
  db_status=0
else
  db_status=$?
fi

# Stage 2: opencode session-file retention, dry-run only in this epoch.
# The reviewed helper (azumag/docich#3140 / ADR 0006) plans age-based removals
# for snapshot/ and storage/session_diff/ under the same exclusive rotation
# gate. This epoch passes no apply switch: nothing is deleted and both apply
# modes stay behind the owner gate. The 0600 state file lives under
# tmp/state (outside the opencode store) and warms the two-pass continuity
# check; it is only a plan record, never session content.
docich_root="/home/ubuntu/docich"
session_helper="$docich_root/ops/vm_actions/opencode_session_file_reclaim.py"
session_data="/home/ubuntu/.local/share/opencode"
session_gate="$root/tmp/state/.opencode_rotation_gate.lock"
session_state="$root/tmp/state/opencode_session_file_reclaim_state.json"
session_manifest="$root/tmp/state/opencode_session_file_reclaim_manifest.jsonl"
session_status_file="$root/tmp/state/opencode_session_file_retention.json"

session_status=0
if [[ -f "$session_helper" && ! -L "$session_helper" && -d "$session_data" ]]; then
  if python3 "$session_helper" "$session_data" "$session_gate" \
      --state-file "$session_state" --manifest-file "$session_manifest" \
      --status-file "$session_status_file" \
      --batch 50 --cutoff-hours 24 --auto-apply \
      --gate-wait-sec 300; then
    session_status=0
  else
    session_status=$?
    echo "opencode session-file dry-run status=$session_status" >&2
  fi
else
  session_status=2
  echo "opencode session-file helper or data dir unavailable" >&2
fi

# Report the DB result first so a deferred (75) DB pass keeps its meaning.
if [[ "$db_status" -ne 0 ]]; then
  exit "$db_status"
fi
exit "$session_status"
