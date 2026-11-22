#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded storage reclaim helper for the production VM.
# Dry-run by default; APPLY=1 comes only from the owner-only control plane.
# Fixed allowlists only: live paths (browser profile, state, debug,
# live streaming encoder) are never removed. All targets are age-gated;
# clones and verify entries also need fail-closed process-reference checks
# (any doubt = KEEP/SKIP).
# Docker: dangling images / build cache older than 7d only (no tagged/volumes).
# External inputs: APPLY, VOICEVOX_ARCHIVE, AIVIS_ENGINE. SIZE: gateway exec
# caps stdin at 16384 bytes incl. the 3-line preamble; wire test holds 16000.
#
# Test-only flags: --root, --min-age-days, --verify-min-age-days,
#   --voicevox-root, --include-voicevox-archive, --skip-system,
#   --snap-cache-root, --stale-clone "PATH|ORIGIN", --home-root, --sys-tmp,
#   --aivis-root, --include-aivis-engine

apply="${APPLY:-0}"
root="/home/ubuntu/soren"
min_age_days=21
verify_min_age_days=3
voicevox_root="/opt/voicevox"
include_voicevox="${VOICEVOX_ARCHIVE:-0}"
skip_system=0
# snapd cache (root 0700); only in the system section or via --snap-cache-root.
snap_cache_root="/var/lib/snapd/cache"
snap_cache_explicit=0
# Fixed "path|expected-origin" allowlist; --stale-clone (tests only) replaces it.
stale_clones=(
  "/tmp/opencode/docich-sync|github.com/azumag/docich"
  "/home/ubuntu/soren-src|github.com/azumag/soviet_now"
)
# Fixed one-off leftovers (HOME + /tmp): absolute allowlist, 7d gate + refs.
# --home-root/--sys-tmp are test-only so CI/dev never evaluates real /tmp.
home_root="/home/ubuntu"
sys_tmp="/tmp"
aivis_root="/home/ubuntu/.local/share/AivisSpeech-Engine"
# AIVIS_ENGINE arrives via the stdin preamble; --include-aivis-engine for tests.
include_aivis="${AIVIS_ENGINE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) root="$2"; shift 2 ;;
    --min-age-days) min_age_days="$2"; shift 2 ;;
    --verify-min-age-days) verify_min_age_days="$2"; shift 2 ;;
    --voicevox-root) voicevox_root="$2"; shift 2 ;;
    --include-voicevox-archive) include_voicevox=1; shift ;;
    --skip-system) skip_system=1; shift ;;
    --snap-cache-root) snap_cache_root="$2"; snap_cache_explicit=1; shift 2 ;;
    --stale-clone) stale_clones=("$2"); shift 2 ;;
    --home-root) home_root="$2"; shift 2 ;;
    --sys-tmp) sys_tmp="$2"; shift 2 ;;
    --aivis-root) aivis_root="$2"; shift 2 ;;
    --include-aivis-engine) include_aivis=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ "$apply" == 0 || "$apply" == 1 ]] || { echo "invalid APPLY" >&2; exit 2; }
[[ "$include_voicevox" == 0 || "$include_voicevox" == 1 ]] || { echo "invalid VOICEVOX_ARCHIVE" >&2; exit 2; }
[[ "$include_aivis" == 0 || "$include_aivis" == 1 ]] || { echo "invalid AIVIS_ENGINE" >&2; exit 2; }
[[ "$min_age_days" =~ ^[0-9]+$ ]] || { echo "invalid min-age-days" >&2; exit 2; }
[[ "$verify_min_age_days" =~ ^[0-9]+$ ]] || { echo "invalid verify-min-age-days" >&2; exit 2; }
[[ -d "$root" ]] || { echo "root not found: $root" >&2; exit 2; }

# Unused second TTS engine: opt-in like VOICEVOX, use-site guarded.

# Absolute allowlist, 7d gate + refs. build/ (live encoder) and soren-persist
# (referenced by strategy/persist.sh) are deliberately absent.
stale_paths=(
  "$home_root/2026-08-11 05-35-22.mkv"
  "$home_root/soren91-r97"
  "$home_root/docich-soren91"
  "$home_root/soren91-corner-verify"
  "$home_root/docich-paper-strategy-315"
  "$home_root/soren-phase1-cc1a1e362.tar"
  "$home_root/soren-phase1-pre-cc1a1e362.tgz"
  "$sys_tmp/s91test"
  "$sys_tmp/soren91-phase1-rx.ts"
  "$sys_tmp/issue303_srt_recv.ts"
  "$sys_tmp/issue303_final_recv.ts"
  "$sys_tmp/issue303_final3_recv.ts"
  "$sys_tmp/issue303_early_recv.ts"
  "$sys_tmp/issue303_audio_recv.ts"
  "$sys_tmp/soviet-2489-fix"
  "$sys_tmp/persist-recon"
  "$sys_tmp/wt1029"
)
stale_min_age_days=7

# $root/tmp entries safe to reclaim past min_age_days (glob allowlist; the age
# gate still decides per entry, including dated manual_challenge_* backups).
stale_patterns=(direct_av_sync direct_stream_benchmark "manual_challenge*" game-lifecycle-e2e "soviet-iso-verify-*" deploy radio_quarantine)
# Live / protected paths that must never be reclaimed.
protected=(soviet_local_chromium_profile state debug)

now=$(date +%s)
total=0

say() { printf '%s\n' "$*"; }

bytes_of() {
  # 1K-block size; sudo fallback for root-owned targets.
  local kb
  kb="$(du -skx "$1" 2>/dev/null | awk 'NR==1{print $1}')"
  if ! [[ "$kb" =~ ^[0-9]+$ ]]; then
    kb="$(sudo -n du -skx "$1" 2>/dev/null | awk 'NR==1{print $1}')" || kb=""
  fi
  [[ "$kb" =~ ^[0-9]+$ ]] || { printf '0\n'; return 0; }
  printf '%s\n' "$(( kb * 1024 ))"
}

mtime_of() {
  # GNU stat, BSD/macOS, then sudo for root-owned paths.
  local m
  if m="$(stat -c %Y "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$m"
    return 0
  fi
  if m="$(stat -f %m "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$m"
    return 0
  fi
  if m="$(sudo -n stat -c %Y "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$m"
    return 0
  fi
  return 1
}

is_older_than() {
  # True when PATH's mtime is more than DAYS old.
  local path="$1" days="$2" mtime
  mtime="$(mtime_of "$path")" || return 1
  (( now - mtime > days * 86400 ))
}

is_old() {
  is_older_than "$1" "$min_age_days"
}

remove_path() {
  local path="$1" bytes parent
  # Missing = skip; sudo probes root-owned trees ubuntu cannot stat.
  [[ -e "$path" ]] || sudo -n test -e "$path" 2>/dev/null || return 0
  bytes="$(bytes_of "$path")"; bytes="${bytes:-0}"
  printf 'DEL  %12s B  %s\n' "$bytes" "$path"
  total=$(( total + bytes ))
  if [[ "$apply" == 1 ]]; then
    parent="$(dirname "$path")"
    if [[ -w "$parent" ]]; then
      rm -rf -- "$path"
    else
      # Root-owned target (e.g. snapd cache): escalate explicitly. If sudo is
      # unavailable the script fails loudly instead of silently skipping.
      sudo -n rm -rf -- "$path"
    fi
  fi
}

run_cmd() {
  printf 'CMD  %s\n' "$*"
  if [[ "$apply" == 1 ]]; then
    "$@"
  fi
}

stale_clone_refs() {
  # Count processes whose args/cwd reference PATH (excludes this script).
  # Non-zero return = scan failed; caller skips (fail-closed).
  local path="$1" ps_out line cwd found=0
  if ! ps_out="$(ps -eo args= 2>/dev/null)"; then
    return 1
  fi
  while IFS= read -r line; do
    [[ "$line" == *storage_reclaim* ]] && continue
    if [[ "$line" == *"$path"* ]]; then found=$(( found + 1 )); fi
  done <<< "$ps_out"
  # Probe /proc directly (Linux); the for list expands the per-pid globs.
  if [[ -d /proc ]]; then
    for cwd in /proc/[0-9]*/cwd; do
      [[ -L "$cwd" ]] || continue
      # Other-uid cwd: retry via passwordless sudo. A cwd still present but
      # unresolvable is a scan failure, never refs=0 (fail-closed).
      line="$(readlink -f "$cwd" 2>/dev/null || sudo -n readlink -f "$cwd" 2>/dev/null || true)"
      if [[ -z "$line" ]]; then
        [[ -L "$cwd" ]] && return 1
        continue
      fi
      if [[ "$line" == "$path" || "$line" == "$path"/* ]]; then found=$(( found + 1 )); fi
    done
  fi
  printf '%s\n' "$found"
}

say "=== storage_reclaim mode=$([[ "$apply" == 1 ]] && echo apply || echo dry-run) root=$root min_age_days=$min_age_days verify_min_age_days=$verify_min_age_days ==="

# 1. Stale tmp entries (allowlist + age gate).
tmp="$root/tmp"
for pat in "${stale_patterns[@]}"; do
  for path in "$tmp"/$pat; do
    [[ -e "$path" ]] || continue
    base="$(basename "$path")"
    for pr in "${protected[@]}"; do
      [[ "$base" == "$pr" ]] && continue 2
    done
    if is_old "$path"; then
      remove_path "$path"
    else
      say "KEEP $path (recent)"
    fi
  done
done

# 2. Deploy backups: old entries only; keep the dir.
deploy_backups="$tmp/deploy-backups"
if [[ -d "$deploy_backups" ]]; then
  for path in "$deploy_backups"/*; do
    [[ -e "$path" ]] || continue
    is_old "$path" || continue
    remove_path "$path"
  done
fi

# 2b. Verify sweep: age gate + deep freshness + fail-closed refs. Linked
#     worktrees go through git, never a bare rm -rf.
verify_dir="$tmp/verify"
if [[ -d "$verify_dir" ]]; then
  for path in "$verify_dir"/*; do
    [[ -e "$path" || -L "$path" ]] || continue
    # A symlink is only a link: keep it, never follow it.
    [[ -L "$path" ]] && { say "KEEP $path (symlink; not followed)"; continue; }
    is_older_than "$path" "$verify_min_age_days" || { say "KEEP $path (recent)"; continue; }
    found="$(find "$path" -mindepth 1 -mtime -"$verify_min_age_days" 2>/dev/null)" || {
      say "SKIP $path (mtime scan failed)"; continue; }
    [[ -z "$found" ]] || { say "KEEP $path (entries modified within ${verify_min_age_days}d)"; continue; }
    refs="$(stale_clone_refs "$path")" || { say "SKIP $path (reference check failed)"; continue; }
    [[ "$refs" == 0 ]] || { say "KEEP $path (referenced by $refs running process(es))"; continue; }
    if [[ ! -f "$path/.git" ]]; then remove_path "$path"; continue; fi
    # Linked worktree: unregister + prune; any failure keeps the entry.
    common="$(git --no-optional-locks -C "$path" rev-parse --git-common-dir 2>/dev/null)" || {
      say "KEEP $path (worktree metadata unreadable)"; continue; }
    bytes="$(bytes_of "$path")"; bytes="${bytes:-0}"
    if [[ "$apply" == 1 ]]; then
      git --git-dir="$common" worktree remove -- "$path" 2>/dev/null || {
        say "KEEP $path (git worktree remove failed)"; continue; }
      git --git-dir="$common" worktree prune 2>/dev/null || true
    fi
    printf 'DEL  %12s B  %s\n' "$bytes" "$path"
    total=$(( total + bytes ))
  done
fi

# 3. Stale clones (path|origin): age, identity, refs. Freshness runs BEFORE
#    any git call (index.lock would bump .git mtime). Failures = KEEP/SKIP.
tmp_clone_min_age_days=7

for clone_spec in "${stale_clones[@]}"; do
  clone="${clone_spec%%|*}"
  expected_origin="${clone_spec#*|}"
  if [[ "$clone" == "$expected_origin" || -z "$expected_origin" ]]; then
    say "SKIP clone spec without origin: $clone_spec"
    continue
  fi
  [[ -e "$clone" ]] || { say "SKIP $clone (absent)"; continue; }
  if [[ ! -d "$clone" || ! -d "$clone/.git" ]]; then
    say "SKIP $clone (not a git working tree)"
    continue
  fi
  # Freshness BEFORE git: dir/.git/objects mtimes, then files.
  clone_fresh=0
  for probe in "$clone" "$clone/.git" "$clone/.git/objects"; do
    [[ -e "$probe" ]] || continue
    if ! is_older_than "$probe" "$tmp_clone_min_age_days"; then
      clone_fresh=1
    fi
  done
  if [[ "$clone_fresh" == 1 ]]; then
    say "KEEP $clone (touched within ${tmp_clone_min_age_days}d)"
    continue
  fi
  if ! clone_recent="$(find "$clone" -type f -mtime -"$tmp_clone_min_age_days" -print 2>/dev/null)"; then
    say "SKIP $clone (mtime scan failed)"
    continue
  fi
  if [[ -n "$clone_recent" ]]; then
    say "KEEP $clone (files modified within ${tmp_clone_min_age_days}d)"
    continue
  fi
  origin="$(git -C "$clone" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    *"$expected_origin"*) ;;
    *) say "SKIP $clone (origin does not match $expected_origin)"; continue ;;
  esac
  # Dirty = still in use. --no-optional-locks avoids index rewrites.
  if [[ -n "$(git --no-optional-locks -C "$clone" status --porcelain 2>/dev/null || true)" ]]; then
    say "KEEP $clone (uncommitted changes present)"
    continue
  fi
  if ! refs="$(stale_clone_refs "$clone")"; then
    say "SKIP $clone (reference check failed)"
    continue
  fi
  if [[ "$refs" != 0 ]]; then
    say "KEEP $clone (referenced by $refs running process(es))"
    continue
  fi
  remove_path "$clone"
done

# 3b. Fixed leftovers: 7d gate + the same fail-closed reference check.
for path in "${stale_paths[@]}"; do
  [[ -e "$path" ]] || { say "SKIP $path (absent)"; continue; }
  if ! is_older_than "$path" "$stale_min_age_days"; then
    say "KEEP $path (touched within ${stale_min_age_days}d)"
    continue
  fi
  if ! refs="$(stale_clone_refs "$path")"; then
    say "SKIP $path (reference check failed)"
    continue
  fi
  if [[ "$refs" != 0 ]]; then
    say "KEEP $path (referenced by $refs running process(es))"
    continue
  fi
  remove_path "$path"
done

# 4. snapd cache: age-gated blobs only, never the snaps (root 0700 -> sudo).
snap_cache_min_age_days=7
if [[ "$skip_system" == 0 || "$snap_cache_explicit" == 1 ]]; then
  if [[ -d "$snap_cache_root" ]]; then
    if [[ -r "$snap_cache_root" && -x "$snap_cache_root" ]]; then
      # Portable maxdepth-1 listing (BSD find).
      snap_list=""
      for blob in "$snap_cache_root"/* "$snap_cache_root"/.[!.]* "$snap_cache_root"/..?*; do
        [[ -e "$blob" ]] || continue
        snap_list+="$blob"$'\n'
      done
    else
      if ! snap_list="$(sudo -n find "$snap_cache_root" -maxdepth 1 -type f -print 2>/dev/null)"; then
        snap_list=""
        say "SKIP snap cache listing (sudo unavailable): $snap_cache_root"
      fi
    fi
    while IFS= read -r blob; do
      [[ -n "$blob" ]] || continue
      if is_older_than "$blob" "$snap_cache_min_age_days"; then
        remove_path "$blob"
      else
        say "KEEP $blob (not older than ${snap_cache_min_age_days}d)"
      fi
    done <<< "$snap_list"
  fi
fi

# 5. VOICEVOX archive: opt-in, only while the extracted engine exists.
if [[ "$include_voicevox" == 1 ]]; then
  archive="$voicevox_root/voicevox.7z.001"
  if [[ -f "$archive" && -x "$voicevox_root/current/run" ]]; then
    remove_path "$archive"
  else
    say "SKIP voicevox archive (archive or extracted engine missing)"
  fi
fi

# 5b. AivisSpeech-Engine: opt-in, only while VOICEVOX (the active TTS) works.
if [[ "$include_aivis" == 1 ]]; then
  if [[ -d "$aivis_root" && -x "$voicevox_root/current/run" ]]; then
    remove_path "$aivis_root"
  else
    say "SKIP aivis engine (engine missing or voicevox not intact)"
  fi
fi

# 6. Fixed system maintenance + docker reclaim.
if [[ "$skip_system" == 0 ]]; then
  run_cmd sudo -n apt-get clean
  run_cmd sudo -n journalctl --vacuum-size=200M
  if command -v snap >/dev/null 2>&1; then
    while IFS= read -r line; do
      [[ "$line" == *disabled* ]] || continue
      name="$(awk '{print $1}' <<<"$line")"
      rev="$(awk '{print $3}' <<<"$line")"
      [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ && "$rev" =~ ^[0-9]+$ ]] || continue
      run_cmd sudo -n snap remove "$name" --revision="$rev"
    done < <(snap list --all 2>/dev/null | tail -n +2 || true)
  fi

  # Docker: dangling images / build cache older than 168h only.
  docker_image_max_age="168h"
  docker_builder_max_age="168h"
  if command -v docker >/dev/null 2>&1; then
    if sudo -n docker info >/dev/null 2>&1; then
      run_cmd sudo -n docker image prune -f --filter "until=${docker_image_max_age}"
      run_cmd sudo -n docker builder prune -f --filter "until=${docker_builder_max_age}"
    else
      say "SKIP docker reclaim (docker/sudo unavailable)"
    fi
  else
    say "SKIP docker reclaim (docker not installed)"
  fi

  # Rotate soren logs with copytruncate; opencode.log gets its own stanza.
  # tool-output stays out: opencode reads it live (#389).
  logrotate_conf='/etc/logrotate.d/soren'
  read -r -d '' conf <<'CONF' || true
/home/ubuntu/soren/logs/*.log {
  su root root
  weekly
  rotate 4
  size 100M
  missingok
  notifempty
  compress
  delaycompress
  copytruncate
}
/home/ubuntu/.local/share/opencode/log/opencode.log {
  rotate 3
  size 50M
  missingok
  notifempty
  compress
  delaycompress
  copytruncate
}
CONF
  printf 'CONF would install %s\n' "$logrotate_conf"
  if [[ "$apply" == 1 ]]; then
    printf '%s\n' "$conf" | sudo -n tee "$logrotate_conf" >/dev/null
    sudo -n logrotate -f "$logrotate_conf"
  fi
fi

say "=== planned reclaim: $total bytes ($(( total / 1048576 )) MiB) ==="
