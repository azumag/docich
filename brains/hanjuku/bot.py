#!/usr/bin/env python3
"""Token-free Hanjuku command bot: Observation JSON -> bounded pad actions."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from docich.game_switch import atomic_write_json
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.retroarch_boundary import read_record


def main():
    actions=[]
    code=0
    try:
        obs=json.load(sys.stdin)
        if not isinstance(obs,dict) or obs.get('game')!='hanjuku-hero':
            raise ValueError('wrong game')
        meta=obs.get('meta') or {}
        runtime=Path(meta['runtime_dir'])
        relative=runtime.resolve().relative_to(ROOT.resolve())
        if len(relative.parts)!=3 or relative.parts[1]!='runtimes' or runtime.is_symlink():
            raise ValueError('invalid runtime')
        if not meta.get('terminal_reason') and not meta.get('terminal_candidate'):
            frame=read_png(Path(obs['screenshot'])).resized()
            state=read_record(runtime/'hanjuku_bot.json')
            actions,state=decide(frame,state)
            atomic_write_json(runtime/'hanjuku_bot.json',state)
    except (KeyError,TypeError,ValueError,OSError):
        code=2
        print('hanjuku-bot: invalid observation',file=sys.stderr)
    print(json.dumps({'actions':actions}),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
