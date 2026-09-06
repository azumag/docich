#!/usr/bin/env python3
"""Apply the reviewed #182 improve-model-list fix to the exact live config preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/core/config.sh")
OLD_SHA256 = "19b8728b5ebaa8819ef65cefbe991d4bb3e0dafad5beb2ecbed77737da9182b4"
NEW_SHA256 = "46e5d2a90f05c62755665bcda121403c1610bce2e5dd07405b0c4038d775fd6a"

OLD_FRAGMENT = (
    '# MODEL_IMPROVE_LIST: 改善ループのリスト。共通チェーンから local と openrouter/free を\n'
    '# 除いたもの。run_ai_list() が順に試行する。\n'
    'MODEL_IMPROVE_LIST="${MODEL_IMPROVE_LIST:-opencode:muse-spark-1.3-contributor-free,opencode:muse-spark-1.2-contributor-free,amd:DeepSeek-V4-Flash,minimax-api:MiniMax-M3,opencode-go:omen-alpha,opencode-go:muse-spark-1.3-contributor,opencode-go:muse-spark-1.2-contributor,opencode-go:deepseek-v4-flash}"\n'
)
NEW_FRAGMENT = (
    '# MODEL_IMPROVE_LIST: 改善ループのリスト。共通チェーンから local と openrouter/free を\n'
    '# 除き、strategy/ai.sh の run_cmd() がモデル指定を保持できる opencode/opencode-go のみ。\n'
    '# amd:/minimax-api: は run_cmd() では legacy codex 正規化され指定モデルが保持されないため含めない。\n'
    'MODEL_IMPROVE_LIST="${MODEL_IMPROVE_LIST:-opencode:muse-spark-1.3-contributor-free,opencode:muse-spark-1.2-contributor-free,opencode-go:omen-alpha,opencode-go:muse-spark-1.3-contributor,opencode-go:muse-spark-1.2-contributor,opencode-go:deepseek-v4-flash}"\n'
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    if text.count(OLD_FRAGMENT) != 1:
        raise ValueError("reviewed hotfix preimage fragment mismatch")
    return text.replace(OLD_FRAGMENT, NEW_FRAGMENT, 1)


def apply(path: Path = TARGET) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("target must be a regular non-symlink file")
    raw = path.read_bytes()
    current = sha256(raw)
    if current == NEW_SHA256:
        return "already_applied"
    if current != OLD_SHA256:
        raise ValueError(f"refusing unreviewed live drift: sha256={current}")
    updated = transform(raw.decode("utf-8")).encode("utf-8")
    if sha256(updated) != NEW_SHA256:
        raise ValueError("hotfix output hash does not match reviewed soviet_now main")
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(updated)
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
    if sha256(path.read_bytes()) != NEW_SHA256:
        raise RuntimeError("post-replace verification failed")
    return "applied"


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
