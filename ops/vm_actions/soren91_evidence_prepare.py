#!/usr/bin/env python3
"""Prepare Soren91 manual evidence with a bounded sanitized failure envelope.

This wrapper exists only for the owner-only manual evidence workflow. On a
normal prepare it delegates to the reviewed exporter unchanged. If preparation
fails, it writes a tiny fixed-shape diagnostic bundle through the same
short-lived state contract so the read-only diagnostics gateway can report a
reason category without exposing paths, stderr, file contents, or secrets.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORTER_PATH = HERE / "soren91_evidence_export.py"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("soren91_evidence_export_runtime", EXPORTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("exporter unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REASON_CODES = frozenset({
    "no_recent_completed_evidence",
    "evidence_path_contract",
    "evidence_file_contract",
    "screenshot_transcode_failed",
    "screenshot_contract",
    "bundle_contract",
    "invalid_request",
    "io_error",
    "unexpected_error",
})


def classify_failure(exc: BaseException) -> str:
    text = str(exc)
    if text == "no completed Soren91 evidence in the last 72 hours":
        return "no_recent_completed_evidence"
    if text in {
        "path outside Soren91 runtime",
        "runtime root is symlink",
        "symlink evidence path rejected",
    }:
        return "evidence_path_contract"
    if text in {
        "required evidence missing",
        "evidence file outside size/type contract",
        "evidence file grew beyond limit",
    }:
        return "evidence_file_contract"
    if text in {"ffmpeg unavailable", "screenshot transcode failed"}:
        return "screenshot_transcode_failed"
    if text == "transcoded screenshot outside size contract":
        return "screenshot_contract"
    if text in {"evidence bundle outside size contract", "evidence chunk count outside contract"}:
        return "bundle_contract"
    if text in {"invalid game count", "invalid prepare arguments"}:
        return "invalid_request"
    if isinstance(exc, OSError):
        return "io_error"
    return "unexpected_error"


def _diagnostic_bundle(reason: str) -> bytes:
    if reason not in _REASON_CODES:
        reason = "unexpected_error"
    body = (json.dumps(
        {"schema": 1, "status": "prepare_failed", "reason": reason},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("diagnostic.json")
        info.size = len(body)
        info.mode = 0o600
        info.mtime = 0
        archive.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def write_failure_envelope(exporter, reason: str, *, root: Path, now_ms: int) -> None:
    bundle = _diagnostic_bundle(reason)
    if not bundle or len(bundle) > exporter.MAX_BUNDLE_BYTES:
        raise RuntimeError("diagnostic bundle contract failure")
    digest = hashlib.sha256(bundle).hexdigest()
    chunk_count = math.ceil(len(bundle) / exporter.CHUNK_BYTES)
    if not 1 <= chunk_count <= 256:
        raise RuntimeError("diagnostic chunk contract failure")
    exporter._write_private(exporter._bundle_path(root), bundle)
    state = {
        "version": 1,
        "createdAtMs": now_ms,
        "expiresAtMs": now_ms + exporter.EXPORT_TTL_MS,
        "bundleBytes": len(bundle),
        "bundleSha256": digest,
        "chunkBytes": exporter.CHUNK_BYTES,
        "chunkCount": chunk_count,
        "currentChunk": 0,
        # Sentinel used only by this private diagnostic envelope. The archive
        # itself identifies the failure contract and is never uploaded as a
        # successful evidence export.
        "games": [0],
    }
    exporter._write_private(
        exporter._state_path(root),
        (json.dumps(state, sort_keys=True) + "\n").encode("utf-8"),
    )


def prepare(game_count: int, *, root: Path | None = None, now_ms: int | None = None) -> int:
    exporter = _load_exporter()
    root = exporter.PRODUCTION_ROOT if root is None else Path(root)
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        exporter.prepare_export(root, game_count=game_count, now_ms=now_ms)
        return 0
    except Exception as exc:
        # Never persist the raw exception. Only one fixed enum leaves this
        # wrapper, and failure-envelope creation is itself best-effort.
        reason = classify_failure(exc)
        try:
            write_failure_envelope(exporter, reason, root=root, now_ms=now_ms)
        except Exception:
            pass
        return 20


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdigit():
        return 20
    count = int(argv[0])
    if not 1 <= count <= 3:
        return 20
    return prepare(count)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
