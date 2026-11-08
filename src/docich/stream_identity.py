"""Who is the live encoder runner? (#3553, ADR 0007 stage D2)

Pure command-line classification shared by the operator tools that must work
with either owner of the single encoder:

* ``legacy``: ``python3 <soren_root>/lib/direct_stream.py run`` (the
  ``start_all.sh`` supervisor's worker),
* ``docich``: ``python3 -m docich.stream_supervisor [--root <soren_root>] run``
  (``docich-stream.service``).

Both shapes are matched *exactly* (interpreter, script or module, root, action)
so an unrelated process, a ``status`` invocation or a runner of another tree is
never taken for the encoder owner. Nothing here reads /proc or signals a
process: callers pass the argv and working directory they already verified.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Sequence

STREAM_IDENTITY_CONTRACT_VERSION = 1
DOCICH_ENTRY_MODULE = "docich.stream_supervisor"
LEGACY_SCRIPT_RELATIVE = "lib/direct_stream.py"
PYTHON = re.compile(r"python3(?:\.[0-9]+)?")


def _is_python(argv0: str) -> bool:
    return PYTHON.fullmatch(Path(argv0).name) is not None


def is_legacy_runner_args(args: Sequence[str], soren_root: str | Path, *, cwd: str | Path | None = None) -> bool:
    """``python3 <root>/lib/direct_stream.py run`` (relative path needs cwd == root)."""
    if len(args) != 3 or not _is_python(args[0]) or args[2] != "run":
        return False
    root = Path(soren_root)
    script = Path(args[1])
    if not script.is_absolute():
        if cwd is None or Path(cwd) != root:
            return False
        script = root / script
    return script == root / LEGACY_SCRIPT_RELATIVE


def is_docich_runner_args(args: Sequence[str], soren_root: str | Path) -> bool:
    """``python3 -m docich.stream_supervisor [--root <root>] run``."""
    if len(args) < 4 or not _is_python(args[0]) or args[1] != "-m" or args[2] != DOCICH_ENTRY_MODULE:
        return False
    rest = list(args[3:])
    if rest and rest[0] == "--root":
        if len(rest) != 3 or Path(rest[1]) != Path(soren_root):
            return False
        rest = rest[2:]
    return rest == ["run"]


def runner_kind(args: Sequence[str], soren_root: str | Path, *, cwd: str | Path | None = None) -> str | None:
    """``"legacy"``, ``"docich"`` or ``None``."""
    if is_legacy_runner_args(args, soren_root, cwd=cwd):
        return "legacy"
    if is_docich_runner_args(args, soren_root):
        return "docich"
    return None
