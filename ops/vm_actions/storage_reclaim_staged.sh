#!/usr/bin/env bash
# Reviewed, staged general storage maintenance helper (docich#2769).
# Each stage runs independently: one failure does not stop the others.
# Fixed enums only: stage=..., result=ok|failed, reason=...
# Dry-run by default (APPLY=1 to apply). Never removes protected paths.
set -uo pipefail

apply="${APPLY:-0}"
snap_cache_root="/var/lib/snapd/cache"
snap_cache_explicit=0
stage_filter="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage|--only) stage_filter="$2"; shift 2 ;;
    --snap-cache-root) snap_cache_root="$2"; snap_cache_explicit=1; shift 2 ;;
    --root|--home-root|--sys-tmp) shift 2 ;;
    *)
      if [[ "$stage_filter" == "all" && "$1" =~ ^(journal_vacuum|apt_clean|snap_disabled|snapd_cache|docker_prune|logrotate_install|logrotate_run)$ ]]; then
        stage_filter="$1"; shift
      else
        echo "unknown option: $1" >&2; exit 2
      fi
      ;;
  esac
done

[[ "$apply" == 0 || "$apply" == 1 ]] || { echo "invalid APPLY" >&2; exit 2; }

# Fail-closed snap cache root boundary (docich#2769 DSH review): in APPLY mode the
# helper may only delete under the reviewed production snapd cache path unless an
# explicit test-only gate permits a temp root.
if [[ "$apply" == 1 && "$snap_cache_root" != "/var/lib/snapd/cache" ]]; then
  if [[ "${STORAGE_RECLAIM_TEST_ALLOW_CUSTOM_SNAP_CACHE_ROOT:-0}" != "1" ]]; then
    echo "refusing non-default snap cache root in APPLY mode: $snap_cache_root" >&2
    exit 2
  fi
fi

case "$stage_filter" in
  all|journal_vacuum|apt_clean|snap_disabled|snapd_cache|docker_prune|logrotate_install|logrotate_run) ;;
  *) echo "invalid stage: $stage_filter" >&2; exit 2 ;;
esac

say() { printf '%s\n' "$*"; }
stage_line() { printf 'stage=%s result=%s reason=%s\n' "$1" "$2" "$3"; }

mtime_of() {
  local m
  if m="$(stat -c %Y "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then printf '%s\n' "$m"; return 0; fi
  if m="$(stat -f %m "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then printf '%s\n' "$m"; return 0; fi
  if m="$(sudo -n stat -c %Y "$1" 2>/dev/null)" && [[ "$m" =~ ^[0-9]+$ ]]; then printf '%s\n' "$m"; return 0; fi
  return 1
}

is_older_than() {
  local path="$1" days="$2" mtime now
  now="$(date +%s)"
  mtime="$(mtime_of "$path")" || return 1
  (( now - mtime > days * 86400 ))
}

should_run() {
  [[ "$stage_filter" == "all" || "$stage_filter" == "$1" ]]
}

if should_run journal_vacuum; then
  if ! command -v journalctl >/dev/null 2>&1; then
    stage_line journal_vacuum failed not_present
  elif [[ "$apply" == 0 ]]; then
    say "CMD sudo -n journalctl --vacuum-size=200M"
    stage_line journal_vacuum ok none
  else
    err="$(sudo -n journalctl --vacuum-size=200M 2>&1 >/dev/null)"; rc=$?
    if [[ $rc -eq 0 ]]; then stage_line journal_vacuum ok none
    elif [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then stage_line journal_vacuum failed sudo_denied
    else stage_line journal_vacuum failed command_failed; fi
  fi
fi

if should_run apt_clean; then
  if ! command -v apt-get >/dev/null 2>&1; then
    stage_line apt_clean failed not_present
  elif [[ "$apply" == 0 ]]; then
    say "CMD sudo -n apt-get clean"
    stage_line apt_clean ok none
  else
    err="$(sudo -n apt-get clean 2>&1 >/dev/null)"; rc=$?
    if [[ $rc -eq 0 ]]; then stage_line apt_clean ok none
    elif [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then stage_line apt_clean failed sudo_denied
    else stage_line apt_clean failed command_failed; fi
  fi
fi

if should_run snap_disabled; then
  if ! command -v snap >/dev/null 2>&1; then
    stage_line snap_disabled failed not_present
  else
    rc_all=0
    reason="none"
    found=0
    if [[ "$apply" == 0 ]]; then
      while IFS= read -r line; do
        [[ "$line" == *disabled* ]] || continue
        name="$(awk '{print $1}' <<<"$line")"
        rev="$(awk '{print $3}' <<<"$line")"
        [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ && "$rev" =~ ^[0-9]+$ ]] || continue
        found=1
        say "CMD sudo -n snap remove $name --revision=$rev"
      done < <(snap list --all 2>/dev/null | tail -n +2 || true)
      stage_line snap_disabled ok none
    else
      while IFS= read -r line; do
        [[ "$line" == *disabled* ]] || continue
        name="$(awk '{print $1}' <<<"$line")"
        rev="$(awk '{print $3}' <<<"$line")"
        [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ && "$rev" =~ ^[0-9]+$ ]] || continue
        found=1
        err="$(sudo -n snap remove "$name" --revision="$rev" 2>&1 >/dev/null)"; rc=$?
        if [[ $rc -ne 0 ]]; then
          rc_all=1
          if [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then
            reason="sudo_denied"
          else
            [[ "$reason" == "none" ]] && reason="command_failed"
          fi
        fi
      done < <(snap list --all 2>/dev/null | tail -n +2 || true)
      if [[ $found -eq 0 ]]; then
        stage_line snap_disabled ok skipped
      elif [[ $rc_all -eq 0 ]]; then
        stage_line snap_disabled ok none
      else
        stage_line snap_disabled failed "$reason"
      fi
    fi
  fi
fi

if should_run snapd_cache; then
  if [[ ! -d "$snap_cache_root" ]]; then
    stage_line snapd_cache ok not_present
  else
    rc_all=0
    reason="none"
    found=0
    snap_list=""
    if [[ -r "$snap_cache_root" && -x "$snap_cache_root" ]]; then
      for blob in "$snap_cache_root"/* "$snap_cache_root"/.[!.]* "$snap_cache_root"/..?*; do
        [[ -e "$blob" ]] || continue
        snap_list+="$blob"$'\n'
      done
    else
      if ! snap_list="$(sudo -n find "$snap_cache_root" -maxdepth 1 -type f -print 2>/dev/null)"; then
        snap_list=""
        reason="sudo_denied"
        rc_all=1
      fi
    fi
    while IFS= read -r blob; do
      [[ -n "$blob" ]] || continue
      found=1
      if is_older_than "$blob" 7; then
        if [[ "$apply" == 1 ]]; then
          if [[ -w "$(dirname "$blob")" ]]; then
            if ! rm -rf -- "$blob" 2>/dev/null; then rc_all=1; [[ "$reason" == "none" ]] && reason="command_failed"; fi
          else
            err="$(sudo -n rm -rf -- "$blob" 2>&1 >/dev/null)"; rc=$?
            if [[ $rc -ne 0 ]]; then
              rc_all=1
              if [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then reason="sudo_denied"
              else [[ "$reason" == "none" ]] && reason="command_failed"; fi
            fi
          fi
        fi
      fi
    done <<< "$snap_list"
    if [[ $rc_all -ne 0 ]]; then
      stage_line snapd_cache failed "$reason"
    elif [[ $found -eq 0 ]]; then
      stage_line snapd_cache ok skipped
    else
      stage_line snapd_cache ok none
    fi
  fi
fi

if should_run docker_prune; then
  if ! command -v docker >/dev/null 2>&1; then
    stage_line docker_prune failed not_present
  else
    if [[ "$apply" == 0 ]]; then
      say "CMD sudo -n docker image prune -f --filter until=168h"
      say "CMD sudo -n docker builder prune -f --filter until=168h"
      stage_line docker_prune ok none
    else
      err="$(sudo -n docker info 2>&1 >/dev/null)"; rc=$?
      if [[ $rc -ne 0 ]]; then
        if [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then
          stage_line docker_prune failed sudo_denied
        else
          stage_line docker_prune ok skipped
        fi
      else
        rc_all=0; reason="none"
        err="$(sudo -n docker image prune -f --filter until=168h 2>&1 >/dev/null)"; rc=$?
        if [[ $rc -ne 0 ]]; then rc_all=1
          if [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then reason="sudo_denied"; else reason="command_failed"; fi
        fi
        err="$(sudo -n docker builder prune -f --filter until=168h 2>&1 >/dev/null)"; rc=$?
        if [[ $rc -ne 0 ]]; then rc_all=1
          if [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then
            [[ "$reason" == "none" ]] && reason="sudo_denied"
          else
            [[ "$reason" == "none" ]] && reason="command_failed"
          fi
        fi
        if [[ $rc_all -eq 0 ]]; then stage_line docker_prune ok none
        else stage_line docker_prune failed "$reason"; fi
      fi
    fi
  fi
fi

if should_run logrotate_install; then
  logrotate_conf="/etc/logrotate.d/soren"
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
  if [[ "$apply" == 0 ]]; then
    say "CONF would install $logrotate_conf"
    stage_line logrotate_install ok none
  else
    err="$(printf '%s\n' "$conf" | sudo -n tee "$logrotate_conf" 2>&1 >/dev/null)"; rc=$?
    if [[ $rc -eq 0 ]]; then stage_line logrotate_install ok none
    elif [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then stage_line logrotate_install failed sudo_denied
    else stage_line logrotate_install failed command_failed; fi
  fi
fi

if should_run logrotate_run; then
  logrotate_conf="/etc/logrotate.d/soren"
  if ! command -v logrotate >/dev/null 2>&1; then
    stage_line logrotate_run failed not_present
  elif [[ "$apply" == 0 ]]; then
    say "CMD sudo -n logrotate -f $logrotate_conf"
    stage_line logrotate_run ok none
  else
    err="$(sudo -n logrotate -f "$logrotate_conf" 2>&1 >/dev/null)"; rc=$?
    if [[ $rc -eq 0 ]]; then stage_line logrotate_run ok none
    elif [[ "$err" == *"password is required"* || "$err" == *"a password is required"* || "$err" == *"sudo:"* ]]; then stage_line logrotate_run failed sudo_denied
    else stage_line logrotate_run failed command_failed; fi
  fi
fi

exit 0
