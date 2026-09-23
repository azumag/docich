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
    link = original_tmp / f"{_LINK_PREFIX}{os.getpid()}"
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
            link.unlink()
        except OSError:
            pass
