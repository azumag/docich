#!/usr/bin/env bash
set -euo pipefail

# Reviewed, bounded storage reclaim helper for the production VM.
#
# Safety properties:
#   - Dry-run by default. Changes happen only when APPLY=1 (set by the
#     owner-only control plane: .github/workflows/vm-operations.yml,
#     operation=reclaim, apply=true).
#   - Only a fixed allowlist of paths and commands is touched.
#   - Live runtime paths (browser profile, state, debug) are never removed.
#   - Stale /tmp clones and the snapd download cache are age-gated (7 days)
#     and only removed after identity (git origin) / reference (ps args,
#     /proc cwd) checks pass; any doubt skips the target.
#   - Docker pruning is limited to dangling images and build cache older than
#     7 days: tagged images, containers, and volumes are never removed (the
#     PAPER sandbox contract runs without volumes).
#   - No argument from the network is interpolated into a path; the only
#     external inputs are the APPLY and VOICEVOX_ARCHIVE flags.
#
# Optional flags exist so repository tests can run against a temporary root:
#   --root DIR, --min-age-days N, --voicevox-root DIR,
#   --include-voicevox-archive, --skip-system,
#   --snap-cache-root DIR, --stale-clone DIR

apply="${APPLY:-0}"
root="/home/ubuntu/soren"
min_age_days=21
voicevox_root="/opt/voicevox"
include_voicevox="${VOICEVOX_ARCHIVE:-0}"
skip_system=0
# snapd download cache: fixed system path (root 0700 on production), part of
# the privileged system section unless a test provides --snap-cache-root.
snap_cache_root="/var/lib/snapd/cache"
snap_cache_explicit=0
# Stale /tmp clones: fixed allowlist. --stale-clone replaces the list for
# repository tests only; the control plane never passes it, so production
# always evaluates this default.
stale_clones=("/tmp/opencode/docich-sync")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) root="$2"; shift 2 ;;
    --min-age-days) min_age_days="$2"; shift 2 ;;
    --voicevox-root) voicevox_root="$2"; shift 2 ;;
    --include-voicevox-archive) include_voicevox=1; shift ;;
    --skip-system) skip_system=1; shift ;;
    --snap-cache-root) snap_cache_root="$2"; snap_cache_explicit=1; shift 2 ;;
    --stale-clone) stale_clones=("$2"); shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ "$apply" == 0 || "$apply" == 1 ]] || { echo "invalid APPLY" >&2; exit 2; }
[[ "$include_voicevox" == 0 || "$include_voicevox" == 1 ]] || { echo "invalid VOICEVOX_ARCHIVE" >&2; exit 2; }
[[ "$min_age_days" =~ ^[0-9]+$ ]] || { echo "invalid min-age-days" >&2; exit 2; }
[[ -d "$root" ]] || { echo "root not found: $root" >&2; exit 2; }

# Directories under $root/tmp that are safe to reclaim when older than min_age_days.
# manual_challenge* also matches dated sibling backups (e.g.
# manual_challenge_20260824_meriken); the age gate still decides per entry.
stale_patterns=(direct_av_sync direct_stream_benchmark "manual_challenge*" game-lifecycle-e2e "soviet-iso-verify-*")
# Explicitly live / protected paths that must never be reclaimed.
protected=(soviet_local_chromium_profile state debug)

now=$(date +%s)
total=0

say() { printf '%s\n' "$*"; }

bytes_of() {
  # 1K-block size, works with both GNU and BSD du. Root-owned targets (snapd
  # cache) fall back to passwordless sudo so size reporting never claims 0.
  local kb
  kb="$(du -skx "$1" 2>/dev/null | awk 'NR==1{print $1}')"
  if ! [[ "$kb" =~ ^[0-9]+$ ]]; then
    kb="$(sudo -n du -skx "$1" 2>/dev/null | awk 'NR==1{print $1}')" || kb=""
  fi
  [[ "$kb" =~ ^[0-9]+$ ]] || { printf '0\n'; return 0; }
  printf '%s\n' "$(( kb * 1024 ))"
}

mtime_of() {
  # GNU stat first, then BSD/macOS, then passwordless sudo for root-owned
  # paths (snapd cache is 0700 root on production).
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
  # is_older_than PATH DAYS — true when PATH's mtime is more than DAYS old.
  local path="$1" days="$2" mtime
  mtime="$(mtime_of "$path")" || return 1
  (( now - mtime > days * 86400 ))
}

is_old() {
  is_older_than "$1" "$min_age_days"
}

remove_path() {
  local path="$1" bytes parent
  # Local existence first, then passwordless sudo for root-owned trees where
  # the ubuntu user cannot even stat the entry. Missing target = skip.
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

say "=== storage_reclaim mode=$([[ "$apply" == 1 ]] && echo apply || echo dry-run) root=$root min_age_days=$min_age_days ==="

# 1. Stale verification artifacts under soren/tmp (allowlist + age gate).
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

# 2. Per-deploy backups: remove only entries older than the age gate, keep the dir.
deploy_backups="$tmp/deploy-backups"
if [[ -d "$deploy_backups" ]]; then
  for path in "$deploy_backups"/*; do
    [[ -e "$path" ]] || continue
    is_old "$path" || continue
    remove_path "$path"
  done
fi

# 3. Stale one-off clones under /tmp: fixed allowlist + age gate + identity and
#    reference checks. Any doubt skips (fail-safe), and dry-run only prints.
tmp_clone_min_age_days=7
stale_clone_refs() {
  # Print the number of running processes whose args or cwd reference PATH.
  # Excludes this script itself (invoked as storage_reclaim.sh on stdin/argv).
  # Returns non-zero when the args listing fails, so the caller can skip
  # instead of treating an unverified scan as "no references" (fail-closed).
  local path="$1" ps_out line cwd found=0
  if ! ps_out="$(ps -eo args= 2>/dev/null)"; then
    return 1
  fi
  while IFS= read -r line; do
    [[ "$line" == *storage_reclaim* ]] && continue
    if [[ "$line" == *"$path"* ]]; then found=$(( found + 1 )); fi
  done <<< "$ps_out"
  # /proc exists on Linux (production) only; [[ ]] does not glob, so probe the
  # mount point itself and let the for-loop glob expand outside [[ ]].
  if [[ -d /proc ]]; then
    for cwd in /proc/[0-9]*/cwd; do
      [[ -e "$cwd" ]] || continue
      line="$(readlink -f "$cwd" 2>/dev/null || true)"
      [[ -n "$line" ]] || continue
      if [[ "$line" == "$path" || "$line" == "$path"/* ]]; then found=$(( found + 1 )); fi
    done
  fi
  printf '%s\n' "$found"
}

for clone in "${stale_clones[@]}"; do
  [[ -e "$clone" ]] || { say "SKIP $clone (absent)"; continue; }
  if [[ ! -d "$clone" || ! -d "$clone/.git" ]]; then
    say "SKIP $clone (not a git working tree)"
    continue
  fi
  origin="$(git -C "$clone" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    https://github.com/azumag/docich|https://github.com/azumag/docich.git|git@github.com:azumag/docich.git) ;;
    *) say "SKIP $clone (origin is not azumag/docich)"; continue ;;
  esac
  # Freshness: top dir, .git, and .git/objects mtimes (fetch/gc touch these),
  # then any file inside modified within the gate. Order is cheap checks first;
  # every failure mode falls through to KEEP/SKIP (fail-safe).
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

# 4. snapd download cache: age-gated blobs only, never the snaps themselves.
#    Root-owned (0700) on production, so listing uses passwordless sudo and
#    per-file stat/-e fall back to sudo inside the helpers above; the tests
#    point --snap-cache-root at a temp dir where no escalation is needed.
snap_cache_min_age_days=7
if [[ "$skip_system" == 0 || "$snap_cache_explicit" == 1 ]]; then
  if [[ -d "$snap_cache_root" ]]; then
    if [[ -r "$snap_cache_root" && -x "$snap_cache_root" ]]; then
      # Portable maxdepth-1 listing (BSD find has no -maxdepth).
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

# 5. VOICEVOX installer archive: opt-in, and only when the extracted engine exists.
if [[ "$include_voicevox" == 1 ]]; then
  archive="$voicevox_root/voicevox.7z.001"
  if [[ -f "$archive" && -x "$voicevox_root/current/run" ]]; then
    remove_path "$archive"
  else
    say "SKIP voicevox archive (archive or extracted engine missing)"
  fi
fi

# 6. Fixed system maintenance commands (bounded, standard) + docker reclaim.
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

  # Docker: dangling images and build cache older than 168h (7 days) only.
  # image: `until=<duration>` prunes images CREATED more than that duration
  # ago (daemon clock); dangling=true is forced without -a, so tagged images
  # can never be candidates. builder: `until` is the documented synonym of
  # `unused-for` (KeepDuration), so cache RECENTLY USED within 168h survives
  # regardless of creation date. Containers and volumes are never touched;
  # Local Volumes is 0 on production and the PAPER sandbox runs without them.
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

  # Rotate only soren application logs (never truncate live file; copytruncate).
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
CONF
  printf 'CONF would install %s\n' "$logrotate_conf"
  if [[ "$apply" == 1 ]]; then
    printf '%s\n' "$conf" | sudo -n tee "$logrotate_conf" >/dev/null
    sudo -n logrotate -f "$logrotate_conf"
  fi
fi

say "=== planned reclaim: $total bytes ($(( total / 1048576 )) MiB) ==="
