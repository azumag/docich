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
    args = parser.parse_args()
    if not 0 <= args.takeoff <= .35:
        parser.error('takeoff must be between 0 and .35 seconds')
    source = subprocess.check_output(['git', 'show', REVISION + ':src/docich/flyhome/mpc.py']).decode('utf-8')
    source += f'''

# Local real-game comparison; original controller source from Git {REVISION}.
_Baseline = MpcController
from .mpc import MpcController as _Current
class MpcController(_Baseline, _Current):
    profile_name = "{REVISION}-actual-level{args.level}-takeoff{args.takeoff:g}"
    def __init__(self, physics=None, cfg=None):
        super().__init__(physics, MpcConfig(takeoff_s={args.takeoff!r}))
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
