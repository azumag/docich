"""Offline core for the 参加型 TRPG / マーダーミステリー corner (Issue #684).

No Twitch I/O, no shell execution, no game switch, no production writes.
State machine + ledger + validation only. Wiring into a live corner tick
(reading the chat daemon, rendering the 960x540 view, TTS, GameSwitch) is a
separate, explicitly enabled step — see
``docs/features/participatory-trpg-murder-mystery-corner.md``.

Design reference: ``docs/features/participatory-trpg-murder-mystery-corner.md``
(Issue #684). Precedents: ``prediction_leaderboard.py`` (offline ledger),
``paper_corner.py`` / ``tsuitate_corner.py`` (state machine + persistence).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = 1
STATE_FILE = "trpg_corner_state.json"
LEDGER_FILE = "trpg_corner_ledger.json"
HISTORY_FILE = "trpg_corner_history.jsonl"
LOCK_FILE = "trpg_corner.lock"

# --- limits (fail-closed) ---
MAX_TEXT = 256
MAX_TITLE = 128
MAX_SCENARIO_ID = 64
MAX_GENRE = 64
MAX_SCENES = 64
MAX_SCENE_TEXT = 4096
MAX_CHOICES = 4
MIN_CHOICES = 2
MAX_CHOICE_TEXT = 512
MAX_TRICK_TEXT = 512
MAX_SUSPECTS = 16
MAX_PARTICIPANTS = 100000
MAX_VOTE_WINDOW_S = 600
MAX_ACCUSE_WINDOW_S = 600
MAX_CORNER_SECONDS = 600
MIN_CORNER_SECONDS = 30
MAX_HISTORY_ENTRIES = 500
MAX_BYTES = 256 * 1024
MAX_USER_ID = 64
MAX_VOTE_WINDOW_DEFAULT = 60
MAX_ACCUSE_WINDOW_DEFAULT = 120

PHASES = ("IDLE", "SCENE", "CHOICE", "RESOLVE", "CLIMAX", "REVEAL", "END", "FAILED", "TIMEOUT")
PHASE_SET = frozenset(PHASES)

TRANSITIONS: dict[str, frozenset[str]] = {
    "IDLE": frozenset({"SCENE"}),
    "SCENE": frozenset({"CHOICE", "FAILED", "TIMEOUT"}),
    "CHOICE": frozenset({"RESOLVE", "FAILED", "TIMEOUT"}),
    "RESOLVE": frozenset({"SCENE", "CLIMAX", "END", "FAILED", "TIMEOUT"}),
    "CLIMAX": frozenset({"REVEAL", "FAILED", "TIMEOUT"}),
    "REVEAL": frozenset({"END", "FAILED", "TIMEOUT"}),
    "END": frozenset({"IDLE"}),
    "FAILED": frozenset({"IDLE"}),
    "TIMEOUT": frozenset({"IDLE", "FAILED"}),
}

GENRES = frozenset({"trpg", "murder_mystery", "both"})
OUTCOMES = frozenset({"completed", "failed", "timeout", "aborted"})


class TrpgCornerError(ValueError):
    """The scenario or state violates the corner contract."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, what: str, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= limit):
        raise TrpgCornerError(f"invalid {what}")
    return value


def _int(value: Any, what: str, *, lo: int | None = None, hi: int | None = None) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise TrpgCornerError(f"invalid {what}")
    if lo is not None and value < lo:
        raise TrpgCornerError(f"invalid {what}")
    if hi is not None and value > hi:
        raise TrpgCornerError(f"invalid {what}")
    return value


def _dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise TrpgCornerError(f"invalid {what}")
    return value


def _scene(state: dict) -> dict:
    scen = state.get("scenario")
    if not isinstance(scen, dict):
        raise TrpgCornerError("state has no scenario")
    sid = state.get("scene_id")
    for s in scen.get("scenes", []):
        if s.get("id") == sid:
            return s
    raise TrpgCornerError("unknown current scene")


def _user(value: Any) -> str:
    return _text(value, "user id", limit=MAX_USER_ID)


# ---------------------------------------------------------------------------
# scenario validation
# ---------------------------------------------------------------------------


def _validate_climax(raw: Any) -> dict:
    raw = _dict(raw, "climax")
    suspects_raw = raw.get("suspects")
    if not isinstance(suspects_raw, list) or not (1 <= len(suspects_raw) <= MAX_SUSPECTS):
        raise TrpgCornerError("invalid suspects")
    suspects = [_text(s, "suspect", limit=MAX_TEXT) for s in suspects_raw]
    if len(set(suspects)) != len(suspects):
        raise TrpgCornerError("duplicate suspect")
    answer = _text(raw.get("answer"), "climax answer", limit=MAX_TEXT)
    if answer not in suspects:
        raise TrpgCornerError("invalid climax answer")
    trick = raw.get("trick", "")
    if trick is None:
        trick = ""
    trick = _text(trick, "climax trick", limit=MAX_TRICK_TEXT) if trick != "" else ""
    # allow empty trick but keep type check
    if not isinstance(trick, str):
        raise TrpgCornerError("invalid trick")
    window = _int(raw.get("window_seconds", MAX_ACCUSE_WINDOW_DEFAULT), "climax window",
                  lo=1, hi=MAX_ACCUSE_WINDOW_S)
    return {"suspects": suspects, "answer": answer, "trick": trick, "window_seconds": window}


def validate_scenario(raw: Any) -> dict:
    """Validate and normalize a scenario definition. Fail-closed."""
    raw = _dict(raw, "scenario")
    sid = _text(raw.get("id"), "scenario id", limit=MAX_SCENARIO_ID)
    title = _text(raw.get("title"), "scenario title", limit=MAX_TITLE)
    genre = raw.get("genre", "both")
    if genre not in GENRES:
        raise TrpgCornerError("invalid scenario genre")
    duration = _int(raw.get("duration_minutes", 8), "duration_minutes",
                    lo=1, hi=MAX_CORNER_SECONDS // 60)
    # scenes
    scenes_raw = raw.get("scenes")
    if not isinstance(scenes_raw, list) or not (1 <= len(scenes_raw) <= MAX_SCENES):
        raise TrpgCornerError("invalid scenes")
    scenes = []
    seen_ids: set[str] = set()
    for s in scenes_raw:
        s = _dict(s, "scene")
        sid_s = _text(s.get("id"), "scene id", limit=MAX_SCENARIO_ID)
        if sid_s in seen_ids:
            raise TrpgCornerError("duplicate scene id")
        seen_ids.add(sid_s)
        text = _text(s.get("text"), "scene text", limit=MAX_SCENE_TEXT)
        choices_raw = s.get("choices")
        if not isinstance(choices_raw, list) or not (MIN_CHOICES <= len(choices_raw) <= MAX_CHOICES):
            raise TrpgCornerError("invalid scene choices")
        choices = [_text(c, "choice text", limit=MAX_CHOICE_TEXT) for c in choices_raw]
        nxt = s.get("next", {})
        if not isinstance(nxt, dict):
            raise TrpgCornerError("invalid scene next")
        next_map: dict[str, str | None] = {}
        valid_keys = {str(i) for i in range(1, len(choices) + 1)}
        for k, v in nxt.items():
            if k not in valid_keys:
                raise TrpgCornerError("invalid next key")
            if v is None:
                next_map[k] = None
            else:
                next_map[k] = _text(v, "next scene id", limit=MAX_SCENARIO_ID)
        scenes.append({"id": sid_s, "text": text, "choices": choices, "next": next_map})
    # validate next targets exist
    all_ids = {s["id"] for s in scenes}
    for s in scenes:
        for v in s["next"].values():
            if v is not None and v not in all_ids:
                raise TrpgCornerError("unknown next scene")
    # climax (optional)
    climax_raw = raw.get("climax")
    climax = None if climax_raw is None else _validate_climax(climax_raw)
    return {
        "id": sid,
        "title": title,
        "genre": genre,
        "duration_minutes": duration,
        "scenes": scenes,
        "climax": climax,
        "first_scene": scenes[0]["id"],
    }


# ---------------------------------------------------------------------------
# state factory + transitions
# ---------------------------------------------------------------------------


def new_state() -> dict:
    return {
        "schema": SCHEMA,
        "phase": "IDLE",
        "scenario": None,
        "scenario_id": None,
        "scene_id": None,
        "started_at": None,
        "phase_started_at": None,
        "vote_window_seconds": None,
        "choice_opened_at": None,
        "climax_window_seconds": None,
        "climax_opened_at": None,
        "votes": {},          # choice(str) -> list[user_id]
        "vote_order": {},     # user_id -> choice(str)  (last vote wins)
        "accusations": {},    # user_id -> {"suspect":..., "trick":...}
        "tally": None,
        "climax_result": None,
        "scores": {},
        "failure_reason": None,
        "ended_at": None,
        "outcome": None,
    }


def _transition(state: dict, to: str) -> dict:
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase not in PHASE_SET:
        raise TrpgCornerError("invalid state phase")
    if to not in TRANSITIONS.get(phase, frozenset()):
        raise TrpgCornerError(f"invalid transition {phase} -> {to}")
    state["phase"] = to
    state["phase_started_at"] = _now()
    return state


def start(state: Any, scenario: Any, now: str | None = None) -> dict:
    """IDLE -> SCENE (first scene). Validates the scenario."""
    state = _dict(state, "state")
    if state.get("phase") != "IDLE":
        raise TrpgCornerError("start requires IDLE phase")
    scen = validate_scenario(scenario)
    ts = now or _now()
    state["scenario"] = scen
    state["scenario_id"] = scen["id"]
    state["scene_id"] = scen["first_scene"]
    state["started_at"] = ts
    state["phase"] = "SCENE"
    state["phase_started_at"] = ts
    state["vote_window_seconds"] = None
    state["choice_opened_at"] = None
    state["climax_window_seconds"] = None
    state["climax_opened_at"] = None
    state["votes"] = {}
    state["vote_order"] = {}
    state["accusations"] = {}
    state["tally"] = None
    state["climax_result"] = None
    state["scores"] = {}
    state["failure_reason"] = None
    state["ended_at"] = None
    state["outcome"] = None
    return state


def open_choice(state: Any, window_seconds: int = MAX_VOTE_WINDOW_DEFAULT, now: str | None = None) -> dict:
    """SCENE -> CHOICE. Opens a vote window."""
    state = _dict(state, "state")
    if state.get("phase") != "SCENE":
        raise TrpgCornerError("open_choice requires SCENE phase")
    ws = _int(window_seconds, "window_seconds", lo=1, hi=MAX_VOTE_WINDOW_S)
    state["vote_window_seconds"] = ws
    state["choice_opened_at"] = now or _now()
    state["votes"] = {}
    state["vote_order"] = {}
    state["tally"] = None
    return _transition(state, "CHOICE")


def record_vote(state: Any, user_id: Any, choice: Any, now: str | None = None) -> dict:
    """Record a vote during CHOICE. Idempotent: a user's last vote wins."""
    state = _dict(state, "state")
    if state.get("phase") != "CHOICE":
        raise TrpgCornerError("record_vote requires CHOICE phase")
    uid = _user(user_id)
    scene = _scene(state)
    choices = scene["choices"]
    if isinstance(choice, bool) or type(choice) is not int:
        raise TrpgCornerError("invalid choice")
    if not (1 <= choice <= len(choices)):
        raise TrpgCornerError("invalid choice")
    ci = str(choice)
    # remove previous vote by this user
    prev = state["vote_order"].get(uid)
    if prev is not None and prev in state["votes"]:
        state["votes"][prev] = [u for u in state["votes"][prev] if u != uid]
        if not state["votes"][prev]:
            del state["votes"][prev]
    state["vote_order"][uid] = ci
    state["votes"].setdefault(ci, [])
    if uid not in state["votes"][ci]:
        state["votes"][ci].append(uid)
    if len(state["vote_order"]) > MAX_PARTICIPANTS:
        raise TrpgCornerError("too many participants")
    return state


def close_choice(state: Any, now: str | None = None) -> dict:
    """CHOICE -> RESOLVE. Computes the tally."""
    state = _dict(state, "state")
    if state.get("phase") != "CHOICE":
        raise TrpgCornerError("close_choice requires CHOICE phase")
    scene = _scene(state)
    tally = {}
    for i in range(1, len(scene["choices"]) + 1):
        tally[str(i)] = len(state["votes"].get(str(i), []))
    state["tally"] = tally
    return _transition(state, "RESOLVE")


def winning_choice(state: Any) -> int | None:
    """Return the choice index (1-based) with the most votes (tie -> lowest)."""
    state = _dict(state, "state")
    tally = state.get("tally") or {}
    scene = _scene(state)
    n_choices = len(scene["choices"])
    best = None
    best_n = -1
    for i in range(1, n_choices + 1):
        n = tally.get(str(i), 0)
        if n > best_n:
            best_n = n
            best = i
    if best_n <= 0:
        return None
    return best


def advance_scene(state: Any, scene_id: str | None = None, now: str | None = None) -> dict:
    """RESOLVE -> SCENE with the given (or resolved) next scene."""
    state = _dict(state, "state")
    if state.get("phase") != "RESOLVE":
        raise TrpgCornerError("advance_scene requires RESOLVE phase")
    scen = state.get("scenario")
    if not isinstance(scen, dict):
        raise TrpgCornerError("state has no scenario")
    if scene_id is None:
        cur = _scene(state)
        wc = winning_choice(state)
        if wc is None:
            nxt = None
        else:
            nxt = cur["next"].get(str(wc))
        scene_id = nxt
    if scene_id is None:
        # terminal for this branch: go to climax if present else end
        if scen.get("climax") is not None:
            return _transition(state, "CLIMAX")
        return _transition(state, "END")
    ids = {s["id"] for s in scen["scenes"]}
    if scene_id not in ids:
        raise TrpgCornerError("unknown scene")
    state["scene_id"] = scene_id
    state["votes"] = {}
    state["vote_order"] = {}
    state["tally"] = None
    return _transition(state, "SCENE")


def open_climax(state: Any, window_seconds: int = MAX_ACCUSE_WINDOW_DEFAULT, now: str | None = None) -> dict:
    """RESOLVE -> CLIMAX. Opens the accusation window."""
    state = _dict(state, "state")
    if state.get("phase") != "RESOLVE":
        raise TrpgCornerError("open_climax requires RESOLVE phase")
    scen = state.get("scenario")
    if not isinstance(scen, dict) or scen.get("climax") is None:
        raise TrpgCornerError("scenario has no climax")
    ws = _int(window_seconds, "window_seconds", lo=1, hi=MAX_ACCUSE_WINDOW_S)
    state["climax_window_seconds"] = ws
    state["climax_opened_at"] = now or _now()
    state["accusations"] = {}
    return _transition(state, "CLIMAX")


def record_accusation(state: Any, user_id: Any, suspect: Any, trick: Any = "", now: str | None = None) -> dict:
    """Record an accusation during CLIMAX. Last submission per user wins."""
    state = _dict(state, "state")
    if state.get("phase") != "CLIMAX":
        raise TrpgCornerError("record_accusation requires CLIMAX phase")
    uid = _user(user_id)
    scen = state.get("scenario")
    climax = scen.get("climax") if isinstance(scen, dict) else None
    if not isinstance(climax, dict):
        raise TrpgCornerError("scenario has no climax")
    suspects = climax["suspects"]
    suspect = _text(suspect, "suspect", limit=MAX_TEXT)
    if suspect not in suspects:
        raise TrpgCornerError("invalid suspect")
    trick_text = "" if trick is None or trick == "" else _text(trick, "trick", limit=MAX_TRICK_TEXT)
    state["accusations"][uid] = {"suspect": suspect, "trick": trick_text, "at": now or _now()}
    if len(state["accusations"]) > MAX_PARTICIPANTS:
        raise TrpgCornerError("too many participants")
    return state


def close_climax(state: Any, now: str | None = None) -> dict:
    """CLIMAX -> REVEAL. Computes who accused correctly."""
    state = _dict(state, "state")
    if state.get("phase") != "CLIMAX":
        raise TrpgCornerError("close_climax requires CLIMAX phase")
    scen = state.get("scenario")
    climax = scen.get("climax") if isinstance(scen, dict) else None
    if not isinstance(climax, dict):
        raise TrpgCornerError("scenario has no climax")
    answer = climax["answer"]
    correct_users = [u for u, a in state["accusations"].items() if a.get("suspect") == answer]
    state["climax_result"] = {
        "answer": answer,
        "trick": climax.get("trick", ""),
        "total_accusations": len(state["accusations"]),
        "correct_count": len(correct_users),
        "correct_users": correct_users,
    }
    # simple scoring: correct accusation = 10 points
    for u in correct_users:
        state["scores"][u] = state["scores"].get(u, 0) + 10
    return _transition(state, "REVEAL")


def finish(state: Any, outcome: str = "completed", now: str | None = None) -> dict:
    """REVEAL/END -> END (terminal). Sets outcome + ended_at."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase == "REVEAL":
        _transition(state, "END")
    elif phase != "END":
        raise TrpgCornerError("finish requires REVEAL or END phase")
    if outcome not in OUTCOMES:
        raise TrpgCornerError("invalid outcome")
    state["outcome"] = outcome
    state["ended_at"] = now or _now()
    return state


def fail(state: Any, reason: str, now: str | None = None) -> dict:
    """Any active phase -> FAILED."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase in ("IDLE", "END", "FAILED", "TIMEOUT"):
        raise TrpgCornerError("fail requires an active phase")
    if phase not in TRANSITIONS or "FAILED" not in TRANSITIONS.get(phase, frozenset()):
        raise TrpgCornerError("fail not allowed from this phase")
    reason = _text(reason, "failure reason", limit=MAX_TEXT)
    state["failure_reason"] = reason
    state["outcome"] = "failed"
    state["ended_at"] = now or _now()
    return _transition(state, "FAILED")


def timeout(state: Any, now: str | None = None) -> dict:
    """Any active phase -> TIMEOUT."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase in ("IDLE", "END", "FAILED", "TIMEOUT"):
        raise TrpgCornerError("timeout requires an active phase")
    if "TIMEOUT" not in TRANSITIONS.get(phase, frozenset()):
        raise TrpgCornerError("timeout not allowed from this phase")
    state["outcome"] = "timeout"
    state["ended_at"] = now or _now()
    return _transition(state, "TIMEOUT")


def reset(state: Any, now: str | None = None) -> dict:
    """FAILED/TIMEOUT/END -> IDLE. Clears runtime state, keeps nothing."""
    state = _dict(state, "state")
    if state.get("phase") not in ("FAILED", "TIMEOUT", "END"):
        raise TrpgCornerError("reset requires a terminal phase")
    state.clear()
    state.update(new_state())
    return state


# ---------------------------------------------------------------------------
# parsing helpers (chat input)
# ---------------------------------------------------------------------------


def parse_vote(text: Any) -> int | None:
    """Parse a chat line into a 1-based choice index, or None."""
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if t.startswith("!"):
        t = t[1:].strip()
    # accept "!1", "1", "!vote 1"
    parts = t.split()
    if len(parts) == 2 and parts[0] in ("vote", "v"):
        t = parts[1]
    elif len(parts) > 1:
        return None
    if t.isdigit():
        try:
            choice = int(t)
        except ValueError:
            return None
        if 1 <= choice <= MAX_CHOICES:
            return choice
    return None


def parse_accusation(text: Any) -> tuple[str, str] | None:
    """Parse '!accuse <suspect> <trick...>' into (suspect, trick) or None."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t.lower().startswith("!accuse"):
        return None
    rest = t[len("!accuse"):].strip()
    if not rest:
        return None
    parts = rest.split(None, 1)
    suspect = parts[0]
    trick = parts[1].strip() if len(parts) > 1 else ""
    if not (1 <= len(suspect) <= MAX_TEXT):
        return None
    if len(trick) > MAX_TRICK_TEXT:
        return None
    return suspect, trick


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


def new_ledger() -> dict:
    return {"schema": SCHEMA, "entries": []}


def _trim_ledger(ledger: dict) -> None:
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return
    # trim by count
    while len(entries) > MAX_HISTORY_ENTRIES:
        entries.pop(0)
    # trim by size
    while entries:
        try:
            size = len(json.dumps(ledger, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            break
        if size <= MAX_BYTES:
            break
        entries.pop(0)


def record_run(ledger: Any, state: Any, outcome: str | None = None, now: str | None = None) -> bool:
    """Append a run summary from a terminal state. Returns True if recorded."""
    ledger = _dict(ledger, "ledger")
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase not in ("END", "FAILED", "TIMEOUT"):
        return False
    if "entries" not in ledger or not isinstance(ledger["entries"], list):
        ledger["entries"] = []
    if outcome is None:
        outcome = state.get("outcome") or ("completed" if phase == "END" else "failed" if phase == "FAILED" else "timeout")
    if outcome not in OUTCOMES:
        raise TrpgCornerError("invalid outcome")
    scen = state.get("scenario") or {}
    participants = len(state.get("vote_order") or {}) + len(state.get("accusations") or {})
    # count unique participants across votes + accusations
    uniq = set((state.get("vote_order") or {}).keys()) | set((state.get("accusations") or {}).keys())
    participants = len(uniq)
    votes_total = sum(len(v) for v in (state.get("votes") or {}).values())
    entry = {
        "scenario_id": state.get("scenario_id"),
        "title": scen.get("title") if isinstance(scen, dict) else None,
        "started_at": state.get("started_at"),
        "ended_at": now or state.get("ended_at") or _now(),
        "outcome": outcome,
        "phase": phase,
        "participants": participants,
        "votes_total": votes_total,
        "accusations": len(state.get("accusations") or {}),
        "climax_result": state.get("climax_result"),
        "scores": dict(state.get("scores") or {}),
        "failure_reason": state.get("failure_reason"),
    }
    ledger["entries"].append(entry)
    _trim_ledger(ledger)
    return True


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def _lock_path(directory: Path) -> Path:
    return directory / LOCK_FILE


def _atomic_write(path: Path, payload: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def load_state(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return new_state()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return new_state()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return new_state()
    # shallow merge into a fresh template to fill missing keys
    base = new_state()
    base.update(data)
    return base


def save_state(path: Any, state: Any) -> None:
    path = Path(path)
    state = _dict(state, "state")
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write(path, payload + "\n")


def load_ledger(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return new_ledger()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return new_ledger()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return new_ledger()
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    base = new_ledger()
    base.update(data)
    return base


def save_ledger(path: Any, ledger: Any) -> None:
    path = Path(path)
    ledger = _dict(ledger, "ledger")
    payload = json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write(path, payload + "\n")


def append_history_jsonl(path: Any, entry: Mapping[str, Any]) -> None:
    """Append one run summary as a JSONL line (append-only log)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(entry), ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def with_lock(directory: Any, fn, *args, **kwargs):
    """Run fn under an exclusive lock in directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(directory)
    with lock.open("w", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            return fn(*args, **kwargs)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
