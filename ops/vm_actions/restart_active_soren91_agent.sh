#!/usr/bin/env bash
set -euo pipefail
[[ "$PWD" == "/home/ubuntu/docich" ]]
env_file="/home/ubuntu/soren/soren91-macos-agent.env"
[[ -f "$env_file" && ! -L "$env_file" ]]
set -a
# This fixed production file is the same EnvironmentFile used by the Soren91
# systemd unit; do not print its values.
. "$env_file"
set +a
exec python3 ops/vm_actions/restart_active_soren91_agent.py
