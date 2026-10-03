"""Observed Hanjuku facts for an asynchronous, silent-on-failure narrator.

The command bot writes this bounded sidecar and may start one separate worker.
No generation, shell bootstrap or audio I/O runs in the input process.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tomllib

from .game_switch import atomic_write_json
from .retroarch_boundary import read_record

VERSION = 'hanjuku-scene-v1'
SCENE_FILE = 'hanjuku_scene.json'
WORKER_FILE = 'hanjuku_scene_worker.json'
LOCK_FILE = 'hanjuku_scene_worker.lock'
LOG_NAME = 'hanjuku_scene_commentary'
KEYS = ('game', 'runtime_id', 'generation', 'lease_id')
RECENT = 64
MAX_FACTS = 3
MAX_TEXT = 120
HISTORY_KINDS = frozenset({'battle_result', 'castle_owned_observed', 'castle_lost_observed',
                           'soldier_refill_receipt', 'chikujou'})
CARD_DECISIONS = frozenset({'battle_card', 'battle_card_selected', 'battle_card_damage_replan',
                           'battle_card_damage_rejected'})
CARD_ROLES = frozenset({'nonlethal_egg_risk', 'damage_or_egg_risk_unclassified',
                       'single_card_lethal', 'healing_not_a_kill', 'no_autonomous_egg_trigger',
                       'egg_drop_candidate_not_a_kill', 'inspect_live_inventory_before_selection',
                       'observed_control_chain_not_a_kill', 'control_has_no_observed_followup',
                       'summon_already_observed'})
TARGET_KINDS = frozenset({'general', 'boss_general', 'egg_monster', 'boss_monster', 'unknown'})
ESTIMATE_NUMBERS = ('raw_damage_min', 'enemy_soldier_hp_upper', 'damage_lower_bound',
                    'remaining_hp_upper')
_NAME = re.compile(r'[ぁ-ゖァ-ヺ一-龯々ー・]{1,24}')


def number(value, high=99999):
    return value if type(value) is int and 0 <= value <= high else None


def stamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 10**12 else None


def name(value):
    return value if isinstance(value, str) and _NAME.fullmatch(value) else None


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def identity(value):
    if (not isinstance(value, dict) or value.get('game') != 'hanjuku-hero'
            or not isinstance(value.get('runtime_id'), str)
            or not re.fullmatch(r'g[1-9][0-9]*-[a-f0-9]{6,32}', value['runtime_id'])
            or type(value.get('generation')) is not int or value['generation'] < 1
            or not isinstance(value.get('lease_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['lease_id'])):
        raise ValueError('invalid scene identity')
    return {key: value[key] for key in KEYS}


def same_identity(value, expected):
    return isinstance(value, dict) and all(type(value.get(key)) is type(expected[key])
                                          and value.get(key) == expected[key] for key in KEYS)


def settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('invalid scene enable flag')
    result = {'enabled': enabled}
    for key, default, low, high in (('cooldown_s', 25, 25, 300),
                                  ('max_age_s', 20, 5, 20),
                                  ('generation_timeout_s', 15, 1, 15)):
        value = raw.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('invalid scene timing')
        result[key] = float(value)
    return result


def config(repo_root):
    """Read on every new bot process; a malformed/disabled config never starts AI."""
    with (Path(repo_root) / 'config/games/hanjuku-hero.toml').open('rb') as stream:
        doc = tomllib.load(stream)
    return settings((doc.get('hanjuku') or {}).get('scene_commentary'))


def context(state):
    policy = state.get('policy') or {}
    battle = policy.get('battle') or {}
    month = policy.get('month')
    month = month if isinstance(month, str) and re.fullmatch(r'[1-9][0-9]{0,2}-(?:[1-9]|1[0-2])', month) else None
    screen = str(state.get('screen_kind') or '')
    phase = ('battle' if battle else 'month' if screen.startswith('month') or policy.get('shop')
             and screen not in {'map', 'world_map'} else 'field' if screen in {'map', 'world_map'} else 'other')
    result = {'chapter': number(policy.get('chapter'), 12), 'month': month, 'phase': phase,
              'battle_number': number((policy.get('tally') or {}).get('battles_started'), 10**6)}
    if battle:
        result.update(ally=name(battle.get('ally')), enemy=name(battle.get('enemy')))
    return result


def epoch_id(context):
    """A completed event may cross a menu fade, but never a new battle/chapter."""
    return digest({key: context.get(key) for key in ('chapter', 'battle_number')})


def fact(record):
    """Whitelist actual observations; intended buttons never become successful uses.

    All facts are historical evidence, even a policy-change event. In particular,
    a battle loss is not proof of death, and a win is not proof of castle ownership.
    """
    if not isinstance(record, dict):
        return None
    kind = record.get('decision')
    observed = record.get('observed_metric')
    observed = observed if isinstance(observed, dict) else {}
    result = {'kind': kind}
    if kind == 'battle_start':
        values = {key: name(record.get(key)) for key in ('ally', 'enemy')}
        values.update({key: number(observed.get(key), 999) for key in ('ally_hp', 'enemy_hp')})
        if any(value is None for value in values.values()):
            return None
        result.update(values)
    elif kind == 'battle_result':
        values = {key: name(record.get(key)) for key in ('ally', 'enemy')}
        if any(value is None for value in values.values()) or record.get('outcome') not in {'win', 'loss'}:
            return None
        result.update(values, outcome=record['outcome'])
        for key in ('ally_hp', 'enemy_hp'):
            value = number(observed.get(key), 999)
            if value is not None:
                result[key] = value
        # Consumption is explicitly distinguished from card selection by policy.
        if observed.get('card_consumption_complete') is True:
            used = observed.get('cards_used') or []
            if isinstance(used, list):
                result['cards_used'] = [value for item in used[:4] if (value := name(item))]
    elif kind in {'castle_owned_observed', 'castle_lost_observed'}:
        if not (castle := name(record.get('castle'))):
            return None
        result['castle'] = castle
    elif kind == 'soldier_refill_receipt':
        if (record.get('resulting_event') != 'paid'
                or observed.get('basis') not in {'screen_total', 'observed_plus_paid_refill'}
                or number(observed.get('soldiers_after')) is None
                or number(observed.get('qty')) is None or observed['qty'] < 1):
            return None
        result.update(qty=observed['qty'], soldiers_after=observed['soldiers_after'])
    elif kind == 'chikujou':
        if (observed.get('upgrade_verified') is not True or observed.get('payment_verified') is not True
                or not (castle := name(observed.get('castle')))
                or number(observed.get('quoted_cost')) is None):
            return None
        result.update(castle=castle, cost=observed['quoted_cost'])
    elif kind == 'egg_priority_replan':
        count = number(observed.get('soldiers'))
        if count is None or count < 50:
            return None
        result.update(soldiers=count, policy_change='egg_recovery_before_soldiers')
    elif kind in CARD_DECISIONS:
        gate = observed if kind == 'battle_card_damage_rejected' else observed.get('card_damage_gate')
        if (not isinstance(gate, dict) or type(gate.get('allowed')) is not bool
                or gate.get('role') not in CARD_ROLES or not (card := name(record.get('card')))):
            return None
        estimate = gate.get('estimate') or {}
        if not isinstance(estimate, dict) or estimate.get('target_kind') not in TARGET_KINDS:
            return None
        result = {'kind': 'card_decision', 'decision_kind': kind, 'card': card,
                  'allowed': gate['allowed'], 'role': gate['role'], 'target_kind': estimate['target_kind']}
        for key in ESTIMATE_NUMBERS:
            if number(estimate.get(key)) is not None:
                result[key] = estimate[key]
        if type(estimate.get('lethal')) is bool:
            result['lethal'] = estimate['lethal']
    else:
        return None
    return result


def publish(runtime, state, records, meta, cfg, *, now=None):
    now = time.time() if now is None else now
    ident = identity(meta.get('hanjuku'))
    if Path(runtime).name != ident['runtime_id'] or stamp(now) is None:
        raise ValueError('invalid scene runtime')
    old = read_record(Path(runtime) / SCENE_FILE, limit=32768) or {}
    if not same_identity(old, ident):
        old = {}
    scene = context(state)
    scene_id = digest(scene)
    epoch = epoch_id(scene)
    request = old.get('request')
    if (not isinstance(request, dict)
            or (request.get('scope') == 'history' and request.get('epoch_id') != epoch)
            or (request.get('scope') != 'history' and request.get('scene_id') != scene_id)
            or stamp(request.get('expires_at')) is None or request['expires_at'] <= now):
        request = None
    found = [value for record in records if (value := fact(record)) is not None]
    history = [value for value in found if value['kind'] in HISTORY_KINDS]
    if history:
        found = history
    scope = 'history' if history else 'scene'
    # Result/receipt events beat an opening reading when both share a bot tick.
    found.sort(key=lambda item: item['kind'] == 'battle_start')
    found = found[:MAX_FACTS]
    event_key = digest({'scene': epoch if history else scene_id, 'facts': found}) if found else None
    seq = number(old.get('seq'), 10**9) or 0
    if cfg['enabled'] and found and event_key != old.get('last_event_key'):
        seq += 1
        request = {'seq': seq, 'event_key': event_key, 'at': now,
                   'expires_at': now + cfg['max_age_s'], 'scene_id': scene_id,
                   'scope': scope, 'epoch_id': epoch, 'source_scene': scene,
                   'facts': [{**item, 'id': f'f{i + 1}'} for i, item in enumerate(found)]}
    if not cfg['enabled']:
        request = None
    value = {'schema': 1, 'version': VERSION, **ident, 'enabled': cfg['enabled'],
             'observed_at': now, 'scene_id': scene_id, 'scene': scene, 'seq': seq,
             'last_event_key': event_key or old.get('last_event_key'), 'request': request}
    atomic_write_json(Path(runtime) / SCENE_FILE, value)
    return value


def start_worker(repo_root, runtime, snapshot, cfg, *, now=None, popen=None):
    """Claim and transfer a nonblocking lock to one bounded child process."""
    if not cfg['enabled'] or not snapshot.get('request'):
        return False
    now = time.time() if now is None else now
    runtime = Path(runtime)
    path = runtime / LOCK_FILE
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        previous = read_record(runtime / WORKER_FILE, limit=32768) or {}
        if not same_identity(previous, snapshot):
            previous = {}
        request = snapshot['request']
        last_at = stamp(previous.get('last_attempt_at'))
        last_launch = stamp(previous.get('last_launch_at'))
        if (request['seq'] <= (number(previous.get('last_request_seq'), 10**9) or 0)
                or (last_at is not None and now - last_at < cfg['cooldown_s'])
                or (last_launch is not None and now - last_launch < 5)):
            return False
        previous.update(schema=1, **{key: snapshot[key] for key in KEYS}, last_launch_at=now)
        atomic_write_json(runtime / WORKER_FILE, previous)
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(repo_root) / 'src')
        (popen or subprocess.Popen)(
            [sys.executable, '-m', 'docich.hanjuku_scene_worker', '--runtime', str(runtime),
             '--lock-fd', str(fd)], cwd=str(repo_root), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, pass_fds=(fd,),
        )
        return True
    finally:
        os.close(fd)


def observe(repo_root, runtime, state, records, meta):
    """Errors are isolated by the bot caller so gameplay always keeps its actions."""
    cfg = config(repo_root)
    snapshot = publish(runtime, state, records, meta, cfg)
    return start_worker(repo_root, runtime, snapshot, cfg)
