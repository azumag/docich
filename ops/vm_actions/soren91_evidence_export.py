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
from pathlib import Path
from typing import Callable

PRODUCTION_ROOT = Path("/home/ubuntu/soren")
MAX_GAMES = 3
DEFAULT_GAMES = 2
MAX_AGE_MS = 24 * 60 * 60 * 1000
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_BYTES = 256 * 1024
MAX_STRATEGY_BYTES = 256 * 1024
MAX_TELEMETRY_BYTES = 2 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOTS_PER_GAME = 3
MAX_BUNDLE_BYTES = 3 * 1024 * 1024
CHUNK_BYTES = 24 * 1024
EXPORT_TTL_MS = 10 * 60 * 1000
STATE_NAME = "soren91_manual_evidence_export.json"
BUNDLE_NAME = "soren91_manual_evidence_export.tar.gz"
GAME_RE = re.compile(r"game_(\d+)\.json\Z")
SCREENSHOT_RE = re.compile(r"turn_(\d+)(?:[._-][A-Za-z0-9._-]+)?\.png\Z", re.I)


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
            "-vf", "scale=960:-2:force_original_aspect_ratio=decrease",
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
    if not selected_games:
        raise EvidenceError("no completed Soren91 evidence in the last 24 hours")
    games = [game for game, _token in selected_games]

    staging = Path(tempfile.mkdtemp(prefix=".soren91-evidence-", dir=state_dir))
    manifest: dict[str, object] = {
        "schema": 1,
        "createdAtMs": now_ms,
        "windowHours": 24,
        "games": games,
        "files": [],
    }
    files_meta: list[dict[str, object]] = manifest["files"]  # type: ignore[assignment]
    try:
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

        for telemetry_name in ("soren91_loop_metrics.json", "soren91_runtime_metrics.json"):
            src = state_dir / telemetry_name
            data = _read_regular(runtime, src, MAX_TELEMETRY_BYTES, required=False)
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
            "games": games,
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
