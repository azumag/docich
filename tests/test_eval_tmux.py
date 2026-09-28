"""Evaluation tmux isolation and crash-safe pane cleanup (Issue #1280).

Root cause of the 2026-09-28 incident: evaluation sessions ran on the shared
production tmux server.  When a transient ``systemd-run`` improvement unit
ended, systemd killed its cgroup - including a tmux server the unit had
implicitly started - and every production pane in that server died at once.
Evaluations now use the private ``docich-eval`` server; panes are recorded in
a durable ledger so a server crash cannot leave game processes burning CPU.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import eval_tmux, tmux  # noqa: E402
from docich.resolver import bot_eval, improve, runner  # noqa: E402


def test_default_eval_server_is_private(monkeypatch):
    monkeypatch.delenv("DOCICH_EVAL_TMUX_SERVER", raising=False)
    assert tmux.eval_server_name() == "docich-eval"
    assert tmux.eval_tmux_argv(["list-sessions"]) == [
        "tmux", "-L", "docich-eval", "list-sessions",
    ]


def test_eval_server_override_is_validated(monkeypatch):
    monkeypatch.setenv("DOCICH_EVAL_TMUX_SERVER", "docich-eval-test")
    assert tmux.eval_server_name() == "docich-eval-test"
    monkeypatch.setenv("DOCICH_EVAL_TMUX_SERVER", "bad;name")
    with pytest.raises(Exception):
        tmux.eval_server_name()


@pytest.mark.parametrize("module_name", ["runner", "improve", "bot_eval"])
def test_eval_modules_use_the_private_server(monkeypatch, module_name):
    module = {"runner": runner, "improve": improve, "bot_eval": bot_eval}[module_name]
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._tmux(["list-sessions"])
    assert calls[0][:3] == ["tmux", "-L", "docich-eval"]


def test_tmux_class_private_server_prefixes_socket(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux.procs, "run", fake_run)
    client = tmux.Tmux(server="docich-eval")
    assert client.session_target_exists("docich-game-g1") is True
    assert calls[0][:3] == ["tmux", "-L", "docich-eval"]


def test_run_match_kills_only_exact_session_names(monkeypatch):
    targets = []

    def fake_tmux(args):
        if args[0] == "kill-session":
            targets.append(args[2])
        if args[0] == "has-session":
            return SimpleNamespace(returncode=1, stdout="", stderr="can't find session")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_tmux", fake_tmux)
    monkeypatch.setattr(runner, "register", lambda: None)
    monkeypatch.setattr(runner, "release", lambda: None)
    runner.run_match(["game"], 80, 24, lambda _text: [], interval_s=0, max_turns=0)
    assert targets and all(t.startswith("=evalr-") for t in targets)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "eval-panes.json"
    monkeypatch.setenv(eval_tmux.LEDGER_ENV, str(path))
    return path


def _owners(path):
    return json.loads(path.read_text())["owners"]


def test_register_reaps_panes_of_a_gone_owner(ledger, monkeypatch):
    ledger.write_text(json.dumps({"owners": {"999999:77": [[12345, 42]]}}))
    killed = []
    monkeypatch.setattr(eval_tmux, "is_running", lambda pid: pid == 12345)
    monkeypatch.setattr(eval_tmux, "process_start_ticks", lambda pid: 42 if pid == 12345 else None)
    monkeypatch.setattr(eval_tmux, "process_cgroup",
                        lambda pid: "0::/user.slice/user@1000.service/tmux-spawn-abc.scope\n")
    monkeypatch.setattr(eval_tmux, "terminate_process_tree",
                        lambda pids: killed.append(list(pids)) or SimpleNamespace(remaining=()))

    eval_tmux.register()

    assert killed == [[12345]]
    assert "999999:77" not in _owners(ledger)
    assert list(_owners(ledger)) == [eval_tmux._owner_key()]


def test_register_keeps_a_live_owner_pane(ledger, monkeypatch):
    pid = os.getpid()
    ledger.write_text(json.dumps({"owners": {f"{pid}:42": [[12345, 42]]}}))
    killed = []
    monkeypatch.setattr(eval_tmux, "is_running", lambda _pid: True)
    monkeypatch.setattr(eval_tmux, "process_start_ticks", lambda _pid: 42)
    monkeypatch.setattr(eval_tmux, "process_cgroup", lambda _pid: "tmux-spawn-abc.scope")
    monkeypatch.setattr(eval_tmux, "terminate_process_tree",
                        lambda pids: killed.append(list(pids)) or SimpleNamespace(remaining=()))

    eval_tmux.register()

    assert killed == []
    assert f"{pid}:42" in _owners(ledger)


@pytest.mark.parametrize("cgroup,ticks", [
    ("0::/user.slice/user@1000.service/app.slice/session-1.scope", 42),
    ("0::/user.slice/user@1000.service/tmux-spawn-abc.scope", 41),
])
def test_register_never_signals_a_recycled_or_foreign_pid(ledger, monkeypatch, cgroup, ticks):
    ledger.write_text(json.dumps({"owners": {"999999:77": [[12345, 42]]}}))
    killed = []
    monkeypatch.setattr(eval_tmux, "is_running", lambda _pid: True)
    monkeypatch.setattr(eval_tmux, "process_start_ticks", lambda _pid: ticks)
    monkeypatch.setattr(eval_tmux, "process_cgroup", lambda _pid: cgroup)
    monkeypatch.setattr(eval_tmux, "terminate_process_tree",
                        lambda pids: killed.append(list(pids)) or SimpleNamespace(remaining=()))

    eval_tmux.register()

    assert killed == []
    # Foreign/recycled PIDs are dropped from the ledger, never signalled.
    assert "999999:77" not in _owners(ledger)


def test_register_keeps_a_failed_reap_for_the_next_run(ledger, monkeypatch):
    ledger.write_text(json.dumps({"owners": {"999999:77": [[12345, 42]]}}))
    monkeypatch.setattr(eval_tmux, "is_running", lambda _pid: True)
    monkeypatch.setattr(eval_tmux, "process_start_ticks", lambda _pid: 42)
    monkeypatch.setattr(eval_tmux, "process_cgroup", lambda _pid: "tmux-spawn-abc.scope")
    monkeypatch.setattr(eval_tmux, "terminate_process_tree",
                        lambda _pids: SimpleNamespace(remaining=(12345,)))

    eval_tmux.register()

    assert "999999:77" in _owners(ledger)


def test_kill_session_releases_only_settled_panes(ledger, monkeypatch):
    monkeypatch.setattr(eval_tmux, "process_start_ticks", lambda _pid: 42)
    eval_tmux.register()
    eval_tmux.record_pane(12345)

    def tmux_call(args):
        if args[0] == "list-panes":
            return SimpleNamespace(returncode=0, stdout="12345\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(eval_tmux, "terminate_process_tree",
                        lambda _pids: SimpleNamespace(remaining=(12345,)))
    assert eval_tmux.kill_session(tmux_call, "evalr-1-1") == (12345,)
    assert [12345, 42] in next(iter(_owners(ledger).values()))

    monkeypatch.setattr(eval_tmux, "terminate_process_tree",
                        lambda _pids: SimpleNamespace(remaining=()))
    assert eval_tmux.kill_session(tmux_call, "evalr-1-1") == ()
    assert next(iter(_owners(ledger).values())) == []


def test_release_keeps_panes_recorded_for_parallel_matches(ledger, monkeypatch):
    monkeypatch.setattr(eval_tmux, "process_start_ticks", lambda _pid: 42)
    eval_tmux.register()
    eval_tmux.record_pane(12345)

    eval_tmux.release()

    assert [12345, 42] in next(iter(_owners(ledger).values()))


def test_marker_claim_checks_sessions_on_the_private_server(tmp_path, monkeypatch):
    from docich.resolver import improve as improve_module

    marker = tmp_path / "resolver" / "active" / "robots.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({
        "pid": 999999, "game": "robots", "generation": 1, "lease_id": "old",
        "sessions": ["evalr-999999-1"],
    }))
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(improve_module.subprocess, "run", fake_run)
    with pytest.raises(Exception):
        improve_module._claim_activity_marker(
            marker, {"pid": 1, "game": "robots", "generation": 2, "lease_id": "new",
                     "sessions": []}
        )
    assert calls[0][:3] == ["tmux", "-L", "docich-eval"]
    assert calls[0][3] == "has-session" and calls[0][5] == "=evalr-999999-1"


def test_arena_uses_the_private_server(monkeypatch):
    from docich.ninvaders import arena

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(arena.subprocess, "run", fake_run)
    arena._tmux("list-sessions")
    assert calls[0][:3] == ["tmux", "-L", "docich-eval"]


def test_bot_eval_match_never_leaves_a_ledger_owner(tmp_path, monkeypatch):
    ledger_path = tmp_path / "eval-panes.json"
    monkeypatch.setenv(eval_tmux.LEDGER_ENV, str(ledger_path))
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(bot_eval, "_tmux", lambda args: completed)
    summary = bot_eval.run_bot_matches(
        label="ninvaders", binary=["true"], bot_cmd=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path), cols=80, rows=24, matches=1, boot_sleep_s=0,
        interval_s=0, max_turns=0, game_over_res=["Game Over"], score_res=[],
    )
    assert summary["matches"]
    assert json.loads(ledger_path.read_text())["owners"] == {}
