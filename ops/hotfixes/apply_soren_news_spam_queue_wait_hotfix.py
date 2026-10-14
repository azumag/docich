#!/usr/bin/env python3
"""Apply reviewed Soren generation-queue fixes to exact production preimages."""
from __future__ import annotations

import gzip
import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

SOREN_ROOT = Path("/home/ubuntu/soren")
PAYLOAD_DIR = Path(__file__).resolve().parent / "payloads" / "soren_6a59ea7"
# Production is missing the queue-owner serialization commit as well as #203.
# The payloads are exactly the two affected files at the #203 merge commit.
QUEUE_OWNER_FIX_COMMIT = "74ecf1da7309db065b1ce49ac1f56ab6b6c661f4"
NEWS_QUEUE_FIX_COMMIT = "6a59ea7bb7c7affb44937fa2d0ac58af99fc932c"


class PatchSpec(NamedTuple):
    relpath: str
    old_sha256: str
    new_sha256: str
    payload_name: str


SPECS = (
    PatchSpec(
        "lib/ai_generate.sh",
        "8b43d7ddc305d5ec80e8c78aa1c35ade5f392979078564a06611b0bceb2fbd4a",
        "d417e583ad0dcb7afe5faae37de1c07e66d9342172749fa66aea8646e76cc9b1",
        "lib_ai_generate.sh.gz",
    ),
    PatchSpec(
        "broadcast/radio_news.sh",
        "3614091c7c2bd2ddd089ec5c72220fc47d3769a329ff37a084514233c6f52520",
        "c3faf52e8b8f6920d804b28f8bd3f539a93210f6d86c466297fa8993c0e70cf3",
        "broadcast_radio_news.sh.gz",
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_replace(path: Path, data: bytes) -> None:
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, stat.S_IMODE(st.st_mode))
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def apply(root: Path = SOREN_ROOT, payload_dir: Path = PAYLOAD_DIR) -> str:
    prepared: list[tuple[Path, PatchSpec, bytes]] = []
    already = 0

    # Preflight every target and payload before changing either production file.
    for spec in SPECS:
        path = root / spec.relpath
        payload = payload_dir / spec.payload_name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{spec.relpath}: target must be a regular non-symlink file")
        if payload.is_symlink() or not payload.is_file():
            raise ValueError(f"{spec.relpath}: reviewed payload missing or unsafe")
        try:
            postimage = gzip.decompress(payload.read_bytes())
        except (OSError, EOFError) as exc:
            raise ValueError(f"{spec.relpath}: reviewed payload is not valid gzip") from exc
        if sha256(postimage) != spec.new_sha256:
            raise ValueError(f"{spec.relpath}: reviewed payload hash mismatch")

        raw = path.read_bytes()
        current = sha256(raw)
        if current == spec.new_sha256:
            already += 1
            continue
        if current != spec.old_sha256:
            raise ValueError(f"refusing unreviewed live drift: {spec.relpath} sha256={current}")
        prepared.append((path, spec, postimage))

    for path, spec, postimage in prepared:
        _atomic_replace(path, postimage)
        if sha256(path.read_bytes()) != spec.new_sha256:
            raise RuntimeError(f"post-replace verification failed: {spec.relpath}")

    return "already_applied" if already == len(SPECS) else "applied"


def main() -> int:
    if len(sys.argv) != 1:
        print("no arguments accepted", file=sys.stderr)
        return 2
    try:
        result = apply()
    except Exception as exc:
        print(f"hotfix refused: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
