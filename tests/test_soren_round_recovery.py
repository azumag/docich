import copy
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.game_switch import GameSwitchStore, GameSwitchBusyError, StateCorruptError, atomic_write_json
from docich.naming import runtime_names
from docich.soren_round_recovery import OwnedRoundRecovery, RecoveryRefused, JOURNAL, read_object
from docich.soren_recovery_process import LinuxRecoveryEffects
from docich.corner_rotation import CornerRotationManager, RotationError


class Effects:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.failed = False

    def call(self, name):
        self.calls.append(name)
        if self.fail == name and not self.failed:
            self.failed = True
            raise RecoveryRefused("injected failure")

    def target_released(self, *args): self.call("target_released")
    def preflight(self):
        self.call("preflight")
        return {"roots": {}, "processes": [], "common": []}
    def pause(self, j): self.call("pause")
    def freeze(self, j): self.call("freeze")
    def archive(self, j):
        self.call("archive")
        j["archive_sha256"] = "synthetic-result"
    def verify_archive(self, j): self.call("verify_archive")
    def stop(self, j): self.call("stop")
    def clear_old(self, j): self.call("clear_old")
    def start_bridge(self, j): self.call("start_bridge")
    def start_runner(self, j): self.call("start_runner")
    def verify_new(self, j): self.call("verify_new")
    def common_unchanged(self, j): self.call("common_unchanged")
    def common_changed(self, j):
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
    rid = str(uuid.uuid4())
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    done = dt.datetime.fromtimestamp(now - 100, dt.timezone.utc).isoformat()
    with store.transaction() as tx:
        tx.transition({"idle"}, "ready", updates={"active": active, "next_generation": 2})
        accept = tx.accept_request(rid, "switch", "hanjuku")
        result = dict(request_id=rid, operation="switch", status="failed", from_game="sorengame",
                      to_game="hanjuku", generation=accept.generation, error_code="timeout", detail="boundary timeout",
                      failure_phase="round_boundary", retained_active=active, boundary_cancelled=True)
        tx.transition({"validating"}, "ready", updates={"operation": None, "request_id": None,
                       "last_result": result, "last_error": {"error_code": "timeout"}})
        tx.finish_request(rid, "failed", result)
    request = dict(schema=1, request_id=rid, game="sorengame", generation=1,
                   deadline_epoch=now - 100, deadline_at=done)
    atomic_write_json(life / "request.json", request)
    atomic_write_json(life / "ack.json", dict(request, status="cancelled"))
    rotation = dict(schema_version=1, seed="fixture", slot=1, last_seen_at=now - 150,
                    next_due_at=now + 1000, last_slot_at=now - 150, history=[{"corner": "fixture", "at": now - 200}],
                    status="recovery_required", pending=dict(request_id=rid, phase="dispatched", corner="fixture",
                    selected_at=now - 150), known_corners={"fixture": {"id": "fixture", "game": "hanjuku", "adapter": "retro"}})
    retro = dict(status="failed", game="hanjuku", previous_game="sorengame", last_error_code="timeout",
                 rotation_request_id=rid, switch_request_id=rid, completed_at=done)
    atomic_write_json(state / "corner_rotation.json", rotation)
    atomic_write_json(state / "retro_corner.json", retro)
    effects = Effects()
    now += 1
    recovery = OwnedRoundRecovery(state, root, effects, clock=lambda: now)
    return SimpleNamespace(state=state, root=root, life=life, store=store, rid=rid, active=active,
                           effects=effects, recovery=recovery, now=now, rotation=rotation, retro=retro)


def test_fixed_sequence_preserves_owner_receipt_reservation_and_history(owned):
    before = owned.store.receipts.load(owned.rid)
    assert owned.recovery.run() == {"status": "completed", "rotation": "held", "result": "interrupted",
                                    "common_workers_changed": 0}
    assert owned.effects.calls.index("freeze") < owned.effects.calls.index("pause")
    assert owned.effects.calls.index("archive") < owned.effects.calls.index("stop") < owned.effects.calls.index("start_bridge")
    assert owned.store.canonical.load()[0]["active"] == owned.active
    assert owned.store.canonical.load()[0]["phase"] == "ready"
    assert owned.store.receipts.load(owned.rid) == before
    assert read_object(owned.state / "corner_rotation.json") == owned.rotation
    retro = read_object(owned.state / "retro_corner.json")
    assert retro["status"] == "interrupted"
    assert retro["completed_at"] == owned.retro["completed_at"]
    assert retro["rotation_request_id"] == retro["switch_request_id"] == owned.rid


def test_completed_retry_does_not_recreate_another_round(owned):
    owned.recovery.run()
    owned.effects.calls.clear()
    owned.recovery.run()
    assert not {"freeze", "pause", "archive", "stop", "start_bridge", "start_runner"} & set(owned.effects.calls)


@pytest.mark.parametrize("failure", ["freeze", "pause", "archive", "stop", "clear_old", "start_bridge", "start_runner", "verify_new", "common_unchanged"])
def test_partial_failure_retains_hold_and_retries_exact_identity(owned, failure):
    owned.effects.fail = failure
    with pytest.raises(RecoveryRefused):
        owned.recovery.run()
    assert owned.store.canonical.load()[0]["phase"] == "recovery_required"
    assert read_object(owned.state / "corner_rotation.json") == owned.rotation
    assert (owned.state / JOURNAL).exists()
    assert owned.recovery.run()["status"] == "completed"
    assert owned.store.canonical.load()[0]["active"] == owned.active


@pytest.mark.parametrize("which,change", [
    ("canonical", {"phase": "draining", "operation": "switch", "request_id": str(uuid.uuid4())}),
    ("canonical", {"previous": "same"}),
    ("canonical", {"active": "new"}),
    ("retro", {"last_error_code": "quiesce_failed"}),
    ("retro", {"rotation_request_id": str(uuid.uuid4())}),
    ("retro", {"completed_at": "2099-01-01T00:00:00+00:00"}),
    ("rotation", {"status": "ready"}),
    ("rotation", {"manual_pending": {"request_id": str(uuid.uuid4())}}),
    ("ack", {"status": "stopping"}),
    ("ack", {"generation": 3}),
    ("request", {"operation": "player_change"}),
    ("request", {"generation": True}),
    ("request", {"deadline_epoch": float("nan")}),
])
def test_unproved_or_changed_owner_refuses_before_effects(owned, which, change):
    paths = {"canonical": owned.state / "game_switch.json", "retro": owned.state / "retro_corner.json",
             "rotation": owned.state / "corner_rotation.json", "ack": owned.life / "ack.json",
             "request": owned.life / "request.json"}
    value = read_object(paths[which])
    change = copy.deepcopy(change)
    if change.get("active") == "new":
        change["active"] = dict(value["active"], runtime_id="g1-8765abcd")
    if change.get("previous") == "same":
        change["previous"] = dict(value["active"], generation=3, runtime_id="g3-1234abcd",
                                  game_window=runtime_names(3).game_window, agent_window=runtime_names(3).agent_window,
                                  adapter_session=runtime_names(3).adapter_session)
        value["next_generation"] = 4
    value.update(change)
    paths[which].write_text(json.dumps(value))
    with pytest.raises((RecoveryRefused, ValueError, StateCorruptError)):
        owned.recovery.run()
    assert not owned.effects.calls
    assert not (owned.state / JOURNAL).exists()


def test_orphan_unknown_refuses_without_creating_fence(owned):
    owned.effects.fail = "target_released"
    with pytest.raises(RecoveryRefused): owned.recovery.run()
    assert not (owned.state / JOURNAL).exists()
    assert owned.store.canonical.load()[0]["phase"] == "ready"


def test_legacy_requires_exact_boundary_event_not_timeout_text(owned):
    receipt = owned.store.receipts.load(owned.rid)
    for key in ("failure_phase", "retained_active", "boundary_cancelled"):
        receipt["result"].pop(key)
    owned.store.receipts.save(receipt)
    canonical = owned.store.canonical.load()[0]
    canonical["last_result"] = receipt["result"]
    owned.store.canonical.save(canonical)
    with pytest.raises(RecoveryRefused): owned.recovery.run()
    log = owned.state / "logs/game_switch.log"
    log.parent.mkdir(exist_ok=True)
    row = dict(event="round_boundary_failed", request_id=owned.rid, operation="switch", generation=2,
               from_game="sorengame", to_game="hanjuku", phase="ready", result="failed", error_code="timeout",
               timestamp=dt.datetime.fromtimestamp(owned.now, dt.timezone.utc).isoformat())
    log.write_text(json.dumps(row) + "\n")
    assert owned.recovery.run()["status"] == "completed"


def test_hold_blocks_normal_coordinator_and_rotation_recovery(owned):
    owned.recovery.run()
    with pytest.raises(GameSwitchBusyError):
        owned.store.accept_request(str(uuid.uuid4()), "switch", "hanjuku")
    g = SimpleNamespace(state_dir=owned.state)
    manager = object.__new__(CornerRotationManager)
    manager.g = g
    assert manager.tick()["reason"] == "owned-soren-recovery-held"
    with pytest.raises(RotationError): manager.recover()
    with pytest.raises(RotationError): manager.queue_manual("hanjuku")


def test_identity_change_after_partial_stop_cannot_resume_or_launch(owned):
    owned.effects.fail = "stop"
    with pytest.raises(RecoveryRefused): owned.recovery.run()
    rotation = read_object(owned.state / "corner_rotation.json")
    rotation["pending"]["request_id"] = str(uuid.uuid4())
    atomic_write_json(owned.state / "corner_rotation.json", rotation)
    owned.effects.calls.clear()
    with pytest.raises(RecoveryRefused): owned.recovery.run()
    assert not owned.effects.calls


def test_saved_result_blocks_late_writer_and_preserves_evidence(owned):
    effects = LinuxRecoveryEffects(owned.root, owned.state)
    (owned.root / "game_history").mkdir()
    (owned.root / "game_state.json").write_text('{"state":"STOP","score":42}')
    (owned.root / "game_history/latest.jsonl").write_text('{"turn":1}\n')
    j = dict(stage="frozen", request_id=owned.rid, active=owned.active,
             completed_at=owned.retro["completed_at"], inventory={"processes": []})
    effects.archive(j)
    j["stage"] = "saved"
    effects.verify_archive(j)
    (owned.root / "commands.txt").write_text("retry\n")
    effects._rows = lambda: {}
    with pytest.raises(RecoveryRefused, match="late writer"):
        effects.clear_old(j)
    assert (owned.root / "commands.txt").exists()
    assert (owned.state / "soren-round-recovery" / owned.rid / "result.json").exists()


def test_corrupted_saved_result_refuses_restart(owned):
    effects = LinuxRecoveryEffects(owned.root, owned.state)
    (owned.root / "game_history").mkdir()
    (owned.root / "game_state.json").write_text('{}')
    (owned.root / "game_history/latest.jsonl").write_text('[]')
    j = dict(stage="frozen", request_id=owned.rid, active=owned.active, completed_at=owned.retro["completed_at"])
    effects.archive(j)
    j["stage"] = "saved"
    (owned.state / "soren-round-recovery" / owned.rid / "game_state.json").write_text('{}\n')
    with pytest.raises(RecoveryRefused): effects.verify_archive(j)


def test_shared_descendant_and_lookalike_script_are_not_game_roots(tmp_path):
    effects = LinuxRecoveryEffects(tmp_path, tmp_path)
    row = dict(pid=1, birth=1, ppid=0, state="S", cwd=str(tmp_path), args=["node", "soviet_local.mjs"])
    child = dict(pid=2, birth=2, ppid=1, state="S", cwd=str(tmp_path), args=["bash", "audio_worker.sh"])
    with pytest.raises(RecoveryRefused, match="shared worker"):
        effects._tree({1: row, 2: child})
    row["args"] = ["cat", "soviet_local.mjs"]
    with pytest.raises(RecoveryRefused, match="unattributed"):
        effects._tree({1: row})


@pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"),
                    reason="real pidfd/process-tree contract requires Linux")
def test_real_pidfd_freezes_and_stops_only_bound_child_tree(tmp_path):
    # A synthetic runner creates one child. No game, browser, stream or
    # production operation runs. This exercises real procfs + pidfd signals.
    script = tmp_path / "strategy_runner.py"
    script.write_text("import subprocess,time\n"
                      "p=subprocess.Popen(['python3','-c','import time; time.sleep(60)'])\n"
                      "open('child.pid','w').write(str(p.pid))\n"
                      "time.sleep(60)\n")
    process = subprocess.Popen(["python3", "-u", str(script)], cwd=tmp_path)
    unrelated = subprocess.Popen(["python3", "-c", "import time; time.sleep(60)"])
    effects = LinuxRecoveryEffects(tmp_path, tmp_path)
    child_pid = None
    try:
        end = time.monotonic() + 5
        while not (tmp_path / "child.pid").exists() and time.monotonic() < end:
            time.sleep(.01)
        child_pid = int((tmp_path / "child.pid").read_text())
        rows = effects._rows()
        (tmp_path / "game_state.json").write_text('{"state":"STOP"}')
        j = dict(inventory=dict(roots={"strategy_runner.py": effects._public(rows[process.pid])},
                                board={"state": "STOP"}))
        effects.freeze(j)
        assert {process.pid, child_pid} == {r["pid"] for r in j["inventory"]["processes"]}
        assert unrelated.poll() is None
        effects.stop(j)
        process.wait(timeout=5)
        assert effects._old_gone(j)
        assert unrelated.poll() is None
    finally:
        for p in (process, unrelated):
            if p.poll() is None: p.kill()
            p.wait(timeout=5)
        if child_pid:
            try: os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError: pass


def test_pid_reuse_is_never_signalled(tmp_path, monkeypatch):
    effects = LinuxRecoveryEffects(tmp_path, tmp_path, proc=tmp_path)
    proc = tmp_path / "42"
    proc.mkdir()
    fields = ["S", "0"] + ["0"] * 17 + ["999"]
    (proc / "stat").write_text("42 (fixture) " + " ".join(fields))
    read_fd, write_fd = os.pipe()
    sent = []
    monkeypatch.setattr(os, "pidfd_open", lambda pid: os.dup(read_fd), raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *args: sent.append(args), raising=False)
    try:
        effects._signal({"pid": 42, "birth": 100}, signal.SIGKILL, missing=True)
        assert not sent
        with pytest.raises(RecoveryRefused, match="birth changed"):
            effects._signal({"pid": 42, "birth": 100}, signal.SIGSTOP)
        assert not sent
    finally:
        os.close(read_fd)
        os.close(write_fd)


# --- production-shaped topology on a fake /proc (runs on any OS) ------------

CLOCK = 1_000_000.0
TCK = os.sysconf("SC_CLK_TCK")


class FakeProc:
    def __init__(self, tmp_path, root):
        self.proc, self.root = tmp_path / "proc", root
        self.proc.mkdir()
        (self.proc / "stat").write_text("cpu 0\nbtime 1000\n")

    def add(self, pid, ppid, args, age=300, state="S", cwd=True):
        d = self.proc / str(pid)
        d.mkdir()
        birth = int((CLOCK - age - 1000) * TCK)
        (d / "stat").write_text(f"{pid} (x) {state} {ppid} " + "0 " * 17 + str(birth))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        if cwd:
            (d / "cwd").symlink_to(self.root)
        return birth

    def remove(self, pid):
        import shutil
        shutil.rmtree(self.proc / str(pid))


@pytest.fixture
def topo(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    (root / "tmp/state").mkdir(parents=True)
    (root / "game_history").mkdir()
    fake = FakeProc(tmp_path, root)
    fake.add(10, 1, ["bash", "./start_all.sh"], age=9000)
    fake.add(20, 10, ["bash", "./soren_loop.sh"])
    fake.add(21, 20, ["bash", "./soren_loop.sh"])        # subshell fork keeps the argv
    fake.add(22, 20, ["python3", "-u", "strategy_runner.py"], age=250)
    fake.add(30, 10, ["bash", "./soviet_watchdog.sh"])
    fake.add(31, 1, ["node", "soviet_local.mjs"])         # tmux-launched, not a watchdog child
    fake.add(40, 10, ["bash", "./audio_worker.sh"], age=9000)
    fake.add(41, 1, ["ffmpeg", "-i", "x"], age=3)         # short-lived: must not be pinned
    state = root / "game_state.json"
    state.write_text('{"state":"GAMEOVER","score":42,"makeSorenCount":0}')
    os.utime(state, (CLOCK - 300, CLOCK - 300))
    (root / "game_history/latest.jsonl").write_text('{"turn":1}\n')
    (root / "tmp/state/main_strategy_runner_active.json").write_text(json.dumps(
        {"pid": 22, "game": 7, "started_at": int(CLOCK - 250)}))
    (root / "tmp/state/game_observation.json").write_text('{"game_id":"old-bridge","board":{"state":"GAMEOVER"}}')
    monkeypatch.setattr(os, "pidfd_open", lambda pid: None, raising=False)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *a: None, raising=False)
    effects = LinuxRecoveryEffects(root, tmp_path, proc=fake.proc, clock=lambda: CLOCK, sleep=lambda s: None)
    return SimpleNamespace(root=root, fake=fake, effects=effects)


def test_subshell_fork_is_not_a_second_root_and_short_lived_common_is_not_pinned(topo):
    inventory = topo.effects.preflight()
    assert inventory["roots"]["soren_loop.sh"]["pid"] == 20
    assert {r["pid"] for r in inventory["processes"]} == {20, 21, 22, 30, 31}
    assert [r["pid"] for r in inventory["common"]] == [10, 40]
    assert inventory["observation_game_id"] == "old-bridge"


def test_improve_lock_is_refused_before_any_effect(topo):
    (topo.root / "tmp/improve.lock").write_text("")
    with pytest.raises(RecoveryRefused, match="improve lock"):
        topo.effects.preflight()


def test_common_pin_ignores_exited_transients_but_reports_a_real_change(topo):
    j = dict(inventory=topo.effects.preflight())
    topo.fake.remove(41)
    assert topo.effects.common_changed(j) == []
    topo.fake.remove(40)
    assert topo.effects.common_changed(j) == [40]
    with pytest.raises(RecoveryRefused, match="common worker"):
        topo.effects.common_unchanged(j)


def _replace_game(topo, j, *, marker_pid=122, game_id="new-bridge", state="MOVE"):
    for pid in (20, 21, 22, 30, 31):
        topo.fake.remove(pid)
    topo.fake.add(120, 10, ["bash", "./soren_loop.sh"], age=50)
    topo.fake.add(121, 120, ["bash", "./soren_loop.sh"], age=49)
    topo.fake.add(122, 120, ["python3", "-u", "strategy_runner.py"], age=40)
    topo.fake.add(130, 10, ["bash", "./soviet_watchdog.sh"], age=60)
    topo.fake.add(131, 1, ["node", "soviet_local.mjs"], age=55)
    j["started_epoch"] = CLOCK - 100
    (topo.root / "game_state.json").write_text(json.dumps({"state": state, "score": 0, "makeSorenCount": 0}))
    os.utime(topo.root / "game_state.json", (CLOCK - 5, CLOCK - 5))
    (topo.root / "tmp/state/game_observation.json").write_text(json.dumps(
        {"game_id": game_id, "board": {"state": state}, "observed_epoch": CLOCK - 1}))
    (topo.root / "tmp/state/main_strategy_runner_active.json").write_text(json.dumps(
        {"pid": marker_pid, "game": 8, "started_at": int(CLOCK - 40)}))


def test_new_bridge_and_runner_are_proved_by_birth_nonce_and_marker(topo):
    j = dict(inventory=topo.effects.preflight())
    for pid in (20, 21, 22, 30, 31):
        j["inventory"]["processes"] = j["inventory"]["processes"]
    _replace_game(topo, j)
    assert topo.effects._old_gone(j)
    assert topo.effects._new_board(j)
    topo.effects.verify_new(j)           # raises on any identity gap


@pytest.mark.parametrize("kwargs,match", [
    (dict(marker_pid=121), "marker"),           # marker names a subshell, not the runner
    (dict(marker_pid=22), "marker"),            # stale pre-recovery runner pid
])
def test_runner_marker_must_name_the_new_runner(topo, kwargs, match):
    j = dict(inventory=topo.effects.preflight())
    _replace_game(topo, j, **kwargs)
    with pytest.raises(RecoveryRefused, match=match):
        topo.effects.verify_new(j)


@pytest.mark.parametrize("kwargs", [dict(game_id="old-bridge"), dict(state="GAMEOVER"), dict(state="STOP")])
def test_old_bridge_nonce_or_carried_over_board_is_not_a_fresh_game(topo, kwargs):
    j = dict(inventory=topo.effects.preflight())
    _replace_game(topo, j, **kwargs)
    assert not topo.effects._new_board(j)


def test_runner_output_is_archived_and_removed_with_the_old_round(topo, tmp_path, monkeypatch):
    out = tmp_path / "eloop_runner.AbC123"
    out.write_text("round log\n")
    j = dict(stage="frozen", request_id="rid", active={}, completed_at="t",
             inventory={"processes": [], "runner_output": str(out)})
    topo.effects.archive(j)
    j["stage"] = "saved"
    topo.effects.verify_archive(j)
    saved = tmp_path / "soren-round-recovery/rid/runner_output.txt"
    assert saved.read_text() == "round log\n"
    topo.effects.clear_old(j)
    assert not out.exists() and saved.exists()
    assert not (topo.root / "game_state.json").exists()


def test_crash_between_completed_journal_and_canonical_ready_reconciles(owned):
    canonical = owned.recovery.store.canonical
    real = canonical.transition
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args[1])
        if args[1] == "ready" and len(calls) == 2:      # the post-completion commit
            raise OSError("crash")
        return real(*args, **kwargs)

    canonical.transition = flaky
    with pytest.raises(OSError):
        owned.recovery.run()
    assert read_object(owned.state / JOURNAL)["stage"] == "completed"
    assert owned.store.canonical.load()[0]["phase"] == "recovery_required"
    canonical.transition = real
    owned.effects.calls.clear()
    assert owned.recovery.run()["status"] == "completed"
    assert owned.store.canonical.load()[0]["phase"] == "ready"
    assert "start_bridge" not in owned.effects.calls and "stop" not in owned.effects.calls


def test_fixed_entry_refuses_arguments_and_wrong_root(tmp_path):
    ops = Path(__file__).resolve().parents[1] / "ops/vm_actions"
    for argv in (["python3", str(ops / "recover_soren_round.py"), "extra"],
                 ["python3", str(ops / "recover_soren_round.py")]):
        p = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
        assert p.returncode != 0 and "fixed production root" in (p.stderr + p.stdout)
    assert subprocess.run(["bash", str(ops / "recover_soren_round.sh"), "x"], cwd=tmp_path).returncode == 64
    assert subprocess.run(["bash", str(ops / "recover_soren_round.sh")], cwd=tmp_path).returncode == 65
