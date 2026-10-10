"""Bounded refusal classification only. Never supplies release authority.

No process names, PID values, arguments, paths or ownership identities leave
this module. Traversing visible procfs is not proof of host-wide coverage or
positive attribution. There is no live producer-enrollment protocol yet.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import stat

from .nethack_admin_release import (MAX_PROCESSES, TAG_KEYS, _chain, _digest,
                                    _safe_root, read_record)
from .nethack_admin_resources import containers, processes

CATEGORIES = ('control_ancestry_observed', 'normal_tmux_server_observed',
              'explicit_nethack', 'old_or_conflicting_tags',
              'current_soren_tags_unproven', 'untagged_state_uid_unproven',
              'foreign_uid_unproven', 'alternate_tmux_unproven')
PRODUCERS = ('coordinator', 'tiles_supervisor', 'daily_and_canary',
             'host_canary_launcher', 'resolver', 'shared_controller')


def unavailable(reason='code_unverified'):
    return dict(schema_version=1, status='unavailable', reason=reason,
                release_authority=False, history_authority=False,
                resource_absence_proven=False)


def _inventory(rows, uid):
    if not isinstance(rows, list) or len(rows) > MAX_PROCESSES:
        raise ValueError()
    seen = set()
    scopes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError()
        for key in ('pid', 'start_ticks', 'uid', 'ppid'):
            if type(row.get(key)) is not int or row[key] < (1 if key in ('pid', 'start_ticks') else 0):
                raise ValueError()
        if row['pid'] in seen:
            raise ValueError()
        seen.add(row['pid'])
        if (not isinstance(row.get('boot_id'), str)
                or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', row['boot_id'])
                or not isinstance(row.get('pid_namespace'), str)
                or not re.fullmatch(r'pid:\[[0-9]+\]', row['pid_namespace'])):
            raise ValueError()
        scopes.add((row['boot_id'], row['pid_namespace']))
        if row['uid'] == uid:
            if (not isinstance(row.get('tags'), dict)
                    or any(k not in TAG_KEYS or not isinstance(v, str) for k, v in row['tags'].items())
                    or not isinstance(row.get('exe'), str) or not isinstance(row.get('cwd'), str)
                    or not isinstance(row.get('argv'), list)
                    or any(not isinstance(v, bytes) for v in row['argv'])):
                raise ValueError()
    if os.getpid() not in seen or len(scopes) != 1:
        raise ValueError()
    # CPU counters are not identity. Sort and compare only retained metadata;
    # neither the digest nor any of its private inputs are projected.
    fingerprints = []
    for row in sorted(rows, key=lambda r: r['pid']):
        retained = {k: row[k] for k in ('pid', 'start_ticks', 'uid', 'ppid', 'boot_id', 'pid_namespace')}
        if row['uid'] == uid:
            retained.update({k: row[k] for k in ('tags', 'exe', 'cwd')})
            retained['argv_digest'] = hashlib.sha256(b'\0'.join(row['argv'])).hexdigest()
        fingerprints.append(_digest(retained))
    return _digest(fingerprints)


def _classify(rows, uid, active, server):
    by_pid = {r['pid']: r for r in rows}
    ancestors, pid = set(), os.getpid()
    while pid in by_pid and pid not in ancestors:
        ancestors.add(pid)
        pid = by_pid[pid]['ppid']
    counts = dict.fromkeys(CATEGORIES, 0)
    for row in rows:
        if row['uid'] != uid:
            kind = 'control_ancestry_observed' if row['pid'] in ancestors else 'foreign_uid_unproven'
        else:
            tags, argv = row['tags'], row['argv']
            if (row['pid'] == server and row['exe'] in {'/usr/bin/tmux', '/usr/local/bin/tmux'}
                    and argv and argv[0] == b'tmux: server' and all(not v for v in argv[1:])):
                kind = 'normal_tmux_server_observed'
            elif row['exe'] in {'/usr/bin/tmux', '/usr/local/bin/tmux'}:
                kind = 'alternate_tmux_unproven'
            elif any(b'nethack' in v.lower() and not (row['pid'] == os.getpid()
                    and v in {b'docich.nethack_admin_release', b'docich.nethack_admin_preflight'}) for v in argv):
                kind = 'explicit_nethack'
            elif tags:
                matched = (set(tags) == set(TAG_KEYS) and tags[TAG_KEYS[0]] == active['runtime_id']
                    and tags[TAG_KEYS[1]] == str(active['generation'])
                    and tags[TAG_KEYS[2]] in {'game', 'agent', 'adapter'})
                kind = ('control_ancestry_observed' if matched and row['pid'] == os.getpid()
                        else 'current_soren_tags_unproven' if matched else 'old_or_conflicting_tags')
            else:
                kind = 'control_ancestry_observed' if row['pid'] in ancestors else 'untagged_state_uid_unproven'
        counts[kind] += 1
    return counts


def _container_inventory(rows):
    if (not isinstance(rows, list) or len(rows) > 256
            or any(not isinstance(r, list) or len(r) != 2
                   or not isinstance(r[0], str) or not re.fullmatch('[0-9a-f]{64}', r[0])
                   or not isinstance(r[1], str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}', r[1]) for r in rows)
            or len({r[0] for r in rows}) != len(rows)):
        raise ValueError()
    return _digest(sorted(rows))


def _fence_file(root):
    """Only inspect the fixed inode; no creation, flock, read or repair."""
    parent = None
    try:
        parent = os.open(root / 'locks', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if os.fstat(parent).st_uid != root.stat().st_uid:
            return 'unsafe'
        info = os.stat('nethack-resource-fence.lock', dir_fd=parent, follow_symlinks=False)
        return ('safe_inode_observed' if stat.S_ISREG(info.st_mode)
                and info.st_uid == root.stat().st_uid and info.st_nlink == 1
                and not info.st_mode & 0o022 else 'unsafe')
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'unavailable'
    finally:
        if parent is not None:
            os.close(parent)


def preflight(root, soren, *, player, now, probe=None, tmux=None, container_probe=None):
    """Fixed failed-automatic reservation only; two bounded visible samples."""
    from .hanjuku_manual_cancel import _ProbeTmux
    result = unavailable('evidence_unproven')
    try:
        if type(now) not in (int, float) or not 0 <= now < 10**12:
            raise ValueError()
        root, soren = _safe_root(Path(root)), _safe_root(Path(soren))
        instant = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        def context():
            return _chain(root, read_record(root, ('nethack_corner.json',)),
                read_record(root, ('corner_rotation.json',)), player=player, now=instant)
        first_context = context()
        uid = root.stat().st_uid
    except Exception:
        return result
    tmux = tmux or _ProbeTmux()
    result.update(status='observed', reason='classification_only', reservation_context='matched',
        process_observation=dict(scope='visible_proc_only', host_scope_proven=False,
            traversal_complete=False, snapshot='unavailable', classification_basis='first_sample',
            categories=None),
        containers=dict(scope='fixed_local_socket_only', snapshot='unavailable',
            nethack_named=None, unclassified=None, name_is_ownership=False),
        old_tmux=dict(snapshot='unavailable', targets_present=None),
        producer_participation=dict(status='unproven', reason='live_participation_contract_absent',
            required=list(PRODUCERS), verified=0, live_proof_available=False,
            fence_file=_fence_file(root)))
    observation = result['process_observation']
    try:
        rows = (probe or processes)(uid)
        first = _inventory(rows, uid)
        server = tmux._server_pid()
        server = server if type(server) is int and server > 0 else None
        observation['categories'] = _classify(rows, uid, first_context['active_runtime'], server)
        observation['snapshot'] = 'single_sample'
        second = _inventory((probe or processes)(uid), uid)
        second_server = tmux._server_pid()
        second_server = second_server if type(second_server) is int and second_server > 0 else None
        observation.update(traversal_complete=True,
            snapshot='stable' if first == second and server == second_server else 'changed')
    except Exception:
        # Partial first-sample categories remain visibly incomplete; never turn
        # a missing or malformed scan into an empty successful inventory.
        pass
    try:
        rows = (container_probe or containers)()
        first = _container_inventory(rows)
        named = sum(r[1].startswith('docich-nh-canary-') for r in rows)
        result['containers'].update(nethack_named=named, unclassified=len(rows)-named, snapshot='single_sample')
        second = _container_inventory((container_probe or containers)())
        result['containers']['snapshot'] = 'stable' if first == second else 'changed'
    except Exception:
        pass
    try:
        def old_targets():
            values = []
            for source in (first_context['original_source'], first_context['rollback_source']):
                generation = source['generation']
                values.extend(tmux.window_target_exists(f'docich:{role}-g{generation}', strict=True)
                              for role in ('game', 'agent'))
                values.append(tmux.session_target_exists(f'docich-game-g{generation}', strict=True))
            if any(type(v) is not bool for v in values):
                raise ValueError()
            return values
        first, second = old_targets(), old_targets()
        result['old_tmux'].update(snapshot='stable' if first == second else 'changed', targets_present=sum(first))
    except Exception:
        pass
    try:
        if _digest(first_context) != _digest(context()):
            result.update(reason='context_changed', reservation_context='changed')
    except Exception:
        result.update(reason='context_unavailable', reservation_context='unavailable')
    return result
