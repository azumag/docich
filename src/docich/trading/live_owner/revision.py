"""The git revision the running LIVE code is (used by ``activate``).

The enable file binds a policy set to a commit, so the owner CLI must name the same commit.  This is
the only module in the package allowed to run a process; it never uses a shell and passes an
argument list.  A checkout with modified tracked files is not a revision: the running code would
differ from the commit the enable file names.

Every call runs with all inherited ``GIT_*`` variables removed, so ``GIT_DIR``, ``GIT_WORK_TREE`` or
``GIT_INDEX_FILE`` can never point these commands at another repository, and the directory asked
about must be the top of its own checkout (a parent repository's HEAD is not this code's HEAD).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

TIMEOUT_S = 10
_HEX40 = re.compile(r"[0-9a-f]{40}")
_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"}


class RevisionError(RuntimeError):
    pass


def _clean_env() -> dict[str, str]:
    """The process environment without any ``GIT_*`` variable, plus a fixed git configuration."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env.update(_GIT_ENV)
    return env


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=str(repo_dir), capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False, env=_clean_env())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RevisionError(f"cannot run git: {getattr(exc, 'strerror', None) or type(exc).__name__}") from exc


def current_revision(repo_dir: Path | None = None) -> str:
    """HEAD of the checkout, requiring a clean tracked tree and the checkout's own top level."""
    root = Path(repo_dir) if repo_dir is not None else Path(__file__).resolve().parents[4]
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        raise RevisionError("not a git checkout with a commit")
    if Path(top.stdout.strip()).resolve() != Path(root).resolve():
        raise RevisionError("the package is not at the top of its own git checkout")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0 or _HEX40.fullmatch(head.stdout.strip()) is None:
        raise RevisionError("not a git checkout with a commit")
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        raise RevisionError("git status failed")
    if status.stdout.strip():
        raise RevisionError("the tracked tree is dirty: commit or revert before activating")
    return head.stdout.strip()
