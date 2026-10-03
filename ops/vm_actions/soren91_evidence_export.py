#!/usr/bin/env python3
"""Prepare a bounded, read-only Soren91 evidence bundle for explicit manual review.

This is not an improvement runner. It never edits strategy/runtime evidence and
never opens a PR. The only persistent writes are one short-lived export bundle
and its fixed-shape state file under soren91/tmp/state/.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PRODUCTION_ROOT = Path("/home/ubuntu/soren")
MAX_GAMES = 3
DEFAULT_GAMES = 2
MAX_AGE_MS = 72 * 60 * 60 * 1000
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_BYTES = 256 * 1024
MAX_STRATEGY_BYTES = 256 * 1024
MAX_TELEMETRY_BYTES = 2 * 1024 * 1024
MAX_CALIBRATION_BYTES = 256 * 1024
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_DIMENSION = 8192
MAX_SCREENSHOT_PIXELS = 16 * 1024 * 1024
MAX_SCREENSHOTS_PER_GAME = 3
MAX_BUNDLE_BYTES = 3 * 1024 * 1024
CHUNK_BYTES = 24 * 1024
EXPORT_TTL_MS = 10 * 60 * 1000
STATE_NAME = "soren91_manual_evidence_export.json"
BUNDLE_NAME = "soren91_manual_evidence_export.tar.gz"
GAME_RE = re.compile(r"game_(\d+)\.json\Z")
SCREENSHOT_RE = re.compile(r"turn_(\d+)(?:[._-][A-Za-z0-9._-]+)?\.png\Z", re.I)
TELEMETRY_NAMES = ("soren91_loop_metrics.json", "soren91_runtime_metrics.json")
SESSION_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
ISO_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
PARTIAL_CLOCK_SLOP_MS = 1000


class EvidenceError(RuntimeError):
    pass


def _runtime(root: Path) -> Path:
    return root / "soren91"


def _state_dir(root: Path) -> Path:
    return _runtime(root) / "tmp" / "state"


def _state_path(root: Path) -> Path:
    return _state_dir(root) / STATE_NAME


def _bundle_path(root: Path) -> Path:
    return _state_dir(root) / BUNDLE_NAME


def _reject_symlink_chain(runtime: Path, path: Path) -> None:
    try:
        rel = path.relative_to(runtime)
    except ValueError as exc:
        raise EvidenceError("path outside Soren91 runtime") from exc
    current = runtime
    if current.is_symlink():
        raise EvidenceError("runtime root is symlink")
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise EvidenceError("symlink evidence path rejected")


def _read_regular(runtime: Path, path: Path, limit: int, *, required: bool = True) -> bytes | None:
    _reject_symlink_chain(runtime, path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if required:
            raise EvidenceError("required evidence missing")
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > limit:
            raise EvidenceError("evidence file outside size/type contract")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise EvidenceError("evidence file grew beyond limit")
        return data
    finally:
        if fd >= 0:
            os.close(fd)


def _read_recent_optional(
    runtime: Path,
    path: Path,
    limit: int,
    *,
    now_ms: int,
) -> bytes | None:
    """Read one optional evidence file only when its mtime is in the review window."""
    _reject_symlink_chain(runtime, path)
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EvidenceError("evidence file outside size/type contract") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > limit:
        raise EvidenceError("evidence file outside size/type contract")
    mtime_ms = int(info.st_mtime * 1000)
    age_ms = now_ms - mtime_ms
    if age_ms < 0 or age_ms > MAX_AGE_MS:
        return None
    return _read_regular(runtime, path, limit, required=False)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".soren91-export-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _safe_games(root: Path, now_ms: int, count: int) -> list[tuple[int, str]]:
    """Return (numeric game id, exact on-disk digit token).

    Soren91 deliberately stores completed evidence as game_0001.*, so never
    round-trip the filename through int() and accidentally look for game_1.*.
    The token comes only from the strict GAME_RE match and is reused across
    history/summary/snapshot/screenshot paths.
    """
    runtime = _runtime(root)
    summary_dir = runtime / "tmp" / "summaries"
    history_dir = runtime / "game_history"
    _reject_symlink_chain(runtime, summary_dir)
    _reject_symlink_chain(runtime, history_dir)
    if not summary_dir.is_dir() or not history_dir.is_dir():
        return []
    candidates: list[tuple[int, str, int]] = []
    for entry in os.scandir(summary_dir):
        match = GAME_RE.fullmatch(entry.name)
        if not match or entry.is_symlink():
            continue
        token = match.group(1)
        game = int(token)
        # Completed Soren91 runtime evidence uses the canonical four-digit
        # token. Reject aliases such as game_1.json instead of widening the
        # reviewed export surface.
        if token != f"{game:04d}":
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SUMMARY_BYTES:
            continue
        history = history_dir / f"game_{token}.jsonl"
        try:
            _read_regular(runtime, history, MAX_HISTORY_BYTES)
        except EvidenceError:
            continue
        mtime_ms = int(info.st_mtime * 1000)
        if now_ms - mtime_ms < 0 or now_ms - mtime_ms > MAX_AGE_MS:
            continue
        candidates.append((game, token, mtime_ms))
    candidates.sort(key=lambda item: (item[2], item[0]), reverse=True)
    return [(game, token) for game, token, _ in candidates[:count]]


def _default_transcode(src: Path, dst: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise EvidenceError("ffmpeg unavailable")
    subprocess.run(
        [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            "-vf", "scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-frames:v", "1", "-q:v", "6", str(dst),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=True,
    )


def _copy_bytes(dst: Path, data: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_stable_optional(runtime: Path, path: Path, limit: int) -> tuple[bytes, os.stat_result] | None:
    """Snapshot an optional, bounded file without following links or a live rewrite.

    Live PNGs are overwritten and histories are appended. Transcoding must use
    these checked bytes, never reopen the mutable source after validation.
    """
    _reject_symlink_chain(runtime, path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EvidenceError("evidence file outside size/type contract") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise EvidenceError("evidence file outside size/type contract")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1
            data = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        if len(data) > limit:
            raise EvidenceError("evidence file grew beyond limit")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) or len(data) != after.st_size:
            return None
        return data, after
    finally:
        if fd >= 0:
            os.close(fd)


def _source_times(info: os.stat_result, now_ms: int, updated_at_ms: int) -> dict[str, int]:
    mtime_ms = info.st_mtime_ns // 1_000_000
    return {
        "sourceMtimeMs": mtime_ms,
        "sourceAgeMs": now_ms - mtime_ms,
        "sourceMinusTelemetryMs": mtime_ms - updated_at_ms,
    }


def _partial_file_in_window(info: os.stat_result, now_ms: int, updated_at_ms: int) -> bool:
    times = _source_times(info, now_ms, updated_at_ms)
    return (
        0 <= times["sourceAgeMs"] <= MAX_AGE_MS
        and times["sourceMinusTelemetryMs"] <= PARTIAL_CLOCK_SLOP_MS
    )


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if (
        len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n"
        or data[8:12] != b"\x00\x00\x00\r" or data[12:16] != b"IHDR"
    ):
        return None
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if not (
        1 <= width <= MAX_SCREENSHOT_DIMENSION and 1 <= height <= MAX_SCREENSHOT_DIMENSION
        and width * height <= MAX_SCREENSHOT_PIXELS
    ):
        raise EvidenceError("evidence file outside size/type contract")
    return width, height


def _timestamp_ms(value: object) -> int | None:
    if not isinstance(value, str) or not ISO_TIMESTAMP_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (OverflowError, ValueError):
        return None


def _project_calibration(data: bytes) -> dict | None:
    """Only the current fixed numeric schema, two method enums and an ISO date."""
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    groups = {
        "screen": ("width", "height"),
        "board": ("left", "right", "top", "bottom", "width", "height"),
        "walls": ("leftOuter", "leftInner", "rightInner", "rightOuter"),
        "dropArea": ("pixelLeft", "pixelRight"),
    }
    projected: dict = {}
    for group, keys in groups.items():
        source = value.get(group)
        if not isinstance(source, dict):
            return None
        projected[group] = {}
        for key in keys:
            number = source.get(key)
            if type(number) not in (int, float) or not -1_000_000 <= number <= 1_000_000:
                return None
            projected[group][key] = number
    for key in ("pixelsPerUnit", "confidence"):
        number = value.get(key)
        if type(number) not in (int, float) or not 0 <= number <= 1_000_000:
            return None
        projected[key] = number
    if value.get("method") not in ("profile", "fallback") or type(value.get("isFallback")) is not bool:
        return None
    if _timestamp_ms(value.get("timestamp")) is None:
        return None
    projected.update({key: value[key] for key in ("method", "isFallback", "timestamp")})
    return projected


def _has_completed_evidence(runtime: Path, token: str) -> bool:
    # Completion first renames history and only later writes the summary. Either
    # marker excludes this game, including a completion concurrent with export.
    for path in (
        runtime / "game_history" / f"game_{token}.jsonl",
        runtime / "tmp" / "summaries" / f"game_{token}.json",
    ):
        _reject_symlink_chain(runtime, path)
        if path.exists():
            return True
    return False


def _prepare_partial_evidence(
    runtime: Path, staging: Path, now_ms: int, transcode: Callable[[Path, Path], None]
) -> tuple[dict, list[dict], bytes] | None:
    """Export at most one historical unfinished game, separately from completions.

    This is a snapshot of saved evidence, not a claim that a process is running
    or that the separately saved calibration was used to analyze this frame.
    """
    loop_path = runtime / "tmp" / "state" / TELEMETRY_NAMES[0]
    loop_snapshot = _read_stable_optional(runtime, loop_path, MAX_TELEMETRY_BYTES)
    if loop_snapshot is None:
        return None
    loop_data, loop_info = loop_snapshot
    try:
        loop = json.loads(loop_data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(loop, dict) or loop.get("schemaVersion") != 1:
        return None
    game, turn, updated = (loop.get(key) for key in ("game", "turn", "updatedAtMs"))
    elapsed = loop.get("elapsedMs")
    profile = loop.get("dropProfile")
    session = profile.get("session") if isinstance(profile, dict) else None
    if (
        type(game) is not int or not 1 <= game <= 9999
        or type(turn) is not int or not 0 <= turn <= 9999
        or type(updated) is not int or not 0 <= now_ms - updated <= MAX_AGE_MS
        or type(elapsed) not in (int, float) or not 0 <= elapsed <= MAX_AGE_MS
        or not isinstance(session, str) or not SESSION_RE.fullmatch(session)
        or not _partial_file_in_window(loop_info, now_ms, updated)
        or abs(loop_info.st_mtime_ns // 1_000_000 - updated) > PARTIAL_CLOCK_SLOP_MS
    ):
        return None
    token = f"{game:04d}"
    if _has_completed_evidence(runtime, token):
        return None
    screenshot_path = runtime / "tmp" / "screenshots" / f"turn_{turn:04d}.png"
    history_path = runtime / "game_history" / f"latest_{token}.jsonl"
    screenshot = _read_stable_optional(runtime, screenshot_path, MAX_SCREENSHOT_BYTES)
    history = _read_stable_optional(runtime, history_path, MAX_HISTORY_BYTES)
    screenshot_status = "included" if screenshot is not None else "missing-or-unstable"
    if screenshot is not None and (
        not _partial_file_in_window(screenshot[1], now_ms, updated)
        or screenshot[1].st_mtime_ns / 1_000_000 < updated - elapsed - PARTIAL_CLOCK_SLOP_MS
    ):
        # turn_NNNN is reused each game. A same-name image from an earlier turn
        # window must never be attributed to this telemetry session.
        screenshot = None
        screenshot_status = "outside-time-window"
    dimensions = _png_dimensions(screenshot[0]) if screenshot is not None else None
    if screenshot is not None and dimensions is None:
        screenshot = None
        screenshot_status = "invalid-or-incomplete-png"
    if history is not None:
        if not _partial_file_in_window(history[1], now_ms, updated) or not history[0].endswith(b"\n"):
            history = None
        else:
            try:
                records = [json.loads(line) for line in history[0].splitlines() if line.strip()]
                if not records or any(
                    not isinstance(row, dict) or type(row.get("turn")) is not int
                    or not 0 <= row["turn"] <= turn
                    or _timestamp_ms(row.get("timestamp")) is None
                    or _timestamp_ms(row["timestamp"]) > updated + PARTIAL_CLOCK_SLOP_MS
                    for row in records
                ) or any(a["turn"] >= b["turn"] for a, b in zip(records, records[1:])):
                    history = None
            except (ValueError, UnicodeDecodeError, RecursionError):
                history = None
    if screenshot is None and history is None:
        return None
    calibration_path = runtime / "tmp" / "calibration.json"
    calibration = _read_stable_optional(runtime, calibration_path, MAX_CALIBRATION_BYTES)
    calibration_value = None
    calibration_status = "missing-or-stale"
    if calibration is not None and _partial_file_in_window(calibration[1], now_ms, updated):
        calibration_value = _project_calibration(calibration[0])
        calibration_status = "invalid-schema"
        if calibration_value is not None:
            timestamp = _timestamp_ms(calibration_value["timestamp"])
            if (
                not 0 <= now_ms - timestamp <= MAX_AGE_MS
                or abs(calibration[1].st_mtime_ns // 1_000_000 - timestamp) > PARTIAL_CLOCK_SLOP_MS
            ):
                calibration_value = None
                calibration_status = "timestamp-mismatch"
            elif dimensions != tuple(calibration_value["screen"][key] for key in ("width", "height")):
                calibration_value = None
                calibration_status = "screen-mismatch" if dimensions else "screenshot-unavailable"
            else:
                calibration_status = "included-separate-saved-calibration"

    # Pin the exact telemetry revision, including updatedAtMs, around all source
    # reads. Nothing below reopens a live PNG/history/calibration for content.
    loop_after = _read_stable_optional(runtime, loop_path, MAX_TELEMETRY_BYTES)
    if loop_after is None or loop_after[0] != loop_data or _has_completed_evidence(runtime, token):
        return None

    partial_dir = staging / "partial" / f"game_{token}"
    files: list[dict] = []
    metadata = {
        "completed": False, "game": game, "turn": turn, "telemetrySession": session,
        "basis": "saved-partial-evidence", "telemetryUpdatedAtMs": updated,
        "telemetryAgeMs": now_ms - updated,
        "telemetrySourceMtimeMs": loop_info.st_mtime_ns // 1_000_000,
        "telemetrySourceAgeMs": now_ms - loop_info.st_mtime_ns // 1_000_000,
        "calibrationStatus": calibration_status, "screenshotStatus": screenshot_status,
    }

    def record_file(dst: Path, data: bytes, source: Path, info: os.stat_result, kind: str, **extra) -> None:
        files.append({
            "game": game, "turn": turn, "partial": True, "kind": kind,
            "name": dst.relative_to(staging).as_posix(), "bytes": len(data),
            "sha256": _sha256_bytes(data), "source": source.relative_to(runtime).as_posix(),
            **_source_times(info, now_ms, updated), **extra,
        })

    if history is not None:
        dst = partial_dir / "history.jsonl"
        _copy_bytes(dst, history[0])
        # Saved histories do not carry session IDs. Even with a matching game
        # number and time window, a pre-restart row cannot be attributed to the
        # telemetry process; expose it only as separate historical evidence.
        record_file(dst, history[0], history_path, history[1], "partial-history",
                    relationship="separate-saved-history", sessionAttributed=False)
    if screenshot is not None:
        source_copy = staging / ".partial-source.png"
        dst = partial_dir / "screenshots" / f"turn_{turn:04d}.jpg"
        _copy_bytes(source_copy, screenshot[0])
        dst.parent.mkdir(parents=True, exist_ok=True)
        image = None
        try:
            transcode(source_copy, dst)
            image = dst.read_bytes()
        except (EvidenceError, OSError, subprocess.SubprocessError):
            # A concurrent truncate/write may leave a stable but incomplete PNG
            # snapshot. This optional image must not discard completed evidence.
            metadata["screenshotStatus"] = "transcode-failed"
        finally:
            source_copy.unlink(missing_ok=True)
        if image and len(image) > MAX_SCREENSHOT_BYTES:
            raise EvidenceError("transcoded screenshot outside size contract")
        if image:
            record_file(dst, image, screenshot_path, screenshot[1], "partial-screenshot",
                        sourceWidth=dimensions[0], sourceHeight=dimensions[1])
        else:
            dst.unlink(missing_ok=True)
            if metadata["screenshotStatus"] == "included":
                metadata["screenshotStatus"] = "empty-transcode"
            if calibration_value is not None:
                calibration_value = None
                metadata["calibrationStatus"] = "screenshot-unavailable"
    if calibration_value is not None:
        data = (json.dumps(calibration_value, sort_keys=True, allow_nan=False) + "\n").encode()
        dst = partial_dir / "calibration.json"
        _copy_bytes(dst, data)
        record_file(dst, data, calibration_path, calibration[1], "partial-calibration",
                    relationship="separate-saved-calibration", screenMatchesScreenshot=True,
                    calibrationTimestampMs=_timestamp_ms(calibration_value["timestamp"]))
    if not files:
        return None
    return metadata, files, loop_data


def prepare_export(
    root: Path = PRODUCTION_ROOT,
    *,
    game_count: int = DEFAULT_GAMES,
    now_ms: int | None = None,
    transcode: Callable[[Path, Path], None] = _default_transcode,
) -> dict:
    if isinstance(game_count, bool) or not isinstance(game_count, int) or not 1 <= game_count <= MAX_GAMES:
        raise EvidenceError("invalid game count")
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    runtime = _runtime(root)
    state_dir = _state_dir(root)
    _reject_symlink_chain(runtime, state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    selected_games = _safe_games(root, now_ms, game_count)
    fresh_partial_telemetry: dict[str, bytes] = {}
    if not selected_games:
        for telemetry_name in TELEMETRY_NAMES:
            data = _read_recent_optional(
                runtime,
                state_dir / telemetry_name,
                MAX_TELEMETRY_BYTES,
                now_ms=now_ms,
            )
            if data:
                fresh_partial_telemetry[telemetry_name] = data
        if not fresh_partial_telemetry:
            raise EvidenceError("no completed Soren91 evidence in the last 72 hours")

    games = [game for game, _token in selected_games]
    evidence_mode = "completed_games" if selected_games else "telemetry_only"
    # The transport contract historically requires one to three non-negative
    # numeric game identifiers. Zero is reserved as a no-completed-game
    # sentinel; no game_0000 files are ever synthesized or exported.
    transport_games = games if games else [0]

    staging = Path(tempfile.mkdtemp(prefix=".soren91-evidence-", dir=state_dir))
    manifest: dict[str, object] = {
        "schema": 1,
        "createdAtMs": now_ms,
        "windowHours": 72,
        "evidenceMode": evidence_mode,
        "games": games,
        "files": [],
    }
    files_meta: list[dict[str, object]] = manifest["files"]  # type: ignore[assignment]
    try:
        partial = _prepare_partial_evidence(runtime, staging, now_ms, transcode)
        if partial is not None:
            manifest["partialEvidence"] = partial[0]
            files_meta.extend(partial[1])
            if not selected_games:
                evidence_mode = "partial_game"
                manifest["evidenceMode"] = evidence_mode
        for game, token in selected_games:
            game_dir = f"game_{token}"
            specs = [
                (
                    runtime / "game_history" / f"{game_dir}.jsonl",
                    staging / game_dir / "history.jsonl",
                    MAX_HISTORY_BYTES,
                    "history",
                    True,
                ),
                (
                    runtime / "tmp" / "summaries" / f"{game_dir}.json",
                    staging / game_dir / "summary.json",
                    MAX_SUMMARY_BYTES,
                    "summary",
                    True,
                ),
                (
                    runtime / "tmp" / "strategy_snapshots" / f"{game_dir}_strategy.mjs",
                    staging / game_dir / "strategy.mjs",
                    MAX_STRATEGY_BYTES,
                    "strategy",
                    False,
                ),
            ]
            for src, dst, limit, kind, required in specs:
                data = _read_regular(runtime, src, limit, required=required)
                if data is None:
                    continue
                _copy_bytes(dst, data)
                files_meta.append({
                    "game": game,
                    "kind": kind,
                    "name": dst.relative_to(staging).as_posix(),
                    "bytes": len(data),
                    "sha256": _sha256_bytes(data),
                })

            screenshot_dir = runtime / "tmp" / "game_screenshots" / game_dir
            _reject_symlink_chain(runtime, screenshot_dir)
            if screenshot_dir.is_dir():
                shots: list[tuple[int, Path]] = []
                for entry in os.scandir(screenshot_dir):
                    match = SCREENSHOT_RE.fullmatch(entry.name)
                    if not match or entry.is_symlink():
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SCREENSHOT_BYTES:
                        continue
                    shots.append((int(match.group(1)), Path(entry.path)))
                shots.sort(key=lambda item: (item[0], item[1].name))
                for turn, src in shots[:MAX_SCREENSHOTS_PER_GAME]:
                    _read_regular(runtime, src, MAX_SCREENSHOT_BYTES)
                    dst = staging / game_dir / "screenshots" / f"turn_{turn}.jpg"
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        transcode(src, dst)
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise EvidenceError("screenshot transcode failed") from exc
                    image = dst.read_bytes()
                    if not image or len(image) > MAX_SCREENSHOT_BYTES:
                        raise EvidenceError("transcoded screenshot outside size contract")
                    files_meta.append({
                        "game": game,
                        "kind": "screenshot",
                        "turn": turn,
                        "name": dst.relative_to(staging).as_posix(),
                        "bytes": len(image),
                        "sha256": _sha256_bytes(image),
                    })

        for telemetry_name in TELEMETRY_NAMES:
            if partial is not None and telemetry_name == TELEMETRY_NAMES[0]:
                data = partial[2]
            elif fresh_partial_telemetry:
                data = fresh_partial_telemetry.get(telemetry_name)
            else:
                data = _read_regular(
                    runtime,
                    state_dir / telemetry_name,
                    MAX_TELEMETRY_BYTES,
                    required=False,
                )
            if data is None:
                continue
            dst = staging / "telemetry" / telemetry_name
            _copy_bytes(dst, data)
            files_meta.append({
                "kind": "telemetry",
                "name": dst.relative_to(staging).as_posix(),
                "bytes": len(data),
                "sha256": _sha256_bytes(data),
            })

        manifest_bytes = (json.dumps(manifest, sort_keys=True, ensure_ascii=False) + "\n").encode()
        _copy_bytes(staging / "manifest.json", manifest_bytes)

        bundle_tmp = state_dir / f".{BUNDLE_NAME}.tmp"
        try:
            with tarfile.open(bundle_tmp, "w:gz", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(staging).as_posix(), recursive=False)
            bundle = bundle_tmp.read_bytes()
        finally:
            bundle_tmp.unlink(missing_ok=True)
        if not bundle or len(bundle) > MAX_BUNDLE_BYTES:
            raise EvidenceError("evidence bundle outside size contract")
        digest = hashlib.sha256(bundle).hexdigest()
        chunk_count = math.ceil(len(bundle) / CHUNK_BYTES)
        if chunk_count < 1 or chunk_count > 256:
            raise EvidenceError("evidence chunk count outside contract")

        _write_private(_bundle_path(root), bundle)
        state = {
            "version": 1,
            "createdAtMs": now_ms,
            "expiresAtMs": now_ms + EXPORT_TTL_MS,
            "bundleBytes": len(bundle),
            "bundleSha256": digest,
            "chunkBytes": CHUNK_BYTES,
            "chunkCount": chunk_count,
            "currentChunk": 0,
            "evidenceMode": evidence_mode,
            "games": transport_games,
        }
        _write_private(_state_path(root), (json.dumps(state, sort_keys=True) + "\n").encode())
        return state
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _load_state(root: Path = PRODUCTION_ROOT) -> dict:
    runtime = _runtime(root)
    raw = _read_regular(runtime, _state_path(root), 4096)
    try:
        state = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid export state") from exc
    if not isinstance(state, dict) or state.get("version") != 1:
        raise EvidenceError("invalid export state")
    return state


def select_chunk(index: int, root: Path = PRODUCTION_ROOT, *, now_ms: int | None = None) -> dict:
    if isinstance(index, bool) or not isinstance(index, int):
        raise EvidenceError("invalid chunk index")
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    state = _load_state(root)
    count = state.get("chunkCount")
    expires = state.get("expiresAtMs")
    if not isinstance(count, int) or not 1 <= count <= 256 or not isinstance(expires, int) or now_ms > expires:
        raise EvidenceError("export state expired or invalid")
    if not 0 <= index < count:
        raise EvidenceError("chunk index outside contract")
    state["currentChunk"] = index
    _write_private(_state_path(root), (json.dumps(state, sort_keys=True) + "\n").encode())
    return state


def clear_export(root: Path = PRODUCTION_ROOT) -> None:
    _state_path(root).unlink(missing_ok=True)
    _bundle_path(root).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if not argv:
        raise EvidenceError("usage: prepare [1-3] | select INDEX | clear")
    command = argv[0]
    if command == "prepare":
        if len(argv) > 2:
            raise EvidenceError("invalid prepare arguments")
        count = DEFAULT_GAMES if len(argv) == 1 else int(argv[1])
        state = prepare_export(game_count=count)
        print(json.dumps({k: state[k] for k in ("version", "bundleBytes", "chunkCount", "games")}, separators=(",", ":")))
        return 0
    if command == "select" and len(argv) == 2 and re.fullmatch(r"\d+", argv[1]):
        state = select_chunk(int(argv[1]))
        print(json.dumps({"currentChunk": state["currentChunk"], "chunkCount": state["chunkCount"]}, separators=(",", ":")))
        return 0
    if command == "clear" and len(argv) == 1:
        clear_export()
        print('{"cleared":true}')
        return 0
    raise EvidenceError("invalid command")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (EvidenceError, ValueError) as exc:
        print(f"soren91 evidence export failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
