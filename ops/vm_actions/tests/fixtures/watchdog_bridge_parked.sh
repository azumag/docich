# Public predicate from azumag/soviet_now at
# 0771c697bdf2dd83e8ee62177a8347983fc40eee, lib/game_lifecycle.sh.
game_lifecycle_bridge_parked() {
	[ "${GAME_LIFECYCLE_ENABLED:-1}" = "1" ] || return 1
	python3 - "$GAME_LIFECYCLE_DIR/request.json" "$GAME_LIFECYCLE_DIR/ack.json" <<'PY'
import json
import sys
import time

try:
    request = json.load(open(sys.argv[1], encoding="utf-8"))
    ack = json.load(open(sys.argv[2], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if not isinstance(request, dict) or not isinstance(ack, dict):
    raise SystemExit(1)
if request.get("schema") != 1 or ack.get("schema") != 1:
    raise SystemExit(1)
for field in ("request_id", "game", "generation", "deadline_epoch", "deadline_at"):
    if field not in request or ack.get(field) != request.get(field):
        raise SystemExit(1)
status = ack.get("status")
if status == "stopped":
    # Terminal: a stopped bridge awaits an explicit fresh launch for resume.
    raise SystemExit(0)
if status in {"stop_requested", "resume_requested", "stopping"}:
    # Non-terminal parks expire with the request deadline.  A stale park must
    # not suppress watchdog recovery forever while the bridge treats the same
    # request as expired.
    try:
        deadline = float(request.get("deadline_epoch"))
    except (TypeError, ValueError):
        raise SystemExit(1)
    if deadline > time.time():
        raise SystemExit(0)
    raise SystemExit(1)
raise SystemExit(1)
PY
}
