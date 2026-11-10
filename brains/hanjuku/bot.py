#!/usr/bin/env python3
"""Token-free Hanjuku command bot: Observation JSON -> bounded pad actions.

Writes policy memory and observed scene facts into this generation's runtime.
It may start one separately locked scene worker, but never waits for a model,
provider, network or audio queue. Failed narration never changes game input.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time
import traceback
import tomllib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from docich.game_switch import atomic_write_json
from docich import hanjuku_chart_adjust
from docich import hanjuku_experience
from docich import hanjuku_resume
from docich.hanjuku_bot import BOT_VERSION, decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku.decision_log import _record
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku_run import append_log
from docich.hanjuku.os_layer.commands import menu_navigation_feedback, status_panel_feedback
from docich.retroarch_boundary import read_record

BOT_STATE_LIMIT = 256 * 1024   # the bot's own memory file, not a boundary record


# Chart/interim/hold decisions are the primary evidence for stall diagnosis
# (g530: the 05:26-06:0x sortie stop left no trace once the decisions log
# rotated). They are mirrored into a separate bounded log so a long run's
# tail rotation cannot erase them.
CHART_DECISIONS=frozenset({
    'chart_adjust_request','chart_adjust_applied',
    'chart_interim_order','chart_interim_hold',
    # The A/B edition of the run must survive decision-log rotation.
    'chart_edition_pinned',
})

# Only records that explain this observation's planned input/hold may override
# the active battle. Observation, migration and result records are not actions.
INPUT_CONTEXT_DECISIONS=frozenset({
    'starter_select','starter_confirm','starter_underfilled_rejected',
    'starter_unread','starter_navigation','starter_navigation_stalled',
    'battle_command_open_wait',
    'battle_card_damage_rejected','battle_card_damage_replan','battle_card_damage_recheck',
    'battle_survival','battle_survival_select','battle_survival_unavailable','battle_egg_before_retreat',
    'egg_recover_select','egg_recover_confirm','egg_recover_skip',
    'battle_okunote_scroll','battle_okunote_select','battle_okunote_risk_declined','battle_okunote_risk_accepted','battle_okunote_risk_conserved',
    'battle_okunote_summon_scroll','battle_okunote_summon_unconfirmed_exhausted','battle_okunote_committed_unread','battle_okunote_parent_resend',
    'battle_hero_retreat_open','battle_hero_retreat_select',
    'battle_hero_retreat_unavailable','battle_hero_retreat_cancel_card',
    'name_wait','name_confirm','name_delete','name_type','order_start',
    'unexpected_target','order_substitute','order_source_changed','order_launched','order_failed',
    'attack_observed','field_encounter_observed','resume_sortie_cancel','defense_observed','card_missing',
    'card_pick','sortie_confirm','sortie_input','battle_card','battle_card_missing','battle_card_selected','battle_card_list_unclassified','battle_card_open_unclassified',
    'barrier_removed','month_plan','month_confirm','month_done','buy_skip','buy',
    'soldier_refill','prompt','egg_battle','gift','close_panel','situation_held',
    'sortie_departure_requested','sortie_departure_input_sent','sortie_departure_resend','sortie_departure_not_accepted',
    'sortie_launch_confirmed','sortie_launch_unverified','sortie_launch_failed',
    'chart_adjust_request','chart_adjust_applied','independent_menu',
    'camp_recall_cursor','camp_menu_unread','camp_recall_requested','camp_recall_unconfirmed',
    'camp_recall_picker_step','camp_found','camp_enter','camp_group_screen',
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


#: How much of the decisions log tail is scanned for an identical record.
#: The bot process reloads per observation, so state/mem signatures cannot
#: suppress a caller that replays the policy on a copy (its chart_plan /
#: y_jumps never persist, so the same record re-emits every cycle). The log
#: itself is the only durable, shared artifact, so dedupe happens here.
DECISION_DEDUPE_TAIL_BYTES = 65536


def _decision_tail(runtime: Path):
    """Recent decision rows of this runtime's log; empty when unreadable."""
    path = runtime / 'hanjuku_decisions.jsonl'
    try:
        if path.is_symlink() or not path.is_file():
            return []
        with path.open('rb') as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - DECISION_DEDUPE_TAIL_BYTES)
            stream.seek(offset)
            tail = stream.read()
        if offset:
            tail = tail.partition(b'\n')[2]
    except OSError:
        return []
    rows = []
    for raw in tail.splitlines():
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _recorded_before(rows, record, identity):
    """Deduplicate exact records only within the same runtime generation.

    A missing key is not equivalent to a key explicitly set to None.
    Different generations may legitimately repeat the same chart decision.
    """
    return any(
        row.get('event') == 'decision'
        and all(row.get(key) == value for key, value in identity.items())
        and all(key in row and row[key] == value for key, value in record.items())
        for row in rows
    )


def persist(runtime: Path, state: dict, records: list, obs_meta: dict, *, actions, frame_sha256, frame=None):
    now=time.time()
    identity={k:(obs_meta.get('hanjuku') or {}).get(k) for k in ('game','runtime_id','generation','lease_id')}
    decision_id=f"{identity['runtime_id']}:{identity['generation']}:{state.get('step')}"
    state['decision_trace']={**identity, 'decision_id': decision_id, 'frame_sha256': frame_sha256}
    policy=state.get('policy') or {}
    recall = (policy.get('flow') or {}).get('recall') or {}
    if recall.get('stage') == 'await_dispatch' and not recall.get('request_trace'):
        recall['request_trace'] = {**state['decision_trace'], 'planned_at': now}
    boundary = policy.get('card_upper_boundary') or {}
    if (boundary.get('await_input') is True and actions == [{'type': 'pad', 'buttons': ['up'], 'hold_ms': 100}]):
        boundary['request_trace'] = {**state['decision_trace'], 'planned_at': now}
    request = (policy.get('flow') or {}).get('departure_request') or {}
    if (request.get('status') == 'requested' and not request.get('request_trace')
            and actions == [CONFIRM]):
        request['request_trace'] = {**state['decision_trace'], 'planned_at': now}
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
    # battle_result is recorded after leaving combat and may already show
    # the map. Keep combat pixels in the same bounded ring, including holds
    # and enemy-owned menus, without claiming an input or a battle outcome.
    capture=(menu_navigation_feedback(state, actions) or status_panel_feedback(state, actions) or bool(card_flow)
             or bool((policy.get('map_discovery') or {}).get('probe'))
             or any(r.get('decision') in {'map_bootstrap_started', 'map_bootstrap_exhausted',
                                         'map_search_candidate', 'map_castle_confirmed',
                                         'map_registration_rejected', 'map_reanchored'} for r in records)
             or (bool(battle.get('survival')) and state.get('screen_kind') == 'battle_menu') or state.get('screen_kind') in {'general_list','card_select','sortie_confirm','battle','egg_battle_menu','monster_menu'} or any(
        r.get('decision') in {'name_confirm','chapter_seen','battle_result','barrier_removed','field_encounter_observed','resume_sortie_cancel',
                              'card_pick','card_missing','sortie_confirm','order_substitute',
                              'order_launched_unconfirmed','sortie_arrival_confirmed',
                              'sortie_departed_observed','sortie_cancelled_observed',
                              'camp_recall_cursor','camp_menu_unread','camp_recall_requested','camp_recall_unconfirmed',
                              'camp_recall_picker_step','camp_found','camp_enter','camp_group_screen',
                              'recall_arrival_status_started','recall_in_castle_observed',
                              'battle_survival_critical_recheck',
                              'house_dispatch_requested','house_arrival_seen',
                              'soldiers_seen','soldier_refill','soldier_refill_receipt','soldier_refill_outcome',
                              'egg_priority_replan'}
        or (r.get('decision') == 'order_start' and r.get('cards'))
        or (r.get('decision') == 'situation_held' and r.get('screen') in {'card_select','sortie_confirm'})
        or str(r.get('decision','')).startswith(('starter_','battle_card','battle_command','battle_survival','battle_okunote','battle_hero_retreat','egg_recover','chikujou')) for r in records))
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
        'observation_interval_ms':observation_interval_ms(state, actions),
        'menu_navigation_feedback': state.get('menu_navigation_reading') if menu_navigation_feedback(state, actions) else None,
        'status_panel_feedback': state.get('status_panel_feedback') if status_panel_feedback(state, actions) else None,
        'strategy_variant':strategy_variant,'deviation_reason':deviation_reason,
        'expected_metric':input_context.get('expected_metric') if input_context is not None else battle.get('strategy_expected'),
        'planned_actions':actions,
        'reason_decisions':[r.get('decision') for r in records],
        'dispatch_status':'planned_not_yet_sent'})
    prior = _decision_tail(runtime)
    for record in records:
        # Records re-emitted from a copy-mem path (no persisted plan/jump state)
        # are byte-identical to an earlier row; write each content once.
        if _recorded_before(prior, record, identity):
            continue
        payload={'schema':1,'event':'decision','at':now,'bot_version':BOT_VERSION,
                 'step':state.get('step'),'screen_kind':state.get('screen_kind'),
                 'phase':state.get('phase'),'decision_id':decision_id,
                 'frame_sha256':frame_sha256,'snapshot':snapshot,**identity,**record}
        append_log(runtime,'hanjuku_decisions',payload)
        prior.append(payload)
        if record.get('decision') in CHART_DECISIONS:
            append_log(runtime,'hanjuku_chart_decisions',
                       chart_decision_summary(record,now=now,identity=identity,
                                              decision_id=decision_id))
    # The fresh command process hot-loads the producer/worker launcher. No
    # fixed template candidates are emitted, including as an AI fallback.
    try:
        from docich.hanjuku_scene import observe as observe_scene
        observe_scene(ROOT, runtime, state, records, obs_meta)
    except Exception:
        print('hanjuku-bot: scene_commentary_unavailable', file=sys.stderr)


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


def chart_edition_setting():
    """``[hanjuku].chart_edition``; read every observation, applied at a new game."""
    try:
        with (ROOT/'config/games/hanjuku-hero.toml').open('rb') as stream:
            raw=tomllib.load(stream)
        value=(raw.get('hanjuku') or {}).get('chart_edition')
        return value if isinstance(value,str) else None
    except (OSError,tomllib.TOMLDecodeError):
        return None


def approx_map_setting():
    """``[hanjuku].approx_map``; source-seeded cells for chapters 3–12.

    Default true: the deployed bot entrypoint may run from a checkout whose
    config predates this key (the docich package is current). An explicit
    ``approx_map = false`` remains the rollback switch.
    """
    try:
        with (ROOT/'config/games/hanjuku-hero.toml').open('rb') as stream:
            raw=tomllib.load(stream)
        value=(raw.get('hanjuku') or {}).get('approx_map')
        return value is not False
    except (OSError,tomllib.TOMLDecodeError):
        return True


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


def observation_interval_ms(state, actions=None):
    """Short feedback for living melee and its in-flight rescue commands.

    g486: slowing to 1500 ms as soon as B planned a card let the Queen
    summon between the chained B and its next readable command menu.
    Keep the existing allowed 500 ms cycle across card-menu fades too.
    """
    if menu_navigation_feedback(state, actions) or status_panel_feedback(state, actions):
        return 500
    policy=state.get('policy') or {}
    battle=policy.get('battle') or {}
    if state.get('screen_kind') == 'battle' and not battle and policy.get('battle_seen'):
        # g749 Quiche: the first complete HP17 panel still awaits the second
        # name reading. A 1500ms observation gap let HP fall to8 before B,
        # then0 before a menu. Keep the confirmation gate, shorten its wait.
        return 500
    preempt=battle.get('okunote_egg_preempt') or {}
    rescue=battle.get('survival') or {}
    retreat=battle.get('hero_retreat') or {}
    command_pending=(battle.get('command_open_pending')
                     or ((rescue.get('pending_opens', 0) > 0 or rescue.get('menu_ticks', 0) > 0)
                         and not rescue.get('exhausted'))
                     or (retreat.get('opens', 0) > 0 and not retreat.get('exhausted')
                         and not retreat.get('unavailable')))
    living=all(type(battle.get(k)) is int and battle[k]>0 for k in ('ally_hp','enemy_hp'))
    if (living and not policy.get('egg_battle')
            and (state.get('screen_kind') == 'battle'
                 or (battle.get('card_flow') and state.get('screen_kind') in
                     {'unknown', 'text', 'battle_menu'})
                 or (command_pending and state.get('screen_kind') in
                     {'unknown', 'text', 'battle_menu', 'battle_menu_pending'})
                 or (((preempt.get('stage') in {'opening', 'menu', 'selected'}
                       and not preempt.get('exhausted')) or preempt.get('close_pending'))
                     and state.get('screen_kind') in
                     {'unknown', 'text', 'battle_menu', 'battle_menu_pending', 'okunote_menu'}))):
        return 500
    return 1500


def _valid_request_trace(trace, identity):
    """A request trace bound to this run, decision and observed frame."""
    keys = ('game', 'runtime_id', 'generation', 'lease_id')
    return (bool(trace)
            and not any(identity.get(k) is None or trace.get(k) != identity[k] for k in keys)
            and isinstance(trace.get('decision_id'), str)
            and isinstance(trace.get('frame_sha256'), str) and len(trace['frame_sha256']) == 64
            and type(trace.get('planned_at')) in (int, float)
            and 0 <= trace['planned_at'])


def _journal_pad_inputs(runtime, trace, button):
    """Distinct delivered ``button`` presses matching this request trace.

    Reads only a bounded tail of the existing sender journal; never replays.
    None means the journal could not be read, which is *unknown*, not
    *unsent* — callers must not treat it as permission to resend.
    """
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
        seen = set()
        for raw in tail.splitlines():
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if (isinstance(row, dict) and row.get('event') == 'input_sent'
                    and row.get('decision_id') == trace['decision_id']
                    and row.get('decision_frame_sha256') == trace['frame_sha256']
                    and type(row.get('at')) in (int, float)
                    and trace['planned_at'] <= row['at'] < 10**12
                    and row.get('type') == 'pad' and row.get('buttons') == [button]
                    and row.get('hold_ms') == 100):
                seen.add(row['at'])
    except OSError:
        return None
    return min(2, len(seen))


def recall_input_receipts(runtime, state, meta):
    """Read only a bounded tail of the existing sender journal; never replay.

    This command process reloads on each observation. No controller restart
    or new controller receipt writer is required for the current live run.
    """
    recall = ((state.get('policy') or {}).get('flow') or {}).get('recall') or {}
    trace = recall.get('request_trace') or {}
    if (recall.get('stage') != 'await_dispatch'
            or not _valid_request_trace(trace, meta.get('hanjuku') or {})):
        return None
    count = _journal_pad_inputs(runtime, trace, 'a')
    if count is None:
        return None
    return {'request_trace': trace, 'a_inputs': count}


def card_scroll_receipt(runtime, state, meta):
    """Match the previous top-row Up to the sender journal and run identity."""
    boundary = (state.get('policy') or {}).get('card_upper_boundary') or {}
    trace = boundary.get('request_trace') or {}
    if (boundary.get('await_input') is not True
            or not _valid_request_trace(trace, meta.get('hanjuku') or {})):
        return None
    count = _journal_pad_inputs(runtime, trace, 'up')
    if count is None:
        return None
    return {'request_trace': trace, 'up_inputs': count}


def departure_input_receipt(runtime, state, meta):
    """Match a requested departure A to the sender journal and run identity.

    The policy holds the target A as a reservation only; this receipt is the
    sole writer that may prove the pad press actually left (docich#3193 A).
    """
    request = ((state.get('policy') or {}).get('flow') or {}).get('departure_request') or {}
    trace = request.get('request_trace') or {}
    if (request.get('status') not in ('requested', 'sent')
            or not _valid_request_trace(trace, meta.get('hanjuku') or {})):
        return None
    count = _journal_pad_inputs(runtime, trace, 'a')
    if count is None:
        return None
    return {'request_trace': trace, 'a_inputs': count}


# The agent and the corner monitor both write screenshots/latest.png. A pad A
# reaches the game about 0.5 s after the observation it was decided on (median
# of 136 inputs, 2026-10-29). g1075 18:27:47.3: an A decided on a dialogue frame
# was sent at 47.8, after the corner had already captured ラビットサタン's skill
# menu at 47.6, and confirmed デスのカマ outside the skill-stall count.
CONFIRM = {'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}


def overtaken_confirm(path: Path, frame, state: dict, actions):
    """The newest capture when it has left this text frame for a command screen."""
    if actions != [CONFIRM] or state.get('screen_kind') not in ('text', 'unknown'):
        return None
    try:
        fresh = read_png(path).resized()
    except (OSError, ValueError):
        return None
    if fresh.digest() == frame.digest() or classify(frame) == 'event' or classify(fresh) != 'event':
        return None
    return fresh


# g1075 18:58:48.87: an A decided on the dialogue 「ウッドボールは すばやく よけた!」
# was sent after the corner had captured ガートルード's skill box sliding in. It
# chose であい, and the agent's next frame was already the enemy's menu. The
# sender journal records the frame each input reached; the phase change into a
# menu leaves that frame among the hanjuku_frames snapshots.
SNAPSHOT_NAME = re.compile(r'frame-\d{3}\.png')
OVERTAKEN_FRAMES_MAX = 2


def overtaken_frames(runtime, state, *, now=None):
    """(sha256, Frame) of command screens a dialogue-decided A actually reached.

    Reads a bounded tail of the existing journal; inputs are taken once, after
    ``state['overtaken_input_at']`` (the first run only sets that mark).
    """
    now = time.time() if now is None else now
    since = state.get('overtaken_input_at')
    found = []
    try:
        path = runtime / 'hanjuku_events.jsonl'
        if path.is_symlink() or not path.is_file():
            return found
        with path.open('rb') as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - 65536)
            stream.seek(offset)
            tail = stream.read(65536)
            if offset:
                tail = tail.partition(b'\n')[2]
        phases, snapshots, inputs = {}, {}, []
        for raw in tail.splitlines():
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict) or type(row.get('at')) not in (int, float):
                continue
            if row.get('event') == 'observation' and isinstance(row.get('frame_sha256'), str):
                phases[row['frame_sha256']] = row.get('phase')
                if isinstance(row.get('snapshot'), str) and SNAPSHOT_NAME.fullmatch(row['snapshot']):
                    snapshots[row['frame_sha256']] = row['snapshot']
            elif row.get('event') == 'input_sent' and row['at'] <= now + 1:
                inputs.append(row)
        if type(since) not in (int, float):
            state['overtaken_input_at'] = max([row['at'] for row in inputs], default=now)
            return found
        for row in inputs:
            if row['at'] <= since:
                continue
            state['overtaken_input_at'] = max(state['overtaken_input_at'], row['at'])
            sent, decided = row.get('frame_sha256'), row.get('decision_frame_sha256')
            if (row.get('type') != 'pad' or row.get('buttons') != ['a']
                    or not isinstance(sent, str) or not isinstance(decided, str) or sent == decided
                    or phases.get(decided) != 'dialogue' or phases.get(sent) not in ('field', 'event')
                    or sent not in snapshots or len(found) >= OVERTAKEN_FRAMES_MAX):
                continue
            snapshot = runtime / 'hanjuku_frames' / snapshots[sent]
            if snapshot.is_symlink() or not snapshot.is_file():
                continue
            try:
                frame = read_png(snapshot).resized()
            except (OSError, ValueError):
                continue
            if frame.digest() == sent:        # the 120-slot ring may have moved on
                found.append((sent, frame))
    except OSError:
        return found
    return found


def _where(exc):
    """Exception class, message and the innermost frames (no observation data)."""
    frames=[f for f in traceback.extract_tb(exc.__traceback__) if '/lib/python' not in f.filename][-3:]
    trail=' < '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in reversed(frames))
    return f'{type(exc).__name__}({str(exc)[:80]!r}) at {trail}'


def _unsortable_path(obj, path=''):
    """Path of the first dict whose keys mix types (json sort_keys cannot order them)."""
    if isinstance(obj, dict):
        if len({type(k).__name__ for k in obj}) > 1:
            return f'{path or "/"}{sorted({type(k).__name__ for k in obj})}'
        for k, v in obj.items():
            found = _unsortable_path(v, f'{path}/{k}')
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found = _unsortable_path(v, f'{path}[{i}]')
            if found:
                return found
    return None


def _non_json_path(obj, path=''):
    """Path of the first value the JSON writer cannot encode (never the value)."""
    if isinstance(obj, (set, frozenset, bytes, bytearray, Path)):
        return f'{path or "/"}[{type(obj).__name__}]'
    if isinstance(obj, dict):
        for k, v in obj.items():
            found = _non_json_path(v, f'{path}/{k}')
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found = _non_json_path(v, f'{path}[{i}]')
            if found:
                return found
    return None


def _json_safe(obj):
    """JSON types only. Sets become sorted lists so a write never sees them."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return [_json_safe(v) for v in sorted(obj, key=repr)]
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _sortable(name, obj):
    """g1133 11-05: a dict holding both int and str keys made every state write raise
    TypeError, so the already-decided input was dropped for hours. JSON turns every
    key into a string anyway, so normalise here instead of losing the observation.
    g1153 11-08: a `set` (the carry manifest) did the same through a value, so
    values are normalised too and the offending path is named."""
    try:
        json.dumps(obj, sort_keys=True)
        return obj
    except TypeError:
        mixed = _unsortable_path(obj)
        if mixed:
            print(f'hanjuku-bot: mixed_keys {name} {mixed}', file=sys.stderr)
        non_json = _non_json_path(obj)
        if non_json:
            print(f'hanjuku-bot: non_json {name} {non_json}', file=sys.stderr)
        return _json_safe(obj)


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
            hanjuku_resume.inherit_running_chapter(runtime,state)
            experience_path=ROOT/'run'/hanjuku_experience.EXPERIENCE_FILE
            experience=hanjuku_experience.load(experience_path)
            actions,state=decide(frame,state,adjusted=hanjuku_chart_adjust.load(runtime),
                                 interim=state.get('chart_interim_answer'),
                                 experience=experience,
                                 recall_inputs=recall_input_receipts(runtime, state, meta),
                                 card_scroll_input=card_scroll_receipt(runtime, state, meta),
                                 departure_input=departure_input_receipt(runtime, state, meta),
                                 overtaken_frames=overtaken_frames(runtime, state),
                                 run_identity=meta.get('hanjuku'),
                                 chart_edition=chart_edition_setting(),
                                 approx_map=approx_map_setting())
            fresh=overtaken_confirm(Path(obs['screenshot']),frame,state,actions)
            if fresh is not None:
                actions=[]
                policy=state.get('policy') or {}
                state.setdefault('_records',[]).append(_record(
                    {'active':policy.get('active'),'variant':policy.get('variant','chart'),
                     'chapter':policy.get('chapter')},
                    'confirm_overtaken',screen=state.get('screen_kind'),
                    observed_metric={'decided_phase':classify(frame),'fresh_phase':'event',
                                     'fresh_frame_sha256':fresh.digest()},
                    reason='文字画面で決めたAの送信前に最新の画面がコマンド表示へ変わったため、未読の最上段を確定しないよう送らず次の観測で決め直す'))
            interval_ms=observation_interval_ms(state, actions)
            records=state.pop('_records',[])
            updated_experience=state.pop('_experience',None)
            state=_sortable('state',state)
            records=_sortable('records',records)
            if isinstance(updated_experience,dict):
                updated_experience=_sortable('experience',updated_experience)
            # The decision is final from here on. Evidence writes and side
            # channels must never change it: the agent drops the printed
            # actions when this process exits nonzero, so one failed write
            # silently held every input until the run ended as input_stalled
            # (g1153 2026-11-08 19:26: the v519 carry-manifest set made the
            # bot-state write raise TypeError on every observation).
            for label, effect in (
                    ('persist_failed', lambda: persist(
                        runtime, state, records, meta, actions=actions,
                        frame_sha256=frame.digest(), frame=frame)),
                    ('adjust_request_failed', lambda: publish_adjust_request(runtime, records, meta)),
                    ('interim_failed', lambda: ask_interim(runtime, state, meta)),
                    ('state_write_failed', lambda: atomic_write_json(
                        runtime/'hanjuku_bot.json', state))):
                try:
                    effect()
                except Exception as exc:
                    print(f'hanjuku-bot: {label} {_where(exc)}', file=sys.stderr)
            if isinstance(updated_experience,dict):
                try:
                    hanjuku_experience.save(experience_path,updated_experience)
                except (OSError,ValueError):
                    # Losing one experience sample must not drop this frame's input.
                    print('hanjuku-bot: experience_save_failed',file=sys.stderr)
    except (KeyError,TypeError,ValueError,OSError) as exc:
        code=2
        # g1133 11-05: 400+ "invalid observation" exits with no way to tell which
        # line raised, so decided actions were silently dropped for hours.
        print(f'hanjuku-bot: invalid observation {_where(exc)}',file=sys.stderr)
    except Exception as exc:
        # A policy defect must not crash the agent loop or send guesses:
        # hold input for this observation and record only the error class.
        code=2
        actions=[]
        print(f'hanjuku-bot: policy_error {_where(exc)}',file=sys.stderr)
    print(json.dumps({'actions':actions, 'observation_interval_ms':interval_ms}),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
