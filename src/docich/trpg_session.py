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


def prepare(g, spec, *, now=None):
    now = time.time() if now is None else now
    state = core.new_state()
    core.start(state, scenario(g), now=iso(now))
    chat = chat_path(g)
    try:
        with chat.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            stream.seek(max(0, stat.st_size - 1))
            partial = bool(stat.st_size and stream.read(1) != b"\n")
        cursor = {"inode": stat.st_ino, "offset": stat.st_size, "discard": partial}
    except FileNotFoundError:
        cursor = {"inode": None, "offset": 0, "discard": False}
    data = {"schema": 1, **identity(spec), "state": state, "phase_at": now,
            "cursor": cursor, "announced": None, "intro_sent": False, "recorded": False}
    atomic_write_json(spec.runtime_dir / SESSION_FILE, data)
    return data


def load(path, expected):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise core.TrpgCornerError("trpg session is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema") != 1
            or any(data.get(key) != expected.get(key) for key in IDENTITY_KEYS)
            or type(data.get("generation")) is not int
            or not isinstance(data.get("state"), dict)
            or data["state"].get("phase") not in core.PHASE_SET):
        raise core.TrpgCornerError("trpg session identity is invalid")
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


def consume(g, data, now):
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
                return
            stream.seek(cursor["offset"])
            raw = stream.read(READ_BYTES)
    except FileNotFoundError:
        return
    newline = raw.find(b"\n")
    if cursor["discard"]:
        cursor["offset"] += len(raw) if newline < 0 else newline + 1
        cursor["discard"] = newline < 0
        return
    complete = raw.split(b"\n")[:-1][:MAX_LINES]
    if not complete and len(raw) == READ_BYTES:
        cursor.update(offset=cursor["offset"] + len(raw), discard=True)
        return
    for line in complete:
        parsed = parse_chat(line.decode("utf-8", errors="replace"))
        if parsed:
            try:
                core.observe_chat(data["state"], *parsed, now=iso(now))
            except core.TrpgCornerError:
                pass
        cursor["offset"] += len(line) + 1


def public(data, *, now=None):
    state = data["state"]
    scene = next(s for s in state["scenario"]["scenes"] if s["id"] == state["scene_id"])
    phase = state["phase"]
    window = (state.get("vote_window_seconds") if phase == "CHOICE" else
              state["scenario"]["climax"]["window_seconds"] if phase == "CLIMAX" else 0)
    remaining = max(0, int(data["phase_at"] + (window or 0) - (time.time() if now is None else now)))
    out = {"ok": True, **{key: data[key] for key in IDENTITY_KEYS}, "phase": phase,
           "title": state["scenario"]["title"], "scene_text": scene["text"],
           "choices": scene["choices"], "votes": {str(i): len(state["votes"].get(str(i), []))
                                                    for i in range(1, len(scene["choices"]) + 1)},
           "suspects": (state["scenario"].get("climax") or {}).get("suspects", []),
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


def tick(g, path, expected, *, audio_enabled=False, now=None, stop_reason=None):
    now = time.time() if now is None else now
    data = load(path, expected)
    state = data["state"]
    phase = state["phase"]
    if stop_reason and phase not in TERMINAL:
        if stop_reason == "duration":
            core.timeout(state, now=iso(now))
        else:
            core.fail(state, "manual-stop", now=iso(now))
    elif phase not in TERMINAL:
        delay = (state["vote_window_seconds"] if phase == "CHOICE" else
                 state["scenario"]["climax"]["window_seconds"] if phase == "CLIMAX" else
                 HOLD_SECONDS.get(phase, 0))
        if now - data["phase_at"] >= delay:
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
        consume(g, data, now)
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
