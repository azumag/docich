#!/usr/bin/env python3
"""Compare two saved, sanitized diagnostics; never operate production."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat

LIMIT = 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _load(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
            raise ValueError('invalid evidence')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(LIMIT + 1)
        after = os.fstat(fd)
        if (len(raw) > LIMIT or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('changed evidence')
        value = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(value, dict):
            raise ValueError('invalid envelope')
        if 'diagnostics' in value:
            if value.get('status') != 'diagnosed' or not isinstance(value['diagnostics'], dict):
                raise ValueError('invalid envelope')
            value = value['diagnostics']
        return value
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        before, after = _load(args.before), _load(args.after)
        path = Path(__file__).resolve().parents[1] / 'vm_actions/collect_diagnostics.py'
        spec = importlib.util.spec_from_file_location('shared_pid_collector', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.compare_shared_runtime_pids(before, after)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        result = {'match': False, 'reason': 'unavailable_or_partial',
                  'mismatched_roles': ['shared_stream', 'encoder', 'audio']}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['match'] is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
