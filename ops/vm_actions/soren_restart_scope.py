#!/usr/bin/env python3
"""Classify a verified GitHub comparison without executing candidate code."""
import json
import re
import sys
from pathlib import Path

BGM_ONLY = frozenset({'bgm_worker.sh', 'tests/test_bgm_worker.py'})


def classify(data, before, after):
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (before, after)):
        raise ValueError('invalid revisions')
    if (data.get('base_commit', {}).get('sha') != before
            or data.get('merge_base_commit', {}).get('sha') != before
            or data.get('status') != 'ahead' or data.get('behind_by') != 0):
        raise ValueError('comparison is not an ordered update')
    commits = data.get('commits')
    if not isinstance(commits, list) or not commits or commits[-1].get('sha') != after:
        raise ValueError('comparison head is unverified')
    files = data.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('comparison file list unavailable')
    # GitHub caps this list at 300. Only a nonempty list of at most two exact
    # paths can opt out, so a truncated list can never suppress a restart.
    if len(files) <= len(BGM_ONLY) and all(
        item.get('filename') in BGM_ONLY and item.get('status') == 'modified'
        and 'previous_filename' not in item for item in files
    ):
        return 'bgm-only'
    return 'other'


def main(args):
    try:
        if len(args) != 4:
            return 2
        path = Path(args[1])
        if path.stat().st_size > 8 * 1024 * 1024:
            return 2
        print(classify(json.loads(path.read_text()), args[2], args[3]))
        return 0
    except (OSError, ValueError, TypeError, AttributeError):
        return 2  # No API response/error bodies are published.


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
