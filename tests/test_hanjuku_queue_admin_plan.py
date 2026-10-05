"""Private one-use plan binding, replay rejection and fixed queue scope."""
import copy
import fcntl
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import hanjuku_queue_admin_plan as plan
from docich import hanjuku_queue_admin_cancel as operator
from test_hanjuku_queue_admin_cancel import detached
from test_hanjuku_manual_cancel import fixture, snapshot


def binding(run_id="100", run_attempt="1"):
    return {"repository": plan.REPOSITORY, "repository_id": plan.REPOSITORY_ID,
            "actor_id": plan.ACTOR_ID, "workflow_ref": plan.WORKFLOW, "ref": plan.REF,
            "sha": "a" * 40, "run_id": run_id, "run_attempt": run_attempt}


def prepare(f, **kwargs):
    return plan.prepare(f.g, kwargs.pop("binding", binding()), now=lambda: 30, **kwargs)["plan_handle"]


def execute(f, handle, **kwargs):
    at = kwargs.pop("at", 31)
    return plan.execute(f.g, kwargs.pop("binding", binding("101")), handle,
                        acknowledge_unknown_resources=kwargs.pop("ack", True), now=lambda: at, **kwargs)


def path(f, handle):
    return f.soren / "tmp/state" / plan.PLAN_DIR / f"{handle}.json"


def test_prepare_keeps_context_and_fingerprint_private_and_execute_is_single_use(detached):
    f = detached
    before = snapshot(f)
    result = plan.prepare(f.g, binding(), now=lambda: 30)
    assert result == {"status": "plan-prepared", "plan_handle": "100-1"}
    handle = result["plan_handle"]
    saved = json.loads(path(f, handle).read_bytes())
    assert operator._fingerprint(saved["context"]) == saved["fingerprint"]
    assert saved["fingerprint"] not in json.dumps(result)
    assert f.receipt["request_id"] not in json.dumps(result)
    assert path(f, handle).stat().st_mode & 0o777 == 0o600
    assert path(f, handle).parent.stat().st_mode & 0o777 == 0o700
    assert {p: b for p, b in snapshot(f).items() if p != path(f, handle)} == before
    assert execute(f, handle) == {"status": "plan-completed", "plan_handle": handle}
    updated = json.loads(path(f, handle).read_bytes())
    assert updated["status"] == "completed" and updated["execution"] == binding("101")
    assert updated["fingerprint"] == saved["fingerprint"] and updated["context"] == saved["context"]
    assert f.path.read_bytes() == before[f.path]
    before_replay = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_used"):
        execute(f, handle, binding=binding("102"))
    assert snapshot(f) == before_replay


@pytest.mark.parametrize("field,value", [("repository", "other/repo"), ("repository_id", "42"),
    ("actor_id", "42"), ("workflow_ref", "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/main"),
    ("ref", "refs/heads/other"), ("sha", "a" * 40 + ";id"), ("run_id", "0"), ("run_attempt", "0"),
    ("run_id", True), ("run_attempt", 1)])
def test_handle_never_substitutes_for_fixed_owner_binding(detached, field, value):
    f = detached
    invalid = {**binding(), field: value}
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_binding_invalid"):
        plan.prepare(f.g, invalid, now=lambda: 30)
    assert snapshot(f) == before
    handle = prepare(f)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_binding_invalid"):
        execute(f, handle, binding={**binding("101"), field: value})
    assert snapshot(f) == before


@pytest.mark.parametrize("ack", [False, None, 1, "acknowledged"])
def test_execute_requires_explicit_boolean_ack_before_state_changes(detached, ack):
    f = detached
    handle = prepare(f)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="unknown_resources_not_acknowledged"):
        execute(f, handle, ack=ack)
    assert snapshot(f) == before


@pytest.mark.parametrize("handle", ["", "a" * 64, "../100-1", "100-1;id", "0-1", "100-0", True, 100])
def test_arbitrary_path_or_fingerprint_cannot_be_a_plan_handle(detached, handle):
    f = detached
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_handle_invalid"):
        execute(f, handle)
    assert snapshot(f) == before


def test_same_handle_cannot_be_overwritten_and_main_change_rejects_execute(detached):
    f = detached
    handle = prepare(f)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_exists"):
        prepare(f)
    assert snapshot(f) == before
    with pytest.raises(plan.PlanRefused, match="plan_unverified"):
        execute(f, handle, binding={**binding("101"), "sha": "b" * 40})
    assert snapshot(f) == before


@pytest.mark.parametrize("at", [29, 30 + plan.PLAN_TTL + 1])
def test_stale_or_regressed_clock_is_rejected(detached, at):
    f = detached
    handle = prepare(f)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_stale"):
        execute(f, handle, at=at)
    assert snapshot(f) == before


def test_check_run_cannot_execute_itself(detached):
    f = detached
    handle = prepare(f)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_stale"):
        execute(f, handle, binding=binding())
    assert snapshot(f) == before


@pytest.mark.parametrize("source", ["reservation", "canonical", "registry", "queue_scheduled", "queue_manual",
                                    "owner_scheduled", "owner_manual"])
def test_changed_full_source_bytes_prevent_execution_without_claim_or_queue_writes(detached, source):
    f = detached
    handle = prepare(f)
    sources, _, _ = operator._context(f.path.parent, f.soren / "tmp/state", 30)
    target = sources[source]
    value = json.loads(target.read_bytes())
    value["concurrent_writer"] = True
    target.write_text(json.dumps(value))
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_context_changed"):
        execute(f, handle)
    assert snapshot(f) == before
    assert json.loads(path(f, handle).read_bytes())["status"] == "prepared"


def test_new_receipt_prevents_execution_and_is_preserved(detached):
    f = detached
    handle = prepare(f)
    f.store.receipts.save(f.receipt)
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="receipt_now_present"):
        execute(f, handle)
    assert snapshot(f) == before


def test_check_capture_is_bound_to_the_original_successful_check(detached, monkeypatch):
    f = detached
    cancel = operator.cancel
    def change_after_check(*args, **kwargs):
        result = cancel(*args, **kwargs)
        target = f.queue / operator.FILES["manual"]
        value = json.loads(target.read_bytes())
        value["late"] = True
        target.write_text(json.dumps(value))
        return result
    monkeypatch.setattr(operator, "cancel", change_after_check)
    with pytest.raises(plan.PlanRefused, match="plan_context_changed"):
        prepare(f)
    assert not (f.soren / "tmp/state" / plan.PLAN_DIR).exists()


def test_existing_plan_flock_prevents_double_claim(detached):
    f = detached
    handle = prepare(f)
    before = snapshot(f)
    with path(f, handle).open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(plan.PlanRefused, match="plan_used"):
            execute(f, handle)
    assert snapshot(f) == before


@pytest.mark.parametrize("when", [1, 2])
def test_replaced_plan_inode_refuses_even_when_bytes_are_identical(detached, monkeypatch, when):
    f = detached
    handle = prepare(f)
    target = path(f, handle)
    original = plan._read_plan
    calls = 0
    def replace(current):
        nonlocal calls
        if current == target:
            calls += 1
            if calls == when:
                alternate = target.parent / "replacement"
                alternate.write_bytes(target.read_bytes())
                alternate.chmod(0o600)
                os.replace(alternate, target)
        return original(current)
    monkeypatch.setattr(plan, "_read_plan", replace)
    before = {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()}
    with pytest.raises(plan.PlanRefused, match="plan_unverified"):
        execute(f, handle)
    assert {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()} == before


def test_atomic_claim_new_inode_cannot_be_used_by_second_reader(detached, monkeypatch):
    f = detached
    handle = prepare(f)
    write = operator._write_at
    second = []
    def compete(directory, name, raw, **kwargs):
        result = write(directory, name, raw, **kwargs)
        if name == f"{handle}.json" and json.loads(raw)["status"] == "claimed":
            with pytest.raises(plan.PlanRefused, match="plan_used"):
                execute(f, handle, binding=binding("102"))
            second.append(True)
        return result
    monkeypatch.setattr(operator, "_write_at", compete)
    assert execute(f, handle)["status"] == "plan-completed"
    assert second == [True]


def test_claim_is_durable_before_helper_and_failure_consumes_plan(detached, monkeypatch):
    f = detached
    handle = prepare(f)
    events = []
    sync = operator.os.fsync
    cancel = operator.cancel
    def record_sync(fd):
        events.append(os.readlink(f"/proc/self/fd/{fd}"))
        return sync(fd)
    def crash(*args, **kwargs):
        assert kwargs["apply"] is True and kwargs["allow_journal_resume"] is False
        assert json.loads(path(f, handle).read_bytes())["status"] == "claimed"
        assert str(path(f, handle)) in events
        assert str(path(f, handle).parent) in events
        assert str(f.soren / "tmp/state") in events
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(operator.os, "fsync", record_sync)
    monkeypatch.setattr(operator, "cancel", crash)
    before_queues = {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()}
    with pytest.raises(RuntimeError): execute(f, handle)
    assert {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()} == before_queues
    monkeypatch.setattr(operator, "cancel", cancel)
    with pytest.raises(plan.PlanRefused, match="plan_used"):
        execute(f, handle, binding=binding("102"))


def test_claim_durability_failure_never_invokes_queue_helper(detached, monkeypatch):
    f = detached
    handle = prepare(f)
    durable = operator._durable_journal
    invoked = []
    def fail(parent, directory, name, raw):
        if name == f"{handle}.json" and json.loads(raw)["status"] == "claimed":
            raise OSError("synthetic disk failure")
        return durable(parent, directory, name, raw)
    monkeypatch.setattr(operator, "_durable_journal", fail)
    monkeypatch.setattr(operator, "cancel", lambda *args, **kwargs: invoked.append(True))
    with pytest.raises(OSError): execute(f, handle)
    assert invoked == []
    assert json.loads(path(f, handle).read_bytes())["status"] == "claimed"


@pytest.mark.parametrize("target,mode", [("dir", 0o755), ("file", 0o644)])
def test_unsafe_private_plan_storage_is_refused_without_chmod(detached, target, mode):
    f = detached
    handle = prepare(f)
    unsafe = path(f, handle).parent if target == "dir" else path(f, handle)
    unsafe.chmod(mode)
    before = snapshot(f)
    with pytest.raises(operator.CancelRefused, match="journal_unverified"):
        execute(f, handle)
    assert snapshot(f) == before
    assert unsafe.stat().st_mode & 0o777 == mode


def test_second_prepared_plan_for_same_records_is_stale_after_first_completion(detached):
    f = detached
    first = prepare(f)
    second = prepare(f, binding=binding("102"))
    execute(f, first)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_context_changed"):
        execute(f, second, binding=binding("103"))
    assert snapshot(f) == before


def test_fixed_execute_cannot_resume_an_existing_queue_journal(detached, monkeypatch):
    from test_hanjuku_queue_admin_cancel import partial
    f = detached
    handle = prepare(f)
    partial(f, monkeypatch)
    before = snapshot(f)
    with pytest.raises(plan.PlanRefused, match="plan_context_changed"):
        execute(f, handle)
    assert snapshot(f) == before


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Linux existing-fd flock contract")
def test_pending_old_fd_reader_cannot_execute_after_parent_consumes_new_inode(detached):
    import multiprocessing
    f = detached
    handle = prepare(f)
    target = path(f, handle)
    context = multiprocessing.get_context("fork")
    ready_parent, ready_child = context.Pipe(duplex=False)
    go_child, go_parent = context.Pipe(duplex=False)
    result_parent, result_child = context.Pipe(duplex=False)

    def pending_reader():
        real_flock = plan.fcntl.flock
        paused = False
        def before_lock(fd, flags):
            nonlocal paused
            if not paused and os.readlink(f"/proc/self/fd/{fd}") == str(target):
                paused = True
                ready_child.send("opened-old-inode")
                if not go_child.poll(5):
                    raise RuntimeError("test release timeout")
                go_child.recv()
            return real_flock(fd, flags)
        plan.fcntl.flock = before_lock
        try:
            execute(f, handle, binding=binding("102"))
        except plan.PlanRefused as exc:
            result_child.send(str(exc))
        except Exception:
            result_child.send("unexpected-error")
        else:
            result_child.send("unexpected-success")

    process = context.Process(target=pending_reader)
    process.start()
    try:
        assert ready_parent.poll(5)
        assert ready_parent.recv() == "opened-old-inode"
        assert execute(f, handle)["status"] == "plan-completed"
        go_parent.send("continue")
        assert result_parent.poll(5)
        assert result_parent.recv() == "plan_unverified"
        process.join(5)
        assert process.exitcode == 0
        assert json.loads(target.read_bytes())["execution"] == binding("101")
    finally:
        if process.is_alive():
            process.terminate()  # Only this synthetic temp-fixture child.
            process.join(5)
        for connection in (ready_parent, ready_child, go_child, go_parent, result_parent, result_child):
            connection.close()


def test_fixed_shell_wrapper_and_real_cli_complete_only_temp_private_plan(detached):
    import shutil
    import subprocess
    f = detached
    repo = Path(__file__).resolve().parents[1]
    root = f.path.parent.parent / "cli-root"
    shutil.copytree(repo / "src/docich", root / "src/docich", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "config").mkdir()
    (root / "config/docich.soren-live.toml").write_text(
        f'[paths]\nstate_dir={json.dumps(str(f.path.parent))}\n[webui]\nsoren_root={json.dumps(str(f.soren))}\n')
    binaries = root / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    git = binaries / "git"
    git.write_text('#!/bin/bash\ncase "$*" in *rev-parse*) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;; *status*) : ;; esac\n')
    git.chmod(0o755)
    env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"], "DOCICH_PROD_ROOT": str(root),
        "QUEUE_ADMIN_SHA": "a" * 40, "QUEUE_ADMIN_MODE": "check", "QUEUE_ADMIN_HANDLE": "",
        "QUEUE_ADMIN_ACK": "not-acknowledged", "QUEUE_ADMIN_REPOSITORY": plan.REPOSITORY,
        "QUEUE_ADMIN_REPOSITORY_ID": plan.REPOSITORY_ID, "QUEUE_ADMIN_ACTOR_ID": plan.ACTOR_ID,
        "QUEUE_ADMIN_WORKFLOW_REF": plan.WORKFLOW, "QUEUE_ADMIN_REF": plan.REF,
        "QUEUE_ADMIN_RUN_ID": "100", "QUEUE_ADMIN_RUN_ATTEMPT": "1"}
    script = repo / "ops/vm_actions/admin_cancel_hanjuku_queues.sh"
    before = snapshot(f)
    result = subprocess.run(["bash", str(script)], env=env, cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout) == {"status": "plan-prepared", "plan_handle": "100-1"}
    record = json.loads(path(f, "100-1").read_bytes())
    assert record["fingerprint"] not in result.stdout + result.stderr
    result = subprocess.run(["bash", str(script)], cwd=root, env={**env, "QUEUE_ADMIN_MODE": "execute",
        "QUEUE_ADMIN_RUN_ID": "101", "QUEUE_ADMIN_HANDLE": "100-1", "QUEUE_ADMIN_ACK": "acknowledged"},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout) == {"status": "plan-completed", "plan_handle": "100-1"}
    assert record["fingerprint"] not in result.stdout + result.stderr
    assert f.path.read_bytes() == before[f.path]
    for filename in operator.FILES.values():
        assert json.loads((f.queue / filename).read_bytes())["status"] == "cancelled"


@pytest.mark.parametrize("moments,claimed", [([31, 31, 931], False), ([31, 31, 31, 931], True)])
def test_ttl_is_resampled_before_claim_and_before_helper_not_cached_at_process_start(detached, moments, claimed):
    f = detached
    handle = prepare(f)
    clock = iter(moments)
    before = {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()}
    with pytest.raises(plan.PlanRefused, match="plan_stale"):
        plan.execute(f.g, binding("101"), handle, acknowledge_unknown_resources=True, now=lambda: next(clock))
    assert {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()} == before
    assert json.loads(path(f, handle).read_bytes())["status"] == ("claimed" if claimed else "prepared")


def test_completion_clock_regression_keeps_consumed_plan_and_original_audit(detached):
    f = detached
    handle = prepare(f)
    clock = iter([31, 31, 31, 31, 30])
    with pytest.raises(plan.PlanRefused, match="plan_stale"):
        plan.execute(f.g, binding("101"), handle, acknowledge_unknown_resources=True, now=lambda: next(clock))
    assert json.loads(path(f, handle).read_bytes())["status"] == "claimed"
    for filename in operator.FILES.values():
        assert json.loads((f.queue / filename).read_bytes())["status"] == "cancelled"
    with pytest.raises(plan.PlanRefused, match="plan_used"):
        execute(f, handle, binding=binding("102"))


@pytest.mark.parametrize("moments,claimed", [([32, 31], False), ([31, 32, 31], False),
                                            ([31, 32, 33, 31, 31], True)])
def test_every_resampled_clock_must_not_regress_against_its_previous_sample(detached, moments, claimed):
    f = detached
    handle = prepare(f)
    clock = iter(moments)
    before = {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()}
    with pytest.raises(plan.PlanRefused, match="plan_stale"):
        plan.execute(f.g, binding("101"), handle, acknowledge_unknown_resources=True, now=lambda: next(clock))
    assert {kind: (f.queue / filename).read_bytes() for kind, filename in operator.FILES.items()} == before
    saved = json.loads(path(f, handle).read_bytes())
    assert saved["status"] == ("claimed" if claimed else "prepared")
    assert saved["completed_at"] is None
