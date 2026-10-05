"""Offline exact-two admin cancellation, uncertainty and crash recovery."""
import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import hanjuku_queue_admin_cancel as operator
from docich import hanjuku_manual_admin_release as release
from docich import hanjuku_manual_cancel as cancellation
from test_hanjuku_manual_cancel import fixture, snapshot
from test_game_switch import _runtime


@pytest.fixture
def detached(fixture, monkeypatch):
    f = fixture
    from docich.trading import soren_output
    monkeypatch.setattr(soren_output, "resolve_soren_root", lambda g: f.soren)
    monkeypatch.setattr(release, "resolve_soren_root", lambda g: f.soren)
    f.store.receipts._path(f.receipt["request_id"]).unlink()
    (f.path.parent / "locks/retro-corner-tick.lock").touch()
    canonical, _ = f.store.canonical.load()
    canonical.update(phase="ready", active=_runtime(game="sorengame"), next_generation=3)
    f.store.canonical.save(canonical)
    f.queue = f.soren / "tmp/state/docich_program_queue"
    f.queue.mkdir()
    f.original = {}
    for kind, filename in operator.FILES.items():
        # Original whitespace, extra fields and JSON types must survive in audit.
        raw = json.dumps({"status": "error", "requested_at": 11, "wait_deadline_ts": 20,
                          "extra": [True, 1, 1.0, "PRIVATE_PAYLOAD"]}, indent=2).encode()
        (f.queue / filename).write_bytes(raw)
        f.original[kind] = raw
        owner = {"status": "interrupted" if kind == "scheduled" else "completed",
                 "game": "hanjuku-hero" if kind == "scheduled" else "nsnake",
                 "rotation_request_id": str(uuid.uuid4())}
        (f.path.parent / filename).write_text(json.dumps(owner))
    (f.soren / "tmp/state/docich_program_active.json").write_text(json.dumps({
        "owner_state": str(f.path.parent / operator.FILES["manual"])}))
    return f


def run(f, **kwargs):
    return operator.cancel(f.g, now=lambda: 30, **kwargs)


def approved(f):
    return run(f)["approval_fingerprint"]


def apply(f, expected=None):
    return run(f, expected=expected or approved(f), apply=True, acknowledge_unknown_resources=True)


def test_check_does_not_write_or_claim_safe_release_and_apply_preserves_exact_history(detached):
    f = detached
    before = snapshot(f)
    result = run(f)
    assert result["status"] == "admin-check"
    assert result["all_resources_released"] is None and result["cancellation_authority"] is False
    assert result["unknown_resources_acknowledgement_required"] is True
    assert snapshot(f) == before
    fingerprint = result["approval_fingerprint"]
    outcome = apply(f, fingerprint)
    assert outcome["status"] == "admin-cancelled"
    assert outcome["all_resources_released"] is None and outcome["cancellation_authority"] is False
    journal = f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{fingerprint}.json"
    saved = json.loads(journal.read_bytes())
    assert journal.stat().st_mode & 0o777 == 0o600
    assert saved["unknown_resources_acknowledged"] is True
    for kind, filename in operator.FILES.items():
        assert base64.b64decode(saved["queues"][kind]["original"]) == f.original[kind]
        current = json.loads((f.queue / filename).read_bytes())
        expected = json.loads(f.original[kind])
        expected.update(status="cancelled", admin_cancellation={"schema_version": 1,
            "reason": operator.REASON, "audit_id": fingerprint, "at": 30,
            "all_resources_released": None, "cancellation_authority": False})
        assert current == expected
    allowed = {journal, *(f.queue / name for name in operator.FILES.values())}
    assert {p: raw for p, raw in snapshot(f).items() if p not in allowed} == {
        p: raw for p, raw in before.items() if p not in allowed}
    assert f.path.read_bytes() == before[f.path]
    assert "PRIVATE" not in json.dumps(outcome)
    before_retry = snapshot(f)
    assert apply(f, fingerprint) == outcome
    assert snapshot(f) == before_retry


@pytest.mark.parametrize("expected,ack,reason", [
    (None, True, "expected_context_required"), ("wrong", True, "expected_context_required"),
    ("a" * 64, False, "unknown_resources_not_acknowledged"),
    ("a" * 64, 1, "unknown_resources_not_acknowledged")])
def test_apply_requires_exact_token_and_explicit_boolean_uncertainty_acceptance(detached, expected, ack, reason):
    f = detached
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match=reason):
        run(f, expected=expected, apply=True, acknowledge_unknown_resources=ack)
    assert snapshot(f) == before


@pytest.mark.parametrize("change,reason", [
    ("running", "queue_not_error"), ("waiting", "queue_not_error"), ("done", "queue_not_error"),
    ("empty", "queue_not_error"), ("history", "queue_history_unverified"),
    ("clock", "clock_regressed"), ("bad-deadline", "queue_history_unverified"),
    ("owner-active", "owner_not_terminal"), ("owner-matches", "owner_requires_reconciliation"),
    ("registry-outside", "program_owner_unverified"), ("registry-missing-owner", "missing_evidence"),
    ("registry-active", "program_owner_unverified"), ("new-receipt", "receipt_now_present"),
    ("non-soren-active", "target_active"), ("retiring", "switch_not_stable"),
    ("wrong-reservation", "reservation_not_in_scope"), ("wrong-weather", "reservation_not_in_scope")])
@pytest.mark.parametrize("execute", [False, True])
def test_refusal_preserves_all_state(detached, change, reason, execute):
    f = detached
    token = approved(f)
    queue = f.queue / operator.FILES["scheduled"]
    value = json.loads(queue.read_bytes())
    if change in {"running", "waiting", "done"}: value["status"] = change
    if change == "empty": value = {}
    if change == "history": value["admin_cancellation"] = {}
    if change == "clock": value.update(requested_at=100, wait_deadline_ts=120)
    if change == "bad-deadline": value["wait_deadline_ts"] = 0
    if change in {"running", "waiting", "done", "empty", "history", "clock", "bad-deadline"}:
        queue.write_text(json.dumps(value))
    owner_path = f.path.parent / operator.FILES["manual"]
    if change in {"owner-active", "owner-matches"}:
        owner = json.loads(owner_path.read_bytes())
        if change == "owner-active": owner["status"] = "active"
        else: owner["rotation_request_id"] = f.receipt["request_id"]
        owner_path.write_text(json.dumps(owner))
    if change.startswith("registry-"):
        path = f.soren / "tmp/state/docich_program_active.json"
        if change == "registry-outside": path.write_text('{"owner_state":"/private/do-not-read"}')
        else:
            owner = f.path.parent / "weather_corner.json"
            if change == "registry-active": owner.write_text('{"status":"active"}')
            path.write_text(json.dumps({"owner_state": str(owner)}))
    if change == "new-receipt": f.store.receipts.save(f.receipt)
    if change in {"non-soren-active", "retiring"}:
        canonical, _ = f.store.canonical.load()
        if change == "non-soren-active": canonical["active"] = _runtime(game="hanjuku-hero")
        else: canonical["retiring"] = [_runtime(generation=2, game="sorengame")]
        f.store.canonical.save(canonical)
    if change in {"wrong-reservation", "wrong-weather"}:
        state = json.loads(f.path.read_bytes())
        state["manual_pending" if change == "wrong-reservation" else "queued_manual"]["corner"] = "nsnake"
        f.path.write_text(json.dumps(state))
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match=reason):
        run(f, expected=token if execute else None, apply=execute, acknowledge_unknown_resources=True)
    assert snapshot(f) == before


@pytest.mark.parametrize("key", ["reservation", "queue_scheduled", "queue_manual", "owner_scheduled",
                                  "owner_manual", "registry", "canonical"])
@pytest.mark.parametrize("first,changed", [(1, True), (10, 10.0)])
def test_full_context_approval_distinguishes_json_types(detached, key, first, changed):
    f = detached
    paths, _, _ = operator._context(f.path.parent, f.soren / "tmp/state", 30)
    path = paths[key]
    original = json.loads(path.read_bytes())
    original["private_extra"] = first
    path.write_text(json.dumps(original))
    token = approved(f)
    altered = {**original, "private_extra": changed}
    assert original == altered
    path.write_text(json.dumps(altered))
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="context_changed"):
        apply(f, token)
    assert snapshot(f) == before


@pytest.mark.parametrize("key", ["reservation", "queue_scheduled", "queue_manual", "owner_scheduled",
                                  "owner_manual", "registry", "canonical"])
def test_last_moment_drift_never_overwrites_competing_state(detached, monkeypatch, key):
    f = detached
    token = approved(f)
    paths, _, _ = operator._context(f.path.parent, f.soren / "tmp/state", 30)
    target = paths[key]
    read = operator._read
    calls = 0
    changed = None
    def drift(path, **kwargs):
        nonlocal calls, changed
        if path == target:
            calls += 1
            if calls == 2:
                value = json.loads(path.read_bytes())
                value["concurrent_write"] = True
                path.write_text(json.dumps(value))
                changed = path.read_bytes()
        return read(path, **kwargs)
    monkeypatch.setattr(operator, "_read", drift)
    with pytest.raises(operator.CancelRefused, match="context_changed"):
        apply(f, token)
    assert target.read_bytes() == changed
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


@pytest.mark.parametrize("stage", ["before-first", "before-second", "after-second"])
def test_journal_precedes_writes_and_partial_apply_resumes_same_plan_only(detached, monkeypatch, stage):
    f = detached
    token = approved(f)
    write = operator._write_at
    queue_writes = 0
    def crash(directory, name, raw, **kwargs):
        nonlocal queue_writes
        if name in operator.FILES.values():
            queue_writes += 1
            audit = f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{token}.json"
            assert audit.is_file()
            if (stage == "before-first" and queue_writes == 1
                    or stage == "before-second" and queue_writes == 2):
                raise RuntimeError("synthetic interrupt")
            result = write(directory, name, raw, **kwargs)
            if stage == "after-second" and queue_writes == 2:
                raise RuntimeError("synthetic interrupt")
            return result
        return write(directory, name, raw, **kwargs)
    monkeypatch.setattr(operator, "_write_at", crash)
    with pytest.raises(RuntimeError, match="synthetic interrupt"):
        apply(f, token)
    monkeypatch.setattr(operator, "_write_at", write)
    journal = f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{token}.json"
    original_journal = journal.read_bytes()
    assert apply(f, token)["status"] == "admin-cancelled"
    assert journal.read_bytes() == original_journal
    for filename in operator.FILES.values():
        assert json.loads((f.queue / filename).read_bytes())["status"] == "cancelled"


def partial(f, monkeypatch):
    token = approved(f)
    write = operator._write_at
    def crash(directory, name, raw, **kwargs):
        if name == operator.FILES["manual"]: raise RuntimeError("interrupt")
        return write(directory, name, raw, **kwargs)
    monkeypatch.setattr(operator, "_write_at", crash)
    with pytest.raises(RuntimeError): apply(f, token)
    monkeypatch.setattr(operator, "_write_at", write)
    return token


@pytest.mark.parametrize("change", ["reservation", "queue", "journal", "owner"])
def test_partial_recovery_refuses_changed_context_or_tampered_history(detached, monkeypatch, change):
    f = detached
    token = partial(f, monkeypatch)
    target = {"reservation": f.path, "queue": f.queue / operator.FILES["manual"],
              "owner": f.path.parent / operator.FILES["manual"],
              "journal": f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{token}.json"}[change]
    value = json.loads(target.read_bytes())
    value["other_writer"] = True
    target.write_text(json.dumps(value))
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="journal_unverified" if change == "journal" else "context_changed"):
        apply(f, token)
    assert snapshot(f) == before


@pytest.mark.parametrize("kind", ["scheduled", "manual"])
@pytest.mark.parametrize("raw", [b"[]", b"null", b"", b"{", b'{"status":"error","status":"done"}',
                                  b'{"status":"error","requested_at":NaN}', b"x" * (operator.QUEUE_LIMIT + 1)])
def test_invalid_or_unbounded_queue_refuses(detached, kind, raw):
    f = detached
    (f.queue / operator.FILES[kind]).write_bytes(raw)
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused): run(f)
    assert snapshot(f) == before


@pytest.mark.parametrize("ancestor", [False, True])
def test_symlink_does_not_follow_or_create_history(detached, ancestor):
    f = detached
    outside = f.path.parent.parent / "private-outside"
    outside.mkdir()
    if ancestor:
        f.queue.rename(outside / "real-queue")
        f.queue.symlink_to(outside / "real-queue", target_is_directory=True)
    else:
        path = f.queue / operator.FILES["manual"]
        path.rename(outside / "private.json")
        path.symlink_to(outside / "private.json")
    with pytest.raises(operator.CancelRefused): run(f)
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


@pytest.mark.parametrize("name", ["corner-rotation", "retro-corner-tick", "retro-corner", "retro-corner-manual", "game-switch", "program"])
def test_busy_writer_lock_refuses_without_changes(detached, name):
    f = detached
    path = (f.soren / "tmp/state/docich_program.lock" if name == "program"
            else f.path.parent / f"locks/{name}.lock")
    before = snapshot(f)
    with path.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(operator.CancelRefused, match="busy"): run(f)
    assert snapshot(f) == before
    assert run(f)["status"] == "admin-check"


def test_old_release_guard_remains_closed_until_separate_explicit_cancellation(detached):
    f = detached
    expected = hashlib.sha256(release._serialized(f.state["manual_pending"])).hexdigest()
    before = snapshot(f)
    with pytest.raises(cancellation.CancelRefused, match="program_queue_unverified"):
        release.release(f.g, expected=expected, now=lambda: 30)
    assert snapshot(f) == before
    apply(f)
    assert release.release(f.g, expected=expected, now=lambda: 30)["status"] == "admin-eligible"
    # Only a dry-run of reservation release; old reservation is still intact.
    assert f.path.read_bytes() == before[f.path]


def test_public_boundary_never_prints_arbitrary_error_or_private_output(monkeypatch, capsys):
    monkeypatch.setattr(operator, "cancel", lambda *args, **kwargs: (_ for _ in ()).throw(
        operator.CancelRefused("PRIVATE_SECRET /private/path")))
    from docich import config
    monkeypatch.setattr(config, "load_global", lambda *args: None)
    assert operator.main(["check"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "reason": "evidence_unverified"}


def test_fifo_and_lock_symlink_fail_promptly_without_history(detached):
    f = detached
    queue = f.queue / operator.FILES["manual"]
    queue.unlink()
    os.mkfifo(queue)
    with pytest.raises(operator.CancelRefused, match="unsafe_state"):
        run(f)
    queue.unlink()
    queue.write_bytes(f.original["manual"])
    lock = f.path.parent / "locks/retro-corner.lock"
    target = f.path.parent / "private-lock"
    lock.rename(target)
    lock.symlink_to(target)
    with pytest.raises(operator.CancelRefused, match="lock_unavailable"):
        run(f)
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


def test_source_growth_during_read_refuses_and_closes_descriptors(detached, monkeypatch):
    f = detached
    path = f.queue / operator.FILES["manual"]
    before_fds = len(list(Path("/proc/self/fd").iterdir()))
    read = operator.os.read
    def growing(fd, limit):
        target = os.readlink(f"/proc/self/fd/{fd}")
        if target == str(path):
            with path.open("ab") as stream: stream.write(b" " * operator.QUEUE_LIMIT)
        return read(fd, limit)
    monkeypatch.setattr(operator.os, "read", growing)
    with pytest.raises(operator.CancelRefused, match="context_changed"):
        run(f)
    assert len(list(Path("/proc/self/fd").iterdir())) == before_fds
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


def test_queue_parent_substitution_refuses_without_writing_redirect_target(detached, monkeypatch):
    f = detached
    token = approved(f)
    original_read = operator._read
    calls = 0
    old = f.queue.parent / "detached-old-queue"
    redirect = f.queue.parent / "redirect"
    redirect.mkdir()
    for kind, filename in operator.FILES.items(): (redirect / filename).write_bytes(f.original[kind])
    before = {p: p.read_bytes() for p in redirect.iterdir()}
    def substitute(path, **kwargs):
        nonlocal calls
        if path == f.queue / operator.FILES["manual"]:
            calls += 1
            if calls == 2:
                f.queue.rename(old)
                f.queue.symlink_to(redirect, target_is_directory=True)
        return original_read(path, **kwargs)
    monkeypatch.setattr(operator, "_read", substitute)
    with pytest.raises(operator.CancelRefused): apply(f, token)
    assert {p: p.read_bytes() for p in redirect.iterdir()} == before
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


def test_noncooperating_writer_between_queue_steps_is_preserved_for_manual_review(detached, monkeypatch):
    f = detached
    token = approved(f)
    write = operator._write_at
    competing = None
    def drift(directory, name, raw, **kwargs):
        nonlocal competing
        result = write(directory, name, raw, **kwargs)
        if name == operator.FILES["scheduled"]:
            target = f.queue / operator.FILES["manual"]
            value = json.loads(target.read_bytes())
            value["new_writer"] = True
            target.write_text(json.dumps(value))
            competing = target.read_bytes()
        return result
    monkeypatch.setattr(operator, "_write_at", drift)
    with pytest.raises(operator.CancelRefused, match="context_changed"):
        apply(f, token)
    assert (f.queue / operator.FILES["manual"]).read_bytes() == competing
    assert json.loads((f.queue / operator.FILES["scheduled"]).read_bytes())["status"] == "cancelled"
    journal = f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{token}.json"
    assert journal.is_file()
    monkeypatch.setattr(operator, "_write_at", write)
    with pytest.raises(operator.CancelRefused, match="context_changed"):
        apply(f, token)
    assert (f.queue / operator.FILES["manual"]).read_bytes() == competing


def test_journal_corruption_after_creation_prevents_queue_writes(detached, monkeypatch):
    f = detached
    token = approved(f)
    write = operator._write_at
    def tamper(directory, name, raw, **kwargs):
        result = write(directory, name, raw, **kwargs)
        if name == f"{token}.json":
            path = f.soren / "tmp/state" / operator.JOURNAL_DIR / name
            path.write_text("{}")
        return result
    monkeypatch.setattr(operator, "_write_at", tamper)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        apply(f, token)
    for kind, filename in operator.FILES.items():
        assert (f.queue / filename).read_bytes() == f.original[kind]


def test_private_check_token_is_not_an_execution_or_receipt_identifier(detached):
    f = detached
    result = run(f)
    assert operator.DIGEST.fullmatch(result["approval_fingerprint"])
    raw = json.dumps(result)
    assert f.receipt["request_id"] not in raw
    assert str(f.path.parent) not in raw
    assert "PRIVATE" not in raw


def test_valid_json_prefix_from_short_read_is_never_approved_or_journaled(detached, monkeypatch):
    f = detached
    target = f.queue / operator.FILES["manual"]
    target.write_bytes(b'{"status":"error","requested_at":11,"wait_deadline_ts":20}   ')
    actual = operator.os.read
    def short(fd, limit):
        if os.readlink(f"/proc/self/fd/{fd}") == str(target) and limit > 1:
            return actual(fd, target.stat().st_size - 3)
        return actual(fd, limit)
    monkeypatch.setattr(operator.os, "read", short)
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="context_changed"):
        run(f)
    assert snapshot(f) == before
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


def test_existing_journal_is_durably_synced_before_retry_queue_writes(detached, monkeypatch):
    f = detached
    token = partial(f, monkeypatch)
    events = []
    sync = operator.os.fsync
    write = operator._write_at
    def record_sync(fd):
        events.append(("sync", os.readlink(f"/proc/self/fd/{fd}")))
        return sync(fd)
    def record_write(directory, name, raw, **kwargs):
        events.append(("write", name))
        return write(directory, name, raw, **kwargs)
    monkeypatch.setattr(operator.os, "fsync", record_sync)
    monkeypatch.setattr(operator, "_write_at", record_write)
    assert apply(f, token)["status"] == "admin-cancelled"
    first_write = next(i for i, event in enumerate(events) if event[0] == "write")
    before = events[:first_write]
    program = f.soren / "tmp/state"
    assert ("sync", str(program / operator.JOURNAL_DIR / f"{token}.json")) in before
    assert ("sync", str(program / operator.JOURNAL_DIR)) in before
    assert ("sync", str(program)) in before


def test_journal_durability_failure_refuses_before_any_new_queue_write(detached, monkeypatch):
    f = detached
    token = partial(f, monkeypatch)
    before = snapshot(f)
    sync = operator.os.fsync
    journal = f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{token}.json"
    def failed_sync(fd):
        if os.readlink(f"/proc/self/fd/{fd}") == str(journal):
            raise OSError("synthetic storage failure")
        return sync(fd)
    monkeypatch.setattr(operator.os, "fsync", failed_sync)
    with pytest.raises(OSError):
        apply(f, token)
    assert snapshot(f) == before


def test_shared_retro_tick_guard_blocks_normal_queue_producer_inside_write_window(detached, monkeypatch):
    from docich.retro_corner import RetroCornerManager
    from docich.corner_boundary import _queue_write
    f = detached
    token = approved(f)
    manager = object.__new__(RetroCornerManager)
    manager.tick_guard_path = f.path.parent / "locks/retro-corner-tick.lock"
    write = operator._write_at
    attempts = []
    def try_producer(directory, name, raw, **kwargs):
        if name == operator.FILES["manual"]:
            # The official legacy tick holds this guard from before its owner
            # read through all program_slot queue writes. It cannot enter here.
            with manager._tick_guard() as single:
                attempts.append(single)
                if single:
                    _queue_write(f.soren / "tmp/state", "retro_corner_manual",
                                 status="waiting_boundary", requested_at=31, wait_deadline_ts=50)
        return write(directory, name, raw, **kwargs)
    monkeypatch.setattr(operator, "_write_at", try_producer)
    assert apply(f, token)["status"] == "admin-cancelled"
    assert attempts == [False]
    with manager._tick_guard() as single:
        assert single


def test_live_legacy_tick_excludes_cancel_even_with_terminal_owner(detached):
    from docich.retro_corner import RetroCornerManager
    from docich.corner_boundary import _queue_write
    f = detached
    manager = object.__new__(RetroCornerManager)
    manager.tick_guard_path = f.path.parent / "locks/retro-corner-tick.lock"
    before = snapshot(f)
    with manager._tick_guard() as single:
        assert single
        with pytest.raises(operator.CancelRefused, match="busy"):
            run(f)
        assert snapshot(f) == before
        _queue_write(f.soren / "tmp/state", "retro_corner_manual",
                     status="waiting_boundary", requested_at=31, wait_deadline_ts=50)
    assert json.loads((f.queue / operator.FILES["manual"]).read_bytes())["requested_at"] == 31
    assert not (f.soren / "tmp/state" / operator.JOURNAL_DIR).exists()


def test_manual_and_scheduled_managers_share_the_existing_tick_guard():
    import ast
    from docich import retro_corner_manual
    # Any future manual tick-guard override must update this operator's bounded
    # exclusion contract rather than silently introducing an unheld lock.
    source = Path(retro_corner_manual.__file__).read_text()
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                   and node.attr == "tick_guard_path" for node in ast.walk(tree))


def test_absent_shared_tick_guard_refuses_without_creating_it(detached):
    f = detached
    path = f.path.parent / "locks/retro-corner-tick.lock"
    path.unlink()
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="lock_unavailable"):
        run(f)
    assert snapshot(f) == before
    assert not path.exists()


@pytest.mark.parametrize("target,mode", [("directory", 0o755), ("directory", 0o770),
                                        ("journal", 0o644), ("journal", 0o660)])
def test_unsafe_existing_audit_permissions_are_refused_without_chmod_or_queue_writes(detached, monkeypatch, target, mode):
    f = detached
    token = partial(f, monkeypatch)
    directory = f.soren / "tmp/state" / operator.JOURNAL_DIR
    path = directory if target == "directory" else directory / f"{token}.json"
    path.chmod(mode)
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        apply(f, token)
    assert snapshot(f) == before
    assert path.stat().st_mode & 0o777 == mode


def test_existing_unprivate_empty_audit_directory_is_not_used_or_chmodded(detached):
    f = detached
    token = approved(f)
    directory = f.soren / "tmp/state" / operator.JOURNAL_DIR
    directory.mkdir(mode=0o755)
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        apply(f, token)
    assert snapshot(f) == before
    assert directory.stat().st_mode & 0o777 == 0o755


def test_private_audit_journal_with_another_hard_link_is_refused(detached, monkeypatch):
    f = detached
    token = partial(f, monkeypatch)
    path = f.soren / "tmp/state" / operator.JOURNAL_DIR / f"{token}.json"
    os.link(path, f.path.parent / "journal-alias")
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        apply(f, token)
    assert snapshot(f) == before


def test_foreign_owned_audit_storage_is_refused_without_ownership_changes(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(operator.os, "geteuid", lambda: 123)
    info = SimpleNamespace(st_uid=456, st_mode=0o40700, st_nlink=1)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        operator._private_storage(info, 0o700, directory=True)


def test_hard_crash_after_journal_link_before_temp_unlink_is_safe_manual_stop(detached):
    f = detached
    token = approved(f)
    program = f.soren / "tmp/state"
    _, _, sources = operator._context(f.path.parent, program, 30)
    journal = operator._journal(operator._fingerprints(sources), sources, token, 30)
    directory = program / operator.JOURNAL_DIR
    directory.mkdir(mode=0o700)
    temporary = directory / ".admin-cancel-crash.tmp"
    temporary.write_bytes(operator._serialized(journal) + b"\n")
    temporary.chmod(0o600)
    os.link(temporary, directory / f"{token}.json")
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        apply(f, token)
    assert snapshot(f) == before
    for kind, filename in operator.FILES.items():
        assert (f.queue / filename).read_bytes() == f.original[kind]
