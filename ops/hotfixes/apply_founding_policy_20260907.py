#!/usr/bin/env python3
"""Owner-exec only: apply seven reviewed policy files, never runtime code."""
from __future__ import annotations
import hashlib
import json
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
    state = json.loads((ROOT / 'tmp/state/improve_state.json').read_text())
    if state.get('status') != 'idle' or (ROOT / 'tmp/improve.lock').exists():
        raise ValueError('improvement is not idle')
    prefix = ['git', '-C', str(SOURCE), '-c', 'core.hooksPath=/dev/null']
    subprocess.run(prefix + ['fetch', '--no-tags', 'https://github.com/azumag/soviet_now.git', doc['source_sha']],
                   check=True, timeout=120, stdout=subprocess.DEVNULL)
    payload = {rel: subprocess.check_output(prefix + ['show', doc['source_sha'] + ':' + rel], timeout=30)
               for rel in ALLOWLIST}
    print(apply(ROOT, doc['files'], payload))


if __name__ == '__main__':
    main()
