"""One owner-requested Hanjuku checkpoint transfer; no automatic rewind.

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
from .naming import validate_runtime_id
from .retroarch_boundary import checkpoint_digest, identity, matches, read_record

REQUEST_FILE = 'hanjuku_resume.json'
RECEIPT_FILE = 'hanjuku_resume.json'
MAX_BYTES = 64 * 1024 * 1024


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
    """Called inside the durable manual inbox lock before publishing its request."""
    from .adapters.retroarch import resolve_rom, resolve_core
    uuid.UUID(queued['request_id'])
    with _lock(g) as root:
        prior = read_record(root / REQUEST_FILE)
        if prior and prior.get('status') in {'prepared', 'queued', 'claimed'}:
            if prior.get('request_id') == queued['request_id']:
                return
            raise AdapterError('another checkpoint request is pending')
        runtimes = Path(g.state_dir) / 'runtimes'
        if runtimes.is_symlink():
            raise AdapterError('runtime storage may not be a symlink')
        choices = []
        for runtime in runtimes.glob('g*'):
            if runtime.is_symlink() or not runtime.is_dir():
                continue
            try:
                validate_runtime_id(runtime.name)
            except ValueError:
                continue
            states = runtime / 'states'
            if states.is_symlink():
                continue
            for path in states.glob('hanjuku-hero.state*'):
                # Slot 0 and numbered slots only: ignore thumbnails/backups.
                suffix = path.name.removeprefix('hanjuku-hero.state')
                if suffix and not suffix.isdecimal():
                    continue
                if path.is_file() and not path.is_symlink() and 0 < path.stat().st_size:
                    choices.append(path)
        if not choices:
            raise AdapterError('no remaining checkpoint')
        selected = max(choices, key=lambda p: (p.stat().st_mtime_ns, str(p)))
        before = selected.stat()
        if before.st_size > MAX_BYTES:
            raise AdapterError('latest checkpoint exceeds read bound')
        digest = checkpoint_digest(selected)
        mem_digest = core_digest(selected)
        sealed = root / (queued['request_id'] + '.state')
        _persist_copy(selected, sealed)
        after = selected.stat()
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or checkpoint_digest(sealed) != digest or checkpoint_digest(selected) != digest):
            raise AdapterError('selected checkpoint changed during preservation')
        atomic_write_json(root / REQUEST_FILE, dict(schema=1, status='prepared',
            request_id=queued['request_id'], corner=queued['corner'],
            selected_at=queued['selected_at'],
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
                or any(k in prior for k in ('runtime_id', 'generation', 'lease_id'))):
            raise AdapterError('invalid prepared checkpoint request')
        request_id = str(uuid.UUID(prior['request_id']))
        selected_at = timestamp(prior['selected_at'])
        sealed = root / (request_id + '.state')
        if (checkpoint_digest(sealed) != prior.get('sha256')
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
                or queued.get('resume_latest') is not True):
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
            return
        ledger = read_record(Path(g.state_dir) / 'corner_rotation.json', limit=1024 * 1024)
        pending = ledger.get('pending') or {}
        recovery = request.get('startup_recovery')
        attempt_id = (recovery.get('attempt_id') if isinstance(recovery, dict)
                      else request.get('request_id'))
        if (pending.get('request_id') != attempt_id
                or (recovery is not None and pending.get('resume_request_id') != request.get('request_id'))
                or pending.get('corner') != request.get('corner')
                or pending.get('source') != 'manual' or pending.get('resume_latest') is not True):
            return  # another legitimate corner start cannot steal the request
        from .agent.fence import read_canonical
        candidate = read_canonical(g.state_dir).get('candidate') or {}
        if any(candidate.get(k) != getattr(spec, k) for k in ('game', 'runtime_id', 'generation', 'lease_id')):
            raise AdapterError('checkpoint candidate identity mismatch')
        request_id = str(uuid.UUID(request['request_id']))
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
    from .hanjuku_screen import fake_hero_announcement_chapter, parse
    native = read_record(spec.runtime_dir / 'presentation.json')
    capture = spec.runtime_dir / 'screenshots/resume.png'
    capture.parent.mkdir(exist_ok=True)
    chapter = None
    starter_without_chapter = False
    while chapter is None and not starter_without_chapter:
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
    # A transition's party picker does not display a chapter. Its visible
    # names are sufficient to resume selection, never to seed old route state.
    policy = {'name': {'done': True}}
    if chapter is not None:
        policy['chapter'] = chapter
    else:
        policy['resume_chapter_pending'] = True
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
