#!/usr/bin/env bash
set -euo pipefail

# Fixed public-repository operator action for one stalled Hanjuku prediction
# round (Issue #2216). Accepted as a *pair*: the workflow runs `check` first
# and only the reviewed, still-live round may then be `apply`ed.
#
# No command, prediction id, outcome index or path is accepted as input. The
# owner workflow verifies protected main and the exact deployed SHA before
# either mode runs, and the gateway holds its deployment lock while this fixed
# script executes. The operation is a closed enum; arbitrary commands are never
# an authorization source.
if (( $# != 0 )); then
  printf '%s\n' 'prediction control accepts no arguments' >&2
  exit 64
fi

# Both selectors are a closed enum. An unset or empty value is rejected rather
# than defaulted, so a workflow wiring mistake fails closed instead of silently
# running a mode the operator did not ask for.
mode="${PREDICTION_MODE:-}"
operation="${PREDICTION_OPERATION:-}"
case "$mode" in
  check|apply) ;;
  *) exit 64 ;;
esac
case "$operation" in
  cancel|sync) ;;
  *) exit 64 ;;
esac

root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
[[ -f "$root/src/docich/hanjuku_prediction_control.py" && -f "$root/config/docich.soren-live.toml" ]] || exit 25

# The gateway executes fixed scripts in a limited environment that does not
# inherit DOCICH_CONFIG. Bind the reviewed live profile explicitly, otherwise
# load_global(None) selects config/docich.toml (state_dir=run) and would act on
# the wrong state directory. The module also defaults to this path.
config="$root/config/docich.soren-live.toml"

# `check` must not mutate anything: it reports which round the reviewed
# operation would act on, using the same read-only ledger projection that the
# diagnostics collector uses, and refuses an already-terminal round.
if [[ "$mode" == check ]]; then
  export PYTHONPATH="$root/src"
  exec python3 -B -P -m docich.hanjuku_prediction_control_check --config "$config" "$operation"
fi

export PYTHONPATH="$root/src"
exec python3 -B -P -m docich.hanjuku_prediction_control --config "$config" "$operation"
