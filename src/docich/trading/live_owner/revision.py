"""The git revision the running LIVE code is (used by ``activate``).

The enable file binds a policy set to a commit, so the owner CLI must name the same commit.  This is
the only module in the package allowed to run a process; it never uses a shell and passes an
argument list.  A checkout with modified tracked files is not a revision: the running code would
differ from the commit the enable file names.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

TIMEOUT_S = 10
_HEX40 = re.compile(r"[0-9a-f]{40}")


class RevisionError(RuntimeError):
    pass


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=str(repo_dir), capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RevisionError(f"cannot run git: {getattr(exc, 'strerror', None) or type(exc).__name__}") from exc


def current_revision(repo_dir: Path | None = None) -> str:
    """HEAD of the checkout, requiring a clean tracked tree (untracked files are ignored)."""
    root = Path(repo_dir) if repo_dir is not None else Path(__file__).resolve().parents[4]
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0 or _HEX40.fullmatch(head.stdout.strip()) is None:
        raise RevisionError("not a git checkout with a commit")
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        raise RevisionError("git status failed")
    if status.stdout.strip():
        raise RevisionError("the tracked tree is dirty: commit or revert before activating")
    return head.stdout.strip()
