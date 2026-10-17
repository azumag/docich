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

import ctypes
import ctypes.util
import fcntl
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

# Canonical pytest runner contract: every test file can rely on
# ``from docich import ...`` without its own sys.path bootstrap and
# regardless of collection order or standalone invocation
# (``pytest tests/test_x.py``).  Per-file bootstraps remain harmless.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

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


# ---------------------------------------------------------------------------
# Local machine resource policy.
#
# The full suite is ~14.5k tests (~10 CPU-minutes) and leaves ~28k temporary
# entries per run; CI already runs it on every PR.  On the shared developer Mac
# several agents used to run it at the same time with ``-n 8``: every core was
# saturated and fseventsd stayed busy, so interactive apps became sluggish.
# Local runs on macOS therefore
#
# * lower every pytest process (controller and xdist workers; subprocesses
#   inherit both settings) to ``nice`` 10 and the "utility" disk I/O tier, so
#   interactive apps win CPU and disk contention.  (Full background QoS was
#   measured and rejected: it confines work to the efficiency cores and
#   throttles I/O so hard that a 25-test file took 6.9s instead of 0.25s on
#   a Mac whose disk is busy with swap.)
# * cap pytest-xdist at ``DOCICH_TEST_MAX_WORKERS`` workers (default 4, as CI);
# * run at most one full-suite session at a time on the machine: a later
#   full-suite session waits (``DOCICH_TEST_LOCK_WAIT`` seconds at most,
#   default 1800) until the earlier one finishes.
#
# CI (``CI`` set) and non-macOS hosts are untouched.  ``DOCICH_TEST_FOREGROUND=1``
# opts out of all three for a deliberate, attended run.
# ---------------------------------------------------------------------------
_DEFAULT_MAX_WORKERS = 4
_DEFAULT_LOCK_WAIT_SECONDS = 1800
_full_suite_lock_fd: int | None = None


def _local_resource_policy_enabled(environ=os.environ, platform: str = sys.platform) -> bool:
    return (
        platform == "darwin"
        and not environ.get("CI")
        and environ.get("DOCICH_TEST_FOREGROUND") != "1"
    )


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


_NICE_INCREMENT = 10
_IOPOL_TYPE_DISK = 0
_IOPOL_SCOPE_PROCESS = 0
_IOPOL_UTILITY = 4


def _enter_low_priority() -> None:
    """``nice`` 10 plus the utility disk I/O tier for this process and its children."""
    try:
        if os.getpriority(os.PRIO_PROCESS, 0) < _NICE_INCREMENT:
            os.nice(_NICE_INCREMENT)
    except OSError:
        pass
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        libc.setiopolicy_np(_IOPOL_TYPE_DISK, _IOPOL_SCOPE_PROCESS, _IOPOL_UTILITY)
    except (AttributeError, OSError):
        pass


def _cap_xdist_workers(option, cap: int) -> int | None:
    """Shrink ``-n N`` (already expanded by xdist into ``tx``) to ``cap``.

    Returns the original worker count when it was capped, otherwise ``None``.
    Only plain local ``popen`` workers are touched.
    """
    tx = getattr(option, "tx", None)
    if not tx or len(tx) <= cap or any(spec != "popen" for spec in tx):
        return None
    original = len(tx)
    option.tx = list(tx[:cap])
    option.numprocesses = cap
    return original


def _is_full_suite_run(option, args, rootpath: Path, invocation_dir: Path) -> bool:
    """True when the session targets the whole ``tests/`` tree without filters."""
    if (
        getattr(option, "keyword", "")
        or getattr(option, "markexpr", "")
        or getattr(option, "lf", False)
        or getattr(option, "deselect", None)
        or getattr(option, "collectonly", False)
    ):
        return False
    root = rootpath.resolve()
    whole = {root, root / "tests"}
    if not args:
        return True
    for arg in args:
        target = Path(str(arg).split("::", 1)[0])
        if not target.is_absolute():
            target = invocation_dir / target
        if target.resolve() in whole:
            return True
    return False


def _full_suite_lock_path() -> Path:
    # /tmp rather than ~/.cache: sandboxed agents (e.g. Codex) may write only
    # to their cwd and /tmp, and the lock must be shared by every checkout.
    return Path("/tmp") / f"docich-pytest-full-suite-{os.getuid()}.lock"


def _acquire_full_suite_lock(lock_path: Path, max_wait: float, *, sleep=time.sleep, stderr=sys.stderr) -> int | None:
    """Block until no other local full-suite session runs, then hold the lock.

    The lock is an ``flock`` on an open descriptor, so the kernel releases it
    when the process exits for any reason; there are no stale locks.
    Returns the descriptor (keep it open) or ``None`` when locking failed or
    the wait timed out, in which case the session proceeds unlocked.
    """
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        print(f"docich conftest: full-suite lock unavailable ({exc}); running without it", file=stderr)
        return None
    waited = 0.0
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if waited == 0.0 or int(waited) % 60 == 0:
                holder = os.pread(fd, 512, 0).decode("utf-8", "replace").strip() or "unknown"
                print(
                    "docich conftest: another local full-suite run is in progress "
                    f"({holder}); waiting {waited:.0f}s so far. Run only the affected test files, "
                    "or let CI run the full suite.",
                    file=stderr,
                    flush=True,
                )
            if waited >= max_wait:
                print(f"docich conftest: gave up waiting after {waited:.0f}s; running without the lock", file=stderr)
                os.close(fd)
                return None
            sleep(5)
            waited += 5
        except OSError as exc:
            print(f"docich conftest: full-suite lock failed ({exc}); running without it", file=stderr)
            os.close(fd)
            return None
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"pid={os.getpid()} cwd={os.getcwd()} since={time.strftime('%H:%M:%S')}".encode(), 0)
    return fd


class _UncapturedStderr:
    """Write to the real stderr even while pytest's global capture is active.

    pytest captures fd 2 before ``pytest_configure``; without suspending it a
    session that waits for the lock would stay silent until it exits.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._capman = config.pluginmanager.getplugin("capturemanager")

    def write(self, text: str) -> int:
        if self._capman is None:
            return sys.stderr.write(text)
        with self._capman.global_and_fixture_disabled():
            written = sys.stderr.write(text)
            sys.stderr.flush()
        return written

    def flush(self) -> None:
        pass


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    global _full_suite_lock_fd
    if not _local_resource_policy_enabled():
        return
    _enter_low_priority()
    if hasattr(config, "workerinput"):  # pytest-xdist worker: priority only
        return
    cap = _env_int("DOCICH_TEST_MAX_WORKERS", _DEFAULT_MAX_WORKERS, 1)
    original = _cap_xdist_workers(config.option, cap)
    if original is not None:
        print(
            f"docich conftest: local run limited to {cap} xdist workers (requested {original}); "
            "set DOCICH_TEST_MAX_WORKERS to change it",
            file=_UncapturedStderr(config),
        )
    if _is_full_suite_run(config.option, config.args, config.rootpath, config.invocation_params.dir):
        _full_suite_lock_fd = _acquire_full_suite_lock(
            _full_suite_lock_path(),
            _env_int("DOCICH_TEST_LOCK_WAIT", _DEFAULT_LOCK_WAIT_SECONDS, 0),
            stderr=_UncapturedStderr(config),
        )
