#!/usr/bin/env bash
set -euo pipefail

# Emergency-only reclaim for the three /tmp git worktrees identified in #2735.
# Dry-run by default. Deletion requires all guards below to pass.
apply="${APPLY:-0}"
sys_tmp="/tmp"
min_idle_minutes=720

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sys-tmp) sys_tmp="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ "$apply" == 0 || "$apply" == 1 ]] || { echo "invalid APPLY" >&2; exit 2; }

targets=(
  "$sys_tmp/soviet-2489-fix"
  "$sys_tmp/persist-recon"
  "$sys_tmp/wt1029"
)

target_refs() {
  local path="$1" ps_out line link probe found=0
  ps_out="$(ps -eo args= 2>/dev/null)" || return 1
  while IFS= read -r line; do
    [[ "$line" == *storage_reclaim_critical_tmp* ]] && continue
    [[ "$line" == *"$path"* ]] && found=$((found + 1))
  done <<< "$ps_out"

  if [[ -d /proc ]]; then
    for probe in /proc/[0-9]*/cwd; do
      [[ -e "$probe" ]] || continue
      link="$(readlink -f "$probe" 2>/dev/null || true)"
      [[ "$link" == "$path" || "$link" == "$path"/* ]] && found=$((found + 1))
    done
    for probe in /proc/[0-9]*/fd/*; do
      [[ -e "$probe" ]] || continue
      link="$(readlink -f "$probe" 2>/dev/null || true)"
      [[ "$link" == "$path" || "$link" == "$path"/* ]] && found=$((found + 1))
    done
  fi
  printf '%s\n' "$found"
}

for path in "${targets[@]}"; do
  [[ -e "$path" ]] || { printf 'SKIP %s (absent)\n' "$path"; continue; }
  [[ -d "$path" ]] || { printf 'SKIP %s (not directory)\n' "$path"; continue; }

  if ! recent="$(find "$path" -type f -mmin -"$min_idle_minutes" -print -quit 2>/dev/null)"; then
    printf 'SKIP %s (mtime scan failed)\n' "$path"
    continue
  fi
  if [[ -n "$recent" ]]; then
    printf 'KEEP %s (file modified within %sm)\n' "$path" "$min_idle_minutes"
    continue
  fi

  if ! inside="$(git -C "$path" rev-parse --is-inside-work-tree 2>/dev/null)" || [[ "$inside" != true ]]; then
    printf 'SKIP %s (not a git worktree)\n' "$path"
    continue
  fi
  if ! dirty="$(git --no-optional-locks -C "$path" status --porcelain 2>/dev/null)"; then
    printf 'SKIP %s (git status failed)\n' "$path"
    continue
  fi
  if [[ -n "$dirty" ]]; then
    printf 'KEEP %s (uncommitted changes present)\n' "$path"
    continue
  fi

  if ! refs="$(target_refs "$path")"; then
    printf 'SKIP %s (reference scan failed)\n' "$path"
    continue
  fi
  if [[ "$refs" != 0 ]]; then
    printf 'KEEP %s (referenced by %s running process entries)\n' "$path" "$refs"
    continue
  fi

  kb="$(du -skx "$path" 2>/dev/null | awk 'NR==1{print $1}')"
  [[ "$kb" =~ ^[0-9]+$ ]] || kb=0
  printf 'DEL %s bytes %s\n' "$((kb * 1024))" "$path"
  if [[ "$apply" == 1 ]]; then
    rm -rf -- "$path"
  fi
done
