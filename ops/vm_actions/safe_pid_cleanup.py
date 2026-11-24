#!/usr/bin/env python3
"""Read-only PID file diagnostics; deletion is disabled.

The legacy safe_unlink_pid_file() and safe_cleanup_stale_pid_files() names
are retained for compatibility. They never unlink PID files and always
return success=False, including with dry_run=True. Existing path, owner and
process checks provide diagnostic reasons only. A stale observation returns
"cleanup_disabled:no_writer_lock"; it does not authorize later deletion.

There is no exclusion contract shared with PID file writers. Even a final
stat/inode comparison would leave a race before unlink(path), so this generic
helper fails closed without attempting deletion. Diagnostics can become stale
as soon as they are observed. Cleanup belongs to a separately reviewed owner
lifecycle with a writer coordination contract, not to this module or CLI.

Usage:
    from safe_pid_cleanup import safe_unlink_pid_file
    _, reason = safe_unlink_pid_file(path, root="/path/to/soren")
    print(f"Retained: {reason}")
"""

import os


# Allowlisted subdirectories relative to root.
ALLOWED_RELPATHS = ("tmp/state", "tmp/.soren_loop.lock")

# Known worker PID file names (from runtime_registry).
KNOWN_PID_NAMES = None  # populated lazily


def _is_under_allowed_dir(path, root):
    """Check that path is under one of the allowlisted directories."""
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(root)
    except (OSError, ValueError):
        return False
    rel = os.path.relpath(real_path, real_root)
    if rel.startswith(".."):
        return False
    # Check that the first component(s) match an allowed prefix.
    parts = rel.split(os.sep)
    if len(parts) < 2:
        return False
    # tmp/state/... or tmp/.soren_loop.lock/...
    if parts[0] != "tmp":
        return False
    if parts[1] not in ("state", ".soren_loop.lock"):
        return False
    # Must be at least tmp/state/<name>.pid or tmp/.soren_loop.lock/pid
    if len(parts) < 3:
        return False
    return True


def _is_symlink(path):
    """Check if path is a symlink."""
    try:
        return os.path.islink(path)
    except OSError:
        return True  # fail closed


def _is_hardlink(path):
    """Check if path has more than one hard link (nlink > 1)."""
    try:
        st = os.lstat(path)
        return st.st_nlink > 1
    except OSError:
        return False  # file doesn't exist or can't stat


def _owner_matches(path, expected_uid=None):
    """Check that file owner matches expected UID."""
    if expected_uid is None:
        expected_uid = os.getuid()
    try:
        st = os.lstat(path)
        return st.st_uid == expected_uid
    except OSError:
        return False  # fail closed


def _read_pid(path):
    """Read PID from file. Returns (pid, error_reason)."""
    try:
        with open(path, "r") as f:
            raw = f.read(256)
    except OSError:
        return None, "unreadable"
    first = raw.strip().split()
    for token in first:
        if token.isdigit():
            return int(token), None
    return None, "no_pid_found"


def _pid_is_running(pid):
    """Check if PID is currently running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but we can't signal it
    except OSError:
        return False
    return True


def _pid_is_zombie(pid):
    """Check if PID is a zombie process."""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read()
        # State is the 3rd field (after pid and comm)
        # comm may contain spaces/parens, so find last ')'
        idx = stat.rfind(")")
        if idx == -1:
            return False
        state = stat[idx + 2:idx + 3]
        return state == "Z"
    except (OSError, IOError):
        return False


def _pid_reused(pid, expected_cmdline_substring=None):
    """Check if PID has been reused by a different process.

    If expected_cmdline_substring is provided, checks that the process
    command line contains it. If not provided, checks that the process
    is a known worker process (not a system process that happened to
    reuse the PID).

    Returns True if the PID appears to be reused (different process).
    """
    if not _pid_is_running(pid):
        return False  # dead, not reused
    if _pid_is_zombie(pid):
        return False  # zombie, not reused by a live process

    # Check /proc/<pid>/cmdline
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except (OSError, IOError):
        # Can't read cmdline - be conservative
        return True

    if expected_cmdline_substring:
        return expected_cmdline_substring not in cmdline

    # Without a specific expectation, consider any live non-zombie process
    # as potentially reused (we can't verify it's the original worker).
    # This is the conservative (fail-closed) approach.
    return True


def safe_unlink_pid_file(path, root="/home/ubuntu/soren", expected_owner=None,
                         expected_cmdline=None, dry_run=False):
    """Diagnose a PID file without deleting it (legacy API name).

    Args:
        path: Path to the PID file (str or Path).
        root: Root directory of the soren installation.
        expected_owner: Expected file owner UID. Defaults to current user.
        expected_cmdline: Expected substring in the process command line.
            If provided, the PID is only considered "not reused" if the
            running process's cmdline contains this substring.
        dry_run: Accepted for compatibility; both values are read-only.

    Returns:
        (success: bool, reason: str)
        success is always False. reason describes the observed guard failure,
        or cleanup_disabled:no_writer_lock if the checks found a stale PID.
        Neither result guarantees that the path or PID stayed unchanged.
    """
    path = str(path)
    root = str(root)

    # Guard 1: Path must be under allowlisted directory.
    if not _is_under_allowed_dir(path, root):
        return False, "path_not_allowed"

    # Guard 1b: File must exist.
    if not os.path.lexists(path):
        return False, "already_gone"

    # Guard 2: Must not be a symlink.
    if _is_symlink(path):
        return False, "symlink"

    # Guard 3: Must not be a hardlink (nlink > 1).
    if _is_hardlink(path):
        return False, "hardlink"

    # Guard 4: Owner must match.
    if not _owner_matches(path, expected_owner):
        return False, "foreign_owner"

    # Guard 5: Read PID from file.
    pid, err = _read_pid(path)
    if err is not None:
        # File exists but has no valid PID - could be corrupt.
        # Still fail-closed: don't delete unless we can verify it's stale.
        # Exception: if the file is empty or unreadable, we still don't delete
        # because we can't verify ownership of the (missing) PID.
        return False, f"invalid_pid:{err}"

    # Guard 6: PID must not be running (or must be a zombie).
    if _pid_is_running(pid) and not _pid_is_zombie(pid):
        return False, "pid_alive"

    # Guard 7: If PID is running (even as zombie), check it hasn't been reused.
    if _pid_is_running(pid):
        if _pid_reused(pid, expected_cmdline):
            return False, "pid_reused"

    # Last diagnostic observation only; this does not prevent replacement.
    if not os.path.exists(path):
        return False, "already_gone"

    # No shared writer lock: never turn stale observations into unlink(path).
    return False, "cleanup_disabled:no_writer_lock"


def safe_cleanup_stale_pid_files(root="/home/ubuntu/soren", dry_run=False):
    """Diagnose tmp/state/*.pid and tmp/.soren_loop.lock/pid without deletion.

    Returns (path, False, reason) tuples. dry_run is a compatibility no-op.
    """
    root = str(root)
    state_dir = os.path.join(root, "tmp", "state")
    results = []

    if not os.path.isdir(state_dir):
        return results

    # Collect candidate PID files.
    candidates = []
    # tmp/state/*.pid
    try:
        for name in os.listdir(state_dir):
            if name.endswith(".pid"):
                candidates.append(os.path.join(state_dir, name))
    except OSError:
        pass
    # tmp/.soren_loop.lock/pid
    lock_pid = os.path.join(root, "tmp", ".soren_loop.lock", "pid")
    if os.path.exists(lock_pid):
        candidates.append(lock_pid)

    for path in candidates:
        ok, reason = safe_unlink_pid_file(path, root=root, dry_run=dry_run)
        results.append((path, ok, reason))

    return results


def main(argv=None):
    """Run the read-only diagnostic scan; exit status is not cleanup success."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only PID file diagnostics. Never deletes PID files.",
        epilog="Stale observations report cleanup_disabled:no_writer_lock. "
               "Exit status 0 means the diagnostic scan completed, not cleanup.",
    )
    parser.add_argument("root", nargs="?", default="/home/ubuntu/soren",
                        help="installation root (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compatibility option; diagnostics never delete files")
    args = parser.parse_args(argv)
    results = safe_cleanup_stale_pid_files(root=args.root, dry_run=args.dry_run)
    for path, _, reason in results:
        print(f"SKIP: {path} ({reason})")


if __name__ == "__main__":
    main()
