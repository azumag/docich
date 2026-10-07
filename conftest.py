"""Repository-wide pytest configuration.

Redirect every temporary file/directory created during a test session into
pytest's own ``basetemp`` (``$TMPDIR/pytest-of-<user>/pytest-N``).

Many tests (and the production code they exercise, e.g. ``tempfile.mkdtemp(
prefix="docich-tts-")``) create scratch directories via :mod:`tempfile` and
never remove them.  Without this fixture those directories accumulate in the
real ``$TMPDIR`` (tens of thousands of entries were observed on a developer
machine, keeping ``fseventsd`` busy).  pytest prunes ``basetemp`` automatically
(only the most recent three sessions are kept), so routing ``tempfile`` there
makes the leftovers self-cleaning without touching individual tests.

The redirect target is exposed through a *short* symlink placed next to the
original temp directory (``$TMPDIR/pyt-<pid>``) rather than the long basetemp
path itself: several tests bind AF_UNIX sockets under ``tempfile.mkdtemp()``
and the ``sun_path`` limit (104 bytes on macOS) is exceeded when the full
``pytest-of-<user>/pytest-N/tmp`` prefix is used.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP_ENV_VARS = ("TMPDIR", "TEMP", "TMP")
_LINK_PREFIX = "pyt-"


def _prune_dangling_links(parent: Path) -> None:
    """Remove ``pyt-*`` symlinks left behind by sessions that died abruptly."""
    try:
        entries = list(parent.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith(_LINK_PREFIX) and entry.is_symlink() and not entry.exists():
            try:
                entry.unlink()
            except OSError:
                pass


@pytest.fixture(scope="session", autouse=True)
def _redirect_tempfile_to_pytest_basetemp(tmp_path_factory: pytest.TempPathFactory):
    target = tmp_path_factory.getbasetemp() / "tmp"
    target.mkdir(parents=True, exist_ok=True)

    original_tmp = Path(tempfile.gettempdir())
    _prune_dangling_links(original_tmp)
    # NOTE: ``os.getpid()`` alone is not unique when pytest runs in separate
    # PID namespaces sharing one filesystem (e.g. parallel ``exec_command``
    # sandboxes where every process sees pid 2).  A bare ``pyt-<pid>`` link
    # would then be unlinked by a peer session while still in use.  Append a
    # random suffix so each session owns its link, and only ever remove it
    # when it still points at our own basetemp target.
    link = original_tmp / f"{_LINK_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target, target_is_directory=True)

    saved_env = {name: os.environ.get(name) for name in _TMP_ENV_VARS}
    saved_tempdir = tempfile.tempdir

    for name in _TMP_ENV_VARS:
        os.environ[name] = str(link)
    # ``tempfile`` caches the resolved directory; reset so the new env applies
    # to this process.  Child processes inherit ``TMPDIR`` from ``os.environ``.
    tempfile.tempdir = None
    assert tempfile.gettempdir() == str(link)

    try:
        yield target
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tempfile.tempdir = saved_tempdir
        try:
            # Only remove the link we created: a peer in another PID
            # namespace may share our pid but never our uuid suffix, and a
            # stale path must never delete someone else's live link.
            if link.is_symlink() and os.readlink(link) == str(target):
                link.unlink()
        except OSError:
            pass
