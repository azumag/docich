#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
  echo "unexpected arguments" >&2
  exit 2
fi

cd /home/ubuntu/docich
exec python3 ops/vm_actions/enable_twica_common_once.py
