import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import GameSwitchStore, GameSwitchBusyError, atomic_write_json
from docich.naming import runtime_names
from docich.soren_round_recovery import OwnedRoundRecovery, RecoveryRefused, read_object, incomplete_result
from docich.soren_recovery_process import LinuxRecoveryEffects, ROOTS, FILES


class Effects:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RecoveryRefused("injected failure")

    def preflight(self):
        self.call("preflight")
        return {"roots": {}, "processes": [], "common": []}
    def archive(self, recovery): self.call("archive")
    def stop(self, recovery): self.call("stop")
    def verify_new(self, recovery):
        self.call("verify_new")
        return dict(captured_old_targets_gone=True, fresh_game_progress_observed=True,
                    supervisor_unchanged=True, unattributed_profile_candidates=0)
    def common_changed(self, recovery):
        self.call("common_changed")
        return []


@pytest.fixture
def owned(tmp_path):
    state, root = tmp_path / "state", tmp_path / "soren"
    life = root / "tmp/state/game_lifecycle"
    life.mkdir(parents=True)
    store = GameSwitchStore(state)
    store.initialize()
    names = runtime_names(1)
    active = dict(game="sorengame", adapter="soren", generation=1, runtime_id="g1-1234abcd",
                  lease_id=None, game_window=names.game_window, agent_window=names.agent_window,
                  adapter_session=names.adapter_session, started_at="2026-10-04T08:00:00+00:00")
    store.canonical.transition({"idle"}, "ready", updates={"active": active, "next_generation": 2})
    rid = str(uuid.uuid4())
    for name in ("request", "ack", "control", "game_resource", "player_capabilities", "player_state"):
        atomic_write_json(life / (name + ".json"), dict(request_id=rid, status="draining"))
    atomic_write_json(state / "corner_rotation.json", dict(status="running", pending={"request_id": rid}))
    atomic_write_json(state / "retro_corner.json", dict(status="starting", game="hanjuku-hero"))
    effects = Effects()
    return SimpleNamespace(state=state, root=root, life=life, store=store, rid=rid, active=active,
                           effects=effects, recovery=OwnedRoundRecovery(state, root, effects))


def protected_bytes(owned):
    return {p: p.read_bytes() for base in (owned.state, owned.life)
            for p in base.rglob("*.json")}


@pytest.mark.parametrize("draining", [False, True])
def test_three_effects_preserve_all_control_records_and_do_not_create_hold(owned, draining):
    if draining:
        owned.store.canonical.transition({"ready"}, "draining", updates={"operation": "switch", "request_id": owned.rid,
            "round_boundary_deadline": dt.datetime.now(dt.timezone.utc).timestamp() + 3600})
    before = protected_bytes(owned)
    assert owned.recovery.run() == dict(status="incomplete", reason="descendant_exit_unproved",
        descendant_exit="unproved", containment="pidfd_snapshot", captured_old_targets_gone=True,
        fresh_game_progress_observed=True, supervisor_unchanged=True,
        unattributed_profile_candidates=0, common_workers_changed=0)
    assert owned.effects.calls == ["preflight", "archive", "stop", "verify_new", "common_changed"]
    assert protected_bytes(owned) == before
    assert not (owned.state / "soren_round_recovery.json").exists()
    assert not list(owned.root.rglob("*.paused"))


@pytest.mark.parametrize("failure", ["preflight", "archive", "stop", "verify_new"])
def test_failure_leaves_no_persistent_hold_and_releases_coordinator_lock(owned, failure):
    owned.effects.fail = failure
    before = protected_bytes(owned)
    with pytest.raises(RecoveryRefused):
        owned.recovery.run()
    assert protected_bytes(owned) == before
    assert not (owned.state / "soren_round_recovery.json").exists()
    with owned.store.lock(exclusive=True):
        pass
    if failure in {"preflight", "archive"}:
        assert "stop" not in owned.effects.calls


def test_legacy_journal_is_not_a_switch_hold_any_more(owned):
    (owned.state / "soren_round_recovery.json").write_text('{"stage":"completed"}')
    owned.recovery.run()
    assert owned.store.accept_request(str(uuid.uuid4()), "switch", "hanjuku-hero").generation == 2


def test_coordinator_busy_refuses_without_effects(owned):
    with owned.store.lock(exclusive=True):
        with pytest.raises(GameSwitchBusyError):
            owned.recovery.run()
    assert not owned.effects.calls


@pytest.mark.parametrize("phase", ["stopping", "recovery_required"])
def test_non_ready_non_draining_owner_refuses_before_effects(owned, phase):
    owned.store.canonical.transition({"ready"}, phase, updates={"operation": "stop", "request_id": owned.rid})
    with pytest.raises(RecoveryRefused):
        owned.recovery.run()
    assert not owned.effects.calls


def test_non_soren_active_refuses_before_effects(owned):
    owned.store.canonical.transition({"ready"}, "ready", updates={"active": dict(owned.active, game="paper-view", adapter="paper")})
    with pytest.raises(RecoveryRefused):
        owned.recovery.run()
    assert not owned.effects.calls


CLOCK = 1_000_000.0
TCK = os.sysconf("SC_CLK_TCK")


class FakeProc:
    def __init__(self, tmp_path, root):
        self.proc, self.root = tmp_path / "proc", root
        self.proc.mkdir()
        (self.proc / "stat").write_text("cpu 0\nbtime 1000\n")

    def add(self, pid, ppid, args, age=300, state="S", cwd=True):
        directory = self.proc / str(pid)
        directory.mkdir()
        birth = int((CLOCK - age - 1000) * TCK)
        (directory / "stat").write_text(f"{pid} (x) {state} {ppid} " + "0 " * 17 + str(birth))
        (directory / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        if cwd:
            (directory / "cwd").symlink_to(self.root)
        return birth

    def remove(self, pid):
        shutil.rmtree(self.proc / str(pid))


@pytest.fixture
def topo(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    (root / "tmp/state").mkdir(parents=True)
    (root / "game_history").mkdir()
    fake = FakeProc(tmp_path, root)
    fake.add(10, 1, ["bash", "./start_all.sh"], age=9000)
    fake.add(11, 10, ["bash", "./start_all.sh"], age=1)  # supervisor probe fork
    fake.add(20, 10, ["bash", "./soren_loop.sh"])
    fake.add(21, 20, ["bash", "./soren_loop.sh"])
    fake.add(22, 20, ["python3", "-u", "strategy_runner.py"], age=250)
    fake.add(30, 10, ["bash", "./soviet_watchdog.sh"])
    fake.add(31, 1, ["node", "soviet_local.mjs"])  # tmux-launched bridge
    fake.add(32, 31, ["chrome", "--type=renderer"])
    fake.add(33, 1, ["chrome_crashpad_handler", "--database=" + str(root / "tmp/soviet_local_chromium_profile/Crashpad")])
    fake.add(40, 10, ["bash", "./audio_worker.sh"], age=9000)
    fake.add(41, 1, ["ffmpeg", "-i", "x"], age=3)
    fake.add(50, 11, ["python3", "lib/game_lifecycle.py", "probe"])
    state = root / "game_state.json"
    state.write_text('{"state":"GAMEOVER","score":42,"makeSorenCount":1,"pieces":[1]}')
    os.utime(state, (CLOCK - 1200, CLOCK - 1200))
    (root / "game_history/latest.jsonl").write_text('{"turn":1}\n')
    (root / "tmp/state/main_strategy_runner_active.json").write_text(json.dumps(
        {"pid": 22, "game": 7, "started_at": int(CLOCK - 250)}))
    (root / "tmp/state/game_observation.json").write_text('{"game_id":"old-bridge"}')
    def pidfd_open(pid):
        try:
            return os.open(fake.proc / str(pid) / "stat", os.O_RDONLY)
        except FileNotFoundError:
            raise ProcessLookupError() from None
    monkeypatch.setattr(os, "pidfd_open", pidfd_open, raising=False)
    sent = []
    def send(fd, sig):
        pid = int(os.read(fd, 100).decode().split()[0])
        sent.append((pid, sig))
        fake.remove(pid)
    monkeypatch.setattr(signal, "pidfd_send_signal", send, raising=False)
    elapsed = [0.0]
    effects = LinuxRecoveryEffects(root, tmp_path / "state", proc=fake.proc, clock=lambda: CLOCK,
        monotonic=lambda: elapsed[0], sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    return SimpleNamespace(root=root, fake=fake, effects=effects, sent=sent, elapsed=elapsed)


def recovery_for(topo):
    return dict(recovery_id="fixture", active={}, started_epoch=CLOCK - 100, inventory=topo.effects.preflight())


def test_production_shape_attributes_bridge_crashpad_and_subshells(topo):
    inventory = topo.effects.preflight()
    assert inventory["roots"]["soren_loop.sh"]["pid"] == 20
    assert {r["pid"] for r in inventory["processes"]} == {20, 21, 22, 30, 31, 32, 33}
    assert inventory["supervisor"]["pid"] == 10
    assert [r["pid"] for r in inventory["common"]] == [10, 40]


@pytest.mark.parametrize("name", ["tmp/stop", "tmp/improve.lock", "tmp/state/soren_loop.paused", "tmp/state/soviet_watchdog.paused"])
def test_owner_gates_are_refused_and_preserved(topo, name):
    path = topo.root / name
    path.write_text("owner")
    with pytest.raises(RecoveryRefused):
        topo.effects.preflight()
    assert path.read_text() == "owner" and not topo.sent


@pytest.mark.parametrize("state,age", [("MOVE", 1200), ("STOP", 599), ("GAMEOVER", -10)])
def test_playing_or_recent_board_is_refused(topo, state, age):
    path = topo.root / "game_state.json"
    path.write_text(json.dumps({"state": state}))
    os.utime(path, (CLOCK - age, CLOCK - age))
    with pytest.raises(RecoveryRefused):
        topo.effects.preflight()
    assert not topo.sent


@pytest.mark.parametrize("worker", ["audio_worker.sh", "ffmpeg", "youtube_worker.sh", "direct_stream.py"])
def test_shared_descendant_is_refused_before_any_signal(topo, worker):
    topo.fake.add(60, 31, ["bash", worker])
    with pytest.raises(RecoveryRefused, match="shared worker"):
        topo.effects.preflight()
    assert not topo.sent


def test_supervisor_forks_with_intermediate_probe_parent_are_collapsed(topo):
    topo.fake.add(51, 50, ["bash", "./start_all.sh"], age=1)
    assert topo.effects.preflight()["supervisor"]["pid"] == 10


def test_independent_duplicate_and_lookalike_roots_are_refused(topo):
    topo.fake.add(60, 1, ["bash", "./soren_loop.sh"])
    with pytest.raises(RecoveryRefused, match="duplicated"):
        topo.effects.preflight()
    topo.fake.remove(60)
    topo.fake.add(60, 1, ["cat", "soviet_local.mjs"])
    with pytest.raises(RecoveryRefused, match="unattributed"):
        topo.effects.preflight()


def test_other_profile_crashpad_is_never_targeted_and_orphan_browser_refuses(topo):
    topo.fake.add(60, 1, ["chrome_crashpad_handler", "--database=/elsewhere/Crashpad"])
    assert 60 not in {r["pid"] for r in topo.effects.preflight()["processes"]}
    topo.fake.add(61, 1, ["chrome", "--user-data-dir=" + str(topo.root / "tmp/soviet_local_chromium_profile")])
    with pytest.raises(RecoveryRefused, match="unattributed game browser"):
        topo.effects.preflight()


def test_unreadable_descendant_cwd_does_not_abort_inventory(topo, monkeypatch):
    real = Path.resolve
    def resolve(self, *args, **kwargs):
        if self.parent.name == "32" and self.name == "cwd":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    assert topo.effects._rows()[32]["cwd"] == ""
    assert 32 in {r["pid"] for r in topo.effects.preflight()["processes"]}


def test_root_with_unreadable_cwd_is_refused_without_guessing(topo, monkeypatch):
    monkeypatch.setattr(topo.effects, "_cwd", lambda entry: "" if entry.name == "31" else str(topo.root))
    with pytest.raises(RecoveryRefused, match="root missing"):
        topo.effects.preflight()


def test_archive_then_kill_preserves_lifecycle_and_signals_only_bound_game(topo):
    life = topo.root / "tmp/state/game_lifecycle"
    life.mkdir()
    for name in ("request", "ack", "control", "game_resource", "player_capabilities"):
        (life / (name + ".json")).write_text(name)
    before = {p: p.read_bytes() for p in life.iterdir()}
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    topo.effects.stop(recovery)
    assert {pid for pid, sig in topo.sent} == {20, 21, 22, 30, 31, 32, 33}
    assert all(sig == signal.SIGKILL for pid, sig in topo.sent)
    assert {p: p.read_bytes() for p in life.iterdir()} == before
    assert not (topo.root / "game_state.json").exists()
    dest = topo.effects._archive_dir(recovery)
    result = read_object(dest / "result.json")
    assert result["status"] == "interrupted"
    for name, digest in result["files"].items():
        assert hashlib.sha256((dest / name).read_bytes()).hexdigest() == digest
    assert (dest / "retired/game_state.json").exists()
    assert topo.effects._old_gone(recovery)
    assert (topo.fake.proc / "10").exists() and (topo.fake.proc / "40").exists()


def test_disappearing_short_child_is_ignored_at_pin(topo, monkeypatch):
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    pin = topo.effects._pin
    def transient(row, **kwargs):
        if row["pid"] == 32:
            topo.fake.remove(32)
        return pin(row, **kwargs)
    monkeypatch.setattr(topo.effects, "_pin", transient)
    topo.effects.stop(recovery)
    assert not {32, 10, 40} & {pid for pid, sig in topo.sent}


def test_shared_worker_forked_after_preflight_still_refuses_before_detach(topo):
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    topo.fake.add(60, 20, ["bash", "audio_worker.sh"])
    with pytest.raises(RecoveryRefused, match="shared worker"):
        topo.effects.stop(recovery)
    assert (topo.root / "game_state.json").exists() and not topo.sent


@pytest.mark.parametrize("state", ["MOVE", "GAMEOVER"])
def test_live_board_changed_after_archive_refuses_without_detach_or_kill(topo, state):
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    path = topo.root / "game_state.json"
    path.write_text(json.dumps(dict(recovery["inventory"]["board"], state=state, score=43)))
    before = {p: p.read_bytes() for p in topo.root.rglob("*") if p.is_file()}
    with pytest.raises(RecoveryRefused, match="live board changed"):
        topo.effects.stop(recovery)
    assert {p: p.read_bytes() for p in topo.root.rglob("*") if p.is_file()} == before
    assert not topo.sent and not (topo.effects._archive_dir(recovery) / "retired").exists()


@pytest.mark.parametrize("recreate", [False, True])
def test_board_writer_during_detach_is_restored_without_kill(topo, monkeypatch, recreate):
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    path = topo.root / "game_state.json"
    renamed = Path.rename
    changed = '{"state":"MOVE","score":43,"pieces":[2]}'
    with path.open("r+") as writer:
        def rename(self, target):
            result = renamed(self, target)
            if self == path:
                if recreate:
                    path.write_text(changed)
                else:
                    writer.seek(0)
                    writer.write(changed)
                    writer.truncate()
                    writer.flush()
            return result
        monkeypatch.setattr(Path, "rename", rename)
        with pytest.raises(RecoveryRefused, match="live board changed"):
            topo.effects.stop(recovery)
    assert path.read_text() == changed and not topo.sent
    assert (topo.root / "game_history/latest.jsonl").read_text() == '{"turn":1}\n'
    assert (topo.root / "tmp/state/main_strategy_runner_active.json").exists()


def test_target_changed_during_pin_is_refused_without_detach(topo, monkeypatch):
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    pin = topo.effects._pin
    def replaced(row, **kwargs):
        fd = pin(row, **kwargs)
        if row["pid"] == 33:
            (topo.fake.proc / "21/cmdline").write_bytes(b"bash\0audio_worker.sh\0")
        return fd
    monkeypatch.setattr(topo.effects, "_pin", replaced)
    with pytest.raises(RecoveryRefused, match="shared worker"):
        topo.effects.stop(recovery)
    assert (topo.root / "game_state.json").exists() and not topo.sent


@pytest.mark.parametrize("stage", ["pin", "detach"])
@pytest.mark.parametrize("spawn", [False, True])
def test_owned_child_growth_after_pin_refuses_without_kill(topo, monkeypatch, stage, spawn):
    recovery = recovery_for(topo)
    topo.effects.archive(recovery)
    before = {name: (topo.root / name).read_bytes() for name in recovery["files"]}
    original = topo.effects._pin if stage == "pin" else Path.rename
    triggered = False

    def add_child():
        nonlocal triggered
        assert not triggered
        triggered = True
        if spawn:
            topo.fake.add(60, 31, ["chrome", "--type=renderer"], age=1)

    if stage == "pin":
        def pin(row, **kwargs):
            fd = original(row, **kwargs)
            if row["pid"] == 33:
                add_child()
            return fd
        monkeypatch.setattr(topo.effects, "_pin", pin)
    else:
        def rename(path, target):
            result = original(path, target)
            if path == topo.root / "game_state.json":
                add_child()
            return result
        monkeypatch.setattr(Path, "rename", rename)

    if spawn:
        with pytest.raises(RecoveryRefused, match="game process tree changed"):
            topo.effects.stop(recovery)
        assert not topo.sent
        assert {name: (topo.root / name).read_bytes() for name in before} == before
        assert (topo.fake.proc / "60").exists()
        assert {r["pid"] for r in recovery["inventory"]["processes"]} == {20, 21, 22, 30, 31, 32, 33}
        assert not list((topo.effects._archive_dir(recovery) / "retired").rglob("*.json"))
    else:
        topo.effects.stop(recovery)
        assert {pid for pid, sig in topo.sent} == {20, 21, 22, 30, 31, 32, 33}
        assert topo.effects._old_gone(recovery)
        assert not (topo.root / "game_state.json").exists()
    assert triggered
    assert (topo.fake.proc / "10").exists() and (topo.fake.proc / "40").exists()
    for name, digest in recovery["files"].items():
        assert hashlib.sha256((topo.effects._archive_dir(recovery) / name).read_bytes()).hexdigest() == digest


def test_pid_reuse_is_never_signalled(topo):
    old = topo.effects.preflight()["roots"]["soren_loop.sh"]
    topo.fake.remove(20)
    topo.fake.add(20, 10, ["bash", "soren_loop.sh"], age=1)
    assert topo.effects._pin(old, missing=True) is None
    with pytest.raises(RecoveryRefused, match="birth changed"):
        topo.effects._pin(old)
    assert not topo.sent


def test_archive_failure_never_detaches_or_signals(topo):
    recovery = recovery_for(topo)
    (topo.root / "game_history/latest.jsonl").unlink()
    with pytest.raises(RecoveryRefused, match="history missing"):
        topo.effects.archive(recovery)
    assert (topo.root / "game_state.json").exists() and not topo.sent


def replace_game(topo, recovery, *, marker_pid=122, score=106, pieces=None, old_birth=False):
    for pid in (20, 21, 22, 30, 31, 32, 33):
        if (topo.fake.proc / str(pid)).exists(): topo.fake.remove(pid)
    topo.fake.add(120, 10, ["bash", "./soren_loop.sh"], age=300 if old_birth else 50)
    topo.fake.add(121, 120, ["bash", "./soren_loop.sh"], age=49)
    topo.fake.add(122, 120, ["python3", "-u", "strategy_runner.py"], age=40)
    topo.fake.add(130, 10, ["bash", "./soviet_watchdog.sh"], age=60)
    topo.fake.add(131, 1, ["node", "soviet_local.mjs"], age=55)
    (topo.root / "game_state.json").write_text(json.dumps({"state": "MOVE", "score": score, "pieces": pieces or []}))
    os.utime(topo.root / "game_state.json", (CLOCK - 5, CLOCK - 5))
    (topo.root / "tmp/state/main_strategy_runner_active.json").write_text(json.dumps(
        {"pid": marker_pid, "game": 8, "started_at": int(CLOCK - 40)}))


def test_respawn_proves_new_root_births_marker_state_and_nonzero_score(topo):
    recovery = recovery_for(topo)
    replace_game(topo, recovery)
    assert topo.effects.verify_new(recovery) == dict(captured_old_targets_gone=True,
        fresh_game_progress_observed=True, supervisor_unchanged=True, unattributed_profile_candidates=0)
    assert topo.elapsed[0] == 0
    assert topo.effects.common_changed(recovery) == []


@pytest.mark.parametrize("kwargs", [dict(marker_pid=121), dict(marker_pid=22), dict(old_birth=True), dict(score=0)])
def test_false_readiness_times_out_in_120_seconds_without_more_signals(topo, kwargs):
    recovery = recovery_for(topo)
    replace_game(topo, recovery, **kwargs)
    evidence = topo.effects.verify_new(recovery)
    assert evidence["fresh_game_progress_observed"] is False
    assert evidence["captured_old_targets_gone"] is True
    assert topo.elapsed[0] == 120 and not topo.sent


def test_piece_change_proves_progress_without_score(topo):
    recovery = recovery_for(topo)
    replace_game(topo, recovery, score=0)
    def sleep(seconds):
        topo.elapsed[0] += seconds
        (topo.root / "game_state.json").write_text('{"state":"MOVE","score":0,"pieces":[2]}')
    topo.effects.sleep = sleep
    topo.effects.verify_new(recovery)
    assert topo.elapsed[0] == 0.25


def test_stale_game_state_and_supervisor_replacement_cannot_pass(topo):
    recovery = recovery_for(topo)
    replace_game(topo, recovery)
    os.utime(topo.root / "game_state.json", (CLOCK - 1000, CLOCK - 1000))
    assert topo.effects.verify_new(recovery)["fresh_game_progress_observed"] is False
    os.utime(topo.root / "game_state.json", (CLOCK - 5, CLOCK - 5))
    topo.fake.remove(10)
    topo.fake.add(10, 1, ["bash", "./start_all.sh"], age=5)
    assert topo.effects.verify_new(recovery)["supervisor_unchanged"] is False


@pytest.mark.parametrize("profile", [False, True])
def test_post_snapshot_orphan_cannot_complete_with_fresh_progress(topo, monkeypatch, capsys, profile):
    store = GameSwitchStore(topo.effects.state_dir)
    store.initialize()
    names = runtime_names(1)
    active = dict(game="sorengame", adapter="soren", generation=1, runtime_id="g1-1234abcd",
                  lease_id=None, game_window=names.game_window, agent_window=names.agent_window,
                  adapter_session=names.adapter_session, started_at="2026-10-04T08:00:00+00:00")
    store.canonical.transition({"idle"}, "ready", updates={"active": active, "next_generation": 2})
    life = topo.root / "tmp/state/game_lifecycle"
    life.mkdir()
    for name in ("request", "ack", "control", "game_resource", "player_capabilities", "player_state"):
        atomic_write_json(life / (name + ".json"), {"request_id": "private-id", "status": "draining"})
    atomic_write_json(topo.effects.state_dir / "corner_rotation.json", {"status": "running"})
    before = {p: p.read_bytes() for base in (topo.effects.state_dir, life) for p in base.rglob("*.json")}
    send = signal.pidfd_send_signal
    def fork_at_first_signal(fd, sig):
        if not topo.sent:
            args = ["chrome", "--type=renderer"]
            if profile:
                args.append("--user-data-dir=" + str(topo.root / "tmp/soviet_local_chromium_profile"))
            topo.fake.add(60, 31, args, age=0)
        send(fd, sig)
        if len(topo.sent) == 7:
            stat = topo.fake.proc / "60/stat"
            stat.write_text(stat.read_text().replace("(x) S 31 ", "(x) S 1 "))
            replace_game(topo, None)
    monkeypatch.setattr(signal, "pidfd_send_signal", fork_at_first_signal)
    result = OwnedRoundRecovery(topo.effects.state_dir, topo.root, topo.effects, clock=lambda: CLOCK - 100).run()
    assert result == dict(status="incomplete", reason="descendant_exit_unproved", descendant_exit="unproved",
        containment="pidfd_snapshot", captured_old_targets_gone=True, fresh_game_progress_observed=True,
        supervisor_unchanged=True, unattributed_profile_candidates=int(profile), common_workers_changed=0)
    assert (topo.fake.proc / "60").exists()
    assert len(topo.sent) == 7 and 60 not in {pid for pid, sig in topo.sent}
    assert {p: p.read_bytes() for p in before} == before
    assert not (topo.effects.state_dir / "soren_round_recovery.json").exists()
    assert not list(topo.root.rglob("*.paused"))
    with store.lock(exclusive=True):
        pass
    _assert_fixed_incomplete_entry(monkeypatch, capsys, result)
    assert len(topo.sent) == 7


@pytest.mark.parametrize("orphan", [False, True])
def test_new_bridge_crashpad_is_candidate_only_when_unattributed(topo, orphan):
    recovery = recovery_for(topo)
    replace_game(topo, recovery)
    topo.fake.add(132, 1 if orphan else 131,
        ["chrome_crashpad_handler", "--database=" + str(topo.root / "tmp/soviet_local_chromium_profile/Crashpad")], age=1)
    evidence = topo.effects.verify_new(recovery)
    assert evidence["captured_old_targets_gone"] is True
    assert evidence["fresh_game_progress_observed"] is True
    assert evidence["unattributed_profile_candidates"] == int(orphan)
    assert incomplete_result(evidence)["status"] == "incomplete"
    assert not topo.sent


@pytest.mark.parametrize("kind", ["live", "reused", "zombie"])
def test_captured_identity_diagnostic_distinguishes_live_reuse_and_zombie(topo, kind):
    recovery = recovery_for(topo)
    replace_game(topo, recovery)
    topo.fake.add(32, 1, ["chrome"], age=1 if kind == "reused" else 300, state="Z" if kind == "zombie" else "S")
    evidence = topo.effects.verify_new(recovery)
    assert evidence["captured_old_targets_gone"] is (kind != "live")
    assert evidence["fresh_game_progress_observed"] is True
    assert topo.elapsed[0] == (120 if kind == "live" else 0)
    assert incomplete_result(evidence)["status"] == "incomplete"
    assert not topo.sent


def test_duplicate_fresh_root_cannot_prove_progress(topo):
    recovery = recovery_for(topo)
    replace_game(topo, recovery)
    topo.fake.add(134, 1, ["node", "soviet_local.mjs"], age=1)
    evidence = topo.effects.verify_new(recovery)
    assert evidence["captured_old_targets_gone"] is True
    assert evidence["fresh_game_progress_observed"] is False
    assert evidence["unattributed_profile_candidates"] is None
    assert not topo.sent


def test_inventory_failure_reports_unknown_not_absent(topo, monkeypatch):
    recovery = recovery_for(topo)
    def unreadable():
        raise RecoveryRefused("private pid argv path request_id")
    monkeypatch.setattr(topo.effects, "_rows", unreadable)
    assert all(value is None for value in topo.effects.verify_new(recovery).values())
    assert not topo.sent


def test_verification_uses_one_process_snapshot(topo, monkeypatch):
    recovery = recovery_for(topo)
    replace_game(topo, recovery)
    rows = topo.effects._rows
    calls = []
    def inventory():
        calls.append(1)
        assert len(calls) == 1
        return rows()
    monkeypatch.setattr(topo.effects, "_rows", inventory)
    assert topo.effects.verify_new(recovery)["fresh_game_progress_observed"] is True
    assert len(calls) == 1


def test_incomplete_schema_drops_untrusted_values_and_extra_fields():
    result = incomplete_result(dict(captured_old_targets_gone="private-pid", fresh_game_progress_observed=1,
        supervisor_unchanged="private-path", unattributed_profile_candidates=True, request_id="private-id"),
        common_workers_changed="private-argv", reason="private-exception")
    assert result["reason"] == "descendant_exit_unproved"
    assert all(result[name] is None for name in ("captured_old_targets_gone", "fresh_game_progress_observed",
        "supervisor_unchanged", "unattributed_profile_candidates", "common_workers_changed"))
    assert "private" not in json.dumps(result)


@pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"),
                    reason="real pidfd/process-tree contract requires Linux")
def test_real_pidfd_stops_bound_child_tree_and_leaves_supervisor_alive(tmp_path):
    script = tmp_path / "strategy_runner.py"
    script.write_text("import subprocess,time\n"
                      "p=subprocess.Popen(['python3','-c','import time; time.sleep(60)'])\n"
                      "open('child.pid','w').write(str(p.pid))\n"
                      "time.sleep(60)\n")
    process = subprocess.Popen(["python3", "-u", str(script)], cwd=tmp_path)
    unrelated = subprocess.Popen(["python3", "-c", "import time; time.sleep(60)"])
    effects = LinuxRecoveryEffects(tmp_path, tmp_path / "evidence")
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "child.pid").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        child_pid = int((tmp_path / "child.pid").read_text())
        rows = effects._rows()
        (tmp_path / "game_state.json").write_text('{"state":"STOP"}')
        os.utime(tmp_path / "game_state.json", (time.time() - 1200, time.time() - 1200))
        (tmp_path / "game_history").mkdir()
        (tmp_path / "game_history/latest.jsonl").write_text('{}\n')
        recovery = dict(recovery_id="linux", active={}, started_epoch=time.time(),
            inventory=dict(roots={"strategy_runner.py": effects._identity(rows[process.pid])}, board={"state": "STOP"}))
        effects.archive(recovery)
        effects.stop(recovery)
        assert {process.pid, child_pid} == {r["pid"] for r in recovery["inventory"]["processes"]}
        process.wait(timeout=5)
        assert effects._old_gone(recovery) and unrelated.poll() is None
    finally:
        for child in (process, unrelated):
            if child.poll() is None: child.kill()
            child.wait(timeout=5)
        if child_pid:
            try: os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError: pass


def test_fixed_entry_refuses_arguments_and_wrong_root(tmp_path):
    ops = Path(__file__).resolve().parents[1] / "ops/vm_actions"
    for argv in (["python3", str(ops / "recover_soren_round.py"), "extra"],
                 ["python3", str(ops / "recover_soren_round.py")]):
        process = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
        assert process.returncode != 0 and "fixed production root" in (process.stderr + process.stdout)
    assert subprocess.run(["bash", str(ops / "recover_soren_round.sh"), "x"], cwd=tmp_path).returncode == 64
    assert subprocess.run(["bash", str(ops / "recover_soren_round.sh")], cwd=tmp_path).returncode == 65


def test_unexpected_error_output_does_not_publish_private_identity(monkeypatch, capsys):
    import docich.soren_round_recovery as recovery_module
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path("/home/ubuntu/docich")))
    monkeypatch.setattr(sys, "argv", ["recover_soren_round.py"])
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs:
        self if str(self) == "/home/ubuntu/docich" else real_resolve(self, *args, **kwargs))
    class Broken:
        def __init__(self, *args): pass
        def run(self): raise OSError("private request_id pid argv")
    monkeypatch.setattr(recovery_module, "OwnedRoundRecovery", Broken)
    with pytest.raises(SystemExit):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "ops/vm_actions/recover_soren_round.py"))
    assert capsys.readouterr().err == "owned Soren recovery failed unexpectedly; no recovery hold\n"


def _assert_fixed_incomplete_entry(monkeypatch, capsys, result):
    import docich.soren_round_recovery as recovery_module
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path("/home/ubuntu/docich")))
    monkeypatch.setattr(sys, "argv", ["recover_soren_round.py"])
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs:
        self if str(self) == "/home/ubuntu/docich" else real_resolve(self, *args, **kwargs))
    class Incomplete:
        def __init__(self, *args): pass
        def run(self): return result
    monkeypatch.setattr(recovery_module, "OwnedRoundRecovery", Incomplete)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "ops/vm_actions/recover_soren_round.py"))
    output = capsys.readouterr()
    assert exit_info.value.code == 1
    assert json.loads(output.out) == result and output.err == ""
    assert "completed" not in output.out


@pytest.mark.parametrize("progress", [None, False, True])
def test_fixed_entry_emits_machine_incomplete_and_nonzero(monkeypatch, capsys, progress):
    result = incomplete_result(dict(captured_old_targets_gone=True, fresh_game_progress_observed=progress,
        supervisor_unchanged=True, unattributed_profile_candidates=0), common_workers_changed=0)
    _assert_fixed_incomplete_entry(monkeypatch, capsys, result)


def test_fixed_entry_refusal_does_not_publish_exception_details(monkeypatch, capsys):
    import docich.soren_round_recovery as recovery_module
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path("/home/ubuntu/docich")))
    monkeypatch.setattr(sys, "argv", ["recover_soren_round.py"])
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs:
        self if str(self) == "/home/ubuntu/docich" else real_resolve(self, *args, **kwargs))
    class Refused:
        def __init__(self, *args): pass
        def run(self): raise RecoveryRefused("private request_id pid argv path")
    monkeypatch.setattr(recovery_module, "OwnedRoundRecovery", Refused)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "ops/vm_actions/recover_soren_round.py"))
    output = capsys.readouterr()
    assert exit_info.value.code == 1
    assert json.loads(output.out)["reason"] == "recovery_refused"
    assert "private" not in output.out + output.err
