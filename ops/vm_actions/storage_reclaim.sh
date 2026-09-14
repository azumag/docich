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
#   - No argument from the network is interpolated into a path; the only
#     external input is the APPLY flag.
#
# Optional flags exist so repository tests can run against a temporary root:
#   --root DIR, --min-age-days N, --voicevox-root DIR,
#   --include-voicevox-archive, --skip-system,
#   --opencode-rotate, --opencode-retention-days N, --opencode-db PATH,
#   --opencode-force

apply="${APPLY:-0}"
root="/home/ubuntu/soren"
min_age_days=21
voicevox_root="/opt/voicevox"
include_voicevox="${VOICEVOX_ARCHIVE:-0}"
opencode_rotate="${OPENCODE_DB_ROTATE:-0}"
opencode_retention_days="${OPENCODE_DB_RETENTION_DAYS:-3}"
opencode_db="${OPENCODE_DB_PATH:-/home/ubuntu/.local/share/opencode/opencode.db}"
opencode_force=0
skip_system=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) root="$2"; shift 2 ;;
    --min-age-days) min_age_days="$2"; shift 2 ;;
    --voicevox-root) voicevox_root="$2"; shift 2 ;;
    --include-voicevox-archive) include_voicevox=1; shift ;;
    --opencode-rotate) opencode_rotate=1; shift ;;
    --opencode-retention-days) opencode_retention_days="$2"; shift 2 ;;
    --opencode-db) opencode_db="$2"; shift 2 ;;
    --opencode-force) opencode_force=1; shift ;;
    --skip-system) skip_system=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ "$apply" == 0 || "$apply" == 1 ]] || { echo "invalid APPLY" >&2; exit 2; }
[[ "$include_voicevox" == 0 || "$include_voicevox" == 1 ]] || { echo "invalid VOICEVOX_ARCHIVE" >&2; exit 2; }
[[ "$opencode_rotate" == 0 || "$opencode_rotate" == 1 ]] || { echo "invalid OPENCODE_DB_ROTATE" >&2; exit 2; }
[[ "$opencode_retention_days" =~ ^[0-9]+$ ]] || { echo "invalid OPENCODE_DB_RETENTION_DAYS" >&2; exit 2; }
[[ "$min_age_days" =~ ^[0-9]+$ ]] || { echo "invalid min-age-days" >&2; exit 2; }
[[ -d "$root" ]] || { echo "root not found: $root" >&2; exit 2; }

# Directories under $root/tmp that are safe to reclaim when older than min_age_days.
stale_patterns=(direct_av_sync direct_stream_benchmark manual_challenge game-lifecycle-e2e "soviet-iso-verify-*")
# Explicitly live / protected paths that must never be reclaimed.
protected=(soviet_local_chromium_profile state debug)

now=$(date +%s)
total=0

say() { printf '%s\n' "$*"; }

bytes_of() {
  # 1K-block size, works with both GNU and BSD du.
  local kb
  kb="$(du -skx "$1" 2>/dev/null | awk 'NR==1{print $1}')"
  [[ "$kb" =~ ^[0-9]+$ ]] || { printf '0\n'; return 0; }
  printf '%s\n' "$(( kb * 1024 ))"
}

mtime_of() {
  # GNU stat first, then BSD/macOS.
  local m
  if m="$(stat -c %Y "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$m"
    return 0
  fi
  if m="$(stat -f %m "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$m"
    return 0
  fi
  return 1
}

is_old() {
  local mtime
  mtime="$(mtime_of "$1")" || return 1
  (( now - mtime > min_age_days * 86400 ))
}

remove_path() {
  local path="$1" bytes
  [[ -e "$path" ]] || return 0
  bytes="$(bytes_of "$path")"; bytes="${bytes:-0}"
  printf 'DEL  %12s B  %s\n' "$bytes" "$path"
  total=$(( total + bytes ))
  if [[ "$apply" == 1 ]]; then
    rm -rf "$path"
  fi
}

run_cmd() {
  printf 'CMD  %s\n' "$*"
  if [[ "$apply" == 1 ]]; then
    "$@"
  fi
}

# Rotate the opencode session database by deleting sessions older than
# opencode_retention_days and reclaiming the file with VACUUM.
#   - Only runs when no `opencode` process is active (unless --opencode-force),
#     because VACUUM needs an exclusive lock.
#   - The pipeline never resumes past sessions, so old history is expendable.
rotate_opencode_db() {
  local db="$opencode_db" before after
  if [[ ! -f "$db" ]]; then
    say "SKIP opencode db (not found: $db)"
    return 0
  fi
  before="$(bytes_of "$db")"
  say "OPENCODE db path=$db before=${before}B retention_days=$opencode_retention_days"
  if [[ "$opencode_force" != 1 ]] && pgrep -f 'opencode' >/dev/null 2>&1; then
    say "SKIP opencode db rotate (opencode process active; retry later)"
    return 0
  fi
  python3 - "$db" "$opencode_retention_days" "$apply" <<'PY' || return 1
import sqlite3, sys, time
db, days, apply = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
cutoff = int(time.time() * 1000) - days * 86400000
con = sqlite3.connect(db)
con.isolation_level = None
cur = con.cursor()
try:
    old = cur.execute("select count(*) from session where time_created < ?", (cutoff,)).fetchone()[0]
except sqlite3.Error as exc:
    print("OPENCODE error:", exc)
    con.close()
    raise SystemExit(1)
print("OPENCODE old_sessions=%d" % old)
if apply and old > 0:
    cur.execute("pragma foreign_keys=ON")
    cur.execute("create temp table old_sessions as select id from session where time_created < ?", (cutoff,))
    for table in ("todo", "session_share", "session_message", "session_input", "session_context_epoch"):
        try:
            cur.execute("delete from %s where session_id in (select id from old_sessions)" % table)
        except sqlite3.Error:
            pass
    cur.execute("delete from event where aggregate_id in (select id from old_sessions)")
    cur.execute("delete from event_sequence where aggregate_id in (select id from old_sessions)")
    cur.execute("delete from message where session_id in (select id from old_sessions)")
    cur.execute("delete from session where id in (select id from old_sessions)")
    cur.execute("vacuum")
    print("OPENCODE rotated old_sessions=%d" % old)
con.close()
PY
  after="$(bytes_of "$db")"
  say "OPENCODE db after=${after}B freed=$(( before - after ))B"
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

# 3. VOICEVOX installer archive: opt-in, and only when the extracted engine exists.
if [[ "$include_voicevox" == 1 ]]; then
  archive="$voicevox_root/voicevox.7z.001"
  if [[ -f "$archive" && -x "$voicevox_root/current/run" ]]; then
    remove_path "$archive"
  else
    say "SKIP voicevox archive (archive or extracted engine missing)"
  fi
fi

# 3b. opencode session DB rotation (opt-in).
if [[ "$opencode_rotate" == 1 ]]; then
  rotate_opencode_db
fi

# 4. Fixed system maintenance commands (bounded, standard).
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
