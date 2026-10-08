"""PID-reuse regressions: ownership evidence and signals share one handle."""
import errno
import os
import signal
import sys

import pytest

from docich import process_tree, tmux


OWNER = tmux.TmuxOwnership("g1-abcdef", 1, "game")
TAGS = tmux.ownership_environment(OWNER)


class FakeKernel:
    """Reusable numeric PIDs with pidfds bound to the original incarnation."""

    def __init__(self):
        self.current = {4242: "owned", 4243: "owned-child"}
        self.live = set(self.current.values())
        self.handles = {}
        self.closed = []
        self.signals = []
        self.before_open = lambda pid: None
        self.before_send = lambda sig: None

    def replace(self, pid):
        self.live.discard(self.current[pid])
        self.current[pid] = "unrelated"
        self.live.add("unrelated")

    def open(self, pid):
        self.before_open(pid)
        fd = len(self.handles) + 100
        self.handles[fd] = self.current[pid]
        return fd

    def running(self, fd):
        return self.handles[fd] in self.live

    def send(self, fd, sig):
        self.before_send(sig)
        identity = self.handles[fd]
        if identity not in self.live:
            raise ProcessLookupError(errno.ESRCH, "exited")
        self.signals.append((identity, sig, fd))
        if sig == signal.SIGKILL:
            self.live.discard(identity)


@pytest.fixture
def kernel(monkeypatch):
    kernel = FakeKernel()
    monkeypatch.setattr(process_tree.os, "pidfd_open", kernel.open, raising=False)
    monkeypatch.setattr(process_tree.signal, "pidfd_send_signal", kernel.send, raising=False)
    monkeypatch.setattr(process_tree.os, "close", kernel.closed.append)
    monkeypatch.setattr(process_tree, "_pidfd_is_running", kernel.running)
    monkeypatch.setattr(process_tree.os, "kill", lambda *args: pytest.fail("numeric PID signal"))
    monkeypatch.setattr(process_tree, "descendant_pids", lambda *args: pytest.fail("unverified descendant"))
    return kernel


def stop(owns):
    return process_tree.terminate_owned_processes(
        [4242], owns, term_timeout_s=0, kill_timeout_s=0
    )


def sweep(monkeypatch, env_reader, *, legacy=False, roles=("game",)):
    monkeypatch.setattr(tmux, "ancestor_pids", lambda: [1, 999])
    monkeypatch.setattr(tmux, "process_environ", env_reader)
    monkeypatch.setattr(tmux, "processes_with_env", lambda *args: [4242])
    monkeypatch.setattr(tmux, "processes_in_pane_scopes", lambda *args, **kwargs: [4242])
    monkeypatch.setattr(tmux, "is_running", lambda pid: False)
    # Keep waits deterministic; the real helper still performs acquisition,
    # ownership validation, fd signalling and exit checks.
    def immediate(pids, owns):
        return process_tree.terminate_owned_processes(
            pids, owns, term_timeout_s=0, kill_timeout_s=0
        )
    monkeypatch.setattr(tmux, "terminate_owned_processes", immediate)
    return tmux.Tmux()._reap_escaped_processes(
        None if legacy else OWNER, None if legacy else roles,
        {123: tmux.PaneProcessScope(123, "0::/tmux-spawn-old.scope")} if legacy else {}, operation="test"
    )


def test_term_and_kill_retain_the_same_handle(kernel):
    result = stop(lambda pid: True)
    assert result.term_sent == result.kill_sent == (4242,)
    assert result.remaining == ()
    assert kernel.signals == [("owned", signal.SIGTERM, 100), ("owned", signal.SIGKILL, 100)]
    assert kernel.closed == [100]


@pytest.mark.parametrize("phase", ["open", "proof", "term", "kill"])
def test_replacement_never_receives_signal(kernel, monkeypatch, phase):
    def env_reader(pid):
        env = TAGS if kernel.current[pid] == "owned" else {}
        if phase == "proof":
            kernel.replace(pid)
        return env
    if phase == "open":
        kernel.before_open = kernel.replace
    if phase in ("term", "kill"):
        target = signal.SIGTERM if phase == "term" else signal.SIGKILL
        kernel.before_send = lambda sig: kernel.replace(4242) if sig == target else None

    assert sweep(monkeypatch, env_reader) == ()
    assert "unrelated" in kernel.live
    assert all(identity != "unrelated" for identity, _sig, _fd in kernel.signals)
    assert kernel.closed == [100]


@pytest.mark.parametrize("phase", ["open", "proof"])
def test_pane_scope_proof_cannot_authorize_replacement(kernel, monkeypatch, phase):
    monkeypatch.setattr(tmux, "process_pgid", lambda pid: 123)
    def cgroup(pid):
        owned = kernel.current[pid] == "owned"
        if phase == "proof":
            kernel.replace(pid)
        return "0::/tmux-spawn-old.scope" if owned else "0::/tmux-spawn-unrelated.scope"
    monkeypatch.setattr(tmux, "process_cgroup", cgroup)
    if phase == "open":
        kernel.before_open = kernel.replace
    assert sweep(monkeypatch, lambda pid: {}, legacy=True) == ()
    assert kernel.signals == []
    assert kernel.closed == [100]


@pytest.mark.parametrize("env", [{}, {**TAGS, "DOCICH_TMUX_GENERATION": "2"},
                                  {**TAGS, "DOCICH_TMUX_ROLE": "agent"}])
def test_fresh_ownership_must_match_runtime_generation_and_role(kernel, monkeypatch, env):
    assert sweep(monkeypatch, lambda pid: env) == ()
    assert kernel.signals == []
    assert kernel.closed == [100]


def test_session_reclaims_other_roles_after_fresh_proof(kernel, monkeypatch):
    assert sweep(monkeypatch, lambda pid: {**TAGS, "DOCICH_TMUX_ROLE": "agent"}, roles=None) == ()
    assert [identity for identity, _sig, _fd in kernel.signals] == ["owned", "owned"]


def test_legacy_scope_is_revalidated_and_signalled_by_handle(kernel, monkeypatch):
    monkeypatch.setattr(tmux, "process_pgid", lambda pid: 123)
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: "0::/tmux-spawn-old.scope")
    assert sweep(monkeypatch, lambda pid: {}, legacy=True) == ()
    assert kernel.signals == [("owned", signal.SIGTERM, 100), ("owned", signal.SIGKILL, 100)]


def test_server_protection_also_applies_to_fresh_validation(kernel, monkeypatch):
    monkeypatch.setattr(tmux, "process_environ", lambda pid: pytest.fail("protected PID read"))
    assert not tmux.Tmux()._escaped_process_is_owned(
        4321, OWNER, ("game",), {}, frozenset({4321})
    )


@pytest.mark.parametrize("api", ["open", "signal"])
def test_missing_pidfd_api_fails_tmux_closed(kernel, monkeypatch, api):
    if api == "open":
        monkeypatch.delattr(process_tree.os, "pidfd_open")
    else:
        monkeypatch.delattr(process_tree.signal, "pidfd_send_signal")
    with pytest.raises(tmux.TmuxError, match="pidfd support"):
        sweep(monkeypatch, lambda pid: TAGS)
    assert kernel.signals == []


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.EPERM, errno.EMFILE])
def test_failed_acquisition_closes_all_handles_before_signalling(kernel, monkeypatch, code):
    def open_second(pid):
        if pid == 4243:
            raise OSError(code, "unavailable")
        return kernel.open(pid)
    monkeypatch.setattr(process_tree.os, "pidfd_open", open_second)
    with pytest.raises(process_tree.ProcessIdentityError):
        process_tree.terminate_owned_processes([4242, 4243], lambda pid: True)
    assert kernel.closed == [100]
    assert kernel.signals == []


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.EPERM])
def test_signal_failure_has_no_numeric_fallback(kernel, monkeypatch, code):
    def denied(fd, sig):
        raise OSError(code, "unavailable")
    monkeypatch.setattr(process_tree.signal, "pidfd_send_signal", denied)
    with pytest.raises(tmux.TmuxError, match="pidfd operation failed"):
        sweep(monkeypatch, lambda pid: TAGS)
    assert kernel.closed == [100]
    assert kernel.signals == []


def test_process_disappearing_before_open_is_settled(kernel, monkeypatch):
    def gone(pid):
        raise ProcessLookupError(errno.ESRCH, "exited")
    monkeypatch.setattr(process_tree.os, "pidfd_open", gone)
    assert stop(lambda pid: pytest.fail("missing process ownership read")).remaining == ()
    assert kernel.signals == kernel.closed == []


def test_survivor_is_reported_and_handle_closed(kernel, monkeypatch):
    monkeypatch.setattr(process_tree.signal, "pidfd_send_signal", lambda fd, sig: None)
    assert stop(lambda pid: True).remaining == (4242,)
    assert kernel.closed == [100]


def test_poll_failure_is_not_treated_as_exit(kernel, monkeypatch):
    def failed(fd):
        raise OSError(errno.EBADF, "invalid")
    monkeypatch.setattr(process_tree, "_pidfd_is_running", failed)
    with pytest.raises(process_tree.ProcessIdentityError):
        stop(lambda pid: True)
    assert kernel.signals == []
    assert kernel.closed == [100]


@pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"),
                    reason="real pidfd requires Linux")
def test_real_pidfd_observes_exit_and_signals_owned_child():
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = process_tree.terminate_owned_processes([child.pid], lambda pid: pid == child.pid)
        child.wait(timeout=2)
        assert result.term_sent == (child.pid,)
        assert result.kill_sent == result.remaining == ()
    finally:
        # This Popen child is unreaped until wait(), so its PID cannot be reused.
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)
