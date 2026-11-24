"""Reproduce and calibrate the clash gauges (連打ゲージ) from saved frames.

The battle panel keeps one mash gauge under each HP field. gcgx battle.html:
ぶつかった際にバーが青くなる — the bar turns blue in the contact window, and
mashing A then pushes harder. The bot reads the bars through
``hanjuku_screen`` (geometry and red/green states are measured); the blue
contact palette is not: no archived live frame caught the window, so
``CLASH_GAUGE_BLUE`` starts empty and the parser never reports ``'blue'``
until this tool calibrates it from real frames.

Usage (offline, against a runtime ``hanjuku_frames/`` ring; the corner must
have run a melee so the ring holds battle frames):

    python3 -m docich.hanjuku_clash_gauge <frames_dir>            # report
    python3 -m docich.hanjuku_clash_gauge <frames_dir> --json    # machine form
    python3 -m docich.hanjuku_clash_gauge <frames_dir> \
        --blue-calibrate frame-031.png:enemy frame-032.png:ally  # propose blue

``--blue-calibrate`` takes ``<png>:<side>`` samples that the operator marked
as contact-window (blue) bars. The proposal is the dominant tone of each
sample that the measured red/green/dark-red tables do not already explain,
ready to paste into ``CLASH_GAUGE_BLUE``. No ROM, RAM or network access; the
frames are the bot's own PNG ring captures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .hanjuku_pixels import read_png
from .hanjuku_screen import parse


def _known_tables():
    from .hanjuku.os_layer import screen
    return {'red': screen.CLASH_GAUGE_RED, 'green': screen.CLASH_GAUGE_GREEN,
            'dark_red': screen.CLASH_GAUGE_DARK_RED, 'blue': screen.CLASH_GAUGE_BLUE}


def _frame_dir(paths) -> list[Path]:
    """Every readable PNG of a hanjuku_frames ring, in name order."""
    files = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir()
                               if p.is_file() and p.suffix == '.png'))
        else:
            files.append(path)
    return files


def gauge_report(paths) -> list[dict]:
    """Per-frame gauge observations for the given frames/directories.

    Only frames whose battle panel is readable contribute, exactly like the
    live parser: the report is what the bot would observe for each saved
    frame, so a contact-window frame in the ring reproduces here.
    """
    out = []
    for path in _frame_dir(paths):
        screen = parse(read_png(path))
        if screen.battle is None:
            continue
        row = {'frame': path.name,
               'enemy': screen.battle.enemy, 'enemy_hp': screen.battle.enemy_hp,
               'ally': screen.battle.ally, 'ally_hp': screen.battle.ally_hp,
               'gauges': {g.side: {'state': g.state, 'filled': g.filled,
                                   'box': list(g.box), 'tones': [list(t) for t in g.tones]}
                          for g in screen.clash_gauges}}
        out.append(row)
    return out


def calibrate_blue(samples, paths) -> dict:
    """Propose a CLASH_GAUGE_BLUE palette from operator-marked blue bars.

    A sample counts only when its frame's battle panel is readable and the
    named side's gauge is present; tones the measured tables already explain
    are refused so an uncalibrated proposal can never smuggle a known state
    into the blue table.
    """
    wanted = {}
    for sample in samples:
        name, _, side = sample.rpartition(':')
        if not name or side not in ('enemy', 'ally'):
            raise ValueError(f'blue sample must be <png>:<enemy|ally>, got {sample!r}')
        wanted.setdefault(name, []).append(side)
    proposal = {}
    for path in _frame_dir(paths):
        sides = wanted.get(path.name)
        if not sides:
            continue
        screen = parse(read_png(path))
        if screen.battle is None:
            raise ValueError(f'{path.name}: no readable battle panel for a blue sample')
        matched = False
        for gauge in screen.clash_gauges:
            if gauge.side not in sides:
                continue
            matched = True
            for tone in gauge.tones:
                explained = any(tuple(tone) in table for table in _known_tables().values())
                meta = proposal.setdefault(tuple(tone), {'explained': explained, 'count': 0})
                meta['count'] += 1
        if not matched:
            # The parser could not even see the marked bar (hidden or below
            # CLASH_GAUGE_MIN_FILL): calibrating from it would be a guess.
            raise ValueError(f'{path.name}: no readable {"/".join(sides)} gauge for a blue sample')
    return {'samples': samples,
            'tones': [dict(name=list(tone), **meta) for tone, meta in proposal.items()]}


def _emit_python(tones) -> str:
    listed = ', '.join(f'({", ".join(str(c) for c in tone)})' for tone in tones)
    return f'CLASH_GAUGE_BLUE = ({listed},)'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('paths', nargs='+', type=Path,
                        help='hanjuku_frames directory (or individual PNG frames)')
    parser.add_argument('--json', action='store_true', help='JSON report')
    parser.add_argument('--blue-calibrate', metavar='PNG:SIDE', nargs='+',
                        help='operator-marked contact-window bars to propose the blue palette')
    args = parser.parse_args(argv)
    if args.blue_calibrate:
        calibration = calibrate_blue(args.blue_calibrate, args.paths)
        fresh = sorted(tuple(tone['name']) for tone in calibration['tones'] if not tone['explained'])
        print(json.dumps(calibration, ensure_ascii=False, indent=1))
        if fresh:
            print('paste into hanjuku/os_layer/screen.py once verified against the frames:')
            print(_emit_python(fresh))
        else:
            print('no unexplained tones: every marked sample matches a measured table',
                  file=sys.stderr)
        return 0
    report = gauge_report(args.paths)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    for row in report:
        gauges = ' '.join(f"{side}={meta['state']}@{meta['filled']:.2f}"
                         for side, meta in row['gauges'].items())
        print(f"{row['frame']}: {row['enemy']} {row['enemy_hp']} vs "
              f"{row['ally']} {row['ally_hp']} | {gauges}")
    print(f'{len(report)} battle frames', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
