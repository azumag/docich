"""Offline #380 acceptance skeleton. Never imports or executes bundle code.

Only the repository's trusted key/switch engine runs. Generated execution is
unconditionally disabled until an independently reviewed OS sandbox exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

VERSIONS = {"schema": 1, "engine": "key-switch-v1", "judge": "key-switch-v1"}
MAX_BUNDLE = 256 * 1024
MAX_TICKS = 2400
WALL_SECONDS = 240
CONTROLS = "Arrow keys; 1 cell / 4 ticks. K=key, S=switch, E=exit, P=player. Collect K and touch S, then reach E. Green gates are open."
BUTTONS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


class Rejected(ValueError):
    """A fixed, public reason code; never includes bundle or input text."""


def _require(condition, reason):
    if not condition:
        raise Rejected(reason)


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate_field")
            result[key] = value
        return result
    def nonfinite(_):
        raise Rejected("nonfinite")
    try:
        return json.loads(data.decode("utf-8", "strict"), object_pairs_hook=pairs,
                          parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Rejected("invalid_json") from exc


def _fields(value, fields):
    _require(type(value) is dict and set(value) == set(fields), "invalid_fields")


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high, "invalid_integer")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class Rules:
    cells: tuple[str, ...]
    player: tuple[int, int]
    key: tuple[int, int]
    switch: tuple[int, int]
    exit: tuple[int, int]
    gates: tuple[tuple[tuple[int, int], tuple[str, ...]], ...]

    @classmethod
    def parse(cls, data):
        _fields(data, ("cells", "player", "key", "switch", "exit", "gates"))
        cells = data["cells"]
        _require(type(cells) is list and len(cells) == 12 and all(
            type(row) is str and len(row) == 16 and set(row) <= {"#", "."}
            for row in cells), "invalid_cells")
        ids, occupied = set(), set()

        def entity(value, gate=False):
            _fields(value, ("id", "cell", "requires") if gate else ("id", "cell"))
            name, cell = value["id"], value["cell"]
            _require(type(name) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name)
                     and name not in ids, "invalid_id")
            ids.add(name)
            _require(type(cell) is list and len(cell) == 2, "invalid_cell")
            _integer(cell[0], 0, 15)
            _integer(cell[1], 0, 11)
            point = tuple(cell)
            _require(cells[point[1]][point[0]] == "." and point not in occupied, "invalid_cell")
            occupied.add(point)
            return point

        points = [entity(data[name]) for name in ("player", "key", "switch", "exit")]
        _require(type(data["gates"]) is list and len(data["gates"]) <= 2, "invalid_gates")
        gates = []
        for gate in data["gates"]:
            point = entity(gate, True)
            needs = gate["requires"]
            _require(type(needs) is list and 1 <= len(needs) <= 2 and all(
                type(x) is str and x in {"key", "switch"} for x in needs)
                and len(set(needs)) == len(needs), "invalid_condition")
            gates.append((point, tuple(sorted(needs))))
        return cls(tuple(cells), *points, tuple(gates))


@dataclass(frozen=True)
class Artifact:
    """Immutable byte snapshot, including the manifest; hashes are identity, not safety."""
    files: tuple[tuple[str, bytes], ...]
    identity: str
    artifact_id: str
    seed: int
    rules: Rules

    @classmethod
    def load(cls, root: Path):
        try:
            files = {}
            size = 0
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

            def read_files(directory, prefix=""):
                nonlocal size
                # Open relative to pinned directory descriptors. No symlink/FIFO,
                # nested directories, or paths supplied by the manifest are followed.
                with os.scandir(directory) as entries:
                    for entry in entries:
                        name = prefix + entry.name
                        if name == "assets":
                            child = os.open(entry.name, flags | os.O_DIRECTORY, dir_fd=directory)
                            try:
                                read_files(child, "assets/")
                            finally:
                                os.close(child)
                            continue
                        _require(name in {"game.mjs", "rules.json", "manifest.json"} or
                                 re.fullmatch(r"assets/[a-zA-Z0-9_-]+\.(png|txt)", name), "invalid_bundle")
                        _require(len(files) < 32, "invalid_bundle")
                        fd = os.open(entry.name, flags, dir_fd=directory)
                        try:
                            _require(stat.S_ISREG(os.fstat(fd).st_mode), "invalid_bundle")
                            with os.fdopen(fd, "rb", closefd=False) as stream:
                                data = stream.read(MAX_BUNDLE + 1)
                        finally:
                            os.close(fd)
                        size += len(data)
                        _require(size <= MAX_BUNDLE, "bundle_limit")
                        files[name] = data
            directory = os.open(root, flags | os.O_DIRECTORY)
            try:
                read_files(directory)
            finally:
                os.close(directory)
            _require({"game.mjs", "rules.json", "manifest.json"} <= files.keys(), "missing_file")
            manifest = _json(files["manifest.json"])
            _fields(manifest, ("artifact_id", "versions", "seed", "tick_hz", "controls",
                               "goal", "max_ticks", "dependencies", "image_digest", "files"))
            _require(type(manifest["artifact_id"]) is str and re.fullmatch(
                r"[a-zA-Z0-9_-]{1,64}", manifest["artifact_id"]), "invalid_id")
            _require(_encode(manifest["versions"]) == _encode(VERSIONS), "version_mismatch")
            _integer(manifest["seed"], 0, 2**32 - 1)
            _require(type(manifest["tick_hz"]) is int and manifest["tick_hz"] == 20
                     and type(manifest["max_ticks"]) is int and manifest["max_ticks"] == MAX_TICKS
                     and manifest["controls"] == CONTROLS
                     and manifest["goal"] == "alive+key+switch+exit"
                     and manifest["dependencies"] == []
                     and manifest["image_digest"] is None, "invalid_contract")
            hashes = {name: _digest(data) for name, data in files.items() if name != "manifest.json"}
            _require(manifest["files"] == hashes, "file_hash_mismatch")
            identity = _digest(_encode({name: _digest(data) for name, data in files.items()}))
            return cls(tuple(sorted(files.items())), identity, manifest["artifact_id"],
                       manifest["seed"], Rules.parse(_json(files["rules.json"])))
        except OSError as exc:
            raise Rejected("unreadable_bundle") from exc

    def check(self, root):
        _require(Artifact.load(root).identity == self.identity, "artifact_changed")


def start_generated(root: Path):
    """No sandbox is supplied by this prototype. No callback or host fallback."""
    raise Rejected("sandbox_violation")


@dataclass(frozen=True)
class State:
    tick: int
    player: tuple[int, int]
    key: bool = False
    switch: bool = False
    gates: tuple[bool, ...] = ()
    alive: bool = True
    candidate_win: bool = False


def transition(rules: Rules, state: State, button: str | None):
    _require(button is None or button in BUTTONS, "invalid_button")
    _require(state.alive and not state.candidate_win and state.tick < MAX_TICKS, "terminal")
    tick, point = state.tick + 1, state.player
    if button is not None and tick % 4 == 0:
        dx, dy = BUTTONS[button]
        target = point[0] + dx, point[1] + dy
        blocked = {cell for (cell, _), opened in zip(rules.gates, state.gates) if not opened}
        if (0 <= target[0] < 16 and 0 <= target[1] < 12
                and rules.cells[target[1]][target[0]] == "." and target not in blocked):
            point = target
    key, switch = state.key or point == rules.key, state.switch or point == rules.switch
    opened = tuple(all(key if need == "key" else switch for need in needs) for _, needs in rules.gates)
    return State(tick, point, key, switch, opened, True, key and switch and point == rules.exit)


def state_hash(state):
    return _digest(_encode(asdict(state)))


def check_proposal(expected: State, proposal):
    """Pure future sandbox IPC boundary: only a complete matching transition passes."""
    try:
        _require(_encode(proposal) == _encode(asdict(expected)), "invalid_artifact")
    except (TypeError, ValueError) as exc:
        raise Rejected("invalid_artifact") from exc


def render_png(rules, state):
    """Trusted 640x480 front layer; no generated draw surface is accepted."""
    pixels = bytearray(640 * 480 * 3)

    def box(cell, color, margin=2):
        x, y = cell
        row = bytes(color) * (40 - 2 * margin)
        for yy in range(y * 40 + margin, (y + 1) * 40 - margin):
            start = (yy * 640 + x * 40 + margin) * 3
            pixels[start:start + len(row)] = row

    for y, row in enumerate(rules.cells):
        for x, tile in enumerate(row):
            box((x, y), (70, 70, 80) if tile == "#" else (25, 30, 40))
    glyphs = {"P": ("11110", "10001", "11110", "10000", "10000"),
              "K": ("10001", "10010", "11100", "10010", "10001"),
              "S": ("01111", "10000", "01110", "00001", "11110"),
              "E": ("11111", "10000", "11110", "10000", "11111")}

    def mark(cell, color, glyph):
        box(cell, color, 6)
        for y, row in enumerate(glyphs[glyph]):
            for x, bit in enumerate(row):
                if bit == "1":
                    for dy in range(3):
                        for dx in range(3):
                            at = ((cell[1] * 40 + 12 + y * 3 + dy) * 640
                                  + cell[0] * 40 + 12 + x * 3 + dx) * 3
                            pixels[at:at + 3] = b"\xff\xff\xff"
    mark(rules.exit, (20, 130, 60) if state.key and state.switch else (130, 40, 50), "E")
    if not state.key:
        mark(rules.key, (160, 130, 0), "K")
    mark(rules.switch, (20, 130, 60) if state.switch else (120, 40, 130), "S")
    for (cell, _), opened in zip(rules.gates, state.gates):
        box(cell, (20, 100, 60) if opened else (160, 40, 40), 5)
    mark(state.player, (20, 70, 170), "P")

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + pixels[y * 1920:(y + 1) * 1920] for y in range(480))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 480, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class MockCorner:
    """In-memory ownership/restore model, deliberately disconnected from production."""
    def __init__(self, restore_ok=True):
        self.owner = False
        self.previous_visible = True
        self.restore_ok = restore_ok
        self.events = []
        self.common_pid = 100
        self.children = ()  # No process is created by this implementation.

    def acquire(self):
        _require(not self.owner, "owner_busy")
        self.owner, self.previous_visible = True, False

    def finish(self):
        if not self.owner:
            return
        self.events.extend(("release_keys", "stop_and_reap", "release_owner", "restore"))
        self.owner = False
        self.previous_visible = self.restore_ok


class FixtureSession:
    """Trusted synthetic engine only. Solver surface is observation + strict bytes input.

    Artifact files are frozen in memory; disk is rechecked before play and replay.
    Host-side state/evidence are for the judge, never part of solver observation.
    """
    def __init__(self, root, artifact, corner=None, clock=time.monotonic):
        artifact.check(root)
        self.root, self.artifact = root, artifact
        self.corner = corner or MockCorner()
        self.clock, self.started = clock, clock()
        self.state = State(0, artifact.rules.player, gates=(False,) * len(artifact.rules.gates))
        self.seq, self.rejects = 0, 0
        self.inputs, self.hashes, self.refusals = [], [], []
        self.result = self.reason = None
        self.corner.acquire()

    @property
    def frame_id(self):
        return _digest(f"{self.artifact.identity}:{self.state.tick}:{state_hash(self.state)}".encode())

    def observation(self):
        return {"png": render_png(self.artifact.rules, self.state), "frame_id": self.frame_id,
                "instructions": CONTROLS, "remaining_ticks": MAX_TICKS - self.state.tick}

    def _finish(self, result, reason):
        if self.result is None:
            self.result, self.reason = result, reason
            self.corner.finish()

    def cancel(self):
        self._finish("not_cleared", "cancelled")

    def report(self):
        return {"result": self.result, "restored": self.corner.previous_visible,
                "success": self.result == "verified_win" and self.corner.previous_visible,
                "execution": "trusted_synthetic_fixture"}

    def expire_wall(self):
        if self.result is None and self.clock() - self.started >= WALL_SECONDS:
            self._finish("timeout", "wall_limit")
        return self.result is not None

    def submit(self, raw: bytes):
        if self.expire_wall() or self.state.candidate_win:
            return False
        try:
            _require(type(raw) is bytes and len(raw) <= 1024, "input_limit")
            value = _json(raw)
            _fields(value, ("frame_id", "seq", "buttons", "ticks"))
            _require(type(value["frame_id"]) is str and value["frame_id"] == self.frame_id, "stale_frame")
            _integer(value["seq"], 1, MAX_TICKS)
            _require(value["seq"] == self.seq + 1, "invalid_seq")
            _integer(value["ticks"], 1, 60)
            buttons = value["buttons"]
            _require(type(buttons) is list and len(buttons) <= 1 and all(
                type(b) is str and b in BUTTONS for b in buttons), "invalid_buttons")
        except Rejected as exc:
            # Refusal diagnostics are codes only; arbitrary solver text is not persisted.
            self.refusals.append(str(exc))
            self.inputs.append({"accepted": False, "start_tick": self.state.tick})
            self.rejects += 1
            if self.rejects == 3:
                self._finish("invalid_input", "three_rejections")
            return False
        self.inputs.append({"accepted": True, "start_tick": self.state.tick, **value})
        self.seq, self.rejects = value["seq"], 0
        for _ in range(value["ticks"]):
            # Pure fixture proposals are constructed from the trusted engine. Actual
            # generated transition proposals remain behind start_generated's closed gate.
            self.state = transition(self.artifact.rules, self.state, buttons[0] if buttons else None)
            self.hashes.append(state_hash(self.state))
            if self.state.candidate_win:
                break
            if self.state.tick == MAX_TICKS:
                self._finish("timeout", "tick_limit")
                break
        return True

    def evidence(self):
        # Return a detached snapshot, not mutable references into the judge.
        return _json(_encode({"artifact": self.artifact.identity, "versions": VERSIONS,
            "seed": self.artifact.seed, "inputs": self.inputs, "hashes": self.hashes,
            "cutoff_tick": self.state.tick,
            "reason": "candidate_win" if self.state.candidate_win else self.reason}))

    def verify(self):
        _require(self.state.candidate_win or self.result is not None, "not_terminal")
        if not verify_replay(self.root, self.artifact, self.evidence()):
            self.result, self.reason = "replay_mismatch", "replay_mismatch"
            self.corner.finish()
        elif self.state.candidate_win:
            self._finish("verified_win", "candidate_win")
        return self.result


def verify_replay(root, artifact, evidence):
    """Fresh pure trusted engine; not a claim of isolated generated-code replay."""
    try:
        _fields(evidence, ("artifact", "versions", "seed", "inputs", "hashes", "cutoff_tick", "reason"))
        _require(evidence["artifact"] == artifact.identity and
                 _encode(evidence["versions"]) == _encode(VERSIONS) and
                 type(evidence["seed"]) is int and evidence["seed"] == artifact.seed, "replay_mismatch")
        _require(type(evidence["inputs"]) is list and len(evidence["inputs"]) <= MAX_TICKS * 3
                 and type(evidence["hashes"]) is list, "replay_mismatch")
        replay = FixtureSession(root, artifact, clock=lambda: 0)
        for record in evidence["inputs"]:
            _require(replay.result is None and not replay.state.candidate_win, "replay_mismatch")
            _require(type(record) is dict and type(record.get("accepted")) is bool, "replay_mismatch")
            _integer(record.get("start_tick"), 0, MAX_TICKS)
            _require(record["start_tick"] == replay.state.tick, "replay_mismatch")
            if record["accepted"]:
                _fields(record, ("accepted", "start_tick", "frame_id", "seq", "buttons", "ticks"))
                raw = _encode({k: record[k] for k in ("frame_id", "seq", "buttons", "ticks")})
                _require(replay.submit(raw), "replay_mismatch")
            else:
                _fields(record, ("accepted", "start_tick"))
                replay.submit(b"invalid")
            _require(replay.inputs[-1] == record, "replay_mismatch")
        reason = evidence["reason"]
        if reason in {"wall_limit", "cancelled"}:
            _require(replay.result is None and not replay.state.candidate_win, "replay_mismatch")
            replay._finish("timeout" if reason == "wall_limit" else "not_cleared", reason)
        _require(type(evidence["cutoff_tick"]) is int and evidence["cutoff_tick"] == replay.state.tick
                 and evidence["hashes"] == replay.hashes
                 and reason == replay.evidence()["reason"]
                 and (replay.state.candidate_win or replay.result is not None), "replay_mismatch")
        artifact.check(root)
        return True
    except (Rejected, TypeError, ValueError, KeyError, IndexError):
        return False
