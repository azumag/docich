"""Boundary-directed Hanjuku starts with request-bound checkpoint transfer.

Save bytes stay private. A UDP LOAD_STATE acknowledgement only queues a task;
paused core serialization must equal the selected checkpoint before AI starts.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import struct
import time
import uuid
import zlib

from .adapters.base import AdapterError
from .game_switch import atomic_write_json
from .naming import runtime_id_generation
from .retroarch_boundary import (BOUNDARY_FILE, MANUAL_SAVE_FILE, checkpoint_digest,
                                identity, matches, read_record)

REQUEST_FILE = 'hanjuku_resume.json'
RECEIPT_FILE = 'hanjuku_resume.json'
MAX_BYTES = 64 * 1024 * 1024
START_POLICY = 'terminal-boundary-v1'


def _start_mode(record):
    if record.get('start_policy') != START_POLICY:
        return 'resume'  # legacy claimed transfers retain their sealed snapshot
    mode, source = record.get('start_mode'), record.get('source_boundary')
    if mode == 'fresh':
        if ((source is not None and (not isinstance(source, dict)
                                    or source.get('outcome') not in {'game_over', 'suspended', 'manual_forced_stop'}
                                    or (record.get('explicit_resume') is True and source.get('outcome') != 'game_over')))
                or any(k in record for k in ('sha256', 'selected_source', 'core_state_sha256'))):
            raise AdapterError('Hanjuku fresh decision conflicts with saved progress')
    elif mode == 'resume':
        if (not isinstance(source, dict)
                or source.get('outcome') not in {'screen_stalled', 'input_stalled', 'suspended'}
                or source.get('sha256') != record.get('sha256')
                or source.get('checkpoint_bytes') != record.get('bytes')):
            raise AdapterError('Hanjuku resume decision conflicts with its boundary')
    else:
        raise AdapterError('unknown Hanjuku start decision')
    rewind = record.get('rewind_from_boundary')
    if rewind is not None and (
            record.get('explicit_resume') is not True or record.get('dispatch_source') != 'manual'
            or not isinstance(rewind, dict) or rewind.get('outcome') != 'manual_forced_stop'
            or rewind.get('status') != 'reached' or rewind.get('save_error') != 'save_timeout'
            or type(rewind.get('generation')) is not int or not isinstance(source, dict)
            or type(source.get('generation')) is not int or rewind['generation'] <= source['generation']):
        raise AdapterError('Hanjuku rewind decision lacks an explicit unsaved-stop request')
    return mode


def _latest_boundary(g, game, *, explicit_resume=False, _before_generation=None):
    """Order runs by generation, then prove the latest run's reached boundary.

    Unknown or incomplete newer progress never authorizes a rewind or a new
    game. Only explicit resume after an authorized unsaved manual stop may
    inspect an earlier generation, with the same boundary/save verification.
    """
    from . import hanjuku_run
    from .adapters.retroarch import resolve_rom
    from .game_switch import RequestReceiptStore
    runtimes = Path(g.state_dir) / 'runtimes'
    if runtimes.is_symlink():
        raise AdapterError('runtime storage may not be a symlink')
    stems = {'hanjuku-hero', resolve_rom(g, game).stem}
    # A committed newer run still counts when its runtime evidence was lost.
    # Otherwise deleting a game-over run's files could resurrect an old stall.
    # Leases are compared between run/boundary below; rollback may re-lease a
    # runtime after its original start receipt was committed.
    choices = {}
    for receipt in RequestReceiptStore(Path(g.state_dir)).receipts():
        result = receipt.get('result') or {}
        if (receipt.get('status') == 'succeeded'
                and (receipt.get('target') == 'hanjuku-hero'
                     or result.get('to_game') == 'hanjuku-hero')):
            # The queued start/switch catch-up path succeeds without starting
            # its reserved runtime. Recognize that exact result, not all old
            # successes lacking an active_runtime proof (which stay barriers).
            if (receipt.get('operation') in {'start', 'switch'}
                    and result.get('operation') == receipt['operation']
                    and result.get('request_id') == receipt['request_id']
                    and result.get('status') == 'succeeded'
                    and type(result.get('generation')) is int
                    and result['generation'] == receipt['generation']
                    and result.get('from_game') == result.get('to_game') == receipt.get('target')
                    and result.get('detail') == '既に起動中です'
                    and 'active_runtime' not in result and 'source_runtime' not in result):
                continue
            choices[runtimes / receipt['runtime_id']] = receipt['generation']
    for runtime in runtimes.glob('g*'):
        # RUN_FILE is Hanjuku-specific. Saved states alone also constitute
        # progress, but cannot prove a safe start without the run/boundary.
        relevant = (any((runtime / name).exists() or (runtime / name).is_symlink()
                        for name in (hanjuku_run.RUN_FILE, MANUAL_SAVE_FILE, RECEIPT_FILE))
                    or any(any((runtime / 'states').glob(stem + '.state*')) for stem in stems))
        if not relevant:
            boundary = read_record(runtime / BOUNDARY_FILE)
            relevant = boundary.get('game') == 'hanjuku-hero'
        if not relevant:
            continue
        if runtime.is_symlink() or not runtime.is_dir():
            raise AdapterError('Hanjuku runtime may not be a symlink')
        try:
            generation = runtime_id_generation(runtime.name)
        except ValueError as exc:
            raise AdapterError('invalid Hanjuku runtime identity') from exc
        choices[runtime] = generation
    if _before_generation is not None:
        choices = {runtime: generation for runtime, generation in choices.items()
                   if generation < _before_generation}
    if not choices:
        return None
    latest = max(choices.values())
    newest = [runtime for runtime, generation in choices.items() if generation == latest]
    if len(newest) != 1:
        raise AdapterError('ambiguous latest Hanjuku generation')
    runtime = newest[0]
    record = read_record(runtime / BOUNDARY_FILE)
    run = read_record(runtime / hanjuku_run.RUN_FILE)
    expected = dict(game='hanjuku-hero', runtime_id=runtime.name, generation=latest,
                    lease_id=record.get('lease_id'))
    try:
        uuid.UUID(record['request_id'])
        uuid.UUID(expected['lease_id'])
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise AdapterError('Hanjuku boundary identity is unconfirmed') from exc
    if (type(record.get('schema')) is not int or record['schema'] != 1
            or record.get('status') != 'reached'
            or any(type(record.get(k)) is not type(v) or record.get(k) != v
                   for k, v in expected.items())):
        raise AdapterError('Hanjuku reached boundary identity mismatch')
    outcome = record.get('outcome')
    if outcome in hanjuku_run.TERMINAL_REASONS:
        terminal = hanjuku_run.terminal(runtime, expected)
        if (not terminal or terminal['terminal_reason'] != outcome
                or record.get('frame_sha256') != terminal['frame_sha256']
                or type(run.get('generation')) is not int):
            raise AdapterError('Hanjuku terminal boundary evidence mismatch')
    elif outcome in {'suspended', 'manual_forced_stop'}:
        manual = read_record(runtime / MANUAL_SAVE_FILE)
        if (any(type(manual.get(k)) is not type(v) or manual.get(k) != v
                for k, v in {**expected, 'schema': 1, 'request_id': record['request_id']}.items())
                or (run and any(type(run.get(k)) is not type(v) or run.get(k) != v
                                for k, v in expected.items()))):
            raise AdapterError('Hanjuku manual boundary evidence mismatch')
        # A manual marker cannot override an already detected game over/stall.
        if run.get('terminal_reason') is not None:
            raise AdapterError('Hanjuku manual boundary conflicts with terminal evidence')
        if outcome == 'manual_forced_stop':
            if manual.get('allow_unsaved_stop') is not True or record.get('save_error') != 'save_timeout':
                raise AdapterError('Hanjuku unsaved stop authorization mismatch')
            if explicit_resume:
                # Owner-approved exception: an explicit resume may rewind past
                # this confirmed unsaved stop, but not past unknown progress or
                # a game-over boundary. A bad older save still fails closed.
                selection = _latest_boundary(g, game, explicit_resume=True, _before_generation=latest)
                if selection is None:
                    raise AdapterError('no earlier verified Hanjuku checkpoint to resume')
                return dict(selection, rewind_from_boundary=record)
            # A confirmed owner-authorized unsaved stop preserves the old
            # ordinary-start meaning. It never authorizes an older save.
            return dict(mode='fresh', source_boundary=record)
    else:
        raise AdapterError('Hanjuku last outcome requires explicit recovery')
    if outcome == 'game_over':
        return dict(mode='fresh', source_boundary=record)
    name = resolve_rom(g, game).stem + '.state'
    if record.get('checkpoint') != name or (runtime / 'states').is_symlink():
        raise AdapterError('Hanjuku boundary checkpoint basename mismatch')
    selected = runtime / 'states' / name
    digest = checkpoint_digest(selected)
    if (digest != record.get('sha256') or type(record.get('checkpoint_bytes')) is not int
            or selected.stat().st_size != record['checkpoint_bytes']):
        raise AdapterError('Hanjuku boundary checkpoint changed')
    if outcome == 'suspended' and not explicit_resume:
        # Only the requested stalled outcomes gain automatic restoration.
        # Still reject a corrupt saved suspension rather than hiding it with
        # an implicit reset; an ordinary valid suspension keeps fresh semantics.
        core_digest(selected)
        return dict(mode='fresh', source_boundary=record)
    return dict(mode='resume', source_boundary=record, selected=selected)


def prepare_start(g, queued):
    """Common natural/manual dispatch, after the previous program slot ended."""
    from .config import load_game
    if queued.get('corner') != 'hanjuku-hero':
        return
    if queued.get('resume_request_id') is not None:
        # The reviewed failed-before-AI recovery keeps the logical request and
        # sealed bytes; its derived attempt must never prepare a new decision.
        with _lock(g) as root:
            prior = read_record(root / REQUEST_FILE)
            recovery = prior.get('startup_recovery')
            if (not isinstance(recovery, dict)
                    or prior.get('request_id') != queued['resume_request_id']
                    or recovery.get('attempt_id') != queued.get('request_id')
                    or prior.get('corner') != queued['corner']
                    or prior.get('status') not in {'queued', 'claimed', 'verified'}):
                raise AdapterError('checkpoint startup recovery identity mismatch')
        return
    prepare(g, load_game(g, 'hanjuku-hero'), queued)
    publish(g, queued)


def core_digest(path: Path) -> str:
    """Bounded RZIP v1 / RASTATE1 decoder, comparing only the core MEM block."""
    checkpoint_digest(path)
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise AdapterError('checkpoint exceeds read bound')
    if data[:8] == b'#RZIPv\x01#':
        if len(data) < 20:
            raise AdapterError('invalid compressed checkpoint')
        chunk, size = struct.unpack_from('<IQ', data, 8)
        if not 0 < chunk <= MAX_BYTES or not 0 < size <= MAX_BYTES:
            raise AdapterError('checkpoint expansion exceeds bound')
        pos, out = 20, bytearray()
        while pos < len(data):
            if pos + 4 > len(data):
                raise AdapterError('truncated checkpoint chunk')
            length = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            if not 0 < length <= MAX_BYTES or pos + length > len(data):
                raise AdapterError('invalid checkpoint chunk')
            inflater = zlib.decompressobj()
            try:
                decoded = inflater.decompress(data[pos:pos + length], min(chunk, size - len(out)) + 1)
            except zlib.error as exc:
                raise AdapterError('invalid checkpoint compression') from exc
            if (not inflater.eof or inflater.unused_data or inflater.unconsumed_tail
                    or len(decoded) > chunk or len(out) + len(decoded) > size):
                raise AdapterError('invalid checkpoint expansion')
            out.extend(decoded)
            pos += length
        if len(out) != size:
            raise AdapterError('checkpoint expansion size mismatch')
        data = bytes(out)
    if data[:8] != b'RASTATE\x01':
        raise AdapterError('checkpoint format unsupported')
    pos, digest = 8, None
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        length = struct.unpack_from('<I', data, pos + 4)[0]
        pos += 8
        end = pos + ((length + 7) // 8) * 8
        if end > len(data):
            raise AdapterError('truncated checkpoint block')
        if tag == b'MEM ':
            if digest is not None or length == 0:
                raise AdapterError('invalid checkpoint core block')
            digest = hashlib.sha256(data[pos:pos + length]).hexdigest()
        if tag == b'END ':
            if length or end != len(data) or digest is None:
                raise AdapterError('invalid checkpoint terminator')
            return digest
        pos = end
    raise AdapterError('checkpoint terminator missing')


def _private_root(g):
    root = Path(g.state_dir) / 'hanjuku-resume'
    if root.is_symlink():
        raise AdapterError('checkpoint storage may not be a symlink')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


@contextmanager
def _lock(g):
    root = _private_root(g)
    path = root / 'request.lock'
    if path.is_symlink():
        raise AdapterError('checkpoint lock may not be a symlink')
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield root


def _persist_copy(source, destination):
    if destination.exists() or destination.is_symlink():
        raise AdapterError('checkpoint destination already exists')
    with source.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if not 0 < len(data) <= MAX_BYTES:
        raise AdapterError('checkpoint copy exceeds bound')
    with destination.open('xb') as stream:
        os.chmod(destination, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare(g, game, queued):
    """Seal one start decision before publishing its durable request."""
    from .adapters.retroarch import resolve_rom, resolve_core
    uuid.UUID(queued['request_id'])
    with _lock(g) as root:
        prior = read_record(root / REQUEST_FILE)
        if prior and prior.get('status') in {'prepared', 'queued', 'claimed'}:
            if prior.get('request_id') == queued['request_id']:
                if (prior.get('status') != 'claimed'
                        and prior.get('start_policy') != START_POLICY):
                    raise AdapterError('legacy unclaimed checkpoint needs explicit recovery')
                return
            raise AdapterError('another checkpoint request is pending')
        if prior.get('request_id') == queued['request_id']:
            return  # a verified retry must not select from its own new run
        selection = _latest_boundary(g, game, explicit_resume=queued.get('resume_latest') is True)
        if selection is None and queued.get('resume_latest') is True:
            raise AdapterError('no remaining checkpoint')
        selection = selection or dict(mode='fresh', source_boundary=None)
        request = dict(schema=1, status='prepared', start_policy=START_POLICY,
            start_mode=selection['mode'], source_boundary=selection['source_boundary'],
            dispatch_source=queued.get('source', 'manual' if queued.get('resume_latest') is True else 'automatic'),
            explicit_resume=queued.get('resume_latest') is True,
            request_id=queued['request_id'], corner=queued['corner'],
            selected_at=queued['selected_at'])
        if 'rewind_from_boundary' in selection:
            request['rewind_from_boundary'] = selection['rewind_from_boundary']
        if selection['mode'] == 'fresh':
            atomic_write_json(root / REQUEST_FILE, request)
            return
        selected = selection['selected']
        before = selected.stat()
        if before.st_size > MAX_BYTES:
            raise AdapterError('latest checkpoint exceeds read bound')
        digest = checkpoint_digest(selected)
        if digest != selection['source_boundary']['sha256']:
            raise AdapterError('selected checkpoint no longer matches its boundary')
        mem_digest = core_digest(selected)
        sealed = root / (queued['request_id'] + '.state')
        _persist_copy(selected, sealed)
        after = selected.stat()
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or checkpoint_digest(sealed) != digest or checkpoint_digest(selected) != digest):
            raise AdapterError('selected checkpoint changed during preservation')
        # Reprove the same receipt after sealing; a concurrent stop must not
        # silently change which end-of-run this transfer represents.
        if read_record(selected.parent.parent / BOUNDARY_FILE) != selection['source_boundary']:
            raise AdapterError('selected checkpoint boundary changed during preservation')
        atomic_write_json(root / REQUEST_FILE, dict(request,
            selected_source=str(selected.relative_to(Path(g.state_dir))),
            selected_mtime_ns=before.st_mtime_ns, bytes=before.st_size,
            sha256=digest, core_state_sha256=mem_digest,
            rom_sha256=checkpoint_digest(resolve_rom(g, game)),
            core_sha256=checkpoint_digest(Path(resolve_core(game)))))


def recover_prepared(g, corner):
    """Reuse a sealed, unpublished request under the caller's inbox lock."""
    from .corner_rotation import timestamp
    with _lock(g) as root:
        prior = read_record(root / REQUEST_FILE)
        if prior.get('status') != 'prepared':
            return None
        if (prior.get('schema') != 1 or prior.get('corner') != corner
                or prior.get('explicit_resume', True) is not True
                or any(k in prior for k in ('runtime_id', 'generation', 'lease_id'))):
            raise AdapterError('invalid prepared checkpoint request')
        request_id = str(uuid.UUID(prior['request_id']))
        selected_at = timestamp(prior['selected_at'])
        sealed = root / (request_id + '.state')
        if _start_mode(prior) != 'fresh' and (checkpoint_digest(sealed) != prior.get('sha256')
                or core_digest(sealed) != prior.get('core_state_sha256')):
            raise AdapterError('prepared checkpoint changed')
        return dict(corner=corner, selected_at=selected_at,
                    request_id=request_id, resume_latest=True)


def publish(g, queued):
    """Reconcile the durable inbox with its snapshot without changing identity."""
    with _lock(g) as root:
        prior = read_record(root / REQUEST_FILE)
        if (prior.get('request_id') != queued.get('request_id')
                or prior.get('corner') != queued.get('corner')
                or (queued.get('resume_latest') is True) != prior.get('explicit_resume', True)):
            raise AdapterError('checkpoint publication identity mismatch')
        if prior.get('status') == 'prepared':
            atomic_write_json(root / REQUEST_FILE, dict(prior, status='queued'))
        elif prior.get('status') not in {'queued', 'claimed', 'verified'}:
            raise AdapterError('checkpoint publication needs explicit recovery')


def claim(adapter, deadline, cancel):
    """Bind the one-shot transfer to its dispatched canonical candidate."""
    g, spec = adapter.g, adapter.spec
    if spec.game != 'hanjuku-hero':
        return
    from .hanjuku_run import enabled
    strict = enabled(adapter.game)
    adapter._check_active(deadline, cancel)
    prior = read_record(spec.runtime_dir / RECEIPT_FILE)
    if prior:
        if not matches(prior, spec, prior.get('request_id')) or prior.get('status') != 'verified':
            raise AdapterError('checkpoint candidate needs explicit recovery')
        target = adapter._game_window_target()
        if not adapter.tmux.window_target_exists(target):
            raise AdapterError('verified checkpoint process is missing; explicit recovery required')
        adapter._verify_window_ownership(target, 'game')
        return
    with _lock(g) as root:
        request = read_record(root / REQUEST_FILE)
        if request.get('status') not in {'prepared', 'queued'}:
            if request.get('runtime_id') == spec.runtime_id and request.get('status') == 'claimed':
                raise AdapterError('checkpoint candidate needs explicit recovery')
            if strict:
                raise AdapterError('Hanjuku start has no prepared boundary decision')
            return
        ledger = read_record(Path(g.state_dir) / 'corner_rotation.json', limit=1024 * 1024)
        recovery = request.get('startup_recovery')
        if (request.get('start_policy') != START_POLICY
                and not (isinstance(recovery, dict)
                         and isinstance(recovery.get('original'), dict)
                         and recovery['original'].get('status') == 'claimed')):
            raise AdapterError('legacy unclaimed checkpoint needs explicit recovery')
        attempt_id = (recovery.get('attempt_id') if isinstance(recovery, dict)
                      else request.get('request_id'))
        pending = next((row for key in ('pending', 'manual_pending')
                        if isinstance(row := ledger.get(key), dict)
                        and row.get('request_id') == attempt_id), {})
        if (pending.get('request_id') != attempt_id
                or (recovery is not None and pending.get('resume_request_id') != request.get('request_id'))
                or pending.get('corner') != request.get('corner')
                or pending.get('source', 'automatic') != request.get('dispatch_source', 'manual')
                or (pending.get('resume_latest') is True) != request.get('explicit_resume', True)):
            if strict:
                raise AdapterError('Hanjuku start reservation identity mismatch')
            return  # another legitimate corner start cannot steal the request
        from .agent.fence import read_canonical
        candidate = read_canonical(g.state_dir).get('candidate') or {}
        if any(type(candidate.get(k)) is not type(getattr(spec, k))
               or candidate.get(k) != getattr(spec, k)
               for k in ('game', 'runtime_id', 'generation', 'lease_id')):
            raise AdapterError('checkpoint candidate identity mismatch')
        request_id = str(uuid.UUID(request['request_id']))
        if _start_mode(request) == 'fresh':
            local = dict(request, **identity(spec, request_id), status='verified')
            atomic_write_json(root / REQUEST_FILE, local)
            atomic_write_json(spec.runtime_dir / RECEIPT_FILE, local)
            return
        sealed = root / (request_id + '.state')
        from .adapters.retroarch import resolve_rom, resolve_core
        if (checkpoint_digest(sealed, deadline, cancel) != request.get('sha256')
                or checkpoint_digest(resolve_rom(g, adapter.game), deadline, cancel) != request.get('rom_sha256')
                or checkpoint_digest(Path(resolve_core(adapter.game)), deadline, cancel) != request.get('core_sha256')):
            raise AdapterError('checkpoint or core/content changed')
        local = dict(request, **identity(spec, request_id), status='claimed')
        atomic_write_json(root / REQUEST_FILE, local)  # consume even if staging fails
        atomic_write_json(spec.runtime_dir / RECEIPT_FILE, local)
        _persist_copy(sealed, spec.runtime_dir / 'states/hanjuku-hero.state')
        adapter._check_active(deadline, cancel)


def _fresh_roundtrip(adapter, saved, expected, deadline, cancel):
    from .adapters.retroarch import send_ra_cmd
    verified = False
    while not verified:
        adapter._check_active(deadline, cancel)
        adapter._require_paused(deadline, cancel)
        before = saved.stat() if saved.exists() else None
        fingerprint = ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) if before else None)
        requested_ns = time.time_ns()
        send_ra_cmd('SAVE_STATE', port=adapter._network_port())
        save_end = min(deadline, time.monotonic() + 2)
        while time.monotonic() < save_end:
            adapter._check_active(deadline, cancel)
            time.sleep(.05)
            if not saved.exists():
                continue
            metadata = saved.stat()
            current = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
            if metadata.st_mtime_ns < requested_ns or current == fingerprint:
                continue
            try:
                digest = core_digest(saved)
            except AdapterError:
                continue  # asynchronous file write is incomplete
            if digest != expected:
                break  # LOAD task may not have completed before this SAVE
            first = (metadata.st_size, metadata.st_mtime_ns, checkpoint_digest(saved, deadline, cancel))
            time.sleep(.05)
            metadata = saved.stat()
            second = (metadata.st_size, metadata.st_mtime_ns, checkpoint_digest(saved, deadline, cancel))
            if first == second:
                with saved.open('rb') as stream:
                    os.fsync(stream.fileno())
                adapter._require_paused(deadline, cancel)
                verified = True
                break


SOURCE_KEYS = ('game', 'generation', 'lease_id', 'runtime_id')


STALLED_OUTCOMES = frozenset({'input_stalled', 'screen_stalled'})


def source_chapter(state_dir, receipt):
    """Chapter the stalled source run held at the exact checkpoint frame.

    Owner rule (2026-10-28): keep the chapter across a stall and restore it.
    The source bot's last decision must be on the boundary frame and carry
    the boundary's run identity; anything else stays chapterless.
    """
    from .naming import runtime_directory
    boundary = receipt.get('source_boundary') if isinstance(receipt, dict) else None
    if not isinstance(boundary, dict) or not isinstance(boundary.get('frame_sha256'), str):
        return None
    try:
        source = runtime_directory(Path(state_dir), boundary.get('runtime_id'))
        record = read_record(source / 'hanjuku_bot.json', limit=256 * 1024)
    except (AdapterError, ValueError, TypeError):
        return None
    trace = record.get('decision_trace') if isinstance(record.get('decision_trace'), dict) else {}
    policy = record.get('policy') if isinstance(record.get('policy'), dict) else {}
    owner = policy.get('_run_identity') if isinstance(policy.get('_run_identity'), dict) else {}
    if any(trace.get(k) != boundary.get(k) or owner.get(k) != boundary.get(k)
           for k in SOURCE_KEYS):
        return None
    if (trace.get('frame_sha256') != boundary['frame_sha256']
            and boundary.get('outcome') not in STALLED_OUTCOMES):
        # g1067 11:23: animated water gave the stall boundary a newer digest
        # than the bot's last decision. A stalled run sent no input after it,
        # so its chapter cannot have changed; other outcomes need the frame.
        return None
    chapter = policy.get('chapter')
    return chapter if type(chapter) is int and 1 <= chapter <= 12 else None


def inherit_running_chapter(runtime, state):
    """Seed a verified chapterless restore with its source chapter (hot-loaded)."""
    policy = state.get('policy') if isinstance(state, dict) else None
    if (not isinstance(policy, dict) or policy.get('resume_chapter_pending') is not True
            or policy.get('chapter') is not None):
        return None
    receipt = read_record(Path(runtime) / RECEIPT_FILE)
    if (receipt.get('status') != 'verified' or receipt.get('runtime_id') != Path(runtime).name
            or receipt.get('chapter') is not None):
        return None
    chapter = source_chapter(Path(runtime).parent.parent, receipt)
    if chapter is None:
        return None
    from .hanjuku.strategy.campaign import _enter_chapter
    policy.pop('resume_chapter_pending', None)
    _enter_chapter(policy, chapter, evidence='resume_source_boundary',
                   reason='停滞時の最終フレームで確定していた章を再開後に引き継ぐ')
    return chapter


def _castle_dialogue(screen):
    """A castle-hall dialogue: text plus the native year/month header.

    g1059 stalled on a seasonal 「メリ〜 クリスマ〜ス!」 event, which shows
    neither a chapter nor a known panel; the restore waited for one until
    its deadline on every retry (g1063, g1065).
    """
    header = screen.header or {}
    return (screen.kind == 'text' and header.get('chapter') is None
            and type(header.get('year')) is int and type(header.get('month')) is int)


def restore(adapter, deadline, cancel):
    """Pause, load, then reserialize and compare before staging any agent."""
    from .adapters.retroarch import send_ra_cmd
    spec = adapter.spec
    deadline = min(deadline, time.monotonic() + 10)
    receipt_path = spec.runtime_dir / RECEIPT_FILE
    request = read_record(receipt_path)
    if not request:
        return
    if matches(request, spec, request.get('request_id')) and request.get('status') == 'verified':
        if _start_mode(request) == 'fresh':
            return
        adapter._verify_manual_content(deadline, cancel)
        return  # readiness/agent repair never reloads an already verified run
    if not matches(request, spec, request.get('request_id')) or request.get('status') != 'claimed':
        raise AdapterError('checkpoint restoration is not pending')
    saved = spec.runtime_dir / 'states/hanjuku-hero.state'
    adapter._verify_manual_content(deadline, cancel)
    if checkpoint_digest(saved, deadline, cancel) != request.get('sha256'):
        raise AdapterError('staged checkpoint changed')
    reply = send_ra_cmd('GET_STATUS', port=adapter._network_port(), wait_reply_s=.1)
    if not reply or not reply.startswith('GET_STATUS PAUSED '):
        send_ra_cmd('PAUSE_TOGGLE', port=adapter._network_port(), wait_reply_s=.1)
    adapter._require_paused(deadline, cancel)
    # This reply confirms receipt only, not completion of unserialize.
    reply = send_ra_cmd('LOAD_STATE_SLOT 0', port=adapter._network_port(), wait_reply_s=.5)
    if reply is None or reply.strip() != 'LOAD_STATE_SLOT 0':
        raise AdapterError('checkpoint load command is unconfirmed')
    # Do not overwrite slot 0 while the asynchronous load task may still be
    # reading it. Prove the core using slot 1, then reset and prove slot 0.
    send_ra_cmd('STATE_SLOT_PLUS', port=adapter._network_port(), wait_reply_s=.1)
    _fresh_roundtrip(adapter, spec.runtime_dir / 'states/hanjuku-hero.state1',
                     request.get('core_state_sha256'), deadline, cancel)
    send_ra_cmd('STATE_SLOT_MINUS', port=adapter._network_port(), wait_reply_s=.1)
    _fresh_roundtrip(adapter, saved, request.get('core_state_sha256'), deadline, cancel)
    # Render one loaded frame while retaining pause; seed only the observed
    # chapter. Never carry the source run's final policy/roster/terminal state.
    send_ra_cmd('FRAMEADVANCE', port=adapter._network_port())
    from .xkit import XKit
    from .hanjuku_pixels import read_png
    from .hanjuku_screen import (fake_hero_announcement_chapter, parse, resume_sortie_panel,
                                resume_egg_ritual_panel, resume_battle_menu_panel,
                                resume_bijin_gift_panel)
    native = read_record(spec.runtime_dir / 'presentation.json')
    capture = spec.runtime_dir / 'screenshots/resume.png'
    capture.parent.mkdir(exist_ok=True)
    chapter = None
    starter_without_chapter = False
    sortie_without_chapter = False
    ritual_without_chapter = False
    battle_menu_without_chapter = False
    gift_without_chapter = False
    dialogue_without_chapter = False
    message_without_chapter = False
    inherited = source_chapter(adapter.g.state_dir, request)
    while (chapter is None and not starter_without_chapter and not sortie_without_chapter
           and not ritual_without_chapter and not battle_menu_without_chapter
           and not gift_without_chapter and not dialogue_without_chapter
           and not message_without_chapter):
        adapter._check_active(deadline, cancel)
        time.sleep(.05)
        reply = send_ra_cmd('GET_STATUS', port=adapter._network_port(), wait_reply_s=.1)
        if not reply or not reply.startswith('GET_STATUS PAUSED '):
            continue
        adapter._require_paused(deadline, cancel)
        XKit(native['display']).screenshot(capture, native['width'], native['height'],
                                         window_id=native['window'], low_latency=True)
        frame = read_png(capture).resized()
        screen = parse(frame)
        header = screen.header or {}
        observed = header.get('chapter')
        if type(observed) is int and 1 <= observed <= 12:
            chapter = observed
        elif fake_hero_announcement_chapter(screen) is not None:
            chapter = fake_hero_announcement_chapter(screen)
        elif observed is None and screen.kind in {'starter_list', 'starter_confirm'}:
            from .hanjuku_starter import rows
            upper, lower = rows(screen), rows(screen, selected=True)
            starter_without_chapter = upper is not None and bool(upper) and lower is not None
        elif observed is None and resume_egg_ritual_panel(screen):
            ritual_without_chapter = True
        elif observed is None and resume_sortie_panel(screen):
            sortie_without_chapter = True
        elif observed is None and resume_battle_menu_panel(screen):
            battle_menu_without_chapter = True
        elif observed is None and resume_bijin_gift_panel(screen):
            gift_without_chapter = True
        elif _castle_dialogue(screen):
            dialogue_without_chapter = True
        elif observed is None and inherited is not None and screen.kind == 'text' and screen.text:
            # g1071 11:30: the stall checkpoint showed a headerless field
            # notice (「かんじゅくプリンスじょうは…」) and the wait ran out the
            # deadline. The source run still proves the chapter.
            message_without_chapter = True
    if chapter is None:
        chapter = source_chapter(adapter.g.state_dir, request)
    # These verified chapterless panels seed only their measured foreground
    # owner; route and any old transaction need fresh evidence.
    policy = {'name': {'done': True}}
    if chapter is not None:
        policy['chapter'] = chapter
    else:
        policy['resume_chapter_pending'] = True
        if ritual_without_chapter:
            policy['resume_egg_ritual'] = {'readings': 0, 'month': f"{header['year']}-{header['month']}"}
        if sortie_without_chapter:
            policy['resume_sortie_cancel'] = {'readings': 0}
        if battle_menu_without_chapter:
            policy['resume_battle_menu'] = {'readings': 0}
    atomic_write_json(spec.runtime_dir / 'hanjuku_bot.json', {'policy': policy})
    adapter._check_active(deadline, cancel)
    send_ra_cmd('PAUSE_TOGGLE', port=adapter._network_port())
    from .adapters.retroarch import resolve_rom
    while True:
        adapter._check_active(deadline, cancel)
        reply = send_ra_cmd('GET_STATUS', port=adapter._network_port(), wait_reply_s=.1)
        prefix = 'GET_STATUS PLAYING '
        fields = reply[len(prefix):].strip().split(',') if reply and reply.startswith(prefix) else []
        if len(fields) >= 2 and fields[1] == resolve_rom(adapter.g, adapter.game).stem:
            break
        time.sleep(.05)
    verified_receipt = dict(request, status='verified', chapter=chapter,
                            gameplay_context='verified_checkpoint_foreground',
                            frame_sha256=frame.digest(), verified_at=time.time())
    atomic_write_json(receipt_path, verified_receipt)
    with _lock(adapter.g) as root:
        current = read_record(root / REQUEST_FILE)
        if not matches(current, spec, request['request_id']):
            raise AdapterError('checkpoint request ownership changed')
        atomic_write_json(root / REQUEST_FILE, verified_receipt)


def require_verified(spec):
    receipt = read_record(spec.runtime_dir / RECEIPT_FILE)
    if receipt and (not matches(receipt, spec, receipt.get('request_id'))
                    or receipt.get('status') != 'verified'):
        raise AdapterError('AI cannot start before checkpoint verification')
    if receipt:
        _start_mode(receipt)
