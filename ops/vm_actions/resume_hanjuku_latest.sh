#!/usr/bin/env bash
set -euo pipefail
if (( $# != 0 )); then
  printf '%s\n' 'resume-hanjuku-latest accepts no arguments' >&2
  exit 64
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
root=/home/ubuntu/docich
python="$root/.venv-trading/bin/python3"
[[ -x "$python" ]] || python=/usr/bin/python3
cd / # Never import untracked cwd packages into the owner operation.
"$python" -I -c 'import sys; sys.path.insert(0,"/home/ubuntu/docich/src"); from docich.hanjuku_corner import main; raise SystemExit(main(["--resume-latest"]))' >/dev/null
printf '%s\n' 'queued one Hanjuku latest-checkpoint resumption'
