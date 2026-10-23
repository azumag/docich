#!/usr/bin/env python3
"""Export fixed-shape aggregate Soren strategy performance from production state.

This helper is owner-triggered through the reviewed VM exec path. It reads only
fixed Soren state files and writes one short-lived aggregate bundle through the
existing Soren91 evidence transport staging contract. No game history rows,
paths, prompts, screenshots, or arbitrary strings are exported.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import os
import re
import stat
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORTER_PATH = HERE / "soren91_evidence_export.py"
PRODUCTION_ROOT = Path("/home/ubuntu/soren")
CURRENT_RUN_REL = Path("tmp/state/current_strategy_run.json")
ROLLING_REL = Path("tmp/state/rolling_scores.json")
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_RECENT_STRATEGIES = 8
HASH_RE = re.compile(r"[0-9a-f]{8,64}\Z")
ARCHIVE_RE = re.compile(r"(?:^|/)\d{8}_(\d{6})_score-?\d+\.jsonl\Z")
STAMP_RE = re.compile(r"(?:^|/)(\d{8}_\d{6})_score-?\d+\.jsonl\Z")


def _load_exporter():
    spec = importlib.util.spec_from_file_location("soren91_evidence_transport", EXPORTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("transport unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_fixed_json(root: Path, rel: Path) -> dict:
    path = root / rel
    if root.resolve() not in path.resolve().parents:
        raise RuntimeError("invalid fixed state path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= MAX_STATE_BYTES:
            raise RuntimeError("state file contract")
        raw = os.read(fd, MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise RuntimeError("state file contract")
    finally:
        os.close(fd)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("state json contract")
    return value


def _ints(values) -> list[int]:
    out: list[int] = []
    if not isinstance(values, list):
        return out
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if -10**9 <= number <= 10**9:
            out.append(number)
    return out


def _quantile(values: list[int], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _round_metric(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _progress(values, expected: int) -> list[dict | None]:
    if not isinstance(values, list) or len(values) != expected:
        return [None] * expected
    out: list[dict | None] = []
    for row in values:
        out.append(row if isinstance(row, dict) else None)
    return out


def _metrics(scores: list[int], max_types: list[int], progress_values) -> dict:
    n = len(scores)
    progress = _progress(progress_values, n)
    turns = _ints([row.get("turns") for row in progress if row is not None])
    raw_scores = _ints([row.get("raw") for row in progress if row is not None])
    progress_types = _ints([row.get("t") for row in progress if row is not None])
    if len(max_types) != n:
        max_types = progress_types if len(progress_types) == n else []
    russia = sum(1 for row in progress if row is not None and row.get("r") in (1, True))
    soviet = sum(1 for row in progress if row is not None and row.get("v") in (1, True))
    deaths = sum(1 for row in progress if row is not None and row.get("d") in (1, True))
    return {
        "n": n,
        "score_mean": _round_metric(sum(scores) / n if n else None),
        "score_p50": _round_metric(_quantile(scores, 0.50)),
        "score_p25": _round_metric(_quantile(scores, 0.25)),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "raw_p50": _round_metric(_quantile(raw_scores, 0.50)),
        "turns_p50": _round_metric(_quantile(turns, 0.50)),
        "turns_p25": _round_metric(_quantile(turns, 0.25)),
        "t14": sum(1 for value in max_types if value >= 14),
        "t15": sum(1 for value in max_types if value >= 15),
        "t16": sum(1 for value in max_types if value >= 16),
        "russia": russia,
        "soviet": soviet,
        "instadeath": deaths,
        "progress_n": sum(1 for row in progress if row is not None),
    }


def _stamp(archives) -> tuple[str | None, str | None]:
    stamps: list[str] = []
    if isinstance(archives, list):
        for value in archives:
            if not isinstance(value, str) or len(value) > 256:
                continue
            match = STAMP_RE.search(value)
            if match:
                stamps.append(match.group(1))
    if not stamps:
        return None, None
    stamps.sort()
    return stamps[0], stamps[-1]


def _hash(value) -> str | None:
    text = str(value or "")
    return text if HASH_RE.fullmatch(text) else None


def _entry_summary(strategy_hash: str, entry: dict) -> dict:
    scores = _ints(entry.get("scores"))
    max_types = _ints(entry.get("max_types"))
    first_seen, last_seen = _stamp(entry.get("_recent_archives"))
    metrics = _metrics(scores, max_types, entry.get("progress"))
    metrics["russia_recorded"] = max(0, int(entry.get("russia_count", 0) or 0))
    metrics["soviet_recorded"] = max(0, int(entry.get("soviet_count", 0) or 0))
    metrics["best_max_type"] = max(0, int(entry.get("best_max_type", 0) or 0))
    return {
        "hash": strategy_hash,
        "games_total": max(0, int(entry.get("games_total", len(scores)) or len(scores))),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "metrics": metrics,
    }


def build_report(root: Path = PRODUCTION_ROOT) -> dict:
    current = _read_fixed_json(root, CURRENT_RUN_REL)
    rolling = _read_fixed_json(root, ROLLING_REL)
    current_hash = _hash(current.get("hash"))
    if current_hash is None:
        raise RuntimeError("current hash contract")

    scores = _ints(current.get("scores"))
    max_types = _ints(current.get("max_types"))
    fresh_n = max(0, min(len(scores), int(current.get("_fresh_score_count", 0) or 0)))
    seeded_n = max(0, min(len(scores), int(current.get("_seeded_score_count", 0) or 0)))
    progress = current.get("progress") if isinstance(current.get("progress"), list) else []
    fresh_scores = scores[-fresh_n:] if fresh_n else []
    fresh_types = max_types[-fresh_n:] if fresh_n and len(max_types) >= fresh_n else []
    fresh_progress = progress[-fresh_n:] if fresh_n and len(progress) >= fresh_n else []
    current_first, current_last = _stamp(current.get("_recent_archives"))

    candidates: list[dict] = []
    for key, value in rolling.items():
        strategy_hash = _hash(key)
        if strategy_hash is None or not isinstance(value, dict):
            continue
        summary = _entry_summary(strategy_hash, value)
        candidates.append(summary)
    candidates.sort(key=lambda item: (item.get("last_seen") or "", item.get("games_total") or 0), reverse=True)

    return {
        "schema": 1,
        "current": {
            "hash": current_hash,
            "games_total": max(0, int(current.get("games_total", len(scores)) or len(scores))),
            "seeded_n": seeded_n,
            "fresh_n": fresh_n,
            "first_seen": current_first,
            "last_seen": current_last,
            "fresh_metrics": _metrics(fresh_scores, fresh_types, fresh_progress),
            "retained_metrics": _metrics(scores, max_types, progress),
        },
        "recent_strategies": candidates[:MAX_RECENT_STRATEGIES],
    }


def prepare(root: Path = PRODUCTION_ROOT, now_ms: int | None = None) -> dict:
    exporter = _load_exporter()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    report = build_report(root)
    body = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("performance.json")
        info.size = len(body)
        info.mode = 0o600
        info.mtime = 0
        archive.addfile(info, io.BytesIO(body))
    bundle = buf.getvalue()
    if not bundle or len(bundle) > exporter.CHUNK_BYTES:
        raise RuntimeError("performance bundle contract")
    digest = hashlib.sha256(bundle).hexdigest()
    exporter._write_private(exporter._bundle_path(root), bundle)
    exporter._write_private(
        exporter._state_path(root),
        (json.dumps({
            "version": 1,
            "createdAtMs": now_ms,
            "expiresAtMs": now_ms + exporter.EXPORT_TTL_MS,
            "bundleBytes": len(bundle),
            "bundleSha256": digest,
            "chunkBytes": exporter.CHUNK_BYTES,
            "chunkCount": 1,
            "currentChunk": 0,
            "games": [0],
        }, sort_keys=True) + "\n").encode("utf-8"),
    )
    return report


def main(argv: list[str]) -> int:
    if argv != ["prepare"]:
        return 2
    try:
        prepare()
    except Exception:
        return 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
