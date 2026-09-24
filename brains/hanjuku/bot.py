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
import tomllib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from docich.game_switch import atomic_write_json
from docich import hanjuku_chart_adjust
from docich import hanjuku_experience
from docich.hanjuku_bot import BOT_VERSION, decide
from docich.hanjuku_commentary import SPOKEN, compose
from docich.hanjuku_pixels import read_png
from docich.hanjuku_run import append_log
from docich.retroarch_boundary import read_record


# Only records that explain this observation's planned input/hold may override
# the active battle. Observation, migration and result records are not actions.
INPUT_CONTEXT_DECISIONS=frozenset({
    'name_wait','name_confirm','name_delete','name_type','order_start',
    'unexpected_target','order_substitute','order_source_changed','order_launched','order_failed',
    'attack_observed','defense_observed','card_missing',
    'card_pick','sortie_confirm','sortie_input','battle_card','battle_card_missing','battle_card_selected',
    'barrier_removed','month_plan','month_confirm','month_done','buy_skip','buy',
    'soldier_refill','prompt','egg_battle','gift','close_panel','situation_held',
    'chart_adjust_request','chart_adjust_applied','independent_menu',
})


def persist(runtime: Path, state: dict, records: list, obs_meta: dict, *, actions, frame_sha256, frame=None):
    now=time.time()
    identity={k:(obs_meta.get('hanjuku') or {}).get(k) for k in ('game','runtime_id','generation','lease_id')}
    decision_id=f"{identity['runtime_id']}:{identity['generation']}:{state.get('step')}"
    state['decision_trace']={**identity, 'decision_id': decision_id, 'frame_sha256': frame_sha256}
    policy=state.get('policy') or {}
    snapshot=None
    battle=policy.get('battle') if isinstance(policy.get('battle'),dict) else {}
    chart_step=battle.get('step') if battle else policy.get('active')
    strategy_variant=battle.get('strategy_variant') if battle else policy.get('variant')
    deviation_reason=battle.get('deviation_reason')
    input_context=next((r for r in reversed(records)
                        if r.get('decision') in INPUT_CONTEXT_DECISIONS),None)
    if input_context is not None:
        chart_step=input_context.get('chart_step',chart_step)
        strategy_variant=input_context.get('strategy_variant',strategy_variant)
        deviation_reason=input_context.get('deviation_reason')
    card_flow=battle.get('card_flow')
    capture=(bool(card_flow) or state.get('screen_kind') in {'general_list','card_select','sortie_confirm'} or any(
        r.get('decision') in {'name_confirm','chapter_seen','battle_result','barrier_removed',
                              'card_pick','card_missing','sortie_confirm','order_substitute'}
        or (r.get('decision') == 'order_start' and r.get('cards'))
        or (r.get('decision') == 'situation_held' and r.get('screen') in {'card_select','sortie_confirm'})
        or str(r.get('decision','')).startswith('battle_card') for r in records))
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
        'screen_kind':state.get('screen_kind'),'chart_step':chart_step,
        'strategy_variant':strategy_variant,'deviation_reason':deviation_reason,
        'expected_metric':input_context.get('expected_metric') if input_context is not None else battle.get('strategy_expected'),
        'planned_actions':actions,
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


def publish_adjust_request(runtime: Path, records: list, obs_meta: dict):
    """Hand an off-chart request to the asynchronous chart worker (never sends input)."""
    request=next((r for r in reversed(records) if r.get('decision')=='chart_adjust_request'),None)
    if request is None:
        return None
    identity={k:(obs_meta.get('hanjuku') or {}).get(k) for k in ('game','runtime_id','generation','lease_id')}
    return hanjuku_chart_adjust.write_request(runtime,request,identity)


def chart_adjust_settings():
    try:
        with (ROOT/'config/games/hanjuku-hero.toml').open('rb') as stream:
            raw=tomllib.load(stream)
        return hanjuku_chart_adjust.settings((raw.get('hanjuku') or {}).get('chart_adjust'))
    except (OSError,ValueError,tomllib.TOMLDecodeError):
        return hanjuku_chart_adjust.settings(None)


def ask_interim(runtime: Path, state: dict, obs_meta: dict, *, settings=None, ask=None):
    """Ask JEV once for the pending interim choice; the answer is applied next cycle."""
    policy=state.get('policy') or {}
    pending=policy.get('chart_adjust') or {}
    if not pending.get('interim_wanted'):
        return None
    previous=state.get('chart_interim_answer') or {}
    if (previous.get('request_id')==pending.get('request_id')
            and previous.get('seq')==pending.get('interim_count',0)):
        return None                  # answered; the policy applies it on the map
    settings=settings or chart_adjust_settings()
    if not settings['interim_jev']:
        return None
    if ask is None:
        from docich.hanjuku_interim import ask
    answer=ask(policy,timeout_ms=settings['interim_timeout_ms'])
    state['chart_interim_answer']=answer
    identity={k:(obs_meta.get('hanjuku') or {}).get(k) for k in ('game','runtime_id','generation','lease_id')}
    append_log(runtime,hanjuku_chart_adjust.HISTORY_LOG,
               {'event':'interim_answer','at':time.time(),**identity,**answer})
    return answer


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
            experience_path=ROOT/'run'/hanjuku_experience.EXPERIENCE_FILE
            experience=hanjuku_experience.load(experience_path)
            actions,state=decide(frame,state,adjusted=hanjuku_chart_adjust.load(runtime),
                                 interim=state.get('chart_interim_answer'),
                                 experience=experience)
            records=state.pop('_records',[])
            updated_experience=state.pop('_experience',None)
            persist(runtime,state,records,meta,actions=actions,frame_sha256=frame.digest(),frame=frame)
            publish_adjust_request(runtime,records,meta)
            ask_interim(runtime,state,meta)
            atomic_write_json(runtime/'hanjuku_bot.json',state)
            if isinstance(updated_experience,dict):
                try:
                    hanjuku_experience.save(experience_path,updated_experience)
                except (OSError,ValueError):
                    # Losing one experience sample must not drop this frame's input.
                    print('hanjuku-bot: experience_save_failed',file=sys.stderr)
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
