#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  exit 20
fi
sha=$1
request_id=$2
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 20
[[ "$request_id" =~ ^[0-9a-f]{32}$ ]] || exit 20

root=/home/ubuntu/docich
state=/home/ubuntu/.local/state/docich/nethack-canary-smoke
request="$state/request.json"
result="$state/results/$request_id.json"

[[ "$(git -C "$root" rev-parse HEAD 2>/dev/null)" == "$sha" ]] || exit 26
systemctl is-active --quiet docich-nethack-canary-smoke.path || exit 21
[[ -d "$state/results" ]] || exit 21
[[ ! -e "$request" ]] || exit 22
[[ ! -e "$result" ]] || exit 27

umask 077
tmp="$state/.request-$request_id.tmp"
trap 'rm -f "$tmp"' EXIT
python3 - "$tmp" "$request_id" "$sha" <<'PY'
import json, pathlib, sys
path, request_id, sha = sys.argv[1:]
payload = {"schema_version": 1, "request_id": request_id, "sha": sha}
p = pathlib.Path(path)
p.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
p.chmod(0o600)
PY
mv "$tmp" "$request"

deadline=$((SECONDS + 780))
while (( SECONDS < deadline )); do
  if [[ -f "$result" ]]; then
    set +e
    python3 - "$result" "$request_id" "$sha" <<'PY'
import json, pathlib, sys
path, request_id, sha = sys.argv[1:]
p = pathlib.Path(path)
try:
    if p.is_symlink() or p.stat().st_size > 65536:
        raise ValueError
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(25)
if not isinstance(data, dict):
    raise SystemExit(25)
if data.get("schema_version") != 1 or data.get("request_id") != request_id or data.get("sha") != sha:
    raise SystemExit(25)
status = data.get("status")
category = data.get("category")
if status == "failed":
    raise SystemExit(24)
if status != "passed":
    raise SystemExit(25)
codes = {"terminal": 0, "policy_stall": 10, "turn_limit": 11, "other_timeout": 12}
raise SystemExit(codes.get(category, 25))
PY
    code=$?
    set -e
    exit "$code"
  fi
  sleep 2
done
exit 23
