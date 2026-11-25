"""Manifest for one finished hanjuku-speedrun attempt.

Written next to the spool after the common corner teardown finalized the
attempt.  It is the small, human-readable index an archive step (a later PR)
can use without opening the logs:

* attempt identity and result,
* bot/chart versions used (the attempt may span a deploy),
* SHA-256 of ``config/games/hanjuku-speedrun.toml``,
* the docich commit SHA when ``git`` is available,
* start/end, PB update, dropped bytes,
* the spool inventory at build time (relative path and size; a
  pre-compression snapshot, marked by ``inventory``) and total size,
* ``archived: false`` for the not-yet-implemented R2 step.

A missing value is ``None`` (``null``); nothing is guessed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

from . import spool, store

SCHEMA = 1
CONFIG_PATH = Path('config') / 'games' / 'hanjuku-speedrun.toml'
GIT_TIMEOUT_SECONDS = 2.0
MAX_CONFIG_BYTES = 1024 * 1024
_SHA_PATTERN = re.compile(r'^[0-9a-f]{7,64}$')


def manifest_path(runtime, attempt_id):
    return spool.attempt_dir(runtime, attempt_id) / 'manifest.json'


def _int(value):
    return value if type(value) is int else None


def _repo_root():
    try:
        root = Path(__file__).resolve().parents[4]
    except (IndexError, OSError):
        return None
    return root if (root / 'config').is_dir() else None


def _config_sha256(root):
    if root is None:
        return None
    path = root / CONFIG_PATH
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _docich_commit(root):
    if root is None:
        return None
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    sha = (result.stdout or '').strip()
    if result.returncode != 0 or not _SHA_PATTERN.fullmatch(sha):
        return None
    return sha


def _attempt_source(runtime, attempt_id):
    timer = store.load_timer(runtime)
    if isinstance(timer, dict) and timer.get('attempt_id') == attempt_id:
        return timer
    for row in reversed(store.load_attempts(runtime)):
        if isinstance(row, dict) and row.get('attempt_id') == attempt_id:
            return row
    return {}


def _segments(runtime, attempt_id):
    path = spool.attempt_dir(runtime, attempt_id) / 'segments.json'
    document = spool.read_json(path)
    return document if isinstance(document, dict) else {}


def _bot_versions(source, segments):
    values = []
    for value in [source.get('bot_version')] + list(segments.get('bot_versions') or ()):
        if isinstance(value, str) and value and value not in values:
            values.append(value)
    for segment in segments.get('segments') or ():
        if not isinstance(segment, dict):
            continue
        for value in segment.get('bot_versions') or ():
            if isinstance(value, str) and value and value not in values:
                values.append(value)
    return values


def _chart_editions(source, segments):
    values = []
    for value in [source.get('chart_edition')] + list(segments.get('chart_editions') or ()):
        if isinstance(value, str) and value and value not in values:
            values.append(value)
    return values


def build_manifest(runtime, attempt_id):
    """Build and atomically write ``manifest.json``; returns the document."""
    runtime = Path(runtime)
    attempt_id = spool._component(attempt_id, 'attempt_id')
    source = _attempt_source(runtime, attempt_id)
    segments = _segments(runtime, attempt_id)
    root = _repo_root()
    files, total = spool.spool_files(runtime, attempt_id)
    dropped = spool.read_json(spool.dropped_path(runtime, attempt_id))
    document = {
        'schema': SCHEMA,
        'game': store.GAME,
        'attempt_id': attempt_id,
        'attempt_no': _int(source.get('attempt_no')),
        'runtime_id': source.get('runtime_id') if isinstance(source.get('runtime_id'), str) else None,
        'generation': _int(source.get('generation')),
        'status': source.get('status') if isinstance(source.get('status'), str) else None,
        'result': source.get('result') if isinstance(source.get('result'), str) else None,
        'started_at_ms': _int(source.get('started_at_ms')),
        'ended_at_ms': _int(source.get('ended_at_ms')),
        'total_ms': _int(source.get('total_ms')),
        'pb_updated': source.get('pb_updated') is True,
        'dnf_reason': source.get('dnf_reason') if isinstance(source.get('dnf_reason'), str) else None,
        'reason': source.get('reason') if isinstance(source.get('reason'), str) else None,
        'bot_versions': _bot_versions(source, segments),
        'chart_editions': _chart_editions(source, segments),
        'config_sha256': _config_sha256(root),
        'docich_commit': _docich_commit(root),
        'dropped': dropped if isinstance(dropped, dict) else None,
        'inventory': 'pre-compression snapshot',
        'spool_bytes_at_build': total,
        'spool_files_at_build': files,
        'archived': False,
        'built_at_ms': int(time.time() * 1000),
    }
    spool.write_json(manifest_path(runtime, attempt_id), document)
    return document
