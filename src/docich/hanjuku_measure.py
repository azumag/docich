"""Measure Hanjuku castle coordinates from labeled map frames (stdlib only).

Chapter 1 was measured in an isolated emulator. Later chapters need the same
chart facts before ``hanjuku_chart.orders()`` unlocks navigation. This tool
takes PNG frames where the free map cursor sits on a known castle (by label),
seeds a chapter-local world frame from the first sample, then tracks
differential cursor motion and re-anchors on castle roofs to place every
labeled castle. Output is a ``CASTLES[chapter]`` dict ready to paste into
``hanjuku_chart`` or to merge at runtime.

No ROM, RAM or network access. Frames must be native or resized captures the
bot's parser already understands (256x224 RGB after ``Frame.resized``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .hanjuku_pixels import read_png
from .hanjuku_screen import castle_roofs, parse

EDGE_X = (8, 232)
EDGE_Y = (8, 200)
ARRIVE_PX = 4
# A lone roof far from the prediction is ambiguous until a second roof agrees.
MIN_SUPPORT_WITH_PREDICTION = 2


def _cursor(screen):
    return screen.marker or screen.cursor


def _at_edge(value, bounds):
    return value <= bounds[0] + 1 or value >= bounds[1] - 1


def _localize(roofs, castles, predicted_cam):
    """Same constellation vote as policy._localize, used offline on frames."""
    best = None
    for roof in roofs:
        for name, (cx, cy) in castles.items():
            cam = (cx - roof['target'][0], cy - roof['target'][1])
            support = 0
            for other in roofs:
                wx, wy = other['target'][0] + cam[0], other['target'][1] + cam[1]
                if any(abs(wx - x) + abs(wy - y) <= 12 for x, y in castles.values()):
                    support += 1
            drift = (abs(cam[0] - predicted_cam[0]) + abs(cam[1] - predicted_cam[1])
                     if predicted_cam else 0)
            key = (support, -drift)
            if best is None or key > best[0]:
                best = (key, cam, name)
    if best is None:
        return None
    (support, neg_drift), cam, name = best
    if support < MIN_SUPPORT_WITH_PREDICTION and predicted_cam and -neg_drift > 80:
        return None
    return cam, name


def measure(chapter: int, samples, *, seed: tuple[int, int] | None = None,
            known: dict[str, tuple[int, int]] | None = None) -> dict[str, tuple[int, int]]:
    """Return castle name → world cursor cell for ``chapter``.

    ``samples`` is an ordered iterable of ``(frame, label)`` where ``label`` is
    the on-screen castle name under the free cursor (or ``None`` to only
    advance the camera estimate). The first labeled sample seeds the world
    frame: with ``seed`` that absolute cell is used, otherwise the sample's
    screen cursor becomes the origin (chapter-local coordinates only need to
    be self-consistent). ``known`` preloads already-measured castles.
    """
    castles: dict[str, tuple[int, int]] = dict(known or {})
    world: list[int] | None = None
    last_screen: list[int] | None = None
    uncertain = True
    seeded = bool(castles)

    for item in samples:
        if isinstance(item, tuple):
            frame, label = item
        else:
            frame, label = item, None
        if isinstance(frame, (str, Path)):
            frame = read_png(Path(frame))
        if frame.width != 256 or frame.height != 224:
            frame = frame.resized()
        screen = parse(frame, phase='field')
        s = _cursor(screen)
        if not s:
            continue

        if world is not None and last_screen is not None and not uncertain:
            if any(
                _at_edge(s[axis], bounds) or _at_edge(last_screen[axis], bounds)
                for axis, bounds in ((0, EDGE_X), (1, EDGE_Y))
            ):
                uncertain = True
            else:
                world = [
                    world[0] + s[0] - last_screen[0],
                    world[1] + s[1] - last_screen[1],
                ]

        box = (s[0] - 2, s[1] - 2, s[0] + 18, s[1] + 18)
        roofs = [r for r in castle_roofs(frame, exclude=box) if not r['clipped']]
        predicted = (world[0] - s[0], world[1] - s[1]) if world else None
        found = _localize(roofs, castles, predicted)
        if found:
            cam, _anchor = found
            world = [cam[0] + s[0], cam[1] + s[1]]
            uncertain = False

        if label:
            if label in castles:
                world = [castles[label][0], castles[label][1]]
                uncertain = False
                seeded = True
            elif not seeded:
                origin = seed if seed is not None else (s[0], s[1])
                world = [origin[0], origin[1]]
                castles[label] = (world[0], world[1])
                uncertain = False
                seeded = True
            elif world is not None and not uncertain:
                castles[label] = (world[0], world[1])

        last_screen = list(s)

    return castles


def measure_paths(chapter: int, pairs, **kwargs) -> dict[str, tuple[int, int]]:
    """``pairs``: iterable of ``(path, label)`` filesystem samples."""
    return measure(chapter, ((Path(p), label) for p, label in pairs), **kwargs)


def emit_python(chapter: int, castles: dict[str, tuple[int, int]]) -> str:
    rows = ',\n'.join(f"        {name!r}: ({xy[0]}, {xy[1]}),"
                      for name, xy in sorted(castles.items()))
    return f"    {chapter}: {{\n{rows}\n    }},"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Measure Hanjuku castle coordinates from labeled PNG frames')
    parser.add_argument('chapter', type=int, help='chapter number (2-12)')
    parser.add_argument('samples', nargs='+',
                        help='PATH=CastleName pairs in walk order, or PATH alone to only track')
    parser.add_argument('--seed', metavar='X,Y', help='absolute world cell of the first labeled castle')
    parser.add_argument('--known', metavar='JSON', help='JSON object of already measured castles')
    parser.add_argument('--format', choices=('python', 'json'), default='python')
    args = parser.parse_args(argv)

    pairs = []
    for raw in args.samples:
        if '=' in raw:
            path, label = raw.split('=', 1)
        else:
            path, label = raw, None
        pairs.append((path, label))
    seed = None
    if args.seed:
        x, y = args.seed.split(',')
        seed = (int(x), int(y))
    known = json.loads(args.known) if args.known else None
    castles = measure_paths(args.chapter, pairs, seed=seed, known=known)
    if args.format == 'json':
        print(json.dumps({str(k): list(v) for k, v in castles.items()},
                         ensure_ascii=False, indent=2))
    else:
        print(emit_python(args.chapter, castles))
    return 0


if __name__ == '__main__':
    sys.exit(main())
