#!/usr/bin/env python3
"""Token-free Hanjuku command bot: Observation JSON -> bounded pad actions.

Writes the policy memory, structured decision records and commentary
candidates into the generation's runtime directory. It never calls a model,
provider, network or the audio queue; delivery is a separate side channel.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from docich.game_switch import atomic_write_json
from docich.hanjuku_bot import BOT_VERSION, decide
from docich.hanjuku_commentary import SPOKEN, compose
from docich.hanjuku_pixels import read_png
from docich.hanjuku_run import append_log
from docich.retroarch_boundary import read_record


def persist(runtime: Path, state: dict, records: list, obs_meta: dict, *, actions, frame_sha256, frame=None):
    now=time.time()
    identity={k:(obs_meta.get('hanjuku') or {}).get(k) for k in ('game','runtime_id','generation','lease_id')}
    decision_id=f"{identity['runtime_id']}:{identity['generation']}:{state.get('step')}"
    state['decision_trace']={**identity, 'decision_id': decision_id, 'frame_sha256': frame_sha256}
    policy=state.get('policy') or {}
    snapshot=None
    card_flow=(policy.get('battle') or {}).get('card_flow')
    capture=bool(card_flow) or any(
        r.get('decision') in {'name_confirm','chapter_seen','battle_result','barrier_removed'}
        or str(r.get('decision','')).startswith('battle_card') for r in records)
    if frame is not None and capture:
        directory=runtime/'hanjuku_frames'
        if directory.is_symlink():
            raise ValueError('unsafe frame directory')
        directory.mkdir(exist_ok=True)
        snapshot=f"decision-{int(state.get('step',0))%120:03d}.png"
        target=directory/snapshot
        if target.is_symlink():
            raise ValueError('unsafe frame snapshot')
        target.write_bytes(frame.png_bytes())
    append_log(runtime,'hanjuku_decisions',{
        'schema':1,'event':'action_plan','at':now,'bot_version':BOT_VERSION,**identity,
        'decision_id':decision_id,'frame_sha256':frame_sha256,'snapshot':snapshot,
        'screen_kind':state.get('screen_kind'),'chart_step':policy.get('active'),
        'strategy_variant':policy.get('variant'),'planned_actions':actions,
        'reason_decisions':[r.get('decision') for r in records],
        'dispatch_status':'planned_not_yet_sent'})
    for record in records:
        payload={'schema':1,'event':'decision','at':now,'bot_version':BOT_VERSION,
                 'step':state.get('step'),'screen_kind':state.get('screen_kind'),
                 'phase':state.get('phase'),'decision_id':decision_id,
                 'frame_sha256':frame_sha256,'snapshot':snapshot,**identity,**record}
        append_log(runtime,'hanjuku_decisions',payload)
        if record.get('decision') not in SPOKEN:
            continue
        key,text=compose(record)
        seq=int(state.get('commentary_seq',0))+1
        state['commentary_seq']=seq
        append_log(runtime,'hanjuku_commentary',{
            'schema':1,'seq':seq,'at':now,'key':key,'text':text,
            'status':'candidate' if text else 'held',
            'held_reason':None if text else '状況判定保留',
            'decision':record.get('decision'),'chart_step':record.get('chart_step'),
            'strategy_variant':record.get('strategy_variant'),'reason':record.get('reason'),
            **identity})


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
            records=state.pop('_records',[])
            persist(runtime,state,records,meta,actions=actions,frame_sha256=frame.digest(),frame=frame)
            atomic_write_json(runtime/'hanjuku_bot.json',state)
    except (KeyError,TypeError,ValueError,OSError):
        code=2
        print('hanjuku-bot: invalid observation',file=sys.stderr)
    except Exception as exc:
        # A policy defect must not crash the agent loop or send guesses:
        # hold input for this observation and record only the error class.
        code=2
        actions=[]
        print(f'hanjuku-bot: policy_error {type(exc).__name__}',file=sys.stderr)
    print(json.dumps({'actions':actions}),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
