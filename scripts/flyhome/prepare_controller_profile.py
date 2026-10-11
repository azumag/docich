"""Prepare a local actual-game controller comparison from a historical Git revision.

Run from the repository root. This script creates no game input and contacts no VM.
The campaign applies the generated profile only to the explicitly named level.
"""
from pathlib import Path
import argparse
import subprocess
import sys
sys.path.insert(0, 'src')
from docich.flyhome.settings import load

REVISION = '0fe7235'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('level', type=int, choices=range(1, 51))
    parser.add_argument('--takeoff', type=float, default=.35)
    parser.add_argument('--revision', default=REVISION, help='Local Git commit to compare')
    parser.add_argument('--ceiling-y', type=float, help='Upper braking boundary for a compatible revision')
    parser.add_argument('--full-takeoff', action='store_true', help='Keep full takeoff below the upper screen on a compatible revision')
    args = parser.parse_args()
    if not 0 <= args.takeoff <= .35:
        parser.error('takeoff must be between 0 and .35 seconds')
    revision = subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options',
                                        args.revision + '^{commit}']).decode('ascii').strip()
    source = subprocess.check_output(['git', 'show', revision + ':src/docich/flyhome/mpc.py']).decode('utf-8')
    ceiling_arg = ''
    suffix = ''
    if args.ceiling_y is not None:
        if not 12 <= args.ceiling_y <= 24 or 'ceiling_y:' not in source:
            parser.error('ceiling-y requires 12..24 and a compatible controller revision')
        ceiling_arg = f', ceiling_y={args.ceiling_y!r}'
        suffix = f'-ceiling{args.ceiling_y:g}'
    if args.full_takeoff:
        narrow_cap = 'y < 40 or self._narrow_level'
        if source.count(narrow_cap) != 1:
            parser.error('full-takeoff requires a compatible narrow takeoff branch')
        source = source.replace(narrow_cap, 'y < 40')
        suffix += '-fulltakeoff'
    source += f'''

# Local real-game comparison; original controller source from Git {revision[:7]}.
_Baseline = MpcController
from .mpc import MpcController as _Current
class MpcController(_Baseline, _Current):
    profile_name = "{revision[:7]}-actual-level{args.level}-takeoff{args.takeoff:g}{suffix}"
    def __init__(self, physics=None, cfg=None):
        super().__init__(physics, MpcConfig(takeoff_s={args.takeoff!r}{ceiling_arg}))
    def reset(self):
        super().reset()
        self._home_settling = False
    def _estimate(self, state):
        result = super()._estimate(state)
        self._estimate_debug = [round(v, 3) for v in result[2:]]
        return result
'''
    path = load(None).run_dir / 'controller_profiles' / f'level{args.level}.py'
    path.parent.mkdir(parents=True, exist_ok=True)
    compile(source, str(path), 'exec')
    path.write_text(source, encoding='utf-8')
    print('Prepared local actual-game profile:', path)

if __name__ == '__main__':
    main()
