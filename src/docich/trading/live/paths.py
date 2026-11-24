"""Layout of the LIVE state dir.  Pure functions except ``ensure_live_root``."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

LIVE_DIRNAME = "trading-live"


class LiveLayoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveLayout:
    root: Path
    ledger_path: Path
    lock_path: Path
    owner_dir: Path
    control_path: Path
    policy_dir: Path


def live_layout(state_dir) -> LiveLayout:
    root = Path(state_dir) / LIVE_DIRNAME
    owner = root / "owner"
    return LiveLayout(root=root, ledger_path=root / "live.sqlite3",
                      lock_path=root / "live-writer.lock", owner_dir=owner,
                      control_path=owner / "live_control.json", policy_dir=owner / "policy")


def _overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def ensure_live_root(layout: LiveLayout, *, paper_dir) -> None:
    """Create the LIVE root (0700).  Refuses symlinks and PAPER overlap."""
    root = layout.root
    if _overlaps(root, Path(paper_dir)):
        raise LiveLayoutError("LIVE root must not overlap the PAPER dir")
    if root.is_symlink():
        raise LiveLayoutError("LIVE root must not be a symlink")
    if root.exists() and not root.is_dir():
        raise LiveLayoutError("LIVE root is not a directory")
    if not root.exists():
        root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
