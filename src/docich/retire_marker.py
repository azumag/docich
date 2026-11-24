"""Common retire marker for completed runtime generations (docich#3122).

Every corner's runtime generation leaves a ``runtimes/<id>-*`` directory
behind when the game switch retires it, but only hanjuku writes a terminal
run record. Reclaim helpers therefore had no game-agnostic way to tell a
completed generation from a merely abandoned one (weather/yosou/lawsim
profiles could never pass the hanjuku-only gate).

``write_retire_marker`` is called from game-switch post-terminal
housekeeping, at the same point as ``_prune_hanjuku_logs``, once teardown is
confirmed. It writes a tiny ``retired.json`` into the generation directory.
The marker is intentionally minimal so the standalone VM helper
(``ops/vm_actions/runtime_browser_profile_reclaim.py``) can verify it with a
conservative mirror check and no imports from this package:

    {"schema": 1, "kind": "runtime_retired", "runtime_id": ..., "generation": ...}

Generations retired before the marker existed carry no marker; their backlog
handling is a separate decision (see the helper's ``--include-legacy``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .naming import RUNTIME_ID_PATTERN, runtime_directory

MARKER_FILE = 'retired.json'
MARKER_SCHEMA = 1
MARKER_KIND = 'runtime_retired'


class RetireMarkerError(Exception):
    """Fixed-code failure; never carries paths or state content."""


def marker_path(state_dir: Path, runtime_id: str) -> Path:
    return runtime_directory(Path(state_dir), runtime_id) / MARKER_FILE


def write_retire_marker(state_dir: Path, runtime_id: str) -> Path:
    """Write the common retire marker into a completed generation directory.

    Atomic (same-directory temp + rename) so a crash can never leave a torn
    marker that a reader might mistake for a valid one. Idempotent: repeated
    teardown retries rewrite the identical document. The runtime id must be
    canonical-shaped; the generation number is taken from the id itself so
    the marker cannot disagree with the directory name.
    """
    match = RUNTIME_ID_PATTERN.fullmatch(runtime_id)
    if match is None:
        raise RetireMarkerError('invalid_runtime_id')
    generation = int(match[1])
    runtime_dir = runtime_directory(Path(state_dir), runtime_id)
    if runtime_dir.is_symlink() or not runtime_dir.is_dir():
        raise RetireMarkerError('runtime_missing')
    document = {
        'schema': MARKER_SCHEMA,
        'kind': MARKER_KIND,
        'runtime_id': runtime_id,
        'generation': generation,
    }
    payload = json.dumps(document, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    temp = runtime_dir / f'.{MARKER_FILE}.{os.getpid()}.tmp'
    try:
        temp.write_bytes(payload)
        os.replace(temp, runtime_dir / MARKER_FILE)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RetireMarkerError('marker_write') from None
    return runtime_dir / MARKER_FILE
