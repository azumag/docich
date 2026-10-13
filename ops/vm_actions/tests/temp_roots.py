"""Temp roots whose absolute path contains no symlinked component.

``hanjuku_evidence._directory`` deliberately re-walks every path component with
``O_NOFOLLOW`` before it touches evidence.  Two legitimate parts of the local
test environment violate that: macOS keeps the session temp directory under the
``/var`` symlink, and the repository ``conftest.py`` routes ``tempfile`` through
a short ``pyt-*`` symlink so AF_UNIX ``sun_path`` stays inside its limit.

Neither the production guard nor the socket handling is changed for that, so the
tests that exercise the no-follow walker ask for a *real* directory instead.
``os.path.realpath`` resolves both the ``/var`` and the ``pyt-*`` component, and
the result stays inside pytest's ``basetemp`` (so leftovers are still pruned)
when the repository redirect is active.
"""

from __future__ import annotations

import os
import tempfile


def real_temp_root() -> str:
    """Return a temp root whose resolved path has no symlinked component."""
    return os.path.realpath(tempfile.gettempdir())


def real_tempdir(**kwargs) -> tempfile.TemporaryDirectory:
    """``tempfile.TemporaryDirectory`` rooted in a symlink-free directory."""
    return tempfile.TemporaryDirectory(dir=real_temp_root(), **kwargs)
