#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$#" -eq 5 ]] || exit 64
[[ "$1" == "run" ]] || exit 64
[[ "$2" == "--format" && "$3" == "json" ]] || exit 64
[[ "$4" == "--model" ]] || exit 64
[[ "$5" =~ ^[A-Za-z0-9_./:-]{1,160}$ ]] || exit 64
exec /snap/bin/opencode run --format json --agent soren-daily-improve --model "$5"
