#!/usr/bin/env python3
"""Reassemble bounded Soren91 evidence chunks from a diagnostics envelope."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_BUNDLE_BYTES = 3 * 1024 * 1024
MAX_CHUNKS = 256
PART_CHARS = 480


def fail(message: str) -> None:
    raise SystemExit(message)


def _payload(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    item = (data.get("diagnostics") or {}).get("soren91_manual_evidence")
    if not isinstance(item, dict) or item.get("active") is not True:
        fail("Soren91 evidence export chunk unavailable")
    return item


def _identity(item: dict) -> dict:
    result = {
        "bundleBytes": item.get("bundleBytes"),
        "bundleSha256": item.get("bundleSha256"),
        "chunkBytes": item.get("chunkBytes"),
        "chunkCount": item.get("chunkCount"),
        "games": item.get("games"),
    }
    if (
        not isinstance(result["bundleBytes"], int)
        or not 1 <= result["bundleBytes"] <= MAX_BUNDLE_BYTES
        or not isinstance(result["bundleSha256"], str)
        or not SHA_RE.fullmatch(result["bundleSha256"])
        or result["chunkBytes"] != 24 * 1024
        or not isinstance(result["chunkCount"], int)
        or not 1 <= result["chunkCount"] <= MAX_CHUNKS
        or not isinstance(result["games"], list)
        or not 1 <= len(result["games"]) <= 3
        or any(isinstance(game, bool) or not isinstance(game, int) or game < 0 for game in result["games"])
    ):
        fail("invalid Soren91 evidence export metadata")
    return result


def append_chunk(envelope: Path, expected_index: int, output: Path, metadata: Path) -> int:
    item = _payload(envelope)
    identity = _identity(item)
    if item.get("chunkIndex") != expected_index:
        fail("unexpected Soren91 evidence chunk index")
    parts = item.get("parts")
    if (
        not isinstance(parts, list)
        or not parts
        or len(parts) > 100
        or any(not isinstance(part, str) or len(part) > PART_CHARS for part in parts)
    ):
        fail("invalid Soren91 evidence chunk parts")
    try:
        raw = base64.b64decode("".join(parts), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        fail(f"invalid Soren91 evidence chunk encoding: {type(exc).__name__}")
    if not raw or len(raw) > identity["chunkBytes"]:
        fail("invalid Soren91 evidence chunk size")

    if expected_index == 0:
        metadata.write_text(json.dumps(identity, sort_keys=True) + "\n", encoding="utf-8")
        output.write_bytes(raw)
    else:
        expected = json.loads(metadata.read_text(encoding="utf-8"))
        if identity != expected:
            fail("Soren91 evidence export identity changed during transfer")
        with output.open("ab") as handle:
            handle.write(raw)
    return identity["chunkCount"]


def verify(output: Path, metadata: Path) -> None:
    identity = json.loads(metadata.read_text(encoding="utf-8"))
    data = output.read_bytes()
    if len(data) != identity.get("bundleBytes"):
        fail("Soren91 evidence export size mismatch")
    if hashlib.sha256(data).hexdigest() != identity.get("bundleSha256"):
        fail("Soren91 evidence export digest mismatch")


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "append" and len(argv) == 5:
        expected = int(argv[2])
        count = append_chunk(Path(argv[1]), expected, Path(argv[3]), Path(argv[4]))
        print(count)
        return 0
    if len(argv) == 4 and argv[0] == "verify":
        verify(Path(argv[1]), Path(argv[2]))
        print("verified")
        return 0
    fail("usage: append ENVELOPE INDEX OUTPUT META | verify OUTPUT META")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
