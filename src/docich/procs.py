"""Subprocess execution helpers shared by all docich modules."""
from __future__ import annotations

import os
import shutil
import subprocess

EXTRA_PATH_DIRS = ("/usr/games", "/usr/local/games")


def _build_env(env_extra: dict | None, strip_tmux: bool) -> dict:
    env = os.environ.copy()
    extra = os.pathsep.join(EXTRA_PATH_DIRS)
    path = env.get("PATH", "")
    env["PATH"] = f"{path}{os.pathsep}{extra}" if path else extra
    if strip_tmux:
        # ネストした tmux から呼ばれても新しい tmux/シェルが拒否されないように剥がす
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    return env


def run(
    cmd: list[str],
    *,
    check: bool = False,
    timeout: float | None = None,
    env_extra: dict | None = None,
    capture: bool = True,
    strip_tmux: bool = False,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    env = _build_env(env_extra, strip_tmux)
    kwargs: dict = {}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        kwargs["input"] = input
    return subprocess.run(
        cmd,
        env=env,
        timeout=timeout,
        text=True,
        check=check,
        **kwargs,
    )


def which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    search_path = os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(EXTRA_PATH_DIRS)
    return shutil.which(name, path=search_path)


def spawn(
    cmd: list[str],
    *,
    env_extra: dict | None = None,
    strip_tmux: bool = False,
    log_fh=None,
) -> subprocess.Popen:
    env = _build_env(env_extra, strip_tmux)
    stdout = log_fh if log_fh is not None else None
    stderr = log_fh if log_fh is not None else None
    return subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
