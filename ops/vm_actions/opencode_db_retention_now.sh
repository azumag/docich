#!/usr/bin/env bash
set -euo pipefail

# Fixed owner-only production helper for an immediate OpenCode DB retention pass.
# The actual mutation contract lives in the deployed, reviewed soviet_now
# lib/opencode_db_retention.sh: shared producer flock, exclusive retention flock,
# transactional prune, then VACUUM. This helper only invokes that existing
# contract on the two fixed production DB paths.
#
# No restart, sudo, arbitrary path, prompt, model output, or credential data is
# touched. Output is retained only in the VM-private exec log by the gateway.

root="/home/ubuntu/soren"
default_db="/home/ubuntu/.local/share/opencode/opencode.db"
worker_db="$root/tmp/state/xdg_data/opencode/opencode.db"
retention_days=1
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

_opencode_db_retention_rotate "$retention_days" "$worker_db" "$default_db"
