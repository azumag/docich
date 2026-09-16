#!/usr/bin/env bash
set -euo pipefail
umask 077

[[ "$#" -eq 5 ]] || exit 64
[[ "$1" == "run" ]] || exit 64
[[ "$2" == "--format" && "$3" == "json" ]] || exit 64
[[ "$4" == "--model" ]] || exit 64
[[ "$5" =~ ^[A-Za-z0-9_./:-]{1,160}$ ]] || exit 64
[[ -f "$SOREN91_OPENCODE_NONZERO_CLASSIFIER" && ! -L "$SOREN91_OPENCODE_NONZERO_CLASSIFIER" ]] || exit 65

shim_dir="$(cd -- "$(dirname -- "$0")" && pwd -P)"
inner="$shim_dir/opencode-fixed-exec"
[[ -x "$inner" && ! -L "$inner" ]] || exit 66

child_out="$(mktemp /home/ubuntu/.soren91-opencode-child-out.XXXXXX)"
child_err="$(mktemp /home/ubuntu/.soren91-opencode-child-err.XXXXXX)"
child_pid=''

cleanup_child() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  rm -f "$child_out" "$child_err"
}

classify_nonzero() {
  local category
  category="$(python3 "$SOREN91_OPENCODE_NONZERO_CLASSIFIER" "$child_out" 2>/dev/null || printf 'structured_nonzero\n')"
  case "$category" in
    error_event|error_part|tool_event|unexpected_event|invalid_json|structured_nonzero|no_json) ;;
    *) category=structured_nonzero ;;
  esac
  printf 'soren91_opencode_nonzero_json=%s\n' "$category" >&2
}

on_signal() {
  local signal="$1"
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
    child_pid=''
  fi
  classify_nonzero
  printf 'soren91_opencode_signal=%s\n' "$signal" >&2
  case "$signal" in
    TERM) exit 143 ;;
    INT) exit 130 ;;
    HUP) exit 129 ;;
    *) exit 1 ;;
  esac
}

trap cleanup_child EXIT
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'on_signal HUP' HUP

# Bash may attach /dev/null to an asynchronous command's stdin when job
# control is disabled. Preserve the reviewed prompt stream explicitly at the
# compound-command boundary while keeping the fixed child argv unchanged.
exec 8<&0
set +e
{
  "$inner" run --format json --model "$5" >"$child_out" 2>"$child_err" &
  child_pid=$!
} <&8
exec 8<&-
wait "$child_pid"
rc=$?
child_pid=''
set -e

cat "$child_out"
cat "$child_err" >&2
if [[ "$rc" -ne 0 ]]; then
  classify_nonzero
fi
exit "$rc"
