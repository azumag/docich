#!/usr/bin/env bash
set -euo pipefail
[[ "$PWD" == "/home/ubuntu/docich" ]]
env_file="/home/ubuntu/soren/soren91-macos-agent.env"
[[ -f "$env_file" && ! -L "$env_file" ]]
set -a
. "$env_file"
set +a
exec python3 ops/vm_actions/start_soren91_manual_probe.py
