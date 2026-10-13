#!/usr/bin/env bash
set -euo pipefail

# Roll PAPER AI back to legacy research/generation for future invocations.
# It removes only the fixed capability file and never stops an active corner.

[[ $# -eq 0 ]] || exit 64
readonly root="/home/ubuntu/docich"
readonly expected_sha="${EXPECTED_SHA:-}"
readonly target="$HOME/.config/docich/paper-ai.env"

[[ "$(pwd -P)" == "$root" ]] || exit 65
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || exit 66
head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
[[ "$head" == "$expected_sha" ]] || exit 67
[[ -z "$(git -C "$root" status --porcelain --untracked-files=no --ignore-submodules=all 2>/dev/null)" ]] || exit 68

if [[ -e "$target" || -L "$target" ]]; then
  [[ -f "$target" && ! -L "$target" ]] || exit 69
  rm -f "$target"
fi
printf 'paper_ai_disabled sha=%s\n' "$expected_sha"
