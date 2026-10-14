from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def activity_lock(state_dir: Path, game_name: str):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",game_name):
        raise ValueError("invalid resolver game name")
    lock_dir=Path(state_dir)/"resolver"/"locks"
    lock_dir.mkdir(parents=True,exist_ok=True)
    os.chmod(lock_dir,0o700)
    path=lock_dir/f"{game_name}.lock"
    with path.open("a+") as fh:
        os.chmod(path,0o600)
        fcntl.flock(fh.fileno(),fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
