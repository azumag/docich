"""A recycled PGID in another tmux pane is not ownership evidence."""
from unittest import mock

import pytest

from docich import process_tree, tmux


OLD_SCOPE = "0::/user.slice/user-1000.slice/tmux-spawn-original.scope\n"
OTHER_SCOPE = "0::/user.slice/user-1000.slice/tmux-spawn-unrelated.scope\n"


@pytest.fixture
def captured(monkeypatch):
    manager = tmux.Tmux()
    monkeypatch.setattr(manager, "_pane_pids", lambda target: [123])
    monkeypatch.setattr(manager, "_protected_teardown_pids", lambda leaders: frozenset())
    monkeypatch.setattr(tmux, "ancestor_pids", lambda *args: [])
    monkeypatch.setattr(tmux, "process_pgid", lambda pid: 123)
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: OLD_SCOPE)
    monkeypatch.setattr(tmux, "terminate_process_tree", lambda roots: process_tree.TerminationResult(tuple(roots), (), (), ()))
    monkeypatch.setattr(tmux, "is_running", lambda pid: False)
    monkeypatch.setattr(process_tree, "process_pgid_map", lambda: {4242: 123})
    monkeypatch.setattr(process_tree, "process_cgroup", lambda pid: OLD_SCOPE)
    return manager


@pytest.mark.parametrize("scope", [OTHER_SCOPE, OLD_SCOPE.replace("user-1000", "user-2000")])
def test_another_tmux_scope_with_recycled_group_is_never_a_candidate(captured, monkeypatch, scope):
    _remaining, scopes, _protected = captured._stop_scoped_processes("owned-pane")
    # The old group is gone. A different pane has reused its number, and its
    # leader has also died, leaving an unrelated child in a tmux scope.
    monkeypatch.setattr(process_tree, "process_cgroup", lambda pid: scope)
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: scope)
    assert captured._escaped_process_ids(None, None, scopes) == ()
    assert not captured._escaped_process_is_owned(4242, None, None, scopes)


def test_pgid_reuse_between_discovery_and_pidfd_proof_sends_no_signal(captured, monkeypatch):
    _remaining, scopes, _protected = captured._stop_scoped_processes("owned-pane")
    # Discovery still sees the original scope, but the fresh proof sees a
    # replacement in another tmux pane. pidfd pinning alone cannot prove owner.
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: OTHER_SCOPE)
    assert captured._escaped_process_ids(None, None, scopes) == (4242,)
    with (
        mock.patch.object(process_tree.os, "pidfd_open", return_value=100, create=True),
        mock.patch.object(process_tree, "_pidfd_is_running", return_value=True),
        mock.patch.object(process_tree.os, "close") as close,
        mock.patch.object(process_tree.signal, "pidfd_send_signal", create=True) as send,
        mock.patch.object(process_tree.os, "kill", side_effect=AssertionError("numeric signal")),
    ):
        assert captured._reap_escaped_processes(None, None, scopes, operation="test") == ()
    send.assert_not_called()
    close.assert_called_once_with(100)


@pytest.mark.parametrize("scope", [None, "", "0::/user.slice/app.slice\n"])
def test_unavailable_original_scope_does_not_enable_pgid_fallback(captured, monkeypatch, scope):
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: scope)
    _remaining, scopes, _protected = captured._stop_scoped_processes("owned-pane")
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: OTHER_SCOPE)
    monkeypatch.setattr(process_tree, "process_cgroup", lambda pid: OTHER_SCOPE)
    assert captured._escaped_process_ids(None, None, scopes) == ()
    assert not captured._escaped_process_is_owned(4242, None, None, scopes)


def test_original_scope_is_captured_before_stop(captured, monkeypatch):
    def stopped(roots):
        monkeypatch.setattr(tmux, "process_cgroup", lambda pid: OTHER_SCOPE)
        return process_tree.TerminationResult(tuple(roots), (), (), ())
    monkeypatch.setattr(tmux, "terminate_process_tree", stopped)
    _remaining, scopes, _protected = captured._stop_scoped_processes("owned-pane")
    assert not captured._escaped_process_is_owned(4242, None, None, scopes)


def test_scope_changes_during_leader_snapshot_disable_fallback(captured, monkeypatch):
    def read_group(pid):
        monkeypatch.setattr(tmux, "process_cgroup", lambda pid: OTHER_SCOPE)
        return 123
    monkeypatch.setattr(tmux, "process_pgid", read_group)
    _remaining, scopes, _protected = captured._stop_scoped_processes("owned-pane")
    assert scopes == {}


def test_group_and_scope_pairs_cannot_be_cross_combined(captured, monkeypatch):
    _remaining, first, _protected = captured._stop_scoped_processes("owned-pane")
    monkeypatch.setattr(tmux, "process_pgid", lambda pid: 456)
    monkeypatch.setattr(tmux, "process_cgroup", lambda pid: OTHER_SCOPE)
    _remaining, second, _protected = captured._stop_scoped_processes("other-owned-pane")
    monkeypatch.setattr(process_tree, "process_cgroup", lambda pid: OTHER_SCOPE)
    # 4242 has PGID 123, while OTHER_SCOPE was only captured with PGID 456.
    assert process_tree.processes_in_pane_scopes([*first.values(), *second.values()]) == []


def test_original_group_and_scope_remain_valid(captured):
    _remaining, scopes, _protected = captured._stop_scoped_processes("owned-pane")
    assert captured._escaped_process_ids(None, None, scopes) == (4242,)
    assert captured._escaped_process_is_owned(4242, None, None, scopes)
