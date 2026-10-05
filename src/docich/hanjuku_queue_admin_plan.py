"""One-use public run handle for a private, exact-context queue admin plan.

Authorization remains the canonical owner-only workflow and existing VM gateway.
A handle is not a credential. No fingerprint or source state leaves the VM.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time

from . import hanjuku_queue_admin_cancel as operator

REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
ACTOR_ID = "9018513"
WORKFLOW = f"{REPOSITORY}/.github/workflows/corner-rotation-operator.yml@refs/heads/main"
REF = "refs/heads/main"
SHA = re.compile(r"[0-9a-f]{40}\Z")
RUN = re.compile(r"[1-9][0-9]{0,19}\Z")
ATTEMPT = re.compile(r"[1-9][0-9]{0,3}\Z")
HANDLE = re.compile(r"([1-9][0-9]{0,19})-([1-9][0-9]{0,3})\Z")
PLAN_DIR = "docich_program_queue_admin_plans"
PLAN_LIMIT = 16 * 1024
PLAN_TTL = 15 * 60
BASE_CONTEXT = frozenset({"reservation", "canonical", "registry", "queue_scheduled",
                         "queue_manual", "owner_scheduled", "owner_manual", "receipt"})
PLAN_FIELDS = frozenset({"schema_version", "operation", "binding", "fingerprint", "context",
                        "created_at", "expires_at", "status", "execution", "claimed_at", "completed_at"})
REFUSALS = frozenset({"plan_binding_invalid", "plan_handle_invalid", "plan_exists", "plan_unverified",
                     "plan_stale", "plan_used", "plan_context_changed", "unknown_resources_not_acknowledged",
                     "evidence_unverified"})


class PlanRefused(RuntimeError):
    pass


def _binding(value):
    if (not isinstance(value, dict) or set(value) != {"repository", "repository_id", "actor_id",
            "workflow_ref", "ref", "sha", "run_id", "run_attempt"}
            or value["repository"] != REPOSITORY or value["repository_id"] != REPOSITORY_ID
            or value["actor_id"] != ACTOR_ID or value["workflow_ref"] != WORKFLOW or value["ref"] != REF
            or not isinstance(value["sha"], str) or not SHA.fullmatch(value["sha"])
            or not isinstance(value["run_id"], str) or not RUN.fullmatch(value["run_id"])
            or not isinstance(value["run_attempt"], str) or not ATTEMPT.fullmatch(value["run_attempt"])):
        raise PlanRefused("plan_binding_invalid")
    return dict(value)


def _handle(binding):
    return f"{binding['run_id']}-{binding['run_attempt']}"


def _fresh_time(now, previous):
    current = operator._stamp(now())
    if current < previous:
        raise PlanRefused("plan_stale")
    return current


def _program(g):
    from .trading.soren_output import resolve_soren_root
    return resolve_soren_root(g).absolute() / "tmp/state"


def _private_dir(program, *, create=False):
    parent = operator._open_dir(program)
    try:
        if create:
            try:
                os.mkdir(PLAN_DIR, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            os.fsync(parent)
        directory = os.open(PLAN_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            operator._private_storage(os.fstat(directory), 0o700, directory=True)
        except BaseException:
            os.close(directory)
            raise
        return parent, directory
    except BaseException:
        os.close(parent)
        raise


def _read_plan(path):
    raw, value, identity = operator._read_journal(path, limit=PLAN_LIMIT)
    if not 0 < len(raw) <= PLAN_LIMIT:
        raise PlanRefused("plan_unverified")
    return raw, value, identity


def _validate(plan, handle, binding, now):
    try:
        check = _binding(plan["binding"])
        if (set(plan) != PLAN_FIELDS or type(plan["schema_version"]) is not int
                or plan["schema_version"] != 1 or plan["operation"] != "admin-cancel-retro-error-queues"
                or _handle(check) != handle
                or any(check[key] != binding[key] for key in
                       ("repository", "repository_id", "actor_id", "workflow_ref", "ref", "sha"))
                or not isinstance(plan["fingerprint"], str) or not operator.DIGEST.fullmatch(plan["fingerprint"])
                or not isinstance(plan["context"], dict)
                or set(plan["context"]) not in (BASE_CONTEXT, BASE_CONTEXT | {"registered_owner"})
                or any(value is not None and (not isinstance(value, str) or not operator.DIGEST.fullmatch(value))
                       for value in plan["context"].values())
                or plan["context"]["receipt"] is not None
                or any(plan["context"][key] is None for key in
                       ("reservation", "canonical", "queue_scheduled", "queue_manual"))
                or operator._fingerprint(plan["context"]) != plan["fingerprint"]):
            raise ValueError()
        created, expires = operator._stamp(plan["created_at"]), operator._stamp(plan["expires_at"])
        if expires - created != PLAN_TTL:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, operator.CancelRefused, RecursionError):
        raise PlanRefused("plan_unverified") from None
    if now < created or now > expires or binding["run_id"] == check["run_id"]:
        raise PlanRefused("plan_stale")
    if (plan["status"] != "prepared" or plan["execution"] is not None
            or plan["claimed_at"] is not None or plan["completed_at"] is not None):
        raise PlanRefused("plan_used")
    return plan


def prepare(g, binding, *, now=time.time):
    binding = _binding(binding)
    at = operator._stamp(now())
    # The first check establishes all six exclusion guards. The second bounded
    # capture stores the same complete digest map; an intervening change refuses.
    check = operator.cancel(g, now=lambda: at)
    paths, optional, sources = operator._context(Path(g.state_dir).absolute(), _program(g), at)
    context = operator._fingerprints(sources)
    if operator._fingerprint(context) != check["approval_fingerprint"]:
        raise PlanRefused("plan_context_changed")
    operator._recheck(paths, optional, sources, [])
    plan = {"schema_version": 1, "operation": "admin-cancel-retro-error-queues", "binding": binding,
            "fingerprint": check["approval_fingerprint"], "context": context,
            "created_at": at, "expires_at": at + PLAN_TTL, "status": "prepared",
            "execution": None, "claimed_at": None, "completed_at": None}
    raw = operator._serialized(plan) + b"\n"
    if len(raw) > PLAN_LIMIT:
        raise PlanRefused("plan_unverified")
    handle = _handle(binding)
    with ExitStack() as opened:
        parent, directory = _private_dir(_program(g), create=True)
        opened.callback(os.close, parent)
        opened.callback(os.close, directory)
        try:
            installed = operator._write_at(directory, f"{handle}.json", raw, exclusive=True)
        except FileExistsError:
            raise PlanRefused("plan_exists") from None
        operator._durable_journal(parent, directory, f"{handle}.json", raw)
        current, _, current_identity = _read_plan(_program(g) / PLAN_DIR / f"{handle}.json")
        if current != raw or current_identity[:2] != installed:
            raise PlanRefused("plan_unverified")
    # The handle is public GitHub metadata; all state and hashes remain private.
    return {"status": "plan-prepared", "plan_handle": handle}


def execute(g, binding, handle, *, acknowledge_unknown_resources=False, now=time.time):
    binding = _binding(binding)
    if not isinstance(handle, str) or not HANDLE.fullmatch(handle):
        raise PlanRefused("plan_handle_invalid")
    if acknowledge_unknown_resources is not True:
        raise PlanRefused("unknown_resources_not_acknowledged")
    at = operator._stamp(now())
    program = _program(g)
    path = program / PLAN_DIR / f"{handle}.json"
    with ExitStack() as opened:
        parent, directory = _private_dir(program)
        opened.callback(os.close, parent)
        opened.callback(os.close, directory)
        # Lock this existing record; no new lock file or credential is created.
        fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        opened.callback(os.close, fd)
        operator._private_storage(os.fstat(fd), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PlanRefused("plan_used") from None
        before = os.fstat(fd)
        raw, plan, identity = _read_plan(path)
        if (before.st_dev, before.st_ino) != identity[:2]:
            raise PlanRefused("plan_unverified")
        at = _fresh_time(now, at)
        _validate(plan, handle, binding, at)
        paths, optional, sources = operator._context(Path(g.state_dir).absolute(), program, at)
        if operator._fingerprints(sources) != plan["context"]:
            raise PlanRefused("plan_context_changed")
        for kind in operator.FILES:
            operator._queue_original(sources[f"queue_{kind}"][1], at)
        operator._recheck(paths, optional, sources, [])
        current, _, current_identity = _read_plan(path)
        if current != raw or current_identity != identity:
            raise PlanRefused("plan_unverified")
        # Consume before the consequential action. Even an interrupted/failed
        # execution cannot reuse this handle. Partial queue journal recovery is
        # a separate explicit decision, never an automatic workflow retry.
        claim_at = _fresh_time(now, at)
        _validate(plan, handle, binding, claim_at)
        claimed = {**plan, "status": "claimed", "execution": binding, "claimed_at": claim_at}
        claimed_raw = operator._serialized(claimed) + b"\n"
        installed = operator._write_at(directory, path.name, claimed_raw)
        operator._durable_journal(parent, directory, path.name, claimed_raw)
        claimed_current, _, claimed_identity = _read_plan(path)
        if claimed_current != claimed_raw or claimed_identity[:2] != installed:
            raise PlanRefused("plan_unverified")
        action_at = _fresh_time(now, claim_at)
        _validate(plan, handle, binding, action_at)
        operator.cancel(g, expected=plan["fingerprint"], apply=True,
                        acknowledge_unknown_resources=True, allow_journal_resume=False, now=lambda: action_at)
        claimed_current, _, current_identity = _read_plan(path)
        if claimed_current != claimed_raw or current_identity != claimed_identity:
            raise PlanRefused("plan_unverified")
        completed_at = _fresh_time(now, action_at)
        completed = {**claimed, "status": "completed", "completed_at": completed_at}
        completed_raw = operator._serialized(completed) + b"\n"
        installed = operator._write_at(directory, path.name, completed_raw)
        operator._durable_journal(parent, directory, path.name, completed_raw)
        completed_current, _, completed_identity = _read_plan(path)
        if completed_current != completed_raw or completed_identity[:2] != installed:
            raise PlanRefused("plan_unverified")
    return {"status": "plan-completed", "plan_handle": handle}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "execute"))
    for name in ("repository", "repository-id", "actor-id", "workflow-ref", "ref", "sha", "run-id", "run-attempt"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--plan-handle", default="")
    parser.add_argument("--acknowledge-unknown-resources", action="store_true")
    args = parser.parse_args(argv)
    try:
        from .config import load_global
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / "config/docich.soren-live.toml")
        binding = {key: getattr(args, key) for key in
                   ("repository", "repository_id", "actor_id", "workflow_ref", "ref", "sha", "run_id", "run_attempt")}
        if args.operation == "check":
            if args.plan_handle or args.acknowledge_unknown_resources:
                raise PlanRefused("plan_binding_invalid")
            result = prepare(g, binding)
        else:
            result = execute(g, binding, args.plan_handle,
                             acknowledge_unknown_resources=args.acknowledge_unknown_resources)
    except PlanRefused as exc:
        reason = str(exc)
        result = {"status": "refused", "reason": reason if reason in REFUSALS else "evidence_unverified"}
    except operator.CancelRefused:
        result = {"status": "refused", "reason": "evidence_unverified"}
    except Exception:
        result = {"status": "refused", "reason": "evidence_unverified"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] in {"plan-prepared", "plan-completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
