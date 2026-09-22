"""docich-owned comment classification (#882 / #829).

``classify_file(path)`` is the single entry: the local heuristic baseline
always runs first; when ``COMMENT_CLASSIFIER_BACKEND=jev`` the Jev purpose may
replace individual categories through the reviewed semantic-decision core.
Any Jev failure leaves the heuristic rows in place. Consumers (the live chat
worker via ``bin/docich-comment-classify``) hold no classification logic.
"""
from __future__ import annotations

import os
from pathlib import Path
import time

from . import heuristic, jev


def classify_file(path, *, env=None, transport=None):
    """Return ``(rows, event)``; ``event`` is None unless Jev was selected.

    Raises ValueError only when the batch itself is unusable (missing/empty),
    in which case there is no classification to fall back to.
    """
    env = os.environ if env is None else env
    started = time.monotonic()
    lines = heuristic.read_comment_lines(Path(path))
    rows = heuristic.baseline(lines)
    heuristic_ms = (time.monotonic() - started) * 1000
    if env.get('COMMENT_CLASSIFIER_BACKEND') != 'jev':
        return rows, None
    try:
        return jev.run_jev(rows, env=env, heuristic_ms=heuristic_ms, started=started,
                           transport=transport or jev.docich_transport)
    except Exception:
        # A Jev-side defect must never cost the batch its classification.
        return rows, None
