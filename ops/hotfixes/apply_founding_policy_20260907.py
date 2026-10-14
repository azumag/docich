#!/usr/bin/env python3
"""Owner-exec only: apply seven reviewed policy files, never runtime code."""
from __future__ import annotations
import hashlib
import fcntl
import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

ROOT = Path('/home/ubuntu/soren')
SOURCE = Path('/home/ubuntu/docich/games/soviet_now')
ALLOWLIST = {'data/user_review.md', 'prompts/game_theory.md',
             'prompts/improve_strategy.md', 'prompts/analyze_strategy.md',
             'prompts/implement_strategy.md', 'prompts/review_strategy.md',
             'tests/test_founding_policy_contract.py'}
SPAWN_GUARD = 'tmp/state/.improve_spawn.lock'
SPAWN_HELPER_SHA256 = '235f69b2768024a78b4aceba24daa8aef028968db6b02527bfeb88a941a83d55'
SPAWN_WRAPPER_SHA256 = '1b27ccc2df5379a57296d6ec7483f608d1a3c2c6c0fa243e59dabed6d68718ab'


def digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def safe_path(root, rel):
    path = Path(rel)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('unsafe policy path')
    current = root
    for part in (None, *path.parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise ValueError('symlink in policy path')
    return current


def read(path):
    if not path.exists():
        return None, None
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular policy file required')
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def atomic_write(path, raw, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.policy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def require_runtime_protocol(root):
    # Fail closed on an old/missing disk protocol. Deployment must also keep
    # scheduling paused until BOTH running shell spawners reload this version.
    try:
        helper, _ = read(safe_path(root, 'strategy/spawn_guard.py'))
        raw, _ = read(safe_path(root, 'strategy/improve.sh'))
        text = raw.decode('utf-8') if raw is not None else ''
        start = text.index('#=== spawn 排他 mutex')
        end = text.index('#=== ピーク時間帯', start)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError('reviewed spawn lease protocol is not deployed') from exc
    if digest(helper) != SPAWN_HELPER_SHA256 or digest(text[start:end].encode()) != SPAWN_WRAPPER_SHA256:
        raise ValueError('reviewed spawn lease protocol is not deployed')


def require_idle(root):
    state_path = safe_path(root, 'tmp/state/improve_state.json')
    raw, _ = read(state_path)
    if raw is None:
        raise ValueError('improve state is missing')
    state = json.loads(raw)
    if state.get('status') != 'idle' or safe_path(root, 'tmp/improve.lock').exists():
        raise ValueError('improvement is not idle')


@contextmanager
def policy_spawn_lease(root):
    # Permanent inode, shared with strategy/spawn_guard.py kernel-lease-v1.
    # Never unlink it: mtime/PID recycling cannot expire a live kernel lock.
    path = safe_path(root, SPAWN_GUARD + '.lease')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ValueError('invalid spawn lease file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('improvement spawn is in progress') from exc
        current = path.stat()
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise ValueError('spawn lease inode changed')
        yield
    finally:
        os.close(fd)


@contextmanager
def improvement_quiescence(root):
    with policy_spawn_lease(root):
        with _directory_quiescence(root):
            yield


@contextmanager
def _directory_quiescence(root):
    # Reuse the runtime's atomic spawn mutex. A trigger that already owns it
    # wins and makes this installer fail closed; later triggers cannot start
    # until the complete policy transaction has finished. Never steal it.
    guard = safe_path(root, SPAWN_GUARD)
    try:
        guard.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError('improvement spawn is in progress') from exc
    guard_identity = (guard.stat().st_dev, guard.stat().st_ino)
    owner = guard / 'owner'
    owner_written = False
    try:
        owner.write_text(str(os.getpid()))
        owner_written = True
        require_idle(root)
        yield
    finally:
        try:
            current = guard.stat()
            same_guard = (current.st_dev, current.st_ino) == guard_identity
            owned = owner_written and owner.is_file() and owner.read_text().strip() == str(os.getpid())
            empty_failed_guard = not owner_written and not owner.exists()
            if same_guard and (owned or empty_failed_guard):
                owner.unlink(missing_ok=True)
                guard.rmdir()
        except FileNotFoundError:
            pass


def apply(root, specs, payload):
    plans = []
    for rel, spec in specs.items():
        if digest(payload[rel]) != spec['new']:
            raise ValueError('source hash mismatch: ' + rel)
        path = safe_path(root, rel)
        raw, mode = read(path)
        if raw is not None and mode != spec['mode']:
            raise ValueError('mode drift: ' + rel)
        if digest(raw) == spec['new']:
            continue
        if digest(raw) != spec['old']:
            raise ValueError('live drift: ' + rel)
        plans.append((rel, path, raw, mode, payload[rel], spec['mode']))
    if not plans:
        return 'already_applied'
    backup_root = safe_path(root, 'tmp/policy-backups')
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix='founding-20260907-', dir=backup_root))
    for rel, _, raw, mode, _, _ in plans:
        if raw is not None:
            atomic_write(backup / rel, raw, 0o600)
    atomic_write(backup / 'manifest.json', json.dumps(specs, sort_keys=True).encode(), 0o600)
    written = []
    try:
        for item in plans:
            rel, path, old, old_mode, new, mode = item
            if read(safe_path(root, rel)) != (old, old_mode):
                raise ValueError('concurrent policy change: ' + rel)
            written.append(item)
            atomic_write(path, new, mode)
        for rel, spec in specs.items():
            raw, mode = read(safe_path(root, rel))
            if digest(raw) != spec['new'] or mode != spec['mode']:
                raise ValueError('post-apply verification failed: ' + rel)
    except Exception:
        for rel, path, old, old_mode, new, mode in reversed(written):
            current, current_mode = read(safe_path(root, rel))
            if (current, current_mode) == (old, old_mode):
                continue
            if (current, current_mode) != (new, mode):
                raise RuntimeError('concurrent rollback drift; retained backup: ' + str(backup))
            if old is None:
                path.unlink()
            else:
                atomic_write(path, old, old_mode)
        raise
    return 'applied'


def main():
    if len(sys.argv) != 1:
        raise ValueError('no arguments accepted')
    doc = json.loads(Path(__file__).with_name('founding_policy_20260907.json').read_text())
    if set(doc['files']) != ALLOWLIST or not re.fullmatch('[0-9a-f]{40}', doc['source_sha']):
        raise ValueError('invalid pinned policy manifest')
    # Fail early if an improve job is already active, then re-check under the
    # runtime's spawn mutex immediately before touching live policy.
    require_idle(ROOT)
    require_runtime_protocol(ROOT)
    prefix = ['git', '-C', str(SOURCE), '-c', 'core.hooksPath=/dev/null']
    subprocess.run(prefix + ['fetch', '--no-tags', 'https://github.com/azumag/soviet_now.git', doc['source_sha']],
                   check=True, timeout=120, stdout=subprocess.DEVNULL)
    payload = {rel: subprocess.check_output(prefix + ['show', doc['source_sha'] + ':' + rel], timeout=30)
               for rel in ALLOWLIST}
    with improvement_quiescence(ROOT):
        require_runtime_protocol(ROOT)
        print(apply(ROOT, doc['files'], payload))


if __name__ == '__main__':
    main()
