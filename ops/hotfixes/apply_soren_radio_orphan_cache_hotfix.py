#!/usr/bin/env python3
"""Apply reviewed Soren orphan radio render-cache cleanup to the exact live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/broadcast/radio_state.sh")
OLD_SHA256 = "bb14926fb71dc4516b7a8651aa77f0ab3dec4af4c3c5575da27df902c6c8ef2c"
NEW_SHA256 = "0c81ef551a7647ae393a8a876478d5e30bb1e3a9665af120f5f6ed2b542a3ea6"

REPLACEMENTS = (
    (
        '_radio_deferred_queue_count() {\n'
        '\tlocal queue_dir="${RADIO_DEFERRED_QUEUE_DIR:-tmp/.radio_deferred_queue}"\n'
        '\t[ -d "$queue_dir" ] || { printf \'0\'; return 0; }\n'
        '\tfind "$queue_dir" -maxdepth 1 -name \'radio_*.txt\' -type f 2>/dev/null | wc -l | tr -d \' \'\n'
        '}\n\n'
        '_enqueue_deferred_radio_talk() {\n',
        '_radio_deferred_queue_count() {\n'
        '\tlocal queue_dir="${RADIO_DEFERRED_QUEUE_DIR:-tmp/.radio_deferred_queue}"\n'
        '\t[ -d "$queue_dir" ] || { printf \'0\'; return 0; }\n'
        '\tfind "$queue_dir" -maxdepth 1 -name \'radio_*.txt\' -type f 2>/dev/null | wc -l | tr -d \' \'\n'
        '}\n\n'
        '# 完了済み/中断済みの事前合成で本文が消えた後も ready.wav だけ残ると、\n'
        '# 数十MB級の音声キャッシュが無期限に蓄積する。queue本文(.txt)または再生中\n'
        '# (.playing)が存在する世代は必ず保持し、それ以外の古いrender artifactだけを\n'
        '# grace period後に回収する。\n'
        '_radio_cleanup_orphan_render_artifacts() {\n'
        '\tlocal queue_dir="${RADIO_DEFERRED_QUEUE_DIR:-tmp/.radio_deferred_queue}"\n'
        '\tlocal ttl="${RADIO_ORPHAN_RENDER_TTL_SEC:-86400}" now wav base mtime age removed=0\n'
        '\t[ -d "$queue_dir" ] || return 0\n'
        '\tcase "$ttl" in \'\' | *[!0-9]*) ttl=86400 ;; esac\n'
        '\tnow=$(date +%s)\n'
        '\tfor wav in "$queue_dir"/radio_*.ready.wav; do\n'
        '\t\t[ -f "$wav" ] || continue\n'
        '\t\tbase="${wav%.ready.wav}"\n'
        '\t\t[ -f "${base}.txt" ] && continue\n'
        '\t\t[ -f "${base}.playing" ] && continue\n'
        '\t\tmtime=$(stat -f %m "$wav" 2>/dev/null) \\\n'
        '\t\t\t|| mtime=$(stat -c %Y "$wav" 2>/dev/null) \\\n'
        '\t\t\t|| mtime=""\n'
        '\t\tcase "$mtime" in \'\' | *[!0-9]*) continue ;; esac\n'
        '\t\tage=$((now - mtime))\n'
        '\t\t[ "$age" -lt 0 ] && age=0\n'
        '\t\t[ "$age" -le "$ttl" ] && continue\n'
        '\t\trm -f "$wav" "${wav}.tmp" "${base}.render_meta" "${base}.render_retry" "${base}.rendering" 2>/dev/null || true\n'
        '\t\trm -rf "${wav}.bundle" 2>/dev/null || true\n'
        '\t\tremoved=$((removed + 1))\n'
        '\tdone\n'
        '\tif [ "$removed" -gt 0 ]; then\n'
        '\t\tlog "[RADIO:deferred] orphan render cache cleanup: ${removed} item(s)"\n'
        '\tfi\n'
        '\treturn 0\n'
        '}\n\n'
        '_enqueue_deferred_radio_talk() {\n',
    ),
    (
        '_play_deferred_radio_queue_once() {\n'
        '\t# コメント未消化がある間は deferred ラジオを再生しない\n',
        '_play_deferred_radio_queue_once() {\n'
        '\t_radio_cleanup_orphan_render_artifacts\n'
        '\t# コメント未消化がある間は deferred ラジオを再生しない\n',
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise ValueError("reviewed hotfix preimage fragment mismatch")
        text = text.replace(old, new, 1)
    return text


def apply(path: Path = TARGET) -> str:
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
