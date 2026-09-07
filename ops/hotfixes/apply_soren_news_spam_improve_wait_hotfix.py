#!/usr/bin/env python3
"""Apply the reviewed Soren NEWS spam improve-wait bound to the exact live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/broadcast/radio_news.sh")
OLD_SHA256 = "54c76ecce50aae4a1c372ba69dbfe3e5b936b78d3a749fbf7aaa2a49608391b8"
NEW_SHA256 = "3614091c7c2bd2ddd089ec5c72220fc47d3769a329ff37a084514233c6f52520"

OLD_FRAGMENT = '''\tverdict=$(ai_generate \\
\t\t"NEWS:spam_check" "$prompt_file" \\
\t\t"$primary_agent" "$fallback_agent" \\
\t\t"$spam_timeout" _news_spam_verdict_valid)'''
NEW_FRAGMENT = '''\t# スパム判定は補助フィルタであり、改善ジョブを最大20分待って
\t# ニュース枠そのものを遅延させてはいけない。provider timeout と同程度に
\t# improve gate 待ちも束縛し、打ち切り時は従来どおり fail-open とする。
\tlocal improve_wait_max="${NEWS_SPAM_CHECK_IMPROVE_WAIT_MAX_SEC:-$spam_timeout}"
\tverdict=$(AI_RADIO_IMPROVE_WAIT_MAX_SEC="$improve_wait_max" ai_generate \\
\t\t"NEWS:spam_check" "$prompt_file" \\
\t\t"$primary_agent" "$fallback_agent" \\
\t\t"$spam_timeout" _news_spam_verdict_valid)'''


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
        raise ValueError("hotfix output hash does not match reviewed soviet_now source")
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
