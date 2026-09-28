#!/usr/bin/env bash
# Fixed, reviewed owner-only actions. Never receives a command or a URL.
# The VM gateway withholds stdout/stderr; only bounded exit categories are public.
set -euo pipefail
set +x
operation="${1:-}"
case "$operation" in status|prepare|activate|rollback) ;; *) exit 64 ;; esac
[[ $# == 1 ]] || exit 64
root=/home/ubuntu/docich
soren=/home/ubuntu/soren
cd "$root"
export PYTHONPATH="$root/src"
export HOME=/home/ubuntu
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
[[ -d "$XDG_RUNTIME_DIR" && -O "$XDG_RUNTIME_DIR" ]] || exit 65
if [[ "$operation" == prepare ]]; then
  # Explicit prepare only: normal deployment never installs packages/restarts a stream.
  if [[ ! -e "$root/.venv-twica" ]]; then
    python3 -m venv "$root/.venv-twica"
  fi
  [[ -d "$root/.venv-twica" && ! -L "$root/.venv-twica" && -O "$root/.venv-twica" ]] || exit 65
  "$root/.venv-twica/bin/python" -m pip install --disable-pip-version-check -r requirements-twica-overlay.txt
  # OS libraries must already be provisioned; no root/system package installation.
  "$root/.venv-twica/bin/python" -m playwright install chromium
  exec python3 -m docich.twica_setup --soren-root "$soren"
fi
set -a
# Existing operator-owned runtime settings, never echoed.
# shellcheck disable=SC1090
. "$soren/.env"
set +a
if [[ "$operation" == status ]]; then
  python3 - <<'PY'
from pathlib import Path
from docich.twica_config import load_common_config
from docich.twica_state import diagnostics
c=load_common_config(Path('/home/ubuntu/soren'))
d=diagnostics(c.state)
if d['owner']=='common' and d['renderer_active'] and d['renderer_fresh'] and d['compositor_running'] and d['compositor_fresh'] and d['frame_state']=='fresh':
    raise SystemExit(0)
if d['owner']=='legacy': raise SystemExit(10)
if d['owner'] in {'blocked','draining'}: raise SystemExit(12)
raise SystemExit(11)
PY
else
  exec python3 -m docich.twica_control "$operation" --soren-root "$soren"
fi
