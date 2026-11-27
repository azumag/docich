"""Record Soren deadline misplacements through the existing audit pipeline.

soviet_now's ``deadline_misplacement_monitor.py`` already watches the live game
history and writes one JSONL event when a newly landed piece crosses the dead
line.  This module is the docich-side evidence gate requested in docich#5725:
it re-checks the embedded board snapshots with independent geometry, keeps the
"safe alternative existed" condition explicit, and records confirmed events as
monthly-audit incidents.  The incident write and optional GitHub filing reuse
``docich.hanjuku.month_audit``; this module does not add another external
sender or another incident format.

The scanner is deliberately incremental and evidence-only by default.  Passing
``spawn=True`` (or CLI ``--spawn``) starts the same detached issue filer used by
the Hanjuku audit; without it the JSONL incident remains the durable evidence
from which the existing filer can create or update an issue later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from .game_switch import atomic_write_json
from .hanjuku import month_audit

SCHEMA = 1
STATE_FILE = "soren_deadline_audit.json"
MONITOR_LOG_REL = Path("logs") / "deadline_misplacement_monitor.jsonl"
AUDIT_DIR_REL = Path("tmp") / "state" / "soren_deadline_audit"
DEFAULT_SOREN_ROOT = Path("/home/ubuntu/soren")

MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_STATE_BYTES = 128 * 1024
MAX_SEEN_EVENTS = 500
MAX_PIECES = 80
MAX_TEXT = 160

DEADLINE_Y = 3.38
FLOOR_Y = -5.0
DROP_X_MIN = -3.0
DROP_X_MAX = 3.0
LOWER_EPS = 0.05
MERGE_GAP_EPS = 0.35
SUPPORT_GAP_EPS = 0.55
TYPE_RADII = {
    1: 0.207,
    2: 0.259,
    3: 0.316,
    4: 0.380,
    5: 0.414,
    6: 0.470,
    7: 0.559,
    8: 0.660,
    9: 0.746,
    10: 0.846,
    11: 0.982,
    12: 1.068,
    13: 1.207,
    14: 1.385,
    15: 1.600,
}

KIND = "soren_deadline_misplacement"
FINGERPRINT = hashlib.sha256(KIND.encode("utf-8")).hexdigest()[:16]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if type(value) is int:
        return value
    return None


def _piece_type(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bounded_text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ")[:limit]


def _radius(piece: dict[str, Any], fallback_type: int = 0) -> float:
    radius = _num(piece.get("r"), 0.0)
    if radius > 0:
        return radius
    ptype = _piece_type(piece.get("type", fallback_type) or fallback_type)
    return TYPE_RADII.get(ptype, 0.5)


def _piece_top(piece: dict[str, Any]) -> float:
    return _num(piece.get("y")) + _radius(piece)


def _pieces(record: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = record.get("state_snapshot", {})
    pieces = snapshot.get("pieces", []) if isinstance(snapshot, dict) else []
    return pieces if isinstance(pieces, list) else []


def _new_piece(before: list[dict[str, Any]], after: list[dict[str, Any]],
               decision_x: float, next_type: int) -> dict[str, Any] | None:
    before_ids = {piece.get("id") for piece in before}
    added = [piece for piece in after if piece.get("id") not in before_ids]
    if not added:
        return None
    typed = [piece for piece in added if _piece_type(piece.get("type")) == next_type]
    pool = typed or added
    return min(pool, key=lambda piece: (
        abs(_num(piece.get("x")) - decision_x),
        -_piece_top(piece),
    ))


def _landing_at_x(pieces: list[dict[str, Any]], x: float, radius: float) -> dict[str, Any]:
    landing_y = FLOOR_Y + radius
    support_id = None
    for piece in pieces:
        piece_radius = _radius(piece)
        dx = abs(x - _num(piece.get("x")))
        contact = radius + piece_radius
        if dx >= contact:
            continue
        y = _num(piece.get("y")) + math.sqrt(max(contact * contact - dx * dx, 0.0))
        if y > landing_y:
            landing_y = y
            support_id = piece.get("id")
    return {
        "x": round(x, 3),
        "landing_y": round(landing_y, 3),
        "top_y": round(landing_y + radius, 3),
        "support_id": support_id,
    }


def _candidate_xs(decision_x: float, pieces: list[dict[str, Any]]) -> list[float]:
    xs = {
        round(DROP_X_MIN + i * 0.05, 3)
        for i in range(int((DROP_X_MAX - DROP_X_MIN) / 0.05) + 1)
    }
    xs.add(round(max(DROP_X_MIN, min(DROP_X_MAX, decision_x)), 3))
    for piece in pieces:
        px = max(DROP_X_MIN, min(DROP_X_MAX, _num(piece.get("x"))))
        xs.add(round(px, 3))
        xs.add(round(max(DROP_X_MIN, min(DROP_X_MAX, px - 0.2)), 3))
        xs.add(round(max(DROP_X_MIN, min(DROP_X_MAX, px + 0.2)), 3))
    return sorted(xs)


def _merge_alternative(pieces: list[dict[str, Any]], next_type: int, radius: float,
                       candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    same = [piece for piece in pieces if _piece_type(piece.get("type")) == next_type]
    if not same:
        return None
    best = None
    best_key = None
    for candidate in candidates:
        cx = _num(candidate.get("x"))
        cy = _num(candidate.get("landing_y"))
        for piece in same:
            piece_radius = _radius(piece)
            dist = math.hypot(cx - _num(piece.get("x")), cy - _num(piece.get("y")))
            gap = dist - (radius + piece_radius)
            if gap > MERGE_GAP_EPS:
                continue
            key = (abs(gap), _num(candidate.get("top_y")), abs(cx))
            if best_key is None or key < best_key:
                best_key = key
                best = {
                    **candidate,
                    "target_id": piece.get("id"),
                    "target_x": round(_num(piece.get("x")), 3),
                    "target_y": round(_num(piece.get("y")), 3),
                    "contact_gap": round(gap, 3),
                }
    return best


def _summary_piece(piece: dict[str, Any] | None) -> dict[str, Any] | None:
    if not piece:
        return None
    return {
        "id": piece.get("id"),
        "type": piece.get("type"),
        "x": round(_num(piece.get("x")), 3),
        "y": round(_num(piece.get("y")), 3),
        "r": round(_radius(piece), 3),
        "top_y": round(_piece_top(piece), 3),
    }


def _is_physically_supported(piece: dict[str, Any], before: list[dict[str, Any]],
                             fallback_type: int) -> bool:
    """Require contact with the prior board so in-air frames are ignored."""
    px = _num(piece.get("x"))
    py = _num(piece.get("y"))
    radius = _radius(piece, fallback_type)
    if radius <= 0:
        return False
    if abs(py - (FLOOR_Y + radius)) <= SUPPORT_GAP_EPS:
        return True
    for prev_piece in before:
        qx = _num(prev_piece.get("x"))
        qy = _num(prev_piece.get("y"))
        qradius = _radius(prev_piece)
        if qradius <= 0:
            continue
        dist = math.hypot(px - qx, py - qy)
        if abs(dist - (radius + qradius)) <= SUPPORT_GAP_EPS:
            return True
    return False


def evaluate_transition(prev: dict[str, Any], curr: dict[str, Any]) -> dict[str, Any] | None:
    """Evaluate one placement transition with the upstream monitor's geometry.

    ``status=inappropriate`` is returned only when the actually added piece is
    physically supported, crosses the deadline, and either a within-deadline
    landing at another x or an approximate same-type merge improving the top
    existed on the previous board.  If no such alternative exists the result is
    ``appropriate`` evidence, matching the issue's "置ける場所がない場合は対象外".
    """
    before = _pieces(prev)
    after = _pieces(curr)
    if not before or not after:
        return None
    decision_x = _num(prev.get("decision_x"))
    next_type = _piece_type(prev.get("next_type"))
    placed = _new_piece(before, after, decision_x, next_type)
    if not placed:
        return None
    if not _is_physically_supported(placed, before, next_type):
        return None
    actual_top = _piece_top(placed)
    if actual_top <= DEADLINE_Y:
        return None
    radius = _radius(placed, next_type)
    candidates = [_landing_at_x(before, x, radius) for x in _candidate_xs(decision_x, before)]
    chosen_estimate = _landing_at_x(before, decision_x, radius)
    chosen_x = round(max(DROP_X_MIN, min(DROP_X_MAX, decision_x)), 3)
    alt_candidates = [c for c in candidates if _num(c.get("x")) != chosen_x]
    lower = [c for c in alt_candidates if _num(c.get("top_y")) <= DEADLINE_Y]
    lower_only = [
        c for c in alt_candidates
        if DEADLINE_Y < _num(c.get("top_y")) and _num(c.get("top_y")) + LOWER_EPS < actual_top
    ]
    best_lower = min(lower, key=lambda c: (_num(c.get("top_y")), abs(_num(c.get("x")))), default=None)
    best_merge = _merge_alternative(before, next_type, radius, alt_candidates)
    merge_improves_top = bool(
        best_merge and _num(best_merge.get("top_y")) + LOWER_EPS < actual_top
    )
    inappropriate = bool(best_lower or merge_improves_top)
    return {
        "kind": "deadline_misplacement_check",
        "status": "inappropriate" if inappropriate else "appropriate",
        "trigger": "actual_new_piece_top_over_deadline",
        "turn": prev.get("turn"),
        "next_turn": curr.get("turn"),
        "score": prev.get("score"),
        "piece_count": prev.get("piece_count"),
        "decision_x": round(decision_x, 3),
        "decision_reason": prev.get("decision_reason", ""),
        "history_decision_crosses_deadline": bool(prev.get("decision_crosses_deadline", False)),
        "actual_new_piece": _summary_piece(placed),
        "independent_chosen_estimate": chosen_estimate,
        "has_merge_alternative": bool(best_merge),
        "merge_alternative_improves_top": merge_improves_top,
        "has_lower_alternative": bool(best_lower),
        "best_merge_alternative": best_merge,
        "best_lower_alternative": best_lower,
        "candidate_count": len(candidates),
        "lower_candidate_count": len(lower),
        "lower_only_candidate_count": len(lower_only),
        "deadline_y": DEADLINE_Y,
        "prev_pieces": before,
        "curr_pieces": after,
    }


def _independent_assessment(event: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    prev_pieces = event.get("prev_pieces")
    curr_pieces = event.get("curr_pieces")
    if prev_pieces is None and curr_pieces is None:
        return None, "snapshots_missing"
    if not isinstance(prev_pieces, list) or not isinstance(curr_pieces, list):
        return None, "snapshots_invalid"
    actual = event.get("actual_new_piece") if isinstance(event.get("actual_new_piece"), dict) else {}
    next_type = _piece_type(actual.get("type") or event.get("next_type"))
    prev = {
        "state_snapshot": {"pieces": prev_pieces},
        "decision_x": event.get("decision_x"),
        "next_type": next_type,
        "turn": event.get("turn"),
        "score": event.get("score"),
        "piece_count": event.get("piece_count"),
        "decision_reason": event.get("decision_reason", ""),
        "decision_crosses_deadline": event.get("history_decision_crosses_deadline", False),
    }
    curr = {
        "state_snapshot": {"pieces": curr_pieces},
        "turn": event.get("next_turn"),
    }
    assessment = evaluate_transition(prev, curr)
    if assessment is None:
        return None, "independent_no_supported_crossing"
    if assessment.get("status") != "inappropriate":
        return None, "independent_no_safe_alternative"
    return assessment, "independent_geometry"


def _declared_safe_alternative(event: dict[str, Any]) -> bool:
    """Use the upstream monitor's own conclusion for legacy events.

    Events before soviet_now#943 did not embed board snapshots.  They can still
    be evidence because the monitor itself only marks ``inappropriate`` after
    finding a lower within-deadline candidate or a useful merge alternative.
    Snapshot-bearing events never use this fallback; they must survive the
    independent geometry check above.
    """
    actual = event.get("actual_new_piece") if isinstance(event.get("actual_new_piece"), dict) else {}
    deadline = _num(event.get("deadline_y"), DEADLINE_Y)
    if _num(actual.get("top_y")) <= deadline:
        return False
    lower = event.get("best_lower_alternative")
    if isinstance(lower, dict) and _num(lower.get("top_y")) <= deadline:
        return True
    merge = event.get("best_merge_alternative")
    return bool(event.get("merge_alternative_improves_top") is True and isinstance(merge, dict))


def assess_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return confirmed evidence for one monitor event, or ``None``.

    The upstream monitor's ``appropriate`` events are audit telemetry, not
    incidents.  In particular they include the no-alternative case that the
    issue explicitly says to ignore.
    """
    if not isinstance(event, dict):
        return None
    if event.get("kind") != "deadline_misplacement_check":
        return None
    if event.get("status") != "inappropriate":
        return None
    assessment, verification = _independent_assessment(event)
    if assessment is not None:
        return {"event": event, "assessment": assessment, "verification": verification}
    if verification == "snapshots_missing" and _declared_safe_alternative(event):
        return {"event": event, "assessment": event, "verification": "monitor_status"}
    return None


def _canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def event_identity(event: dict[str, Any]) -> str:
    identity = {key: value for key, value in event.items() if key != "ts"}
    if not identity:
        identity = {"ts": event.get("ts")}
    return hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:20]


def _normalized_piece(piece: Any) -> dict[str, Any] | None:
    if not isinstance(piece, dict):
        return None
    out = {}
    if piece.get("id") is not None:
        item = piece.get("id")
        out["id"] = item if type(item) in (int, float) else str(item)[:64]
    if type(piece.get("type")) is int and not isinstance(piece.get("type"), bool):
        out["type"] = piece.get("type")
    for key in ("x", "y", "r"):
        if piece.get(key) is not None:
            out[key] = round(_num(piece.get(key)), 4)
    return out or None


def _normalized_pieces(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    pieces = []
    for piece in value[:MAX_PIECES]:
        normalized = _normalized_piece(piece)
        if normalized is not None:
            pieces.append(normalized)
    return pieces


def _bounded_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out = {}
    for key in ("x", "landing_y", "top_y", "contact_gap", "target_x", "target_y"):
        if value.get(key) is not None:
            out[key] = round(_num(value.get(key)), 4)
    for key in ("support_id", "target_id"):
        if value.get(key) is not None:
            item = value.get(key)
            out[key] = item if type(item) in (int, float) else str(item)[:64]
    return out


def build_incident(confirmed: dict[str, Any], audit_dir: Path, *, now: float | None = None) -> dict[str, Any]:
    """Build one bounded monthly-audit incident from confirmed evidence."""
    event = confirmed["event"]
    assessment = confirmed["assessment"]
    verification = confirmed["verification"]
    created = _num(event.get("ts"), time.time() if now is None else now)
    event_id = event_identity(event)
    actual = assessment.get("actual_new_piece") if isinstance(assessment.get("actual_new_piece"), dict) else {}
    prev_pieces = _normalized_pieces(assessment.get("prev_pieces") or event.get("prev_pieces"))
    curr_pieces = _normalized_pieces(assessment.get("curr_pieces") or event.get("curr_pieces"))
    decision_reason = _bounded_text(assessment.get("decision_reason") or event.get("decision_reason"))
    soren_event = {
        "turn": assessment.get("turn", event.get("turn")),
        "next_turn": assessment.get("next_turn", event.get("next_turn")),
        "score": assessment.get("score", event.get("score")),
        "decision_x": assessment.get("decision_x", event.get("decision_x")),
        "decision_reason": decision_reason,
        "deadline_y": _num(assessment.get("deadline_y"), DEADLINE_Y),
        "actual_new_piece": actual,
        "chosen_estimate": _bounded_mapping(assessment.get("independent_chosen_estimate")),
        "best_lower_alternative": _bounded_mapping(assessment.get("best_lower_alternative")),
        "best_merge_alternative": _bounded_mapping(assessment.get("best_merge_alternative")),
        "candidate_count": _int_or_none(assessment.get("candidate_count")),
        "lower_candidate_count": _int_or_none(assessment.get("lower_candidate_count")),
        "verification": verification,
        "prev_piece_count": len(prev_pieces),
        "curr_piece_count": len(curr_pieces),
        "prev_pieces": prev_pieces,
        "curr_pieces": curr_pieces,
    }
    return {
        "schema": month_audit.SCHEMA,
        "kind": KIND,
        "confidence": "high",
        "confidence_reasons": [
            "実着地ピースのtopがデッドラインを超過",
            "前盤面に安全な代替配置または有効な同type併合候補が存在",
        ],
        "chapter": None,
        "month": None,
        "created_at": float(created),
        "incident_id": f"soren-deadline-{event_id}",
        "signature": KIND,
        "fingerprint": FINGERPRINT,
        "auto_file": True,
        "soren_event": soren_event,
        "evidence": {
            "runtime_dir": audit_dir.name,
            "decisions": str(MONITOR_LOG_REL),
            "events": str(MONITOR_LOG_REL),
            "frames": "game_history/latest.jsonl",
            "bot_version": None,
            "identity": {"game": "sorengame", "runtime_id": "soviet_now"},
            "detector": "docich.soren_deadline_audit",
        },
        "month_summary": {
            "chapter": None,
            "month": None,
            "observations": 1,
            "duration_s": 0.0,
            "max_gap_s": 0.0,
        },
        "decisions": {
            "before": [{
                "at": float(created),
                "decision": "deadline_misplacement_check",
                "reason": decision_reason,
                "observed_metric": {
                    "turn": soren_event.get("turn"),
                    "decision_x": soren_event.get("decision_x"),
                    "actual_top_y": actual.get("top_y") if isinstance(actual, dict) else None,
                    "deadline_y": soren_event.get("deadline_y"),
                    "verification": verification,
                },
            }],
            "counts": {"deadline_misplacement_check": 1},
        },
        "plan": {},
        "world": {},
        "sorties": {},
    }


def record_confirmed(audit_dir: Path, confirmed: dict[str, Any], *, spawn: bool = False,
                     now: float | None = None) -> dict[str, Any]:
    """Append one incident through the existing audit pipeline."""
    audit_dir = Path(audit_dir)
    if audit_dir.is_symlink():
        raise month_audit.AuditError("invalid_runtime")
    audit_dir.mkdir(parents=True, exist_ok=True)
    record = build_incident(confirmed, audit_dir, now=now)
    month_audit.record_incident(audit_dir, record)
    filed = month_audit.spawn_issue_filer(audit_dir, record) if spawn else False
    return {"status": "reported" if filed else "recorded", "incident": record}


def _initial_state() -> dict[str, Any]:
    return {"schema": SCHEMA, "seen": []}


def load_state(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
            return _initial_state()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _initial_state()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return _initial_state()
    seen = data.get("seen")
    return {"schema": SCHEMA, "seen": seen if isinstance(seen, list) else []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = [entry for entry in (state.get("seen") or []) if isinstance(entry, dict)]
    atomic_write_json(path, {"schema": SCHEMA, "seen": seen[-MAX_SEEN_EVENTS:]})


def _remember(state: dict[str, Any], event_id: str, *, incident_id: str | None,
              status: str, now: float) -> None:
    seen = [entry for entry in (state.get("seen") or []) if isinstance(entry, dict)]
    seen.append({"event_id": event_id, "incident_id": incident_id,
                 "status": status, "at": now})
    state["seen"] = seen[-MAX_SEEN_EVENTS:]


def _read_log_tail(path: Path) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > MAX_LOG_BYTES:
            stream.seek(size - MAX_LOG_BYTES)
        raw = stream.read()
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    if size > MAX_LOG_BYTES and lines:
        lines = lines[1:]
    return lines


def scan_log(log_path: Path, audit_dir: Path, *, state_path: Path | None = None,
             spawn: bool = False, dry_run: bool = False,
             now: float | None = None) -> dict[str, Any]:
    """Scan one monitor JSONL log and record newly confirmed incidents.

    The scanner is idempotent.  Event identities, not byte offsets, are the
    dedupe boundary so log trimming/rotation cannot cause either a repeated
    issue comment or a permanently missed event.
    """
    now = float(time.time() if now is None else now)
    log_path = Path(log_path)
    audit_dir = Path(audit_dir)
    state_path = Path(state_path) if state_path is not None else audit_dir / STATE_FILE
    result = {
        "status": "ok",
        "log": str(log_path),
        "audit_dir": str(audit_dir),
        "lines": 0,
        "malformed": 0,
        "candidates": 0,
        "duplicates": 0,
        "ignored": 0,
        "confirmed": 0,
        "recorded": 0,
        "reported": 0,
        "incidents": [],
    }
    if log_path.is_symlink() or not log_path.is_file():
        result.update(status="missing_log")
        return result
    state = load_state(state_path)
    seen_ids = {entry.get("event_id") for entry in state.get("seen", [])
                if isinstance(entry, dict)}
    try:
        lines = _read_log_tail(log_path)
    except OSError:
        result.update(status="unreadable_log")
        return result
    result["lines"] = len(lines)
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            result["malformed"] += 1
            continue
        if not isinstance(event, dict) or event.get("kind") != "deadline_misplacement_check":
            continue
        result["candidates"] += 1
        event_id = event_identity(event)
        if event_id in seen_ids:
            result["duplicates"] += 1
            continue
        confirmed = assess_event(event)
        if confirmed is None:
            result["ignored"] += 1
            if not dry_run:
                _remember(state, event_id, incident_id=None, status="ignored", now=now)
            seen_ids.add(event_id)
            continue
        result["confirmed"] += 1
        incident_id = f"soren-deadline-{event_id}"
        if dry_run:
            record = build_incident(confirmed, audit_dir, now=now)
            result["incidents"].append({
                "incident_id": incident_id,
                "verification": confirmed["verification"],
                "turn": (record.get("soren_event") or {}).get("turn"),
                "decision_x": (record.get("soren_event") or {}).get("decision_x"),
            })
        else:
            recorded = record_confirmed(audit_dir, confirmed, spawn=spawn, now=now)
            record = recorded["incident"]
            result["recorded"] += 1
            if recorded.get("status") == "reported":
                result["reported"] += 1
            result["incidents"].append({
                "incident_id": record.get("incident_id"),
                "verification": confirmed["verification"],
                "status": recorded.get("status"),
                "turn": (record.get("soren_event") or {}).get("turn"),
                "decision_x": (record.get("soren_event") or {}).get("decision_x"),
            })
            incident_id = record.get("incident_id") or incident_id
        if not dry_run:
            _remember(state, event_id, incident_id=incident_id, status="confirmed", now=now)
        seen_ids.add(event_id)
    if not dry_run:
        save_state(state_path, state)
    return result


def default_paths(soren_root: Path) -> tuple[Path, Path]:
    root = Path(soren_root)
    return root / MONITOR_LOG_REL, root / AUDIT_DIR_REL


def watch(log_path: Path, audit_dir: Path, *, interval: float = 60.0,
          spawn: bool = False, once: bool = False) -> int:
    while True:
        result = scan_log(log_path, audit_dir, spawn=spawn)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if once:
            return 0 if result.get("status") in {"ok", "missing_log"} else 2
        time.sleep(max(5.0, float(interval)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m docich.soren_deadline_audit")
    parser.add_argument("--soren-root", type=Path,
                        default=Path(os.environ.get("SOREN_ROOT", str(DEFAULT_SOREN_ROOT))),
                        help="soviet_now runtime root (default: SOREN_ROOT or /home/ubuntu/soren)")
    parser.add_argument("--log", type=Path, help="deadline monitor JSONL path")
    parser.add_argument("--audit-dir", type=Path, help="incident/state output directory")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan once and write incident evidence")
    scan.add_argument("--spawn", action="store_true",
                      help="also start the existing detached GitHub issue filer")
    scan.add_argument("--dry-run", action="store_true",
                      help="evaluate without writing state or incidents")
    watch_cmd = sub.add_parser("watch", help="poll the monitor log")
    watch_cmd.add_argument("--interval", type=float, default=60.0)
    watch_cmd.add_argument("--spawn", action="store_true",
                           help="also start the existing detached GitHub issue filer")
    watch_cmd.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    log_path, audit_dir = default_paths(args.soren_root)
    if args.log is not None:
        log_path = args.log
    if args.audit_dir is not None:
        audit_dir = args.audit_dir
    if args.command == "scan":
        result = scan_log(log_path, audit_dir, spawn=args.spawn, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") in {"ok", "missing_log"} else 2
    return watch(log_path, audit_dir, interval=args.interval,
                 spawn=args.spawn, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
