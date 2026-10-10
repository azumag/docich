"""One separately approved automatic NetHack administrative closure.

Historical R1/source authority is never invented. A read-only check supplies
an expiring context fingerprint; release rechecks actual resources and all
writers, then atomically audits and consumes exactly the same reservation.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .game_switch import atomic_write_json, validate_request_id, validate_state
from .naming import runtime_id_generation, validate_runtime_id
from .nethack_return import (_require, _digest, _instant, _receipt, _boundary,
                             _restored_runtime, read_record, ReturnUnproven)
from .retro_corner import RetroCornerManager

AUDIT_KEY = "nethack_admin_releases"
WINDOW_SECONDS = 600
MAX_PROCESSES = 8192
TAG_KEYS = ("DOCICH_TMUX_RUNTIME_ID", "DOCICH_TMUX_GENERATION", "DOCICH_TMUX_ROLE")
from .nethack_admin_result import REFUSALS
OWNER_FILES = ('retro_corner.json', 'retro_corner_manual.json', 'paper_corner.json',
               'paper_corner_manual.json', 'soren91_corner.json', 'soren91_corner_manual.json',
               'weather_corner.json', 'nethack_corner.json', 'nethack_corner_manual.json')


class Refused(ValueError):
    pass


def refuse(reason):
    raise Refused(reason)


@contextmanager
def _writer_lock(path):
    from .hanjuku_manual_cancel import _existing_lock, CancelRefused
    try:
        with _existing_lock(path):
            yield
    except CancelRefused as exc:
        refuse('busy' if str(exc) == 'busy' else 'resources_unproven')


def _writer_paths(root, soren):
    return (root / 'locks/corner-rotation.lock', root / 'corner-manual-queue.lock',
            root / 'locks/nethack-corner-tick.lock', root / 'locks/nethack-corner.lock',
            root / 'locks/nethack-corner-manual-tick.lock', root / 'locks/nethack-corner-manual.lock',
            soren / 'tmp/state/docich_program.lock', root / 'locks/game-switch.lock')


def _lock_identities(root, soren):
    result = []
    for path in _writer_paths(root, soren):
        if any(p.is_symlink() for p in (path, *path.parents)):
            refuse('resources_unproven')
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != root.stat().st_uid
                        or info.st_nlink != 1 or info.st_mode & 0o022):
                    refuse('resources_unproven')
                result.append([info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)])
            finally:
                os.close(fd)
        except OSError:
            refuse('resources_unproven')
    return result


def _chain(root, owner, ledger, *, player, now):
    from .corner_rotation import timestamp

    _require(type(owner.get("schema_version")) is int and owner["schema_version"] == 1
             and owner.get("previous_game") == "sorengame" and owner.get("game") == "nethack"
             and owner.get("status") == "failed"
             and owner.get("finish_reason") == "terminal"
             and all(key not in owner for key in
                     ("restore_recovery", "restore_cleanup", "restore_cleanup_attempt")))
    _require(_digest(read_record(root, ("nethack_corner.json",))) == _digest(owner))
    _require(_digest(read_record(root, ("corner_rotation.json",))) == _digest(ledger)
             and type(ledger.get("schema_version")) is int and ledger["schema_version"] == 1)
    reservation = owner.get("rotation_request_id")
    restore_id = owner.get("switch_request_id")
    validate_request_id(reservation)
    validate_request_id(restore_id)
    original_runtime = validate_runtime_id(owner.get("rotation_runtime_id"))
    pending = ledger.get("pending")
    _require(ledger.get("status") == "recovery_required"
             and ledger.get("manual_pending") is None and ledger.get("queued_manual") is None
             and isinstance(pending, dict)
             and pending.get("corner") == "nethack" and pending.get("phase") == "dispatched"
             and pending.get("request_id") == reservation and "source" not in pending)
    manual = read_record(root, ("nethack_corner_manual.json",), optional=True)
    if manual is not None:
        _require(type(manual.get("schema_version")) is int and manual["schema_version"] == 1
                 and manual.get("status") in {"idle", "completed", "interrupted", "expired"})
    canonical = read_record(root, ("game_switch.json",))
    _require(type(canonical.get("schema_version")) is int and canonical["schema_version"] == 2)
    validate_state(canonical)
    _require(RetroCornerManager._restore_canonical_clean(canonical)
             and canonical.get("deadline_at") is None
             and _instant(canonical.get("updated_at")) <= now)
    last = canonical.get("last_result")
    _require(isinstance(last, dict))
    return_id = validate_request_id(last.get("request_id"))
    _require(len({reservation, restore_id, return_id}) == 3)
    original = _receipt(root, restore_id, "rolled_back")
    landed = _receipt(root, return_id, "succeeded")
    result = original["result"]
    # This exception is only for old replace-mode rollbacks lacking durable
    # source identities, never a modern proof or an in-place lease renewal.
    _require("source_runtime" not in result and "restored_runtime" not in result
             and "source_runtime" not in landed["result"])
    restored_generation = result.get("restored_generation")
    _require(type(restored_generation) is int
             and runtime_id_generation(original_runtime) < original["generation"]
             < restored_generation < landed["generation"])
    # Historical R0 cleanup may be pending. Independent current census/fence
    # must prove absence now; never rewrite the old receipt as clean/succeeded.
    _require(landed["result"].get("cleanup_pending") is None
             or landed["result"].get("cleanup_pending") is False)
    if owner.get("last_error_code") is not None:
        _require(owner["last_error_code"] == result.get("error_code"))
    started = _instant(owner.get("started_at"))
    completed = _instant(owner.get("completed_at"))
    _require(timestamp(ledger.get("last_seen_at")) <= now.timestamp()
             and timestamp(pending.get("selected_at")) <= started.timestamp())
    # Automatic dispatch omits pending.source and writes a reservation row.
    # A queued manual dispatch uses source=manual/manual-reservation; removing
    # just one of those markers cannot turn it into an automatic owner.
    history = ledger.get("history")
    _require(isinstance(history, list))
    selected_at = timestamp(pending["selected_at"])
    automatic_dispatch = False
    for row in history:
        _require(isinstance(row, dict))
        recorded_at = timestamp(row.get("at"))
        _require(recorded_at <= now.timestamp())
        if row.get("corner") == "nethack" and recorded_at >= selected_at:
            _require(row.get("source") != "manual-reservation")
            if row.get("source") == "reservation" and recorded_at <= started.timestamp():
                automatic_dispatch = True
    _require(automatic_dispatch)
    _require(started <= completed <= _instant(original["created_at"])
             <= _instant(original["updated_at"]) <= _instant(landed["created_at"])
             <= _instant(landed["updated_at"]) <= now)
    first = _boundary(root, original_runtime, restore_id, player, original)
    restored_runtime = _restored_runtime(root, restored_generation)
    second = _boundary(root, restored_runtime, return_id, player, landed)
    active = RetroCornerManager._restore_source_identity(canonical.get("active"))
    _require(active is not None and canonical["active"].get("adapter") == "soren"
             and RetroCornerManager._succeeded_start_receipt_matches_active(
                 canonical, landed, request_id=return_id, target="sorengame")
             and _digest(last) == _digest(landed["result"]))
    active_started = _instant(canonical["active"].get("started_at"))
    _require(_instant(landed["created_at"]) <= _instant(second["recorded_at"])
             <= active_started <= _instant(landed["updated_at"])
             and active_started <= _instant(canonical["updated_at"]) <= now)
    # Capture the complete owner input, not just the fields used above. A new
    # run/history mutation cannot inherit an old reconciliation authorization.
    owner_input = {key: value for key, value in owner.items()
                   if key not in {"switch_status"}}
    return {
        "schema_version": 1, "rotation_request_id": reservation,
        "restore_request_id": restore_id, "return_request_id": return_id,
        "original_source": {"game": "nethack", "runtime_id": original_runtime,
                            "generation": runtime_id_generation(original_runtime)},
        "rollback_source": {"game": "nethack", "runtime_id": restored_runtime,
                            "generation": restored_generation},
        "active_runtime": active, "canonical_revision": canonical["revision"],
        "return_completed_at": landed["updated_at"],
        "canonical_sha256": _digest(canonical),
        "evidence_sha256": _digest([owner_input, ledger, original, landed, first, second, manual]),
    }


def _resources(root, soren, owner, proof, *, probe=None, tmux=None, container_probe=None):
    from .hanjuku_manual_cancel import _ProbeTmux
    from .nethack_admin_resources import processes, containers
    tmux = tmux or _ProbeTmux()
    try:
        inventory = (container_probe or containers)()
    except Exception:
        refuse('process_coverage_unproven')
    if not isinstance(inventory, list) or len(inventory) > 256:
        refuse('process_coverage_unproven')
    if inventory:
        # There is no positive shared-container contract. Do not treat an
        # unrelated name as proof that a detached NetHack job is absent.
        refuse('resources_present' if any(isinstance(r, list) and len(r) == 2
               and isinstance(r[1], str) and r[1].startswith('docich-nh-canary-')
               for r in inventory) else 'process_coverage_unproven')
    try:
        rows = (probe or processes)(root.stat().st_uid)
    except Exception:
        refuse('process_coverage_unproven')
    if not isinstance(rows, list) or len(rows) > MAX_PROCESSES:
        refuse('process_coverage_unproven')
    by_pid = {}
    for row in rows:
        if (not isinstance(row, dict) or type(row.get('pid')) is not int
                or type(row.get('start_ticks')) is not int or row['pid'] <= 0
                or row['start_ticks'] <= 0 or row['pid'] in by_pid):
            refuse('process_coverage_unproven')
        by_pid[row['pid']] = row
    if probe is None and (root.stat().st_uid != os.getuid() or os.getpid() not in by_pid):
        refuse('process_coverage_unproven')
    ancestors, pid = set(), os.getpid()
    while pid in by_pid and pid not in ancestors:
        ancestors.add(pid)
        pid = by_pid[pid].get('ppid')
    current = proof['active_runtime']
    server = tmux._server_pid()
    server = server if type(server) is int and server > 0 else None
    fingerprints = []
    for row in rows:
        if row.get('uid', root.stat().st_uid) != root.stat().st_uid:
            # An old container launcher can die while a foreign-UID runsc task
            # survives on another local daemon. No positive foreign-resource
            # contract exists; PID/birth alone cannot make it harmless.
            if row['pid'] not in ancestors:
                refuse('process_coverage_unproven')
            continue  # Only the actual control ancestry is exempt across UIDs.
        tags = row.get('tags')
        if not isinstance(tags, dict):
            refuse('process_coverage_unproven')
        # Capture ownership/executable changes even when PID and birth stay
        # identical. Raw arguments are kept in memory only, never in output.
        argv = row.get('argv')
        if (not isinstance(row.get('exe'), str) or not isinstance(row.get('cwd'), str)
                or not isinstance(argv, list) or any(not isinstance(v, bytes) for v in argv)):
            refuse('process_coverage_unproven')
        if row['pid'] not in ancestors:
            fingerprints.append(dict(pid=row['pid'], start_ticks=row['start_ticks'],
                ppid=row.get('ppid'), tags=tags, exe=row['exe'], cwd=row['cwd'],
                argv_sha256=hashlib.sha256(b'\0'.join(argv)).hexdigest(),
                boot_id=row.get('boot_id'), pid_namespace=row.get('pid_namespace')))
        # Identify the one normal server through tmux itself and current
        # PID/birth/executable/argv. It can inherit an old game's env tags.
        # Do not permit alternate sockets, other panes or all descendants.
        if (row['pid'] == server and row['exe'] in {'/usr/bin/tmux', '/usr/local/bin/tmux'}
                and argv and argv[0] == b'tmux: server' and all(not v for v in argv[1:])):
            continue
        if row['exe'] in {'/usr/bin/tmux', '/usr/local/bin/tmux'}:
            refuse('process_coverage_unproven')
        # The helper itself necessarily has its module name in argv. That one
        # token on our own PID is control code, not a game producer exemption.
        if any(b'nethack' in v.lower() and not (
                row['pid'] == os.getpid() and v == b'docich.nethack_admin_release')
                for v in argv):
            refuse('resources_present')
        if tags:
            if (set(tags) != set(TAG_KEYS) or tags[TAG_KEYS[0]] != current['runtime_id']
                    or tags[TAG_KEYS[1]] != str(current['generation'])
                    or tags[TAG_KEYS[2]] not in {'game', 'agent', 'adapter'}):
                refuse('resources_present')
        if row['pid'] in ancestors and (row['pid'] == os.getpid() or not tags):
            continue
        # Matching current tags are necessary but not a positive role/ownership
        # edge: a shared pane can inherit them and leave a generic detached
        # child. Until that independent contract exists these remain unknown.
        # A basename or plain PID file cannot distinguish a shared daemon from
        # a detached game child or a reused PID. There is currently no durable
        # positive shared-resource contract, so these processes remain unknown.
        refuse('process_coverage_unproven')
    records = []
    for source in (proof['original_source'], proof['rollback_source']):
        rid, generation = source['runtime_id'], source['generation']
        for target in (f'docich:game-g{generation}', f'docich:agent-g{generation}'):
            if tmux.window_target_exists(target, strict=True):
                refuse('resources_present')
        if tmux.session_target_exists(f'docich-game-g{generation}', strict=True):
            refuse('resources_present')
        # These files can legitimately be absent/clear PID fields after stop.
        # Bind any retained record to approval; it supplies no current resource
        # authority. Closed census and known tmux absence do that independently.
        presentation = read_record(root, ('runtimes', rid, 'presentation.json'), optional=True)
        tiles = read_record(root, ('runtimes', rid, 'nethack_tiles.json'), optional=True)
        records.extend((presentation, tiles))
    # All unknown jobs/children in this UID were refused above, even when the
    # historical owner omitted them. No positive allowlist is input by callers.
    return dict(records=records, processes=sorted(fingerprints, key=lambda r: r['pid']))


def _safe_root(root):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        refuse('evidence_unproven')
    return root


def _lanes(root, soren):
    observations = []
    for name in OWNER_FILES:
        if name == 'nethack_corner.json':
            continue  # The exact failed automatic owner is verified by _chain.
        value = read_record(root, (name,), optional=True)
        if value is not None and value.get('status') not in {'idle', 'completed', 'interrupted', 'expired'}:
            refuse('resources_unproven')
        observations.append(value)
    program = soren / 'tmp/state'
    inbox = read_record(root, ('corner_manual_queue.json',), optional=True)
    if inbox is not None:
        refuse('resources_unproven')
    observations.append(inbox)
    improve = read_record(root, ('corner_improve_nethack.json',), optional=True)
    if improve is not None:
        refuse('resources_unproven')
    observations.append(improve)
    try:
        resolver = read_record(root, ('resolver', 'active', 'nethack.json'), optional=True)
    except FileNotFoundError:
        resolver = None
    if resolver is not None:
        refuse('resources_unproven')
    observations.append(resolver)
    for name in ('docich_program_active.json',):
        value = read_record(program, (name,), optional=True)
        if value is not None:
            refuse('resources_unproven')
        observations.append(value)
    directory = program / 'docich_program_queue'
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        refuse('resources_unproven')
    if directory.exists():
        names = sorted(p.name for p in directory.iterdir())
        if len(names) > len(OWNER_FILES) or any(name not in OWNER_FILES for name in names):
            refuse('resources_unproven')
        for name in names:
            value = read_record(directory, (name,), optional=True)
            if value is None or value.get('status') not in {'done', 'expired', 'cancelled'}:
                refuse('resources_unproven')
            observations.append([name, value])
        if names != sorted(p.name for p in directory.iterdir()):
            refuse('context_changed')
    receipts = root / 'game-switch/requests'
    names = sorted(p.name for p in receipts.iterdir())
    if len(names) > 1024:
        refuse('resources_unproven')
    total = 0
    for name in names:
        if not name.endswith('.json'):
            refuse('evidence_unproven')
        validate_request_id(name[:-5])
        path = receipts / name
        total += path.stat().st_size
        if total > 4 * 1024 * 1024:
            refuse('resources_unproven')
        value = read_record(root, ('game-switch', 'requests', name))
        if value.get('status') not in {'succeeded', 'failed', 'rolled_back'}:
            refuse('resources_unproven')
        observations.append(_digest(value))
    return observations


def _context(root, soren, *, player, now, probe=None, tmux=None, container_probe=None):
    owner = read_record(root, ('nethack_corner.json',))
    ledger = read_record(root, ('corner_rotation.json',))
    if AUDIT_KEY in ledger:
        audit = ledger[AUDIT_KEY]
        if not isinstance(audit, list) or len(audit) > 100 or any(not isinstance(r, dict) for r in audit):
            refuse('audit_unproven')
    proof = _chain(root, owner, ledger, player=player, now=now)
    resources = _resources(root, soren, owner, proof, probe=probe, tmux=tmux,
                           container_probe=container_probe)
    return dict(proof=proof, owner=owner, ledger=ledger, resources=resources,
                lanes=_lanes(root, soren), writers=_lock_identities(root, soren))


def _approval(context, sha, expires):
    return _digest(dict(schema_version=1, code_sha=sha, expires_at=expires,
                        context_sha256=_digest(context)))


def check(root, soren, *, player, sha, now=None, probe=None, tmux=None, container_probe=None):
    """No manager, state creation or lock acquisition. Recheck all inputs."""
    result = dict(status='refused', reason='evidence_unproven', schema_version=1,
                  history_authority=False, all_resources_released=None)
    try:
        root, soren = _safe_root(root), _safe_root(soren)
        clock = time.time() if now is None else now
        if type(clock) not in (int, float) or not 0 <= clock < 10**12:
            refuse('evidence_unproven')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            refuse('code_unverified')
        instant = dt.datetime.fromtimestamp(clock, dt.timezone.utc)
        first = _context(root, soren, player=player, now=instant, probe=probe, tmux=tmux,
                         container_probe=container_probe)
        if _digest(first) != _digest(_context(root, soren, player=player, now=instant,
                                             probe=probe, tmux=tmux, container_probe=container_probe)):
            refuse('context_changed')
        expires = (int(clock) // WINDOW_SECONDS + 1) * WINDOW_SECONDS
        return dict(status='admin-eligible', schema_version=1, history_authority=False,
                    all_resources_released=True, fingerprint=_approval(first, sha, expires),
                    expires_at=expires, code_sha=sha)
    except Refused as exc:
        result['reason'] = str(exc) if str(exc) in REFUSALS else 'evidence_unproven'
    except Exception:
        pass
    return result


def _audit_matches(row, owner):
    required = {'schema_version', 'fingerprint', 'expires_at', 'code_sha', 'context_sha256',
                'owner_sha256', 'reservation', 'released_at', 'history_authority',
                'all_resources_released', 'previous_error_kind', 'previous_reason'}
    return (isinstance(row, dict) and set(row) == required
            and type(row.get('schema_version')) is int and row['schema_version'] == 1
            and row.get('owner_sha256') == _digest(owner)
            and row.get('history_authority') is False and row.get('all_resources_released') is True
            and type(row.get('expires_at')) is int and row['expires_at'] % WINDOW_SECONDS == 0
            and type(row.get('released_at')) in (int, float)
            and row['expires_at'] - WINDOW_SECONDS <= row['released_at'] < row['expires_at']
            and isinstance(row.get('context_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', row['context_sha256']) is not None
            and isinstance(row.get('code_sha'), str) and re.fullmatch('[0-9a-f]{40}', row['code_sha']) is not None
            and row.get('fingerprint') == _digest(dict(schema_version=1, code_sha=row['code_sha'],
                expires_at=row['expires_at'], context_sha256=row['context_sha256']))
            and isinstance(row.get('reservation'), dict)
            and row['reservation'].get('request_id') == owner.get('rotation_request_id')
            and row['reservation'].get('corner') == 'nethack'
            and row['reservation'].get('phase') == 'dispatched')


def administrative_observation(root, owner):
    """Project only a committed administrative closure; preserve the raw owner.

    This independent branch never changes normal/legacy recovery authority.
    A changed owner/request cannot inherit an old administrative decision.
    """
    try:
        if (owner.get('status') != 'failed' or owner.get('game') != 'nethack'
                or owner.get('previous_game') != 'sorengame'):
            return owner
        ledger = read_record(_safe_root(root), ('corner_rotation.json',))
        audit = ledger.get(AUDIT_KEY, [])
        matching = [row for row in audit if _audit_matches(row, owner)]
        if len(matching) != 1:
            return owner
        row = matching[0]
        pending = ledger.get('pending')
        if isinstance(pending, dict) and pending.get('request_id') == owner.get('rotation_request_id'):
            return owner
        # The standard timer compacts history to the latest use per corner.
        # A later NetHack manual execution may replace the initial completion
        # row without replacing the automatic raw owner. Keep its audited
        # closure only with a retained, valid NetHack usage at/after the commit.
        if not any(isinstance(h, dict) and h.get('corner') == 'nethack'
                   and h.get('source') in {'completion', 'manual-completion', 'execution'}
                   and type(h.get('at')) in (int, float)
                   and row['released_at'] <= h['at'] <= ledger.get('last_seen_at', -1)
                   for h in ledger.get('history', [])):
            return owner
        return {**owner, 'status': 'interrupted', 'administrative_closure': True}
    except Exception:
        return owner


def release(root, soren, *, player, sha, expected, expires, now=None, probe=None, tmux=None, container_probe=None):
    """One atomic ledger transaction; all other files remain byte-identical."""
    if (not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)
            or type(expires) is not int or expires % WINDOW_SECONDS != 0):
        refuse('approval_required')
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        refuse('code_unverified')
    root, soren = _safe_root(root), _safe_root(soren)
    clock = time.time() if now is None else now
    with ExitStack() as held:
        for path in _writer_paths(root, soren):
            held.enter_context(_writer_lock(path))
        from .nethack_resource_fence import resource_fence, FenceUnproven
        try:
            held.enter_context(resource_fence(root, exclusive=True))
        except FenceUnproven as exc:
            refuse('busy' if str(exc).endswith('busy') else 'resources_unproven')
        owner = read_record(root, ('nethack_corner.json',))
        ledger = read_record(root, ('corner_rotation.json',))
        audit = ledger.get(AUDIT_KEY, [])
        for row in audit:
            if _audit_matches(row, owner) and row.get('fingerprint') == expected:
                if administrative_observation(root, owner) != owner:
                    return {'status': 'already-admin-released', 'history_authority': False}
                refuse('audit_unproven')
        if not expires - WINDOW_SECONDS <= clock < expires:
            refuse('approval_expired')
        instant = dt.datetime.fromtimestamp(clock, dt.timezone.utc)
        context = _context(root, soren, player=player, now=instant, probe=probe, tmux=tmux,
                           container_probe=container_probe)
        if _approval(context, sha, expires) != expected:
            refuse('fingerprint_changed')
        if _digest(context) != _digest(_context(root, soren, player=player, now=instant,
                                               probe=probe, tmux=tmux, container_probe=container_probe)):
            refuse('context_changed')
        committed_at = time.time() if now is None else now
        if not clock <= committed_at < expires:
            refuse('approval_expired')
        # No owner/receipt edit: audit and pending consumption commit together.
        pending = context['ledger']['pending']
        row = dict(schema_version=1, fingerprint=expected, expires_at=expires, code_sha=sha,
                   context_sha256=_digest(context), owner_sha256=_digest(context['owner']),
                   reservation=pending, released_at=committed_at, history_authority=False,
                   all_resources_released=True, previous_error_kind=ledger.get('error_kind'),
                   previous_reason=ledger.get('reason'))
        updated = {**ledger, AUDIT_KEY: [*audit, row], 'pending': None, 'status': 'ready',
                   'reason': None, 'last_seen_at': committed_at,
                   'history': [*ledger['history'], dict(corner='nethack', at=committed_at, source='completion',
                       request_id=pending['request_id'], administrative=True)],
                   'last_result': dict(corner='nethack', request_id=pending['request_id'],
                       status='interrupted', at=committed_at, administrative=True)}
        if len(json.dumps(updated).encode()) > 65536:
            refuse('audit_unproven')
        try:
            atomic_write_json(root / 'corner_rotation.json', updated)
        except Exception:
            # Atomic replacement may already have succeeded. Do not replay a
            # consumed reservation or overwrite it; same-fingerprint retry reads.
            refuse('persistence_unconfirmed')
        if _digest(read_record(root, ('corner_rotation.json',))) != _digest(updated):
            refuse('persistence_unconfirmed')
        return {'status': 'admin-released', 'history_authority': False}


def main(argv=None):
    from .config import load_global
    from .nethack_run import load_nethack_persistence_settings
    from .trading.soren_output import resolve_soren_root
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expires', required=True, type=int)
    parser.add_argument('--sha', required=True)
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / 'config/docich.soren-live.toml')
        result = release(Path(g.state_dir), resolve_soren_root(g),
                         player=load_nethack_persistence_settings(g).player_name,
                         sha=args.sha, expected=args.expected, expires=args.expires)
        print(json.dumps(result, separators=(',', ':')))
        return 0
    except Refused as exc:
        reason = str(exc) if str(exc) in REFUSALS else 'evidence_unproven'
    except Exception:
        reason = 'evidence_unproven'
    print(json.dumps({'status': 'refused', 'reason': reason}, separators=(',', ':')))
    return REFUSALS[reason]


if __name__ == '__main__':
    raise SystemExit(main())
