"""Contain one scene-generation process tree in its dedicated worker.

Only ``hanjuku_scene_worker.main`` may opt its short-lived process into the
Linux subreaper role. Importing this module, or using it from a shared Python
process, never enables that role. A worker with existing children refuses a
new job, so newly adopted orphans have an unambiguous owner.

PID files and process groups are not ownership evidence. We discover only
descendants of this worker, translate procfs IDs into its PID namespace, bind
each to its start time and a pidfd, and signal only those bound processes.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


class SceneProcessUnavailable(RuntimeError):
    """Required local containment is unavailable; do not start a provider."""


_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_ENABLED_OWNER = None
_POLL_SECONDS = 0.05
_TERM_SECONDS = 1.0
_KILL_SECONDS = 1.0


@dataclass(frozen=True)
class _Stat:
    proc_pid: int
    parent_proc_pid: int
    start_ticks: int
    state: str


@dataclass(frozen=True)
class _Identity:
    proc_pid: int
    pid: int
    start_ticks: int


def _stat(text):
    # The parenthesized comm field can itself contain spaces and parentheses.
    end = text.rfind(')')
    begin = text.find('(')
    if begin < 1 or end <= begin:
        raise ValueError('invalid process stat')
    fields = text[end + 1:].split()
    return _Stat(int(text[:begin].strip()), int(fields[1]), int(fields[19]), fields[0])


def _nspids(text):
    line = next(line for line in text.splitlines() if line.startswith('NSpid:'))
    values = tuple(int(value) for value in line.split()[1:])
    if not values or any(value < 1 for value in values):
        raise ValueError('invalid process namespace')
    return values


class _ProcView:
    def __init__(self):
        self.root = Path('/proc')
        try:
            self.owner = _stat((self.root / 'self/stat').read_text())
            namespaces = _nspids((self.root / 'self/status').read_text())
            # procfs may be mounted in an ancestor namespace. kill()/pidfd_open()
            # take IDs in this process's namespace, never raw procfs directory IDs.
            self.namespace_index = len(namespaces) - 1
            if namespaces[self.namespace_index] != os.getpid():
                raise ValueError('process namespace mismatch')
        except (OSError, ValueError, IndexError, StopIteration):
            raise SceneProcessUnavailable('process identity unavailable') from None

    def single_threaded(self):
        try:
            return sum(path.name.isdigit() for path in (self.root / 'self/task').iterdir()) == 1
        except OSError:
            raise SceneProcessUnavailable('process threads unavailable') from None

    def _read_stat(self, proc_pid):
        return _stat((self.root / str(proc_pid) / 'stat').read_text())

    def identity(self, entry):
        namespaces = _nspids((self.root / str(entry.proc_pid) / 'status').read_text())
        local_pid = namespaces[self.namespace_index]
        # Re-read after status: a reused directory must not bind an old start
        # time to a replacement process's namespace ID.
        after = self._read_stat(entry.proc_pid)
        if after.proc_pid != entry.proc_pid or after.start_ticks != entry.start_ticks:
            raise ProcessLookupError
        return _Identity(entry.proc_pid, local_pid, entry.start_ticks)

    def matches(self, identity):
        try:
            entry = self._read_stat(identity.proc_pid)
            return self.identity(entry) == identity
        except (OSError, ValueError, IndexError, StopIteration):
            return False

    def descendants(self):
        # Some supported Linux kernels omit /proc/*/task/*/children. Read only
        # stat metadata while finding the tree, and status only for our tree.
        try:
            owner = _stat((self.root / 'self/stat').read_text())
            if owner.proc_pid != self.owner.proc_pid or owner.start_ticks != self.owner.start_ticks:
                raise ValueError('process owner changed')
            entries = {}
            for path in self.root.iterdir():
                if not path.name.isdigit():
                    continue
                try:
                    entry = self._read_stat(int(path.name))
                except (FileNotFoundError, ProcessLookupError):
                    continue  # A process can exit during the scan.
                entries[entry.proc_pid] = entry
            seen_owner = entries.get(self.owner.proc_pid)
            if seen_owner is None or seen_owner.start_ticks != self.owner.start_ticks:
                raise ValueError('process tree owner unavailable')
            children = {}
            for entry in entries.values():
                children.setdefault(entry.parent_proc_pid, []).append(entry)
            pending = [self.owner.proc_pid]
            visited = {self.owner.proc_pid}
            found = []
            while pending:
                parent = pending.pop()
                for entry in children.get(parent, ()):
                    if entry.proc_pid in visited:
                        continue
                    try:
                        identity = self.identity(entry)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    # A vanished/reused ancestor cannot establish ownership
                    # of a later snapshot entry that names its numeric PID.
                    visited.add(entry.proc_pid)
                    pending.append(entry.proc_pid)
                    found.append(identity)
            return found
        except (OSError, ValueError, IndexError, StopIteration):
            raise SceneProcessUnavailable('process tree unavailable') from None


def _prctl(option, argument):
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.prctl
    function.restype = ctypes.c_int
    function.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                         ctypes.c_ulong, ctypes.c_ulong]
    if function(option, argument, 0, 0, 0) != 0:
        raise SceneProcessUnavailable('subreaper unavailable')


def _subreaper_enabled():
    value = ctypes.c_int()
    _prctl(_PR_GET_CHILD_SUBREAPER, ctypes.addressof(value))
    return value.value == 1


def enable_subreaper():
    """Opt in only from the validated, dedicated scene worker's main function.

    Raises ``SceneProcessUnavailable`` before any provider is launched when
    Linux/procfs/pidfd containment is unsupported. Do not call from pytest's
    process, the input bot, corner monitor, or any shared service.
    """
    global _ENABLED_OWNER
    if (not sys.platform.startswith('linux') or not hasattr(os, 'pidfd_open')
            or not hasattr(signal, 'pidfd_send_signal') or not hasattr(os, 'P_PIDFD')):
        raise SceneProcessUnavailable('Linux process containment unavailable')
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        # The freshly Popen'd leader must remain unreaped until its pidfd is
        # acquired; an external SIGCHLD reaper would break that ownership proof.
        raise SceneProcessUnavailable('dedicated child reaping required')
    view = _ProcView()
    if not view.single_threaded():
        raise SceneProcessUnavailable('dedicated single-thread worker required')
    try:
        fd = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(fd, 0)
            try:
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                pass  # Our own process is not our child: supported syscall.
        finally:
            os.close(fd)
        _prctl(_PR_SET_CHILD_SUBREAPER, 1)
        if not _subreaper_enabled():
            raise SceneProcessUnavailable('subreaper unavailable')
    except (OSError, AttributeError):
        raise SceneProcessUnavailable('Linux process containment unavailable') from None
    _ENABLED_OWNER = (os.getpid(), view.owner.start_ticks)


def _exited(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    return bool(poll.poll(0))


class OwnedSceneProcess:
    """One child tree. Use ``wait(timeout=...)`` and always ``close()`` in finally.

    The caller must be the dedicated subreaper, with no pre-existing children
    and no other concurrent child-producing work. A nonempty baseline rejects
    the launch; it never adopts or signals another job's children.
    """
    def __init__(self, args, *, cwd=None, env=None):
        self._started_at = time.monotonic()
        self._view = _ProcView()
        if (_ENABLED_OWNER != (os.getpid(), self._view.owner.start_ticks)
                or not _subreaper_enabled()):
            raise SceneProcessUnavailable('dedicated scene worker required')
        if not self._view.single_threaded():
            raise SceneProcessUnavailable('dedicated single-thread worker required')
        if self._view.descendants():
            raise SceneProcessUnavailable('scene worker already owns children')
        self._owned = {}
        self._closed = False
        self._leader_fd = None
        self._leader_identity = None
        self.process = None
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            try:
                self.process = subprocess.Popen(
                    args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True,
                    # This dedicated owner was proved single-threaded above.
                    # Only its launch is shielded; the exec'd shell/provider
                    # must inherit the caller's original stop-signal mask.
                    preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask),
                )
                # Popen owns this unreaped direct child. Hold its exact process
                # before the first procfs scan, which may fail or race with exit.
                self._leader_fd = os.pidfd_open(self.process.pid)
                self._capture()
            finally:
                # A pending stop raises inside the cleanup guard below, only
                # after the child reference and its root pidfd are available.
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        except BaseException as error:
            if self.process is not None:
                try:
                    self.close()
                except Exception:
                    pass  # Preserve the initial launch/capture/stop error.
            if isinstance(error, OSError):
                raise SceneProcessUnavailable('scene process launch unavailable') from None
            raise

    @property
    def pid(self):
        return self.process.pid

    @property
    def returncode(self):
        return self.process.returncode

    def _capture(self):
        for identity in self._view.descendants():
            if identity in self._owned:
                continue
            if (self._leader_identity is None and self._leader_fd is not None
                    and self.process.returncode is None and identity.pid == self.process.pid):
                if self._view.matches(identity):
                    self._leader_identity = identity
                    self._owned[identity] = self._leader_fd
                continue
            try:
                fd = os.pidfd_open(identity.pid)
            except ProcessLookupError:
                continue
            if not self._view.matches(identity):
                os.close(fd)
                continue
            self._owned[identity] = fd

    def _fds(self):
        values = set(self._owned.values())
        if self._leader_fd is not None:
            values.add(self._leader_fd)
        return values

    def _try_capture(self):
        try:
            self._capture()
            return True
        except (SceneProcessUnavailable, OSError):
            return False

    def wait(self, timeout):
        """Wait within the total job budget, including launch and tree scans."""
        deadline = self._started_at + timeout
        while True:
            self._capture()
            result = self.process.poll()
            if result is not None:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self.process.args, timeout)
            time.sleep(min(_POLL_SECONDS, remaining))

    def _signal(self, sig, sent=None):
        sent = set() if sent is None else sent
        succeeded = True
        for fd in self._fds():
            if fd in sent or _exited(fd):
                continue
            # Ownership was checked after pidfd_open. The retained descriptor
            # remains bound even after numeric PID reuse. Re-reading procfs
            # here would turn a normal exit race into an abandoned cleanup.
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                pass
            except OSError:
                succeeded = False
                continue
            sent.add(fd)
        return sent, succeeded

    def _reap(self):
        self.process.poll()
        succeeded = True
        for fd in self._owned.values():
            if fd == self._leader_fd or not _exited(fd):
                continue
            try:
                # P_PIDFD avoids reaping a reused numeric PID. Non-direct
                # descendants simply return ECHILD until the subreaper adopts.
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                pass
            except OSError:
                succeeded = False
        return succeeded

    def _settle(self, deadline, sig, sent):
        while True:
            captured = self._try_capture()
            sent, signalled = self._signal(sig, sent)
            reaped = self._reap()
            if captured and signalled and reaped and all(_exited(fd) for fd in self._fds()):
                # A parent may have died since the first scan. Capture newly
                # adopted grandchildren before deciding the tree is empty.
                captured = self._try_capture()
                reaped = self._reap()
                if captured and reaped and all(_exited(fd) for fd in self._fds()):
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_POLL_SECONDS, remaining))

    def close(self):
        """Stop and reap only this job, including detached/adopted descendants."""
        if self._closed:
            return
        # main's TERM/INT handler raises KeyboardInterrupt. Defer those signals
        # during this bounded cleanup so it cannot abandon already bound child
        # handles halfway through; the dedicated worker has no other threads.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            if self._leader_fd is None:
                # pidfd acquisition failed immediately after Popen. This is
                # still our unreaped direct child, never a discovered PID.
                self.process.kill()
            settled = self._settle(time.monotonic() + _TERM_SECONDS, signal.SIGTERM, set())
            if not settled:
                settled = self._settle(time.monotonic() + _KILL_SECONDS, signal.SIGKILL, set())
            try:
                self.process.wait(timeout=_KILL_SECONDS)
            except subprocess.TimeoutExpired:
                settled = False
            if not settled:
                raise SceneProcessUnavailable('scene process cleanup incomplete')
        finally:
            try:
                self._closed = True
                for fd in self._fds():
                    os.close(fd)
                self._owned.clear()
                self._leader_fd = None
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
