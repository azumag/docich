#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 0 ]] || exit 2
[[ "$(pwd -P)" == /home/ubuntu/docich ]] || exit 3
exec python3 ops/vm_actions/release_soren_loop_pause.py
