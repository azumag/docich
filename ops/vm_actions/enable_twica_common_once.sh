#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
  echo "unexpected arguments" >&2
  exit 2
fi

cd /home/ubuntu/docich
exec python3 ops/vm_actions/twica_common.py enable \
  --confirm-production \
  --confirm-stream-restart \
  --confirm-idle
