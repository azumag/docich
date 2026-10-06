#!/usr/bin/env bash
set -euo pipefail

# Fixed helper for PAPER improvement children started through systemd-run.
# It reads only the owner-managed PAPER AI capability file, then execs the
# already-reviewed argv supplied by PaperCornerManager. Secret values never
# appear in systemd-run argv or unit properties.

[[ "${1:-}" == "--" ]] || exit 64
shift
[[ "$#" -gt 0 ]] || exit 64

env_file="${HOME}/.config/docich/paper-ai.env"
if [[ -e "$env_file" || -L "$env_file" ]]; then
  [[ -f "$env_file" && ! -L "$env_file" ]] || exit 65
  # shellcheck disable=SC1090
  set -a
  . "$env_file"
  set +a
fi

exec "$@"
