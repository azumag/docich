"""Synthetic administrative release: exact request, uncertainty and one write."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import hanjuku_manual_admin_release as admin
from docich import hanjuku_manual_cancel as cancellation
from docich import hanjuku_admin_result as reporting
from test_hanjuku_manual_cancel import fixture, snapshot
from test_game_switch import _runtime


@pytest.fixture
def detached(fixture, monkeypatch):
    f = fixture
    monkeypatch.setattr(admin, "resolve_soren_root", lambda g: f.soren)
    f.store.receipts._path(f.receipt["request_id"]).unlink()
    f.expected = digest(f.state["manual_pending"])
    return f


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(f, **kwargs):
    return admin.release(f.g, expected=f.expected, now=lambda: 30, **kwargs)


def test_check_read_only_release_preserves_everything_else_and_unknown(detached):
    f = detached
    # Keep another active game and old terminal owner/resource intact. They
    # are not evidence this request was never dispatched or released.
    canonical, _ = f.store.canonical.load()
    canonical.update(phase="ready", active=_runtime(game="sorengame"), next_generation=3)
    f.store.canonical.save(canonical)
    (f.path.parent / "retro_corner.json").write_text(json.dumps({
        "status": "interrupted", "game": "hanjuku-hero",
        "rotation_request_id": str(uuid.uuid4()), "recovery_required": False}))
    old = f.path.parent / "runtimes/private-old-runtime"
    old.mkdir(parents=True)
    (old / "presentation.json").write_text('{"status":"ready"}')
    f.state["manual_admin_releases"] = [{"reservation": {"request_id": str(uuid.uuid4())}, "at": 1}]
    f.path.write_text(json.dumps(f.state))
    before = snapshot(f)
    check = run(f)
    assert check["status"] == "admin-eligible"
    assert check["all_resources_released"] is None and check["cancellation_authority"] is False
    assert snapshot(f) == before
    outcome = run(f, apply=True)
    assert outcome == {**check, "status": "admin-released"}
    updated = json.loads(f.path.read_text())
    expected = copy.deepcopy(f.state)
    expected.update(manual_pending=None, status="waiting", reason="manual-request-admin-released", error_kind=None)
    expected["manual_admin_releases"].append({
        "reservation": f.state["manual_pending"], "at": 30,
        "reason": "owner-approved-admin-release-with-unknown-history",
        "receipt_present": False, "request_generation_coverage": "unknown",
        "resource_attribution_unknown": True, "all_resources_released": None,
        "cancellation_authority": False})
    assert updated == expected
    assert {p: b for p, b in snapshot(f).items() if p != f.path} == {p: b for p, b in before.items() if p != f.path}
    assert not f.store.receipts._path(f.receipt["request_id"]).exists()
    before_retry = snapshot(f)
    with pytest.raises(admin.CancelRefused, match="reservation_not_in_scope"):
        run(f, apply=True)
    assert snapshot(f) == before_retry


@pytest.mark.parametrize("statuses", [
    ("error", None),
    (None, "error"),
    ("error", "error"),
    ("done", "error"),
])
@pytest.mark.parametrize("apply", [False, True])
def test_stale_error_program_queue_requires_full_revalidation_and_is_never_rewritten(
        detached, statuses, apply):
    f = detached
    queue_dir = f.soren / "tmp/state/docich_program_queue"
    queue_dir.mkdir(parents=True)
    for name, status in zip(cancellation.OWNER_FILES, statuses):
        if status is not None:
            (queue_dir / name).write_text(json.dumps(
                {"status": status, "private_payload": "PRIVATE_QUEUE_HISTORY"}))
    before = snapshot(f)
    result = run(f, apply=apply)
    assert result["status"] == ("admin-released" if apply else "admin-eligible")
    after = snapshot(f)
    # Administrative release never edits historical program-queue records.
    assert {p: b for p, b in after.items() if p != f.path} == {
        p: b for p, b in before.items() if p != f.path}
    if not apply:
        assert after == before
        return
    updated = json.loads(f.path.read_text())
    assert updated["manual_pending"] is None
    assert updated["queued_manual"] == f.state["queued_manual"]
    assert updated["history"] == f.state["history"]
    audit = updated["manual_admin_releases"][-1]
    assert audit["program_queue_error_observed"] is True
    assert audit["resource_attribution_unknown"] is True
    assert audit["all_resources_released"] is None
    assert audit["cancellation_authority"] is False


@pytest.mark.parametrize("filename", cancellation.OWNER_FILES)
@pytest.mark.parametrize("status", ["waiting", "waiting_turn", "waiting_boundary", "running", "unknown"])
@pytest.mark.parametrize("apply", [False, True])
def test_live_or_unknown_program_queue_still_refuses(detached, filename, status, apply):
    f = detached
    queue_dir = f.soren / "tmp/state/docich_program_queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / filename).write_text(json.dumps({"status": status}))
    before = snapshot(f)
    with pytest.raises(admin.CancelRefused, match="program_queue_unverified"):
        run(f, apply=apply)
    assert snapshot(f) == before


@pytest.mark.parametrize("field,value", [("request_id", str(uuid.uuid4())), ("selected_at", 11),
                                         ("extra", "new-payload")])
@pytest.mark.parametrize("apply", [False, True])
def test_changed_reservation_including_selected_at_refuses(detached, field, value, apply):
    f = detached
    changed = copy.deepcopy(f.state)
    changed["manual_pending"][field] = value
    f.path.write_text(json.dumps(changed))
    before = snapshot(f)
    with pytest.raises(admin.CancelRefused, match="reservation_changed"):
        run(f, apply=apply)
    assert snapshot(f) == before


@pytest.mark.parametrize("change,reason", [
    ("new-receipt", "receipt_now_present"), ("owner-active", "owner_not_terminal"),
    ("owner-matching", "owner_requires_reconciliation"), ("program-live", "program_queue_unverified"),
    ("program-empty", "program_queue_unverified"), ("registry-empty", "program_owner_unverified"),
    ("registry-outside", "program_owner_unverified"), ("registry-matching", "program_owner_unverified"),
    ("target-active", "target_active"), ("retiring", "switch_not_stable"),
    ("auto-pending", "reservation_not_in_scope"), ("different-queue", "reservation_not_in_scope"),
    ("clock", "clock_regressed"), ("audit-invalid", "invalid_admin_audit"),
    ("audit-duplicate", "already_admin_released"),
])
@pytest.mark.parametrize("with_stale_error_queue", [False, True])
@pytest.mark.parametrize("apply", [False, True])
def test_current_evidence_changes_refuse_without_mutation(
        detached, change, reason, apply, with_stale_error_queue):
    f = detached
    owner_path = f.path.parent / "retro_corner_manual.json"
    program = f.soren / "tmp/state"
    if with_stale_error_queue and not change.startswith("program-"):
        queue_dir = program / "docich_program_queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        for name in cancellation.OWNER_FILES:
            (queue_dir / name).write_text('{"status":"error"}')
    if change == "new-receipt":
        f.store.receipts.save(f.receipt)
    elif change.startswith("owner-"):
        owner_path.write_text(json.dumps({"status": "active" if change == "owner-active" else "completed",
            "rotation_request_id": f.receipt["request_id"] if change == "owner-matching" else str(uuid.uuid4())}))
    elif change.startswith("program-"):
        (program / "docich_program_queue").mkdir()
        (program / "docich_program_queue/retro_corner_manual.json").write_text(
            '{"status":"waiting_boundary"}' if change == "program-live" else "{}")
    elif change.startswith("registry-"):
        owner = {"game": "weather", "status": "active", "rotation_request_id": f.receipt["request_id"]}
        weather = f.path.parent / "weather_corner.json"
        weather.write_text(json.dumps(owner))
        value = {} if change == "registry-empty" else {"owner_state": "/outside/not-read" if change == "registry-outside" else str(weather)}
        (program / "docich_program_active.json").write_text(json.dumps(value))
    elif change in {"target-active", "retiring"}:
        canonical, _ = f.store.canonical.load()
        if change == "target-active":
            canonical.update(phase="ready", active=_runtime(game="hanjuku-hero"))
        else:
            canonical["retiring"] = [_runtime(game="sorengame")]
        f.store.canonical.save(canonical)
    else:
        state = copy.deepcopy(f.state)
        if change == "auto-pending": state["pending"] = {"corner": "weather"}
        if change == "different-queue": state["queued_manual"]["corner"] = "nsnake"
        if change == "clock":
            state["manual_pending"]["selected_at"] = 100
            f.expected = digest(state["manual_pending"])
        if change == "audit-invalid": state["manual_admin_releases"] = {}
        if change == "audit-duplicate": state["manual_admin_releases"] = [{"reservation": state["manual_pending"]}]
        f.path.write_text(json.dumps(state))
    before = snapshot(f)
    with pytest.raises(admin.CancelRefused, match=reason):
        run(f, apply=apply)
    assert snapshot(f) == before


@pytest.mark.parametrize("filename", ["corner_rotation.json", "retro_corner.json", "game_switch.json"])
def test_prewrite_snapshot_change_is_preserved(detached, monkeypatch, filename):
    f = detached
    path = f.path.parent / filename
    original = admin._object
    calls = 0
    changed = None
    def competing_read(p, **kwargs):
        nonlocal calls, changed
        if p == path:
            calls += 1
            if calls == 2:
                value = original(p, **kwargs) or {"status": "completed"}
                value["concurrent_writer"] = True
                p.write_text(json.dumps(value))
                changed = p.read_bytes()
        return original(p, **kwargs)
    monkeypatch.setattr(admin, "_object", competing_read)
    with pytest.raises(admin.CancelRefused, match="context_changed"):
        run(f, apply=True)
    assert path.read_bytes() == changed
    assert json.loads(f.path.read_text())["manual_pending"] is not None


@pytest.mark.parametrize("filename", ["retro_corner.json", "retro_corner_manual.json"])
@pytest.mark.parametrize("initially_present", [False, True])
@pytest.mark.parametrize("apply", [False, True])
def test_registry_duplicate_rejects_owner_change_or_creation_immediately(
        detached, monkeypatch, filename, initially_present, apply):
    f = detached
    owner_path = f.path.parent / filename
    if initially_present:
        owner_path.write_text(json.dumps({"status": "completed", "game": "hanjuku-hero",
                                         "rotation_request_id": str(uuid.uuid4())}))
    registry_path = f.soren / "tmp/state/docich_program_active.json"
    registry_path.write_text(json.dumps({"owner_state": str(owner_path)}))
    before = snapshot(f)
    original_read = admin._object
    original_write = admin.atomic_write_json
    calls = writes = 0
    concurrent_owner = {"status": "active", "game": "nsnake",
                        "rotation_request_id": str(uuid.uuid4())}

    def competing_read(path, **kwargs):
        nonlocal calls
        if path == owner_path:
            calls += 1
            if calls == 2:
                owner_path.write_text(json.dumps(concurrent_owner))
        return original_read(path, **kwargs)

    def counted_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        return original_write(*args, **kwargs)

    monkeypatch.setattr(admin, "_object", competing_read)
    monkeypatch.setattr(admin, "atomic_write_json", counted_write)
    with pytest.raises(admin.CancelRefused, match="context_changed"):
        run(f, apply=apply)
    assert calls == 2 and writes == 0
    assert json.loads(owner_path.read_text()) == concurrent_owner
    assert f.path.read_bytes() == before[f.path]
    assert {p: b for p, b in snapshot(f).items() if p != owner_path} == {
        p: b for p, b in before.items() if p != owner_path}


@pytest.mark.parametrize("filename", ["retro_corner.json", "retro_corner_manual.json"])
@pytest.mark.parametrize("apply", [False, True])
def test_unchanged_registry_duplicate_keeps_original_snapshot(detached, filename, apply):
    f = detached
    owner_path = f.path.parent / filename
    owner_path.write_text(json.dumps({"status": "completed", "game": "nsnake",
                                     "rotation_request_id": str(uuid.uuid4())}))
    registry_path = f.soren / "tmp/state/docich_program_active.json"
    registry_path.write_text(json.dumps({"owner_state": str(owner_path)}))
    before = snapshot(f)
    result = run(f, apply=apply)
    assert result["status"] == ("admin-released" if apply else "admin-eligible")
    assert {p: b for p, b in snapshot(f).items() if p != f.path} == {
        p: b for p, b in before.items() if p != f.path}
    if not apply:
        assert snapshot(f) == before


@pytest.mark.parametrize("field,first,changed", [("extra", 1, True), ("selected_at", 10, 10.0)])
@pytest.mark.parametrize("apply", [False, True])
def test_final_read_rechecks_reservation_fingerprint_with_json_types(
        detached, monkeypatch, field, first, changed, apply):
    f = detached
    f.state["manual_pending"][field] = first
    f.expected = digest(f.state["manual_pending"])
    f.path.write_text(json.dumps(f.state))
    original = admin._object
    calls = 0
    changed_bytes = None

    def competing_read(path, **kwargs):
        nonlocal calls, changed_bytes
        if path == f.path:
            calls += 1
            if calls == 2:
                value = original(path, **kwargs)
                value["manual_pending"][field] = changed
                # Python equality accepts these changes, but the approval
                # fingerprint does not. Preserve the competing ledger write.
                assert value["manual_pending"] == f.state["manual_pending"]
                assert digest(value["manual_pending"]) != f.expected
                path.write_text(json.dumps(value))
                changed_bytes = path.read_bytes()
        return original(path, **kwargs)

    monkeypatch.setattr(admin, "_object", competing_read)
    with pytest.raises(admin.CancelRefused, match="reservation_changed"):
        run(f, apply=apply)
    assert calls == 2 and f.path.read_bytes() == changed_bytes
    assert "manual_admin_releases" not in json.loads(f.path.read_text())


@pytest.mark.parametrize("filename", ["retro_corner.json", "retro_corner_manual.json"])
@pytest.mark.parametrize("first,changed", [(1, True), (10, 10.0)])
@pytest.mark.parametrize("registry_duplicate", [False, True])
def test_owner_snapshot_type_changes_refuse_even_when_python_equal(
        detached, monkeypatch, filename, first, changed, registry_duplicate):
    f = detached
    path = f.path.parent / filename
    owner = {"status": "completed", "game": "nsnake",
             "rotation_request_id": str(uuid.uuid4()), "extra": first}
    path.write_text(json.dumps(owner))
    if registry_duplicate:
        registry = f.soren / "tmp/state/docich_program_active.json"
        registry.write_text(json.dumps({"owner_state": str(path)}))
    before = snapshot(f)
    original = admin._object
    calls = 0

    def competing_read(p, **kwargs):
        nonlocal calls
        if p == path:
            calls += 1
            if calls == 2:
                path.write_text(json.dumps({**owner, "extra": changed}))
                assert json.loads(path.read_text()) == owner
        return original(p, **kwargs)

    monkeypatch.setattr(admin, "_object", competing_read)
    with pytest.raises(admin.CancelRefused, match="context_changed"):
        run(f, apply=True)
    assert calls == 2 and f.path.read_bytes() == before[f.path]
    assert {p: b for p, b in snapshot(f).items() if p != path} == {
        p: b for p, b in before.items() if p != path}


@pytest.mark.parametrize("first,changed", [(1, True), (10, 10.0)])
def test_canonical_snapshot_distinguishes_json_types(detached, monkeypatch, first, changed):
    f = detached
    path = f.store.canonical.path
    value = admin._object(path)
    value["extra"] = first
    path.write_text(json.dumps(value))
    before = snapshot(f)
    original = admin._object
    calls = 0
    def competing_read(p, **kwargs):
        nonlocal calls
        if p == path:
            calls += 1
            if calls == 2:
                path.write_text(json.dumps({**value, "extra": changed}))
                assert json.loads(path.read_text()) == value
        return original(p, **kwargs)
    monkeypatch.setattr(admin, "_object", competing_read)
    with pytest.raises(admin.CancelRefused, match="context_changed"):
        run(f, apply=True)
    assert calls == 2 and f.path.read_bytes() == before[f.path]
    assert json.loads(path.read_text())["extra"] == changed
    assert {p: b for p, b in snapshot(f).items() if p != path} == {
        p: b for p, b in before.items() if p != path}


@pytest.mark.parametrize("lock", ["corner-rotation", "retro-corner", "retro-corner-manual", "game-switch", "program"])
@pytest.mark.parametrize("with_stale_error_queue", [False, True])
def test_lock_contention_refuses_and_releases_prior_locks(detached, lock, with_stale_error_queue):
    import fcntl
    f = detached
    if with_stale_error_queue:
        queue_dir = f.soren / "tmp/state/docich_program_queue"
        queue_dir.mkdir(parents=True)
        for name in cancellation.OWNER_FILES:
            (queue_dir / name).write_text('{"status":"error"}')
    path = f.soren / "tmp/state/docich_program.lock" if lock == "program" else f.path.parent / f"locks/{lock}.lock"
    before = snapshot(f)
    with path.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(admin.CancelRefused, match="busy"):
            run(f, apply=True)
    assert snapshot(f) == before
    assert run(f)["status"] == "admin-eligible"


def test_existing_cancellation_still_requires_receipt(detached):
    f = detached
    before = snapshot(f)
    with pytest.raises(cancellation.CancelRefused, match="receipt_unknown"):
        cancellation.cancel(f.g, expected=f.expected, apply=True, tmux=f.tmux, now=lambda: 30)
    assert snapshot(f) == before


def test_cli_withholds_unexpected_private_failure(monkeypatch, capsys):
    monkeypatch.setattr(admin, "load_global", lambda *args: None)
    def fail(*args, **kwargs): raise RuntimeError("PRIVATE_TOKEN /private/runtime")
    monkeypatch.setattr(admin, "release", fail)
    assert admin.main(["release", "--expected", "a" * 64]) == admin.REFUSAL_CODES["evidence_unverified"]
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "reason": "evidence_unverified"}


@pytest.mark.parametrize("mode", ["check", "release"])
@pytest.mark.parametrize("reason,code", list(reporting.REFUSAL_CODES.items()))
def test_cli_refusals_survive_private_gateway_as_fixed_enum(monkeypatch, capsys, mode, reason, code):
    monkeypatch.setattr(admin, "load_global", lambda *args: None)
    def fail(*args, **kwargs):
        raise admin.CancelRefused(reason)
    monkeypatch.setattr(admin, "release", fail)
    assert admin.main([mode, "--expected", "a" * 64]) == code
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "reason": reason}
    raw = json.dumps({"status": "executed", "sha": "b" * 40,
                      "exit_code": code, "output": "withheld",
                      "private": "PRIVATE_TOKEN /private/runtime " + "a" * 64})
    public, rc = reporting.public_result(raw, mode, "b" * 40, code)
    assert rc != 0 and public["status"] == "refused" and public["reason"] == reason
    assert set(public) == {"status", "reason", "corner", "cancellation_authority"}
    assert public["cancellation_authority"] is False


@pytest.mark.parametrize("mode", ["check", "release"])
def test_unknown_exception_reason_is_never_exported(monkeypatch, capsys, mode):
    monkeypatch.setattr(admin, "load_global", lambda *args: None)
    def fail(*args, **kwargs):
        raise admin.CancelRefused("PRIVATE_TOKEN /private/runtime " + "a" * 64)
    monkeypatch.setattr(admin, "release", fail)
    assert admin.main([mode, "--expected", "a" * 64]) == reporting.REFUSAL_CODES["evidence_unverified"]
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "reason": "evidence_unverified"}


@pytest.mark.parametrize("raw", ["", "PRIVATE_TOKEN /private/runtime", "[]", "null", "42",
    '{"status":"executed","status":"rejected"}',
    json.dumps({"status": "executed", "sha": "b" * 40, "output": "withheld", "exit_code": False}),
    json.dumps({"status": "executed", "sha": "b" * 40, "output": "withheld", "exit_code": 0.0}),
    json.dumps({"status": "executed", "sha": "b" * 40, "output": "withheld", "exit_code": -1}),
    json.dumps({"status": "executed", "sha": "b" * 40, "output": "PRIVATE_TOKEN", "exit_code": 0}),
])
def test_unverified_gateway_result_is_fixed_failure(raw):
    public, rc = reporting.public_result(raw, "check", "b" * 40, 0)
    assert rc != 0 and public == {"status": "refused", "reason": "gateway_result_unverified",
                                "corner": "hanjuku-hero", "cancellation_authority": False}


def test_wire_codes_cover_actual_guards_and_do_not_collide():
    import ast
    # Shared readers/locks also raise fixed refusals. A new guard must receive
    # a reviewed code rather than silently losing its reason in the gateway.
    sources = [Path(admin.__file__).read_text()]
    tree = ast.parse(Path(cancellation.__file__).read_text())
    sources.extend(ast.get_source_segment(Path(cancellation.__file__).read_text(), node)
                   for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in {"_object", "_existing_lock"})
    reasons = {node.args[0].value for source in sources for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "CancelRefused" and node.args
               and isinstance(node.args[0], ast.Constant)}
    assert reasons <= reporting.REFUSAL_CODES.keys()
    assert len(set(reporting.REFUSAL_CODES.values())) == len(reporting.REFUSAL_CODES)
    assert all(0 < code < 255 and code not in {25, 64} for code in reporting.REFUSAL_CODES.values())


@pytest.mark.parametrize("filename", ["retro_corner.json", "game_switch.json", "corner_rotation.json"])
@pytest.mark.parametrize("kind", ["symlink", "corrupt", "oversized"])
def test_unsafe_or_unreadable_state_never_writes(detached, filename, kind):
    f = detached
    path = f.path.parent / filename
    if path.exists(): path.unlink()
    if kind == "symlink":
        target = f.soren / "outside.json"
        target.write_text("{}")
        path.symlink_to(target)
    elif kind == "corrupt": path.write_text("{")
    else: path.write_bytes(b" " * (1024 * 1024 + 1))
    before = snapshot(f)
    with pytest.raises(admin.CancelRefused):
        run(f, apply=True)
    assert snapshot(f) == before


def test_audit_growth_refuses_instead_of_pruning_or_writing_unreadable_ledger(detached):
    f = detached
    f.state["history"][0]["preserved"] = ""
    overhead = len(json.dumps(f.state, separators=(",", ":")).encode())
    f.state["history"][0]["preserved"] = "x" * (1024 * 1024 - overhead - 10)
    f.path.write_text(json.dumps(f.state, separators=(",", ":")))
    assert f.path.stat().st_size < 1024 * 1024
    before = snapshot(f)
    with pytest.raises(admin.CancelRefused, match="admin_audit_limit"):
        run(f, apply=True)
    assert snapshot(f) == before


def test_release_leaves_dispatch_to_the_next_normal_tick(detached):
    from docich.config import load_global
    from docich.corner_catalog import Corner
    from docich.corner_rotation import CornerRotationManager
    from test_corner_rotation import Adapter, Executor
    f = detached
    config = f.path.parent.parent / "config.toml"
    config.write_text('[corner_rotation]\nenabled=true\n[paths]\nstate_dir="state"\n')
    g = load_global(config.parent, config)
    executor = Executor()
    executor.result = "queued"
    weather = Corner("weather", "weather", "weather-view", duration_minutes=1)
    manager = CornerRotationManager(g, clock=lambda: 30, seed="private", catalog=[weather],
                                   adapter_factory=Adapter, executor=executor)
    run(f, apply=True)
    assert executor.calls == []
    audit = json.loads(f.path.read_text())["manual_admin_releases"]
    manager.tick()
    assert len(executor.calls) == 1 and executor.calls[0]["corner"] == "weather"
    assert executor.calls[0]["request_id"] == f.state["queued_manual"]["request_id"]
    after = json.loads(f.path.read_text())
    assert after["pending"]["corner"] == "weather"
    assert after["manual_pending"] is None and after["queued_manual"] is None
    assert after["manual_admin_releases"] == audit
