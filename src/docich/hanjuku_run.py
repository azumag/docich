"""Generation-bound Hanjuku observations and actual-input records.

The corner's time limit is a consecutive unchanged-screen interval, never a
fixed session duration. Missing captures and observation gaps are not stasis.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time

from .adapters.base import AdapterError
from .game_switch import atomic_write_json
from .hanjuku_bot import BOT_VERSION, classify
from .hanjuku_pixels import Frame
from .retroarch_boundary import read_record

RUN_FILE='hanjuku_run.json'
TERMINAL_REASONS=frozenset({'game_over','screen_stalled'})
STALL_SECONDS=300
MAX_SAMPLE_GAP=15
MAX_LOG_BYTES=4*1024*1024


def enabled(game):
    raw=game.raw.get('hanjuku',{})
    return game.name=='hanjuku-hero' and isinstance(raw,dict) and raw.get('script_bot') is True


def runtime_identity(spec):
    return {key:getattr(spec,key) for key in ('game','runtime_id','generation','lease_id')}


def event(runtime_dir: Path, payload: dict):
    path=runtime_dir/'hanjuku_events.jsonl'
    if path.is_symlink():
        raise AdapterError('Hanjuku event log may not be a symlink')
    if path.exists() and path.stat().st_size>=MAX_LOG_BYTES:
        previous=runtime_dir/'hanjuku_events.previous.jsonl'
        if previous.is_symlink():
            raise AdapterError('Hanjuku rotated log may not be a symlink')
        os.replace(path,previous)
    with path.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(payload,separators=(',',':'),sort_keys=True)+'\n')
        stream.flush()
        if payload.get('terminal_reason'):
            os.fsync(stream.fileno())


def load(runtime_dir: Path, identity: dict):
    state=read_record(runtime_dir/RUN_FILE)
    if state and any(state.get(k)!=v for k,v in identity.items()):
        raise AdapterError('Hanjuku run identity mismatch')
    if state:
        for key in ('observations', 'actions_sent', 'battles_started', 'battles_finished',
                    'phase_transitions', 'snapshots', 'title_count'):
            value = state.get(key, 0)
            if type(value) is not int or value < 0:
                raise AdapterError('invalid Hanjuku counter')
        for key in ('observed_monotonic', 'observed_at', 'unchanged_since',
                    'unchanged_seconds', 'last_snapshot_at', 'title_since'):
            value = state.get(key, 0)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise AdapterError('invalid Hanjuku timestamp')
    return state


def terminal(runtime_dir: Path, identity: dict):
    state=load(runtime_dir,identity)
    reason=state.get('terminal_reason')
    if reason not in TERMINAL_REASONS:
        return None
    digest=state.get('frame_sha256')
    valid=(isinstance(digest,str) and len(digest)==64
           and all(c in '0123456789abcdef' for c in digest))
    if reason=='screen_stalled':
        valid=valid and state.get('unchanged_seconds',0)>=STALL_SECONDS
    else:
        valid=(valid and state.get('name_entered') is True and state.get('gameplay_seen') is True
               and state.get('phase')=='title' and state.get('title_count',0)>=3
               and state.get('observed_monotonic',0)-state.get('title_since',0)>=2)
    if not valid:
        raise AdapterError('invalid Hanjuku terminal evidence')
    return state


def observe(runtime_dir: Path, identity: dict, frame: Frame, *,
            now=None, wall=None, playing=True):
    now=time.monotonic() if now is None else now
    wall=time.time() if wall is None else wall
    if not math.isfinite(now) or not math.isfinite(wall):
        raise AdapterError('invalid Hanjuku observation time')
    old=load(runtime_dir,identity)
    if old.get('terminal_reason') in TERMINAL_REASONS:
        return old
    digest=frame.digest()
    phase=classify(frame)
    previous=old.get('observed_monotonic')
    consecutive=(type(previous) in (int,float) and 0 <= now-previous <= MAX_SAMPLE_GAP)
    unchanged=consecutive and old.get('frame_sha256')==digest and playing and old.get('playing') is True
    since=old.get('unchanged_since',now) if unchanged else now
    if type(since) not in (int,float) or not math.isfinite(since):
        raise AdapterError('invalid Hanjuku stasis evidence')
    duration=max(0.,now-since)
    # A title return after name entry and gameplay ends the attempt. Do not
    # confuse the initial title/attract demo or an intermediate black scene
    # with game over. Hold input on the first candidate, confirm across time.
    named=old.get('name_entered',False) or phase=='name'
    played=old.get('gameplay_seen',False) or (named and phase in {'battle','field'})
    candidate=phase=='title' and played and playing
    title_since=old.get('title_since',now) if candidate and consecutive and old.get('terminal_candidate') else now
    title_count=int(old.get('title_count',0))+1 if candidate and consecutive and old.get('terminal_candidate') else int(candidate)
    reason='game_over' if candidate and title_count>=3 and now-title_since>=2 else None
    if not reason and duration>=STALL_SECONDS:
        reason='screen_stalled'
    battle_active=old.get('battle_active',False)
    battle_started=phase=='battle' and not battle_active
    battle_ended=battle_active and phase in {'field','field_menu','dialogue','shop','month_menu'}
    if battle_started: battle_active=True
    if battle_ended: battle_active=False
    state={**identity,'schema':1,'bot_version':BOT_VERSION,
           'phase':phase,'frame_sha256':digest,'observed_monotonic':now,
           'observed_at':wall,'unchanged_since':since,'unchanged_seconds':duration,
           'playing':playing,'terminal_reason':reason,
           'name_entered':named,'gameplay_seen':played,
           'terminal_candidate':candidate,'title_since':title_since,'title_count':title_count,
           'terminal_evidence':'title_return_after_gameplay' if reason=='game_over' else None,
           'battle_active':battle_active,
           'battles_started':int(old.get('battles_started',0))+int(battle_started),
           'battles_finished':int(old.get('battles_finished',0))+int(battle_ended),
           'observations':int(old.get('observations',0))+1,
           'actions_sent':int(old.get('actions_sent',0)),
           'phase_transitions':int(old.get('phase_transitions',0))+int(old.get('phase')!=phase)}
    snapshot=None
    last_snapshot=old.get('last_snapshot_at',0)
    if old.get('phase')!=phase or wall-last_snapshot>=60 or reason:
        directory=runtime_dir/'hanjuku_frames'
        if directory.is_symlink():
            raise AdapterError('Hanjuku frame directory may not be a symlink')
        directory.mkdir(exist_ok=True)
        number=int(old.get('snapshots',0))
        snapshot=f'frame-{number%120:03d}.png'
        target=directory/snapshot
        if target.is_symlink():
            raise AdapterError('Hanjuku snapshot may not be a symlink')
        target.write_bytes(frame.png_bytes())
        state.update(snapshots=number+1,last_snapshot_at=wall)
    else:
        state.update(snapshots=int(old.get('snapshots',0)),last_snapshot_at=last_snapshot)
    # Durable evidence must precede a terminal latch or game teardown.
    event(runtime_dir, {
        'event': 'observation', 'at': wall, 'phase': phase,
        'frame_sha256': digest, 'unchanged_seconds': round(duration, 3),
        'playing': playing, 'terminal_reason': reason, 'bot_version': BOT_VERSION,
        'terminal_candidate': candidate, 'terminal_evidence': state['terminal_evidence'],
        'snapshot': snapshot, 'previous_phase': old.get('phase'),
        'battle_started': battle_started, 'battle_ended': battle_ended,
        'battle_outcome': 'unclassified' if battle_ended else None,
    })
    atomic_write_json(runtime_dir/RUN_FILE,state)
    return state


def action_sent(runtime_dir: Path, identity: dict, action):
    state=load(runtime_dir,identity)
    event(runtime_dir,{'event':'input_sent','at':time.time(),'type':action.type,
                      'buttons':list(action.buttons),'hold_ms':action.hold_ms,
                      'frame_sha256':state.get('frame_sha256'),'bot_version':BOT_VERSION})
    if state:
        atomic_write_json(runtime_dir/RUN_FILE,{**state,'actions_sent':int(state.get('actions_sent',0))+1})
