"""Offline cancellation gates: detached receipt only, one ledger mutation."""
import copy
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import hanjuku_manual_cancel as operator
from docich.game_switch import GameSwitchStore, _initial_state


class AbsentTmux:
    def window_target_exists(self, target, *, strict):
        assert strict
        return False

    def session_target_exists(self, target, *, strict):
        assert strict
        return False


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    soren = tmp_path / "soren"
    g = SimpleNamespace(state_dir=state_dir)
    monkeypatch.setattr(operator, "resolve_soren_root", lambda g: soren)
    store = GameSwitchStore(state_dir)
    store.initialize()
    request_id = str(uuid.uuid4())
    receipt = dict(store.accept_request(request_id, "start", "hanjuku-hero").receipt)
    receipt.update(status="failed", result={"request_id": request_id,
        "operation": "start", "status": "failed", "cleanup_pending": False})
    store.receipts.save(receipt)
    idle = _initial_state()
    idle["next_generation"] = 2
    store.canonical.save(idle)
    state = {"schema_version": 1, "seed": "private", "status": "waiting",
        "reason": "manual-request-needs-resume-or-recovery", "error_kind": "execution-error",
        "history": [{"corner": "hanjuku-hero", "at": 10, "source": "manual-reservation"}],
        "slot": 7, "next_due_at": 10, "last_seen_at": 12, "pending": None,
        "queued_manual": {"corner": "weather", "request_id": str(uuid.uuid4()), "selected_at": 11},
        "manual_pending": {"corner": "hanjuku-hero", "request_id": request_id,
            "state_file": "retro_corner_manual.json", "selected_at": 10}}
    path = state_dir / "corner_rotation.json"
    path.write_text(json.dumps(state))
    for name in ("corner-rotation", "retro-corner", "retro-corner-manual"):
        (state_dir / f"locks/{name}.lock").touch()
    (soren / "tmp/state").mkdir(parents=True)
    (soren / "tmp/state/docich_program.lock").touch()
    return SimpleNamespace(g=g, store=store, receipt=receipt, path=path, soren=soren,
                           state=state, tmux=AbsentTmux())


def run(f, **kwargs):
    return operator.cancel(f.g, tmux=f.tmux, now=lambda: 30, **kwargs)


def snapshot(f):
    return {p: p.read_bytes() for p in f.path.parent.rglob("*") if p.is_file()}


def test_check_is_read_only_apply_changes_only_cancel_fields(fixture):
    f = fixture
    before = snapshot(f)
    eligible = run(f)
    assert eligible["status"] == "eligible"
    assert snapshot(f) == before
    outcome = run(f, apply=True, expected=eligible["fingerprint"])
    assert outcome == {"status": "cancelled", "corner": "hanjuku-hero", "weather_queue_preserved": True}
    actual = json.loads(f.path.read_text())
    expected = copy.deepcopy(f.state)
    expected.update(manual_pending=None, status="waiting", reason="manual-request-cancelled", error_kind=None)
    expected["last_manual_cancel"] = {"reservation": f.state["manual_pending"], "at": 30,
                                      "reason": "owner-approved-detached-reservation"}
    assert actual == expected
    assert {p: b for p, b in snapshot(f).items() if p != f.path} == {p: b for p, b in before.items() if p != f.path}


@pytest.mark.parametrize("change,reason", [
    ("no-receipt", "resources_unverified"), ("queued-receipt", "receipt_not_released"),
    ("accepted-receipt", "receipt_not_released"), ("cleanup", "receipt_not_released"),
    ("succeeded", "receipt_not_released"), ("runtime", "runtime_resources_present"),
    ("active", "target_active"), ("missing-canonical", "switch_not_stable"),
    ("owner-busy", "owner_not_terminal"), ("owner-matching", "owner_requires_reconciliation"),
    ("corrupt-owner", "unreadable_evidence"), ("wrong-corner", "reservation_not_in_scope"),
    ("no-weather", "reservation_not_in_scope"), ("auto-pending", "reservation_not_in_scope"),
    ("runtime-window", "runtime_resources_present"), ("tmux-unknown", "evidence_unverified"),
])
def test_refusal_never_changes_any_state(fixture, change, reason):
    f = fixture
    receipt = copy.deepcopy(f.receipt)
    if change == "no-receipt":
        f.store.receipts._path(receipt["request_id"]).unlink()
    elif change in {"queued-receipt", "accepted-receipt"}:
        receipt.update(status=change.split("-")[0], result=None)
        f.store.receipts.save(receipt)
    elif change in {"cleanup", "succeeded"}:
        if change == "cleanup":
            receipt["result"]["cleanup_pending"] = True
        else:
            receipt["status"] = receipt["result"]["status"] = "succeeded"
        f.store.receipts.save(receipt)
    elif change == "runtime":
        Path(receipt["runtime_dir"]).mkdir(parents=True)
    elif change == "active":
        # Corrupt/unknown canonical also refuses; use the validated runtime contract.
        from test_game_switch import _runtime
        canonical, _ = f.store.canonical.load()
        canonical.update(phase="ready", active=_runtime(game="hanjuku-hero"))
        f.store.canonical.save(canonical)
    elif change == "missing-canonical":
        f.store.canonical.path.unlink()
    elif change.startswith("owner-"):
        owner = {"status": "active" if change == "owner-busy" else "completed",
                 "rotation_request_id": receipt["request_id"] if change == "owner-matching" else "other"}
        (f.path.parent / "retro_corner_manual.json").write_text(json.dumps(owner))
    elif change == "corrupt-owner":
        (f.path.parent / "retro_corner_manual.json").write_text("{")
    elif change in {"wrong-corner", "no-weather", "auto-pending"}:
        state = copy.deepcopy(f.state)
        if change == "wrong-corner": state["manual_pending"]["corner"] = "nsnake"
        if change == "no-weather": state["queued_manual"] = None
        if change == "auto-pending": state["pending"] = {"corner": "weather"}
        f.path.write_text(json.dumps(state))
    elif change == "runtime-window":
        f.tmux.window_target_exists = lambda *args, **kwargs: True
    elif change == "tmux-unknown":
        def unknown(*args, **kwargs): raise RuntimeError("private path/token not emitted")
        f.tmux.window_target_exists = unknown
    before = snapshot(f)
    with pytest.raises(Exception) as caught:
        run(f, apply=True, expected=operator.hashlib.sha256(json.dumps(
            json.loads(f.path.read_text())["manual_pending"], sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    if reason != "evidence_unverified":
        assert str(caught.value) == reason
    assert snapshot(f) == before


def test_stale_fingerprint_and_busy_lock_refuse(fixture):
    f = fixture
    fingerprint = run(f)["fingerprint"]
    state = copy.deepcopy(f.state)
    state["manual_pending"]["request_id"] = str(uuid.uuid4())
    f.path.write_text(json.dumps(state))
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="reservation_changed"):
        run(f, apply=True, expected=fingerprint)
    assert snapshot(f) == before
    with (f.path.parent / "locks/retro-corner-manual.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(operator.CancelRefused, match="busy"):
            run(f)
    assert snapshot(f) == before


def test_apply_requires_reviewed_fingerprint(fixture):
    with pytest.raises(operator.CancelRefused, match="expected_reservation_required"):
        run(fixture, apply=True)


def test_absent_owner_does_not_hide_live_program_queue(fixture):
    f = fixture
    program = f.soren / "tmp/state"
    (program / "docich_program_queue").mkdir()
    (program / "docich_program_queue/retro_corner_manual.json").write_text(json.dumps({"status": "waiting_boundary"}))
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="program_queue_unverified"):
        run(f)
    assert snapshot(f) == before


def test_cli_masks_unexpected_private_failure(monkeypatch, capsys):
    monkeypatch.setattr(operator, "load_global", lambda *args: None)
    def fail(*args, **kwargs): raise RuntimeError("PRIVATE_TOKEN /private/runtime")
    monkeypatch.setattr(operator, "cancel", fail)
    assert operator.main(["check"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "reason": "evidence_unverified"}


@pytest.mark.parametrize("change", ["released", "presenter-active", "window", "unknown", "queued-receipt"])
def test_missing_receipt_needs_actual_last_owner_resource_release(fixture, change):
    f = fixture
    f.store.receipts._path(f.receipt["request_id"]).unlink()
    runtime = Path(f.receipt["runtime_dir"])
    runtime.mkdir(parents=True)
    (runtime / "presentation.json").write_text(json.dumps({"status": "stopped" if change != "presenter-active" else "ready"}))
    owner = {"game": "hanjuku-hero", "status": "interrupted", "recovery_required": False,
             "rotation_request_id": str(uuid.uuid4()), "bot_runtime_id": f.receipt["runtime_id"],
             "bot_identity": {"game": "hanjuku-hero", "runtime_id": f.receipt["runtime_id"], "generation": 1}}
    if change == "unknown": owner["recovery_required"] = None
    (f.path.parent / "retro_corner.json").write_text(json.dumps(owner))
    if change == "window": f.tmux.window_target_exists = lambda *args, **kwargs: True
    if change == "queued-receipt": f.store.accept_request(str(uuid.uuid4()), "start", "hanjuku-hero")
    # The negative receipt branch still requires a stable canonical snapshot.
    idle = _initial_state()
    idle["next_generation"] = 3
    f.store.canonical.save(idle)
    before = snapshot(f)
    if change == "released":
        result = run(f)
        assert result["status"] == "eligible"
        assert snapshot(f) == before
        assert run(f, apply=True, expected=result["fingerprint"])["status"] == "cancelled"
        assert json.loads(f.path.read_text())["queued_manual"] == f.state["queued_manual"]
    else:
        with pytest.raises(operator.CancelRefused): run(f)
        assert snapshot(f) == before
