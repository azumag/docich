#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 0 ]] || exit 64
[[ "$(pwd -P)" == /home/ubuntu/docich ]] || exit 65
exec python3 ops/vm_actions/recover_soren_round.py
