#!/usr/bin/env python3
"""Fail-closed guard for PID file deletion.

This module provides safe_unlink_pid_file() which only deletes a PID file
when ALL of the following conditions are met:

  1. Path is under tmp/state/ or tmp/.soren_loop.lock/ (allowlist).
  2. Path is a regular file (not a symlink, not a hardlink with nlink > 1).
  3. Owner matches the expected owner (default: current user).
  4. PID in the file is not currently running.
  5. PID has not been reused by a different process.

If any condition fails, the file is NOT deleted and a reason is returned.
This is the "fail-closed" approach: when in doubt, don't delete.

Usage:
    from safe_pid_cleanup import safe_unlink_pid_file
    ok, reason = safe_unlink_pid_file(path, root="/home/ubuntu/soren")
    if ok:
        print("Deleted")
    else:
        print(f"Skipped: {reason}")
"""

import os
import stat as statmod


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
    """Safely unlink a PID file with fail-closed guards.

    Args:
        path: Path to the PID file (str or Path).
        root: Root directory of the soren installation.
        expected_owner: Expected file owner UID. Defaults to current user.
        expected_cmdline: Expected substring in the process command line.
            If provided, the PID is only considered "not reused" if the
            running process's cmdline contains this substring.
        dry_run: If True, don't actually delete; just return what would happen.

    Returns:
        (success: bool, reason: str)
        success=True means the file was (or would be) deleted.
        success=False means the file was NOT deleted, with reason explaining why.
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

    # Guard 8: File must still exist (TOCTOU protection).
    if not os.path.exists(path):
        return False, "already_gone"

    # All guards passed - safe to delete.
    if dry_run:
        return True, "would_delete"

    try:
        os.unlink(path)
        return True, "deleted"
    except OSError as e:
        return False, f"unlink_failed:{e}"


def safe_cleanup_stale_pid_files(root="/home/ubuntu/soren", dry_run=False):
    """Scan tmp/state/ for stale PID files and safely delete them.

    Returns list of (path, success, reason) tuples.
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


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/soren"
    dry = "--dry-run" in sys.argv
    results = safe_cleanup_stale_pid_files(root=root, dry_run=dry)
    for path, ok, reason in results:
        print(f"{'OK' if ok else 'SKIP'}: {path} ({reason})")
