"""Closed, bounded Linux process coverage. No signals, writes or raw output.

Every process owned by the state UID must be positively attributed to the
current runtime or this check's control ancestors. Untagged game children,
shared processes without an identity contract and unknown jobs never pass.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


class CoverageUnproven(ValueError):
    pass


def containers():
    """The fixed local Docker endpoint only; no inherited remote context.

    An old host launcher may have died with a foreign-UID canary still alive.
    PID coverage of the state UID alone cannot prove those containers absent.
    """
    result = subprocess.run(
        ['docker', '--host', 'unix:///var/run/docker.sock', 'ps', '--no-trunc',
         '--format', '{{.ID}} {{.Names}}'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False,
        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/home/ubuntu',
             'DOCKER_CONFIG': '/home/ubuntu/.docker'},
    )
    if result.returncode or len(result.stdout) > 65536:
        raise CoverageUnproven()
    rows = result.stdout.decode('ascii').splitlines()
    if len(rows) > 256 or any(not re.fullmatch('[0-9a-f]{64} [A-Za-z0-9_.-]{1,128}', r) for r in rows):
        raise CoverageUnproven()
    return [r.split(' ', 1) for r in rows]


def _read(path, maximum=65536):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise CoverageUnproven()
    return raw


def processes(uid, *, proc=Path('/proc')):
    """A vanished process or inaccessible namespace invalidates this sample."""
    if not proc.is_dir():
        raise CoverageUnproven()
    rows = []
    namespace = os.readlink(proc / 'self/ns/pid')
    boot_id = _read(proc / 'sys/kernel/random/boot_id', 64).decode('ascii').strip()
    if not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id):
        raise CoverageUnproven()
    with os.scandir(proc) as listing:
        names = []
        for entry in listing:
            if entry.name.isdigit():
                names.append(entry.name)
                if len(names) > 8192:
                    raise CoverageUnproven()
    for name in sorted(names, key=int):
        root = proc / name
        status = _read(root / 'status', 16384).decode('ascii')
        uids = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
        if len(uids) != 4:
            raise CoverageUnproven()
        uids = [int(v) for v in uids]
        own = uid in uids
        if own and (any(v != uid for v in uids) or os.readlink(root / 'ns/pid') != namespace):
            raise CoverageUnproven()
        raw = _read(root / 'stat', 4096).decode('ascii')
        fields = raw[raw.rfind(')') + 2:].split()
        row = dict(pid=int(name), ppid=int(fields[1]), start_ticks=int(fields[19]),
                   state=fields[0], uid=uids[0], boot_id=boot_id, pid_namespace=namespace)
        # Keep PID/birth visibility across UIDs for old manifest PID checks,
        # but never read another user's environment, command line or paths.
        if not own:
            rows.append(row)
            continue
        tags = {}
        # Only these three public ownership keys are retained. Never retain,
        # emit, hash or inspect values of other environment keys.
        for pair in _read(root / 'environ').split(b'\0'):
            key, _, value = pair.partition(b'=')
            if key in {b'DOCICH_TMUX_RUNTIME_ID', b'DOCICH_TMUX_GENERATION', b'DOCICH_TMUX_ROLE'}:
                decoded = key.decode('ascii')
                if decoded in tags:
                    raise CoverageUnproven()
                tags[decoded] = value.decode('ascii')
        row.update(tags=tags, exe=os.readlink(root / 'exe'), cwd=os.readlink(root / 'cwd'),
                   argv=_read(root / 'cmdline').split(b'\0'))
        again_status = _read(root / 'status', 16384).decode('ascii')
        again_uids = next(line.split()[1:] for line in again_status.splitlines() if line.startswith('Uid:'))
        if ([int(v) for v in again_uids] != uids
                or os.readlink(root / 'ns/pid') != namespace
                or os.readlink(root / 'exe') != row['exe'] or os.readlink(root / 'cwd') != row['cwd']
                or _read(root / 'cmdline').split(b'\0') != row['argv']):
            raise CoverageUnproven()
        again = _read(root / 'stat', 4096).decode('ascii')
        if again != raw:
            # CPU counters change normally. Compare only immutable birth and
            # parentage, not the full stat line.
            fresh = again[again.rfind(')') + 2:].split()
            if (fresh[1], fresh[19], fresh[0]) != (fields[1], fields[19], fields[0]):
                raise CoverageUnproven()
        rows.append(row)
    with os.scandir(proc) as listing:
        after = sorted((e.name for e in listing if e.name.isdigit()), key=int)
    if after != sorted(names, key=int):
        raise CoverageUnproven()
    if (_read(proc / 'sys/kernel/random/boot_id', 64).decode('ascii').strip() != boot_id
            or os.readlink(proc / 'self/ns/pid') != namespace):
        raise CoverageUnproven()
    return rows
