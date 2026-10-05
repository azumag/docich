#!/usr/bin/env bash
set -euo pipefail
(( $# == 0 )) || exit 64
mode="${QUEUE_ADMIN_MODE:-}"
sha="${QUEUE_ADMIN_SHA:-}"
handle="${QUEUE_ADMIN_HANDLE:-}"
acknowledgement="${QUEUE_ADMIN_ACK:-}"
repository="${QUEUE_ADMIN_REPOSITORY:-}"
repository_id="${QUEUE_ADMIN_REPOSITORY_ID:-}"
actor_id="${QUEUE_ADMIN_ACTOR_ID:-}"
workflow_ref="${QUEUE_ADMIN_WORKFLOW_REF:-}"
ref="${QUEUE_ADMIN_REF:-}"
run_id="${QUEUE_ADMIN_RUN_ID:-}"
run_attempt="${QUEUE_ADMIN_RUN_ATTEMPT:-}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ "$repository" == azumag/docich && "$repository_id" == 1327276249 && "$actor_id" == 9018513 ]] || exit 64
[[ "$workflow_ref" == azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main && "$ref" == refs/heads/main ]] || exit 64
[[ "$run_id" =~ ^[1-9][0-9]{0,19}$ && "$run_attempt" =~ ^[1-9][0-9]{0,3}$ ]] || exit 64
case "$mode" in
  check) [[ -z "$handle" && "$acknowledgement" == not-acknowledged ]] || exit 64 ;;
  execute) [[ "$handle" =~ ^[1-9][0-9]{0,19}-[1-9][0-9]{0,3}$ && "$acknowledgement" == acknowledged ]] || exit 64 ;;
  *) exit 64 ;;
esac
root="${DOCICH_PROD_ROOT:-/home/ubuntu/docich}"
[[ -f "$root/src/docich/hanjuku_queue_admin_plan.py" && -f "$root/config/docich.soren-live.toml" ]] || exit 25
# Recheck under the gateway deployment lock, not only in the workflow.
[[ "$(git -C "$root" -c core.hooksPath=/dev/null rev-parse HEAD)" == "$sha" ]] || exit 25
[[ -z "$(git -C "$root" -c core.hooksPath=/dev/null status --porcelain --untracked-files=no --ignore-submodules=all)" ]] || exit 25
export PYTHONPATH="$root/src"
python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 25)'
args=("$mode" --repository "$repository" --repository-id "$repository_id" --actor-id "$actor_id"
      --workflow-ref "$workflow_ref" --ref "$ref" --sha "$sha" --run-id "$run_id" --run-attempt "$run_attempt")
if [[ "$mode" == execute ]]; then
  args+=(--plan-handle "$handle" --acknowledge-unknown-resources)
fi
# Body/result remain solely in the existing owner-only 0600 VM exec log.
exec python3 -B -P -m docich.hanjuku_queue_admin_plan "${args[@]}"
