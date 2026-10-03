"""Durable NetHack expedition/run history for the long-running corner.

The game save is still NetHack's own source of truth. This module tracks the
program-level identity of an adventure and imports terminal facts from
NetHack's machine-readable xlogfile. It deliberately never invents a death
reason when xlog evidence is missing or ambiguous.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import math
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import GlobalConfig, load_game
from .game_switch import atomic_write_json

SCHEMA_VERSION = 1
PROGRESS_SCHEMA_VERSION = 1
MAX_PROGRESS_TRACE_BYTES = 2 * 1024 * 1024
MAX_PROGRESS_LINE_BYTES = 4096
ASCENDED_ACHIEVEMENT = 0x0100
AMULET_ACHIEVEMENT = 0x0020
TERMINAL_STATUSES = frozenset({"dead", "ascended", "ended", "ended_unknown"})
_PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,31}$")


class NethackRunError(RuntimeError):
    """Persistent run history cannot be trusted or safely updated."""


@dataclass(frozen=True)
class NethackPersistenceSettings:
    player_name: str
    save_dir: Path
    xlogfile: Path
    dump_dir: Path


def load_nethack_persistence_settings(
    g: GlobalConfig,
) -> NethackPersistenceSettings | None:
    """Return settings only when the canonical game explicitly opts in."""
    game = load_game(g, "nethack")
    if not isinstance(game.raw, dict):
        return None
    raw = game.raw.get("nethack")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise NethackRunError("[nethack] はtableである必要があります")
    persistent = raw.get("persistent_run", False)
    if type(persistent) is not bool:
        raise NethackRunError("nethack.persistent_run はtrue/falseで指定してください")
    if not persistent:
        return None

    player = raw.get("player_name", "docich")
    if not isinstance(player, str) or _PLAYER_RE.fullmatch(player) is None:
        raise NethackRunError(
            "nethack.player_name は1-31文字の英数字/underscoreで指定してください"
        )
    save_raw = raw.get("save_dir", "/var/games/nethack/save")
    if not isinstance(save_raw, str) or not save_raw:
        raise NethackRunError("nethack.save_dir が不正です")
    save_dir = Path(save_raw)
    if not save_dir.is_absolute():
        raise NethackRunError("nethack.save_dir は絶対pathで指定してください")

    playground = save_dir.parent
    xlog_raw = raw.get("xlogfile", str(playground / "xlogfile"))
    dump_raw = raw.get("dump_dir", str(playground / "dumps"))
    if not isinstance(xlog_raw, str) or not xlog_raw:
        raise NethackRunError("nethack.xlogfile が不正です")
    if not isinstance(dump_raw, str) or not dump_raw:
        raise NethackRunError("nethack.dump_dir が不正です")
    xlogfile = Path(xlog_raw)
    dump_dir = Path(dump_raw)
    if not xlogfile.is_absolute() or not dump_dir.is_absolute():
        raise NethackRunError("nethack.xlogfile/dump_dir は絶対pathで指定してください")
    return NethackPersistenceSettings(
        player_name=player,
        save_dir=save_dir,
        xlogfile=xlogfile,
        dump_dir=dump_dir,
    )


def parse_xlog_line(line: str) -> dict[str, str]:
    """Parse NetHack's tab-separated ``key=value`` xlog record.

    Unknown fields are preserved. Malformed fragments are ignored rather than
    reinterpreted, so callers can require the exact fields they need.
    """
    result: dict[str, str] = {}
    for fragment in line.rstrip("\r\n").split("\t"):
        if "=" not in fragment:
            continue
        key, value = fragment.split("=", 1)
        if key:
            result[key] = value
    return result


def _int_field(record: dict[str, str], key: str) -> int | None:
    raw = record.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def _achievement_bits(record: dict[str, str]) -> int | None:
    raw = record.get("achieve")
    if raw is None or raw == "":
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def classify_terminal_record(record: dict[str, str]) -> str:
    """Classify one genuine terminal xlog record without guessing motives."""
    bits = _achievement_bits(record)
    death = record.get("death", "").strip().casefold()
    if bits is not None and bits & ASCENDED_ACHIEVEMENT:
        return "ascended"
    if "ascended" in death:
        return "ascended"
    if death.startswith("quit") or death.startswith("escaped"):
        return "ended"
    return "dead"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class NethackRunStore:
    """Private, lock-protected history under ``state_dir/nethack``."""

    def __init__(self, g: GlobalConfig, settings: NethackPersistenceSettings):
        self.g = g
        self.settings = settings
        self.root = Path(g.state_dir) / "nethack"
        self.runs_dir = self.root / "runs"
        self.progress_dir = self.root / "progress"
        self.meta_path = self.root / "meta.json"
        self.current_path = self.root / "current.json"
        self.lock_path = self.root / ".lock"

    @classmethod
    def from_global(cls, g: GlobalConfig) -> "NethackRunStore | None":
        settings = load_nethack_persistence_settings(g)
        return None if settings is None else cls(g, settings)

    def _ensure_private_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runs_dir, 0o700)
        self.progress_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.progress_dir, 0o700)

    def append_progress_sample(self, sample: dict[str, object]) -> bool:
        """Append one bounded public-state sample without taking the run lock.

        The current pointer/run JSON are atomically replaced by lifecycle code,
        so a best-effort read is enough here. Avoiding the shared run lock keeps
        telemetry from waiting behind a save/finish transaction in the game
        input path. A trace is deliberately capped and never stores raw TTY.
        """
        allowed = {
            "ts", "phase", "turn", "depth", "hp", "hp_max", "conditions",
            "prompt", "player", "intent", "resolved_intent", "key",
            "frame_hash", "map_hash",
        }
        if set(sample) != allowed:
            raise NethackRunError("progress sample fields are invalid")
        phase = sample.get("phase")
        if phase not in {"sent", "hold"}:
            raise NethackRunError("progress sample phase is invalid")
        for key in ("turn", "depth", "hp", "hp_max"):
            value = sample.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise NethackRunError(f"progress sample {key} is invalid")
        for key in ("intent", "resolved_intent"):
            value = sample.get(key)
            if not isinstance(value, str) or not value or len(value) > 64:
                raise NethackRunError(f"progress sample {key} is invalid")
        timestamp = sample.get("ts")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise NethackRunError("progress sample timestamp is invalid")
        try:
            timestamp_value = float(timestamp)
        except (OverflowError, ValueError) as exc:
            raise NethackRunError("progress sample timestamp is invalid") from exc
        if not math.isfinite(timestamp_value) or timestamp_value < 0:
            raise NethackRunError("progress sample timestamp is invalid")
        conditions = sample.get("conditions")
        if (
            not isinstance(conditions, list)
            or len(conditions) > 20
            or not all(isinstance(item, str) and len(item) <= 24 for item in conditions)
        ):
            raise NethackRunError("progress sample conditions are invalid")
        prompt = sample.get("prompt")
        if prompt not in {"none", "more", "yes_no", "direction", "selection", "text", "unknown"}:
            raise NethackRunError("progress sample prompt is invalid")
        player = sample.get("player")
        if player is not None and (
            not isinstance(player, list)
            or len(player) != 2
            or any(type(value) is not int or value < 0 for value in player)
        ):
            raise NethackRunError("progress sample player is invalid")
        key = sample.get("key")
        if key is not None and (not isinstance(key, str) or len(key) != 1):
            raise NethackRunError("progress sample key is invalid")
        for field in ("frame_hash", "map_hash"):
            value = sample.get(field)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(ch not in "0123456789abcdef" for ch in value)
            ):
                raise NethackRunError(f"progress sample {field} is invalid")

        try:
            current = self._read_json(self.current_path)
            run_id = current.get("run_id") if current else None
            if not isinstance(run_id, str):
                return False
            run_path = self._run_path(run_id)
            run = self._read_json(run_path)
            if (
                run is None
                or run.get("run_id") != run_id
                or run.get("status") != "active"
            ):
                return False
            payload = {"schema_version": PROGRESS_SCHEMA_VERSION, **sample}
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(line) > MAX_PROGRESS_LINE_BYTES:
                raise NethackRunError("progress sample exceeds line size limit")
            self._ensure_private_dirs()
            path = self.progress_dir / f"{run_id}.jsonl"
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                size = os.fstat(fd).st_size
                if size + len(line) > MAX_PROGRESS_TRACE_BYTES - 160:
                    marker = json.dumps(
                        {"schema_version": PROGRESS_SCHEMA_VERSION, "event": "truncated"},
                        separators=(",", ":"),
                    ).encode("ascii") + b"\n"
                    if size + len(marker) <= MAX_PROGRESS_TRACE_BYTES:
                        os.write(fd, marker)
                    return False
                written = os.write(fd, line)
                if written != len(line):
                    raise NethackRunError("progress sample write was incomplete")
            finally:
                os.close(fd)
            return True
        except BlockingIOError:
            return False
        except OSError as exc:
            raise NethackRunError("progress sample could not be written") from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_private_dirs()
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_json(path: Path) -> dict[str, object] | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise NethackRunError(f"run historyを読めません: {path.name}") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise NethackRunError(f"run history JSONが壊れています: {path.name}") from exc
        if not isinstance(payload, dict):
            raise NethackRunError(f"run history JSONが不正です: {path.name}")
        return payload

    def _run_path(self, run_id: str) -> Path:
        try:
            canonical = str(uuid.UUID(run_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise NethackRunError("run_id が不正です") from exc
        if canonical != run_id:
            raise NethackRunError("run_id が標準UUID形式ではありません")
        return self.runs_dir / f"{run_id}.json"

    def _known_max_expedition_unlocked(self) -> int:
        maximum = 0
        try:
            paths = tuple(self.runs_dir.glob("*.json"))
        except OSError as exc:
            raise NethackRunError("run history一覧を取得できません") from exc
        for path in paths:
            payload = self._read_json(path)
            if payload is None:
                continue
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise NethackRunError(f"run schemaが不正です: {path.name}")
            expedition = payload.get("expedition")
            if type(expedition) is not int or expedition < 1:
                raise NethackRunError(f"run expeditionが不正です: {path.name}")
            maximum = max(maximum, expedition)
        return maximum

    def _meta_unlocked(self) -> dict[str, object]:
        payload = self._read_json(self.meta_path)
        if payload is None:
            payload = {"schema_version": SCHEMA_VERSION, "last_expedition": 0}
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise NethackRunError("run history meta schemaが不正です")
        value = payload.get("last_expedition")
        if type(value) is not int or value < 0:
            raise NethackRunError("run history expedition counterが不正です")

        # meta.json is a cache/counter, not the only source of truth. If a
        # process crashed after writing a run but before updating meta, recover
        # the maximum from the durable run files so expedition numbers never
        # repeat.
        known_max = self._known_max_expedition_unlocked()
        if known_max > value:
            payload["last_expedition"] = known_max
            atomic_write_json(self.meta_path, payload)
        return payload

    def _current_id_unlocked(self) -> str | None:
        payload = self._read_json(self.current_path)
        if payload is None:
            return None
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise NethackRunError("current run schemaが不正です")
        run_id = payload.get("run_id")
        if not isinstance(run_id, str):
            raise NethackRunError("current run_idが不正です")
        self._run_path(run_id)
        return run_id

    def _load_run_unlocked(self, run_id: str) -> dict[str, object]:
        payload = self._read_json(self._run_path(run_id))
        if payload is None:
            raise NethackRunError("current run本体がありません")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise NethackRunError("run schemaが不正です")
        if payload.get("run_id") != run_id:
            raise NethackRunError("run_idとファイル内容が一致しません")
        return payload

    def _current_unlocked(self) -> dict[str, object] | None:
        run_id = self._current_id_unlocked()
        return None if run_id is None else self._load_run_unlocked(run_id)

    def _write_run_unlocked(self, run: dict[str, object]) -> None:
        run_id = run.get("run_id")
        if not isinstance(run_id, str):
            raise NethackRunError("run_idがありません")
        atomic_write_json(self._run_path(run_id), run)

    def _set_current_unlocked(self, run_id: str) -> None:
        atomic_write_json(
            self.current_path,
            {"schema_version": SCHEMA_VERSION, "run_id": run_id},
        )

    def _clear_current_unlocked(self) -> None:
        try:
            self.current_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise NethackRunError("current run pointerを削除できません") from exc
        _fsync_directory(self.root)

    def _save_name_matches_player(self, name: str) -> bool:
        player = re.escape(self.settings.player_name)
        return re.fullmatch(
            rf"\d+{player}(?:\.[A-Za-z0-9]+)?",
            name,
            flags=re.IGNORECASE,
        ) is not None

    def _matching_save_files(self) -> tuple[Path, ...]:
        try:
            entries = tuple(self.settings.save_dir.iterdir())
        except FileNotFoundError:
            return ()
        except OSError as exc:
            raise NethackRunError("NetHack save directoryを検査できません") from exc
        return tuple(
            path
            for path in entries
            if path.is_file() and self._save_name_matches_player(path.name)
        )

    def save_exists(self) -> bool:
        return bool(self._matching_save_files())

    def _xlog_offset(self) -> int:
        try:
            return self.settings.xlogfile.stat().st_size
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise NethackRunError("NetHack xlogfileを検査できません") from exc

    def _dump_baseline(self) -> int:
        try:
            entries = tuple(self.settings.dump_dir.iterdir())
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise NethackRunError("NetHack dump directoryを検査できません") from exc
        newest = 0
        needle = self.settings.player_name.casefold()
        for path in entries:
            if not path.is_file() or needle not in path.name.casefold():
                continue
            try:
                newest = max(newest, path.stat().st_mtime_ns)
            except OSError as exc:
                raise NethackRunError("NetHack dumplogを検査できません") from exc
        return newest

    def _new_dump_file(self, baseline: int) -> Path | None:
        try:
            entries = tuple(self.settings.dump_dir.iterdir())
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise NethackRunError("NetHack dump directoryを検査できません") from exc
        needle = self.settings.player_name.casefold()
        candidates: list[tuple[int, Path]] = []
        for path in entries:
            if not path.is_file() or needle not in path.name.casefold():
                continue
            try:
                mtime = path.stat().st_mtime_ns
            except OSError as exc:
                raise NethackRunError("NetHack dumplogを検査できません") from exc
            if mtime > baseline:
                candidates.append((mtime, path))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def current(self) -> dict[str, object] | None:
        with self._locked():
            run = self._current_unlocked()
            return None if run is None else dict(run)

    def prepare_start(
        self,
        *,
        current_is_nethack: bool,
        now: dt.datetime,
    ) -> dict[str, object]:
        """Validate continuity before the coordinator changes any game state."""
        with self._locked():
            run = self._current_unlocked()
            # Recover a crash after terminal run write but before current.json
            # unlink. The terminal run itself is durable, so this stale pointer
            # is safe to clear before allocating the next expedition.
            if run is not None and run.get("status") in TERMINAL_STATUSES:
                self._clear_current_unlocked()
                run = None

            save_exists = bool(self._matching_save_files())
            if run is not None:
                status = run.get("status")
                if status == "active":
                    if not current_is_nethack:
                        if not save_exists:
                            raise NethackRunError(
                                "active runなのにNetHack runtime/saveがありません"
                            )
                        # A coordinator switch outside this corner can safely
                        # suspend NetHack through P1a. Reconcile that evidence.
                        self._close_open_session_unlocked(
                            run, now, "external_suspend_detected"
                        )
                        run["status"] = "suspended"
                        run["last_finished_at"] = now.isoformat()
                        self._write_run_unlocked(run)
                elif status == "suspended":
                    if not current_is_nethack and not save_exists:
                        raise NethackRunError(
                            "suspended runのNetHack saveが見つかりません"
                        )
                else:
                    raise NethackRunError(
                        f"current runのstatusが不正です: {status!r}"
                    )
                return {
                    "kind": "existing",
                    "run_id": run["run_id"],
                    "expected_expedition": run["expedition"],
                }

            meta = self._meta_unlocked()
            expedition = int(meta["last_expedition"]) + 1
            if save_exists:
                kind = "recovered_save"
            elif current_is_nethack:
                kind = "adopted_active_runtime"
            else:
                kind = "new"
            return {
                "kind": kind,
                "run_id": str(uuid.uuid4()),
                "expected_expedition": expedition,
                "xlog_offset": self._xlog_offset(),
                "dump_baseline_mtime_ns": self._dump_baseline(),
            }

    def record_started(
        self,
        probe: dict[str, object],
        *,
        now: dt.datetime,
    ) -> dict[str, object]:
        """Commit a start/resume only after the coordinator transition succeeded."""
        with self._locked():
            kind = probe.get("kind")
            run_id = probe.get("run_id")
            if not isinstance(run_id, str):
                raise NethackRunError("start probe run_idが不正です")

            new_run = False
            meta: dict[str, object] | None = None
            if kind == "existing":
                current_id = self._current_id_unlocked()
                if current_id != run_id:
                    raise NethackRunError("run historyがstart中に変更されました")
                run = self._load_run_unlocked(run_id)
                if run.get("status") not in {"active", "suspended"}:
                    raise NethackRunError("resume対象runのstatusが不正です")
            elif kind in {"new", "recovered_save", "adopted_active_runtime"}:
                if self._current_id_unlocked() is not None:
                    raise NethackRunError("別runがstart中にcurrentになりました")
                meta = self._meta_unlocked()
                expedition = probe.get("expected_expedition")
                if (
                    type(expedition) is not int
                    or expedition != int(meta["last_expedition"]) + 1
                ):
                    raise NethackRunError("expedition counterがstart中に変更されました")
                xlog_offset = probe.get("xlog_offset")
                dump_baseline = probe.get("dump_baseline_mtime_ns")
                if type(xlog_offset) is not int or xlog_offset < 0:
                    raise NethackRunError("xlog baselineが不正です")
                if type(dump_baseline) is not int or dump_baseline < 0:
                    raise NethackRunError("dump baselineが不正です")
                run = {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "expedition": expedition,
                    "player_name": self.settings.player_name,
                    "status": "active",
                    "started_at": now.isoformat(),
                    "started_epoch": int(now.timestamp()),
                    "last_started_at": now.isoformat(),
                    "last_finished_at": None,
                    "xlog_offset": xlog_offset,
                    "dump_baseline_mtime_ns": dump_baseline,
                    "recovered_existing_save": kind == "recovered_save",
                    "adopted_active_runtime": kind == "adopted_active_runtime",
                    "sessions": [],
                    "score": None,
                    "turns": None,
                    "max_depth": None,
                    "death_reason": None,
                    "role": None,
                    "race": None,
                    "gender": None,
                    "alignment": None,
                    "achievement_bits": None,
                    "got_amulet": False,
                    "dump_file": None,
                    "terminal": None,
                    "lessons": [],
                }
                new_run = True
            else:
                raise NethackRunError("start probe kindが不正です")

            run["status"] = "active"
            run["last_started_at"] = now.isoformat()
            sessions = run.get("sessions")
            if not isinstance(sessions, list):
                raise NethackRunError("run sessionsが不正です")
            sessions.append(
                {
                    "started_at": now.isoformat(),
                    "ended_at": None,
                    "outcome": None,
                }
            )

            # Multi-file commit order is deliberate. Never publish a current
            # pointer before its run body exists. meta is last because it can be
            # reconstructed from durable run files after a crash.
            self._write_run_unlocked(run)
            self._set_current_unlocked(run_id)
            if new_run:
                assert meta is not None
                meta["last_expedition"] = run["expedition"]
                atomic_write_json(self.meta_path, meta)
            return dict(run)

    @staticmethod
    def _close_open_session_unlocked(
        run: dict[str, object], now: dt.datetime, outcome: str
    ) -> None:
        sessions = run.get("sessions")
        if not isinstance(sessions, list):
            raise NethackRunError("run sessionsが不正です")
        if not sessions:
            return
        latest = sessions[-1]
        if not isinstance(latest, dict):
            raise NethackRunError("run session entryが不正です")
        if latest.get("ended_at") is None:
            latest["ended_at"] = now.isoformat()
            latest["outcome"] = outcome

    def _terminal_record_since(
        self, offset: int
    ) -> tuple[dict[str, str] | None, str | None]:
        try:
            size = self.settings.xlogfile.stat().st_size
        except FileNotFoundError:
            return None, "xlogfile-missing"
        except OSError as exc:
            raise NethackRunError("NetHack xlogfileを検査できません") from exc
        if size < offset:
            return None, "xlogfile-truncated"
        try:
            with self.settings.xlogfile.open("rb") as stream:
                stream.seek(offset)
                raw = stream.read()
        except OSError as exc:
            raise NethackRunError("NetHack xlogfileを読めません") from exc
        records: list[dict[str, str]] = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            record = parse_xlog_line(line)
            if record.get("name") == self.settings.player_name:
                records.append(record)
        if not records:
            return None, "no-new-player-xlog-record"
        return records[-1], None

    @staticmethod
    def _terminal_payload(record: dict[str, str]) -> dict[str, object]:
        bits = _achievement_bits(record)
        return {
            "source": "xlogfile",
            "death": record.get("death"),
            "points": _int_field(record, "points"),
            "turns": _int_field(record, "turns"),
            "maxlvl": _int_field(record, "maxlvl"),
            "deathlev": _int_field(record, "deathlev"),
            "hp": _int_field(record, "hp"),
            "maxhp": _int_field(record, "maxhp"),
            "role": record.get("role"),
            "race": record.get("race"),
            "gender": record.get("gender"),
            "align": record.get("align"),
            "starttime": _int_field(record, "starttime"),
            "endtime": _int_field(record, "endtime"),
            "realtime": _int_field(record, "realtime"),
            "achievement_bits": bits,
        }

    def record_finished(
        self,
        *,
        now: dt.datetime,
        nethack_still_active: bool,
    ) -> dict[str, object]:
        """Close one program session and reconcile save/xlog evidence."""
        with self._locked():
            run = self._current_unlocked()
            if run is None:
                raise NethackRunError("終了対象のcurrent NetHack runがありません")
            if run.get("status") not in {"active", "suspended"}:
                raise NethackRunError("終了対象runのstatusが不正です")

            if nethack_still_active:
                self._close_open_session_unlocked(run, now, "continued")
                run["status"] = "active"
                run["last_finished_at"] = now.isoformat()
                self._write_run_unlocked(run)
                return dict(run)

            if self._matching_save_files():
                self._close_open_session_unlocked(run, now, "suspended")
                run["status"] = "suspended"
                run["last_finished_at"] = now.isoformat()
                self._write_run_unlocked(run)
                return dict(run)

            offset = run.get("xlog_offset")
            if type(offset) is not int or offset < 0:
                raise NethackRunError("run xlog_offsetが不正です")
            record, analysis_error = self._terminal_record_since(offset)
            self._close_open_session_unlocked(run, now, "terminal")
            run["last_finished_at"] = now.isoformat()
            if record is None:
                run["status"] = "ended_unknown"
                run["terminal"] = {
                    "source": "xlogfile",
                    "analysis_error": analysis_error,
                }
            else:
                status = classify_terminal_record(record)
                terminal = self._terminal_payload(record)
                run["status"] = status
                run["terminal"] = terminal
                run["score"] = terminal["points"]
                run["turns"] = terminal["turns"]
                run["max_depth"] = terminal["maxlvl"]
                run["death_reason"] = terminal["death"]
                run["role"] = terminal["role"]
                run["race"] = terminal["race"]
                run["gender"] = terminal["gender"]
                run["alignment"] = terminal["align"]
                bits = terminal["achievement_bits"]
                run["achievement_bits"] = bits
                run["got_amulet"] = bool(
                    isinstance(bits, int) and bits & AMULET_ACHIEVEMENT
                )

            baseline = run.get("dump_baseline_mtime_ns")
            if type(baseline) is int and baseline >= 0:
                dump = self._new_dump_file(baseline)
                if dump is not None:
                    run["dump_file"] = dump.name

            # Persist the terminal body before clearing the public current
            # pointer. A crash between these writes leaves a stale pointer to a
            # complete terminal run; prepare_start repairs that case safely.
            self._write_run_unlocked(run)
            self._clear_current_unlocked()
            return dict(run)
