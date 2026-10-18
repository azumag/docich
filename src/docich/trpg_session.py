"""One generation-owned TRPG session, driven only by its corner manager."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import tomllib

from . import trpg_corner as core, trpg_narration as narration
from .game_switch import atomic_write_json
from .naming import runtime_directory
from .trading.soren_output import resolve_soren_root

SESSION_FILE = "trpg_session.json"
MAX_BYTES = 2 * 1024 * 1024
READ_BYTES = 64 * 1024
MAX_LINES = 128
HOLD_SECONDS = {"SCENE": 3, "RESOLVE": 3, "REVEAL": 10}
TERMINAL = {"END", "FAILED", "TIMEOUT"}
IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")


def settings(g):
    raw = tomllib.loads(Path(g.config_path).read_text(encoding="utf-8"))
    section = raw.get("trpg_corner", {})
    if not isinstance(section, dict):
        raise core.TrpgCornerError("invalid trpg settings")
    return section


def scenario(g):
    # No generated or implicit story: #684 requires human-authored, reviewed input.
    name = settings(g).get("scenario_file")
    if not isinstance(name, str) or not name.strip():
        raise core.TrpgCornerError("reviewed trpg scenario_file is required")
    path = Path(name)
    if not path.is_absolute():
        path = Path(g.repo_root) / path
    if path.stat().st_size > core.MAX_BYTES:
        raise core.TrpgCornerError("trpg scenario is too large")
    return core.validate_scenario(json.loads(path.read_text(encoding="utf-8")))


def chat_path(g):
    name = settings(g).get("chat_file")
    if name is None:
        return resolve_soren_root(g) / "tmp/.twitch_chat/raw.log"
    if not isinstance(name, str) or not name:
        raise core.TrpgCornerError("invalid trpg chat_file")
    path = Path(name)
    return path if path.is_absolute() else Path(g.repo_root) / path


def iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def identity(spec):
    return {key: getattr(spec, key) for key in IDENTITY_KEYS}


def cursor_at_end(g):
    try:
        with chat_path(g).open("rb") as stream:
            stat = os.fstat(stream.fileno())
            stream.seek(max(0, stat.st_size - 1))
            partial = bool(stat.st_size and stream.read(1) != b"\n")
        return {"inode": stat.st_ino, "offset": stat.st_size, "discard": partial}
    except FileNotFoundError:
        return {"inode": None, "offset": 0, "discard": False}


def prepare(g, spec, *, now=None):
    # Materialization prepares a view, not a running clock or an open ballot.
    bound = identity(spec)
    data = {"schema": 1, **bound, "state": core.new_state(), "scenario": scenario(g),
            "phase_at": None, "activated_at": None, "cursor": cursor_at_end(g),
            "announced": None, "intro_sent": False, "recorded": False,
            "view_identity": bound, "owners": [bound], "owner_request_id": None}
    atomic_write_json(spec.runtime_dir / SESSION_FILE, data)
    return data


def load(path, expected, *, view=False, related=False):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise core.TrpgCornerError("trpg session is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    binding = data.get("view_identity") if view and isinstance(data, dict) else data
    same_runtime = isinstance(data, dict) and all(
        data.get(key) == expected.get(key) for key in IDENTITY_KEYS[:-1])
    if related and same_runtime and expected in data.get("owners", []):
        binding = expected
    if (not isinstance(data, dict) or data.get("schema") != 1
            or not isinstance(binding, dict)
            or any(binding.get(key) != expected.get(key) for key in IDENTITY_KEYS)
            or view and (not same_runtime or
                         {key: data.get(key) for key in IDENTITY_KEYS} not in data.get("owners", []))
            or type(data.get("generation")) is not int
            or not isinstance(data.get("state"), dict)
            or data["state"].get("phase") not in core.PHASE_SET):
        raise core.TrpgCornerError("trpg session identity is invalid")
    return data


def restore(g, spec, previous, *, live=False):
    """Rebind only the coordinator's proven previous TRPG session."""
    source = {key: previous[key] for key in IDENTITY_KEYS}
    path = runtime_directory(g.state_dir, source["runtime_id"]) / SESSION_FILE
    data = load(path, source, related=True)
    # A live server keeps its launch binding; its API reports the renewed lease.
    bound = identity(spec)
    data.update(bound)
    if bound not in data["owners"]:
        data["owners"] = (data["owners"] + [bound])[-32:]
    if not live:
        data["view_identity"] = bound
    atomic_write_json(spec.runtime_dir / SESSION_FILE, data)
    return data


def parse_chat(line):
    """Existing daemon metadata line, or the existing structured JSONL contract."""
    if len(line) > 4096:
        return None
    if line.startswith("{"):
        try:
            row = json.loads(line)
        except ValueError:
            return None
        if not isinstance(row, dict):
            return None
        uid, text = row.get("user_id"), row.get("text")
    else:
        fields = line.split("\t", 5)
        if len(fields) != 6 or not fields[1].startswith("user-id="):
            return None
        uid = fields[1][8:]
        if not uid.isascii() or not uid.isdecimal() or "self" in fields[4].removeprefix("flags=").split(","):
            return None
        _, separator, text = fields[5].partition(": ")
        if not separator:
            return None
    if (not isinstance(uid, str) or not 1 <= len(uid) <= core.MAX_USER_ID
            or not isinstance(text, str) or len(text) > 1024):
        return None
    return uid, text


def consume(g, data, now, *, through=None):
    path = chat_path(g)
    cursor = data["cursor"]
    try:
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            if cursor["inode"] is None:
                cursor.update(inode=stat.st_ino, offset=0, discard=False)
            elif stat.st_ino != cursor["inode"] or stat.st_size < cursor["offset"]:
                # raw.log rotation may retain already-consumed history. Never
                # reinterpret that tail as fresh votes in a later phase.
                stream.seek(max(0, stat.st_size - 1))
                partial = bool(stat.st_size and stream.read(1) != b"\n")
                cursor.update(inode=stat.st_ino, offset=stat.st_size, discard=partial)
                return True
            stream.seek(cursor["offset"])
            limit = through["offset"] if through is not None else stat.st_size
            if through is not None and stat.st_ino != through["inode"]:
                return True  # the collector rotated away the closing snapshot
            raw = stream.read(min(READ_BYTES, max(0, limit - cursor["offset"])))
    except FileNotFoundError:
        return True
    newline = raw.find(b"\n")
    read_end = cursor["offset"] + len(raw)
    if cursor["discard"]:
        cursor["offset"] += len(raw) if newline < 0 else newline + 1
        cursor["discard"] = newline < 0
        return cursor["offset"] >= limit
    complete = raw.split(b"\n")[:-1][:MAX_LINES]
    if not complete and len(raw) == READ_BYTES:
        cursor.update(offset=cursor["offset"] + len(raw), discard=True)
        return cursor["offset"] >= limit
    for line in complete:
        parsed = parse_chat(line.decode("utf-8", errors="replace"))
        if parsed:
            try:
                core.observe_chat(data["state"], *parsed, now=iso(now))
            except core.TrpgCornerError:
                pass
        cursor["offset"] += len(line) + 1
    if through is not None and read_end == limit:
        # An unterminated message at the close boundary is not a complete vote.
        if len(complete) < MAX_LINES:
            cursor["offset"] = limit
    return cursor["offset"] >= limit


def public(data, *, now=None):
    state = data["state"]
    story = state.get("scenario") or data["scenario"]
    scene = next(s for s in story["scenes"] if s["id"] == (state["scene_id"] or story["first_scene"]))
    phase = state["phase"]
    window = (state.get("vote_window_seconds") if phase == "CHOICE" else
              story["climax"]["window_seconds"] if phase == "CLIMAX" else 0)
    remaining = max(0, int((data["phase_at"] or 0) + (window or 0) - (time.time() if now is None else now)))
    out = {"ok": True, **{key: data[key] for key in IDENTITY_KEYS}, "phase": phase,
           "title": story["title"], "scene_text": scene["text"] if phase != "IDLE" else "",
           "choices": scene["choices"], "votes": {str(i): len(state["votes"].get(str(i), []))
                                                    for i in range(1, len(scene["choices"]) + 1)},
           "suspects": (story.get("climax") or {}).get("suspects", []),
           "participants": len(set(state["vote_order"]) | set(state["accusations"])),
           "accusations": len(state["accusations"]), "remaining_seconds": remaining}
    if phase in {"REVEAL", "END"}:
        result = state.get("climax_result") or {}
        out["answer"] = result.get("answer", "")
        out["trick"] = result.get("trick", "")
        out["correct_count"] = result.get("correct_count", 0)
    return out


def announce(g, data, audio_enabled, path):
    state = data["state"]
    if state["phase"] in TERMINAL:
        return
    mark = [state["phase"], state["scene_id"], data["phase_at"]]
    if data["announced"] == mark:
        return
    intro = not data["intro_sent"]
    data.update(announced=mark, intro_sent=True)
    atomic_write_json(path, data)  # do not replay speech after a manager restart
    view = public(data)
    texts = {"SCENE": ("scene", narration.scene_text(state["scene_id"], view["scene_text"])),
             "CHOICE": ("choice", narration.choice_text()),
             "CLIMAX": ("accusation", narration.accusation_text()),
             "REVEAL": ("reveal", narration.reveal_text(view.get("answer", ""), view.get("trick", ""), ""))}
    messages = ([('intro', narration.intro_text())] if intro else [])
    if state["phase"] in texts:
        messages.append(texts[state["phase"]])
    for kind, text in messages:
        narration.publish(g, kind=kind, title=narration.TITLE, overlay_body=text,
                          speech_text=text, request_id=f"{data['runtime_id']}:{data['phase_at']}",
                          audio_enabled=audio_enabled)


def tick(g, path, expected, *, audio_enabled=False, now=None, stop_reason=None, owner_request_id=None):
    now = time.time() if now is None else now
    data = load(path, expected)
    state = data["state"]
    if owner_request_id is not None:
        if data["owner_request_id"] not in (None, owner_request_id):
            raise core.TrpgCornerError("trpg session request owner mismatch")
        data["owner_request_id"] = owner_request_id
    just_activated = state["phase"] == "IDLE"
    if just_activated:
        core.start(state, data["scenario"], now=iso(now))
        data.update(activated_at=now, phase_at=now, cursor=cursor_at_end(g))
    phase = state["phase"]
    if stop_reason and phase not in TERMINAL:
        if stop_reason == "duration":
            core.timeout(state, now=iso(now))
        else:
            core.fail(state, "manual-stop", now=iso(now))
    elif phase not in TERMINAL and not just_activated:
        delay = (state["vote_window_seconds"] if phase == "CHOICE" else
                 state["scenario"]["climax"]["window_seconds"] if phase == "CLIMAX" else
                 HOLD_SECONDS.get(phase, 0))
        due = now - data["phase_at"] >= delay
        drained = True
        if phase in {"CHOICE", "CLIMAX"}:
            if due and "closing" not in data:
                # raw.log has no receive timestamps. The first deadline poll
                # freezes a byte cutoff; later appends cannot extend it.
                data["closing"] = cursor_at_end(g)
            drained = consume(g, data, now, through=data.get("closing"))
        if due and drained:
            if phase == "SCENE":
                core.open_choice(state, now=iso(now))
            elif phase == "CHOICE":
                core.close_choice(state, now=iso(now))
            elif phase == "RESOLVE":
                core.advance_scene(state, now=iso(now))
                if state["phase"] == "END":
                    core.finish(state, now=iso(now))
            elif phase == "CLIMAX":
                core.close_climax(state, now=iso(now))
            elif phase == "REVEAL":
                core.finish(state, now=iso(now))
            data["phase_at"] = now
            data.pop("closing", None)
            if state["phase"] in {"CHOICE", "CLIMAX"}:
                # New windows start strictly after this observed byte boundary.
                data["cursor"] = cursor_at_end(g)
    announce(g, data, audio_enabled, path)
    if state["phase"] in TERMINAL and not data["recorded"]:
        def record():
            directory = Path(g.state_dir) / "trpg"
            ledger_path = directory / core.LEDGER_FILE
            ledger = core.load_ledger(ledger_path)
            core.record_run(ledger, state, outcome="aborted" if stop_reason == "manual" else None)
            core.save_ledger(ledger_path, ledger)
        core.with_lock(Path(g.state_dir) / "trpg", record)
        data["recorded"] = True
    atomic_write_json(path, data)
    return state["phase"] in TERMINAL
