"""One bounded visual proof for an early return from an individual status.

The right roster survives behind the status panel. Only the same named row
and the exact left map seen before A prove that B has exposed the roster.
No status, membership, location or input receipt is inferred here.
"""
from __future__ import annotations

import hashlib

from . import menu_feedback
from .pixels import Frame
from .readers import _map_icon_centres


MAP_RECT = (16, 24, 154, 168)


def _map_hash(frame):
    if (not isinstance(frame, Frame) or type(frame.width) is not int or type(frame.height) is not int
            or (frame.width, frame.height) != (256, 224) or not isinstance(frame.rgb, (bytes, bytearray))
            or len(frame.rgb) != 256 * 224 * 3):
        return None
    left, top, right, bottom = MAP_RECT
    return hashlib.sha256(b''.join(
        frame.rgb[(y * frame.width + left) * 3:(y * frame.width + right) * 3]
        for y in range(top, bottom))).hexdigest()


def _roster(screen):
    reading = menu_feedback.reading(screen)
    return (reading is not None and reading.get('kind') == 'text'
            and reading.get('menu') == 'global_roster'
            and isinstance(screen.selected, str) and bool(screen.selected))


def capture(screen, frame):
    """Remember one informative roster map, never a blank/fading status pane."""
    digest = _map_hash(frame)
    if digest is None or not _roster(screen):
        return None
    left, top, right, bottom = MAP_RECT
    icons = [p for p in _map_icon_centres(frame)
             if left <= p[0] < right and top <= p[1] < bottom]
    if len(icons) < 2:
        return None
    return {'general': screen.selected, 'sha256': digest}


def matches(screen, frame, proof):
    """An early frame must restore the same map and the same selected person."""
    return (isinstance(proof, dict) and set(proof) == {'general', 'sha256'}
            and isinstance(proof.get('sha256'), str) and len(proof['sha256']) == 64
            and proof.get('general') == screen.selected and _roster(screen)
            and _map_hash(frame) == proof['sha256'])
