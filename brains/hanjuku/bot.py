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
from docich.hanjuku_commentary import COMMENTARY_VERSION, SPOKEN, compose, evidence_kind
from docich.hanjuku_pixels import read_png
from docich.hanjuku_run import append_log
from docich.retroarch_boundary import read_record

BOT_STATE_LIMIT = 256 * 1024   # the bot's own memory file, not a boundary record


# Chart/interim/hold decisions are the primary evidence for stall diagnosis
# (g530: the 05:26-06:0x sortie stop left no trace once the decisions log
# rotated). They are mirrored into a separate bounded log so a long run's
# tail rotation cannot erase them.
CHART_DECISIONS=frozenset({
    'chart_adjust_request','chart_adjust_applied',
    'chart_interim_order','chart_interim_hold',
})

# Only records that explain this observation's planned input/hold may override
# the active battle. Observation, migration and result records are not actions.
INPUT_CONTEXT_DECISIONS=frozenset({
    'battle_survival','battle_survival_select','battle_survival_unavailable',
    'egg_recover_select','egg_recover_confirm','egg_recover_skip',
    'battle_okunote_scroll','battle_okunote_select','battle_okunote_risk_declined',
    'battle_hero_retreat_open','battle_hero_retreat_select',
    'battle_hero_retreat_unavailable','battle_hero_retreat_cancel_card',
    'name_wait','name_confirm','name_delete','name_type','order_start',
    'unexpected_target','order_substitute','order_source_changed','order_launched','order_failed',
    'attack_observed','defense_observed','card_missing',
    'card_pick','sortie_confirm','sortie_input','battle_card','battle_card_missing','battle_card_selected','battle_card_list_unclassified','battle_card_open_unclassified',
    'barrier_removed','month_plan','month_confirm','month_done','buy_skip','buy',
    'soldier_refill','prompt','egg_battle','gift','close_panel','situation_held',
    'chart_adjust_request','chart_adjust_applied','independent_menu',
    'camp_recall_cursor','camp_menu_unread','camp_recall_requested','camp_recall_unconfirmed',
})


def chart_decision_summary(record: dict, *, now: float, identity: dict, decision_id: str) -> dict:
    """Bounded summary of a chart/interim/hold decision for the persistent log.

    Keeps the fields stall diagnosis needs (kind, request, choice, sortie,
    reason) without the full observation payload, so the mirror log stays
    small across a whole run.
    """
    return {
        'schema': 1, 'event': 'chart_decision', 'at': now,
        'decision_id': decision_id,
        'decision': record.get('decision'),
        'chart_step': record.get('chart_step'),
        'strategy_variant': record.get('strategy_variant'),
        'request_id': record.get('request_id'),
        'choice': record.get('choice'),
        'general': record.get('general'),
        'source': record.get('source'),
        'target': record.get('target'),
        'purpose': record.get('purpose'),
        'reason': record.get('reason'),
        'deviation_reason': record.get('deviation_reason'),
        **identity,
    }


def persist(runtime: Path, state: dict, records: list, obs_meta: dict, *, actions, frame_sha256, frame=None):
    now=time.time()
    identity={k:(obs_meta.get('hanjuku') or {}).get(k) for k in ('game','runtime_id','generation','lease_id')}
    decision_id=f"{identity['runtime_id']}:{identity['generation']}:{state.get('step')}"
    state['decision_trace']={**identity, 'decision_id': decision_id, 'frame_sha256': frame_sha256}
    policy=state.get('policy') or {}
    recall = policy.get('recall') or {}
    if recall.get('stage') == 'await_dispatch' and not recall.get('request_trace'):
        recall['request_trace'] = {**state['decision_trace'], 'planned_at': now}
    # A durable maximum survives log rotation, policy resets and corner teardown.
    # Prediction bookkeeping must not drop an otherwise valid gameplay action.
    try:
        from docich.hanjuku_progress import record as record_progress
        record_progress(runtime, identity, policy, records, frame_sha256)
    except Exception:
        print('hanjuku-bot: progress_record_failed', file=sys.stderr)
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
    capture=(bool(card_flow) or (bool(battle.get('survival')) and state.get('screen_kind') == 'battle_menu') or state.get('screen_kind') in {'general_list','card_select','sortie_confirm'} or any(
        r.get('decision') in {'name_confirm','chapter_seen','battle_result','barrier_removed',
                              'card_pick','card_missing','sortie_confirm','order_substitute',
                              'order_launched_unconfirmed','sortie_arrival_confirmed',
                              'sortie_departed_observed','sortie_cancelled_observed',
                              'camp_recall_cursor','camp_menu_unread','camp_recall_requested','camp_recall_unconfirmed',
                              'house_dispatch_requested','house_arrival_seen',
                              'soldiers_seen','soldier_refill','soldier_refill_receipt','egg_priority_replan'}
        or (r.get('decision') == 'order_start' and r.get('cards'))
        or (r.get('decision') == 'situation_held' and r.get('screen') in {'card_select','sortie_confirm'})
        or str(r.get('decision','')).startswith(('battle_card','battle_survival','battle_okunote','battle_hero_retreat','egg_recover','chikujou')) for r in records))
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
        'observation_interval_ms':observation_interval_ms(state),
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
        if record.get('decision') in CHART_DECISIONS:
            append_log(runtime,'hanjuku_chart_decisions',
                       chart_decision_summary(record,now=now,identity=identity,
                                              decision_id=decision_id))
        if record.get('decision') not in SPOKEN:
            continue
        key,text=compose(record)
        seq=int(state.get('commentary_seq',0))+1
        state['commentary_seq']=seq
        append_log(runtime,'hanjuku_commentary',{
            'schema':1,'seq':seq,'at':now,'key':key,'text':text,
            'commentary_version':COMMENTARY_VERSION,
            'evidence_kind':evidence_kind(record), 'decision_id':decision_id,
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


def observation_interval_ms(state):
    """Short feedback for living melee and its in-flight rescue commands.

    g486: slowing to 1500 ms as soon as B planned a card let the Queen
    summon between the chained B and its next readable command menu.
    Keep the existing allowed 500 ms cycle across card-menu fades too.
    """
    policy=state.get('policy') or {}
    battle=policy.get('battle') or {}
    preempt=battle.get('okunote_egg_preempt') or {}
    living=all(type(battle.get(k)) is int and battle[k]>0 for k in ('ally_hp','enemy_hp'))
    if (living and not policy.get('egg_battle')
            and (state.get('screen_kind') == 'battle'
                 or (battle.get('card_flow') and state.get('screen_kind') in
                     {'unknown', 'text', 'battle_menu'})
                 or (((preempt.get('stage') in {'opening', 'menu', 'selected'}
                       and not preempt.get('exhausted')) or preempt.get('close_pending'))
                     and state.get('screen_kind') in
                     {'unknown', 'text', 'battle_menu', 'battle_menu_pending', 'okunote_menu'}))):
        return 500
    return 1500


def recall_input_receipts(runtime, state, meta):
    """Read only a bounded tail of the existing sender journal; never replay.

    This command process reloads on each observation. No controller restart
    or new controller receipt writer is required for the current live run.
    """
    recall = (state.get('policy') or {}).get('recall') or {}
    trace = recall.get('request_trace') or {}
    identity = meta.get('hanjuku') or {}
    keys = ('game', 'runtime_id', 'generation', 'lease_id')
    if (recall.get('stage') != 'await_dispatch' or not trace
            or any(identity.get(k) is None or trace.get(k) != identity[k] for k in keys)
            or not isinstance(trace.get('decision_id'), str)
            or not isinstance(trace.get('frame_sha256'), str)
            or len(trace['frame_sha256']) != 64
            or type(trace.get('planned_at')) not in (int, float)):
        return None
    count = 0
    seen = set()
    try:
        path = runtime / 'hanjuku_events.jsonl'
        if path.is_symlink() or not path.is_file():
            return None
        with path.open('rb') as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - 65536)
            stream.seek(offset)
            tail = stream.read(65536)
            if offset:
                tail = tail.partition(b'\n')[2]
            for raw in tail.splitlines():
                try:
                    row = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if (isinstance(row, dict) and row.get('event') == 'input_sent'
                        and row.get('decision_id') == trace.get('decision_id')
                        and row.get('decision_frame_sha256') == trace.get('frame_sha256')
                        and type(row.get('at')) in (int, float)
                        and 0 <= trace['planned_at'] <= row['at'] < 10**12
                        and row.get('type') == 'pad' and row.get('buttons') == ['a']
                        and row.get('hold_ms') == 100):
                    if row['at'] not in seen:
                        seen.add(row['at'])
                        count = min(2, count + 1)
    except OSError:
        return None
    return {'request_trace': trace, 'a_inputs': count}


def main():
    actions=[]
    interval_ms=1500
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
            # g438 04:18: the policy memory passed 16 KiB after a long game and
            # every observation failed to load it (no input, screen_stalled).
            state=read_record(runtime/'hanjuku_bot.json',limit=BOT_STATE_LIMIT)
            experience_path=ROOT/'run'/hanjuku_experience.EXPERIENCE_FILE
            experience=hanjuku_experience.load(experience_path)
            actions,state=decide(frame,state,adjusted=hanjuku_chart_adjust.load(runtime),
                                 interim=state.get('chart_interim_answer'),
                                 experience=experience,
                                 recall_inputs=recall_input_receipts(runtime, state, meta))
            interval_ms=observation_interval_ms(state)
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
    print(json.dumps({'actions':actions, 'observation_interval_ms':interval_ms}),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
