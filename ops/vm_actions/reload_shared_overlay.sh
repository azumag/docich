#!/usr/bin/env bash
set -euo pipefail
root=/home/ubuntu/soren
set -a
# shellcheck disable=SC1091
. "$root/.env"
set +a
if [[ "${SOREN_STREAM_BACKEND:-obs}" != ffmpeg || "${SOREN_DIRECT_STAGE_LAYOUT:-dashboard}" != dashboard ]]; then
  exit 0
fi
python3 /home/ubuntu/docich/ops/vm_actions/reload_shared_overlay.py
