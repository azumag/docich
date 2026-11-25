"""Periodic Hanjuku progress commentary owned by the corner, not the bot.

The corner publishes a bounded situation snapshot roughly every fifteen
minutes and starts one short-lived worker.  The worker asks the existing Soren
radio dispatcher for several long, persona-driven narrative candidates, rejects
any line that uses a name or number not present in the snapshot or merely lists
facts, and speaks the candidate least similar to the recent delivered lines.
Only the existing fenced audio queue is used; this module never posts a chat
comment.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
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
import unicodedata

from .game_switch import atomic_write_json
from .hanjuku_progress import checked_identity, policy_cleared
from .hanjuku_run import append_log
from .retroarch_boundary import read_record

VERSION = 'hanjuku-periodic-v1'
STATE = 'hanjuku_periodic.json'
REQUEST = 'hanjuku_periodic_request.json'
RESULT = 'hanjuku_periodic_result.json'
LOCK = 'hanjuku_periodic.lock'
ERR_FILE = 'hanjuku_periodic_worker.err'
LOG = 'hanjuku_periodic_commentary'

MAX_TEXT = 700
MIN_TEXT = 180
MAX_CANDIDATES = 3
RECENT = 24
MAX_EVENTS = 10
REQUEST_TTL_S = 360
PENDING_TIMEOUT_S = 420
DEFER_MAX_S = 180
DEFER_STEP_S = 30
RETRY_LIMIT = 2
NO_MATERIAL_RETRY_S = 60

RETRY_STATUSES = frozenset({'duplicate', 'invalid_output', 'generation_failed',
                            'timeout', 'unavailable', 'worker_error',
                            'delivery_failed'})
QUIET_STATUSES = frozenset({'disabled', 'stale', 'fence_lost', 'run_ended'})

INTERESTING = frozenset({
    'battle_result', 'battle_start', 'castle_owned_observed',
    'castle_lost_observed', 'order_launched', 'order_start', 'month_plan',
    'buy', 'soldier_refill_receipt', 'chart_adjust_applied',
    'chart_adjust_request', 'poor_harvest', 'gift', 'battle_egg_dropped',
})
MONTH_RE = re.compile(r'([1-9][0-9]{0,2})-(1[0-2]|[1-9])')
NAME_RE = re.compile(r'[ぁ-ゖァ-ヺ一-龯々ー・]{1,24}')
KATAKANA_RE = re.compile(r'[ァ-ヺ][ァ-ヺー・]+')
# Only terms that are unavoidable in a short Japanese recap.  General
# katakana is deliberately not free-form: proper nouns must come from the
# snapshot so an invented general or castle cannot be spoken.
KATAKANA_ALLOWED = frozenset({
    'エッグ', 'モンスター', 'ダメージ', 'ゴールド', 'メニュー', 'チャンス',
    'ピンチ', 'バトル', 'レベル', 'タイミング', 'コスト', 'チャート', 'ボス',
    'ステージ', 'マップ', 'フィールド', 'イベント', 'ゲーム', 'プレイ',
    'データ', 'チェック', 'ルート', 'メンバー', 'パーティー', 'カード',
    'アイテム', 'ショップ', 'ゴール', 'リトライ', 'ヒーロー', 'モード',
    'ペース', 'バランス', 'スタート', 'クリア', 'ミス', 'チャレンジ',
    'ランキング', 'スコア', 'タイトル', 'セーブ', 'ロード', 'リセット',
    'プラン', 'ポイント', 'ボーナス', 'テンポ', 'リズム', 'ピンチ',
    'ドラマ', 'ストーリー', 'ハプニング', 'シーン', 'プレイヤー', 'ユーモア',
    'ジョーク', 'ギャグ', 'ツッコミ', 'ナイス', 'ファイト', 'ラスト',
    'クライマックス', 'リカバリー', 'モチベーション', 'チーム', 'メンタル',
    'テンション', 'ムード', 'エモ', 'ロマン', 'ドキドキ', 'ワクワク',
    'ハラハラ', 'ホッ', 'ウケ', 'ノリ', 'キレ', 'センス', 'スタイル',
    'ポジション', 'スキル', 'ミステリー', 'サプライズ', 'メッセージ', 'エール',
    'シーズン', 'エピソード', 'クエスト', 'ミッション', 'ケア', 'フォロー', 'サポート',
})

DEFAULT_PERSONA = (
    '半熟英雄を長く見守ってきた、落ち着きと熱量のある実況者。'
    '将軍たちの判断を尊重し、小さな進展を一緒に喜び、失敗には優しくツッコミを入れる。'
    '視聴者に語りかけるように、事実と感想を織り交ぜ、最後は前向きな余韻を残す。'
)


class PeriodicError(RuntimeError):
    """A fixed, loggable reason.  Never carries prompt or provider text."""

    def __init__(self, reason):
        self.reason = reason if reason in RETRY_STATUSES | QUIET_STATUSES | {
            'enqueued', 'no_material', 'requested'} else 'worker_error'
        super().__init__(self.reason)


# ---------------------------------------------------------------- settings

def settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('periodic_commentary.enabled must be boolean')
    result = {'enabled': enabled}
    for key, default, low, high in (
            ('interval_s', 900, 300, 3600),
            ('retry_s', 300, 60, 1800),
            ('generation_timeout_s', 90, 5, 240),
            ('max_attempts', 2, 1, 3)):
        value = raw.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'periodic_commentary.{key} is out of range')
        if key == 'max_attempts' and type(value) is not int:
            raise ValueError('periodic_commentary.max_attempts must be an integer')
        result[key] = int(value) if key == 'max_attempts' else float(value)
    agents = raw.get('agents', '')
    if not isinstance(agents, str) or (
            agents and not re.fullmatch(
                r'[A-Za-z0-9._:/-]{1,80}(?:,[A-Za-z0-9._:/-]{1,80}){0,7}', agents)):
        raise ValueError('periodic_commentary.agents is invalid')
    result['agents'] = agents
    persona = raw.get('persona', DEFAULT_PERSONA)
    if (not isinstance(persona, str) or len(persona) > 500
            or re.search(r'[\x00-\x1f<>]', persona)):
        raise ValueError('periodic_commentary.persona is invalid')
    result['persona'] = persona
    return result


def config(game, game_config_path=None):
    """Read the per-game opt-in; a live file is preferred when available."""
    if game_config_path is not None:
        try:
            with Path(game_config_path).open('rb') as stream:
                doc = tomllib.load(stream)
            raw = (doc.get('hanjuku') or {}).get('periodic_commentary')
            return settings(raw)
        except FileNotFoundError:
            pass
        except Exception:
            return settings({})  # unreadable game config must not speak
    raw = game.raw.get('hanjuku') if isinstance(game.raw, dict) else {}
    raw = raw.get('periodic_commentary') if isinstance(raw, dict) else {}
    return settings(raw)


def _game_config_path(g):
    games_dir = getattr(g, 'games_dir', None)
    return Path(games_dir) / 'hanjuku-hero.toml' if games_dir is not None else None


# ---------------------------------------------------------------- durable I/O

def _read_json(path, limit=65536):
    try:
        return read_record(Path(path), limit=limit)
    except Exception:
        return {}


def _record(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _state(runtime_dir, identity):
    row = _read_json(Path(runtime_dir) / STATE)
    valid = (type(row.get('schema')) is int and row['schema'] == 1
             and all(row.get(key) == value for key, value in identity.items())
             and _record(row.get('next_at')) and type(row.get('seq')) is int
             and row['seq'] >= 0 and type(row.get('failures')) is int
             and row['failures'] >= 0
             and isinstance(row.get('recent_texts'), list)
             and all(isinstance(text, str) and len(text) <= MAX_TEXT
                     for text in row['recent_texts']))
    if not valid:
        row = {}
    state = {'schema': 1, **identity}
    for key, default in (
            ('seq', 0), ('next_at', 0.0), ('last_attempt_at', None),
            ('last_delivered_at', None), ('pending_seq', None),
            ('last_result_seq', 0), ('failures', 0), ('recent_texts', []),
            ('defer_since', None), ('run_started_at', 0.0)):
        value = row.get(key, default)
        state[key] = value
    if not _record(state['next_at']):
        state['next_at'] = 0.0
    if state['last_attempt_at'] is not None and not _record(state['last_attempt_at']):
        state['last_attempt_at'] = None
    if state['last_delivered_at'] is not None and not _record(state['last_delivered_at']):
        state['last_delivered_at'] = None
    if state['pending_seq'] is not None and (type(state['pending_seq']) is not int
                                             or state['pending_seq'] < 1):
        state['pending_seq'] = None
    if state['defer_since'] is not None and not _record(state['defer_since']):
        state['defer_since'] = None
    if not _record(state['run_started_at']):
        state['run_started_at'] = 0.0
    state['recent_texts'] = [text for text in state['recent_texts'][-RECENT:]]
    return state


def _save_state(runtime_dir, state):
    atomic_write_json(Path(runtime_dir) / STATE, state)


def _log(runtime_dir, payload):
    try:
        append_log(Path(runtime_dir), LOG, {'schema': 1, 'at': time.time(), **payload})
    except Exception:
        pass


# ---------------------------------------------------------------- situation

def _name(value):
    return value if isinstance(value, str) and NAME_RE.fullmatch(value) else None


def _number(value, high=99999):
    return value if type(value) is int and 0 <= value <= high else None


def _month(value):
    match = MONTH_RE.fullmatch(value) if isinstance(value, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _clean_names(value, limit=6):
    if not isinstance(value, list):
        return []
    names = [name for item in value if (name := _name(item))]
    return names[:limit]


def _event_fact(record):
    kind = record.get('decision')
    if kind == 'battle_result':
        outcome = record.get('outcome')
        ally, enemy = _name(record.get('ally')), _name(record.get('enemy'))
        if outcome not in {'win', 'loss'} or not ally or not enemy:
            return None
        fact = {'kind': 'battle_result', 'outcome': outcome, 'ally': ally, 'enemy': enemy}
        metric = record.get('observed_metric') if isinstance(record.get('observed_metric'), dict) else {}
        for key in ('ally_hp', 'enemy_hp'):
            value = _number(record.get(key))
            if value is None:
                value = _number(metric.get(key))
            if value is not None:
                fact[key] = value
        return fact
    if kind == 'battle_start':
        ally, enemy = _name(record.get('ally')), _name(record.get('enemy'))
        if not ally or not enemy:
            return None
        fact = {'kind': 'battle_start', 'ally': ally, 'enemy': enemy}
        for key in ('ally_hp', 'enemy_hp'):
            if (value := _number(record.get(key))) is not None:
                fact[key] = value
        return fact
    if kind in {'castle_owned_observed', 'castle_lost_observed'}:
        castle = _name(record.get('castle'))
        return {'kind': kind, 'castle': castle} if castle else None
    if kind in {'order_launched', 'order_start'}:
        if kind == 'order_start' and record.get('roster_probe') is True:
            return None  # a list check is not a sortie plan
        general, target = _name(record.get('general')), _name(record.get('target'))
        if not general or not target:
            return None
        fact = {'kind': kind, 'general': general, 'target': target}
        source = _name(record.get('source'))
        if source:
            fact['source'] = source
        cards = _clean_names(record.get('cards'), 4)
        if cards:
            fact['cards'] = cards
        return fact
    if kind == 'month_plan':
        plan = record.get('plan') if isinstance(record.get('plan'), dict) else {}
        year, month = _month(record.get('month'))
        if year is None:
            return None
        fact = {'kind': 'month_plan', 'year': year, 'month': month}
        for key in ('gold', 'soldiers'):
            if (value := _number(record.get(key))) is not None:
                fact[key] = value
        cards = []
        for item in plan.get('cards') or []:
            if isinstance(item, str):
                value = _name(item)
            elif isinstance(item, (list, tuple)) and item:
                value = _name(item[0])
            else:
                value = None
            if value:
                cards.append(value)
        if cards:
            fact['cards'] = cards[:4]
        return fact
    if kind == 'buy':
        card = _name(record.get('card'))
        qty, price = _number(record.get('qty')), _number(record.get('price'))
        if not card or qty is None or qty < 1:
            return None
        fact = {'kind': 'buy', 'card': card, 'qty': qty}
        if price is not None:
            fact['price'] = price
        return fact
    if kind == 'soldier_refill_receipt':
        metric = record.get('observed_metric') if isinstance(record.get('observed_metric'), dict) else {}
        qty, after = _number(metric.get('qty')), _number(metric.get('soldiers_after'))
        if record.get('resulting_event') != 'paid' or qty is None or qty < 1 or after is None:
            return None
        return {'kind': 'soldier_refill_receipt', 'qty': qty, 'soldiers_after': after}
    if kind == 'chart_adjust_applied':
        orders = []
        digest = record.get('order_digest')
        if isinstance(digest, list):
            for order in digest:
                if not isinstance(order, dict) or order.get('roster_probe') is True:
                    continue
                general, target = _name(order.get('general')), _name(order.get('target'))
                if general and target:
                    orders.append([general, target])
                if len(orders) == 3:
                    break
        if not orders:
            steps = record.get('steps')
            if isinstance(steps, list) and steps:
                return {'kind': 'chart_adjust_applied', 'steps': len(steps)}
        return {'kind': 'chart_adjust_applied', 'orders': orders} if orders else None
    if kind == 'chart_adjust_request':
        reason = record.get('off_chart_reason')
        reason = reason if reason in {'orders_locked', 'orders_exhausted',
                                      'chart_unavailable'} else None
        return {'kind': 'chart_adjust_request', **({'reason': reason} if reason else {})}
    if kind == 'poor_harvest':
        return {'kind': 'poor_harvest'}
    if kind == 'gift':
        item, price = _name(record.get('item')), _number(record.get('price'))
        if not item:
            return None
        return {'kind': 'gift', 'item': item, **({'price': price} if price is not None else {})}
    if kind == 'battle_egg_dropped':
        enemy, card = _name(record.get('enemy')), _name(record.get('card'))
        return {'kind': 'battle_egg_dropped', 'enemy': enemy, 'card': card} if enemy and card else None
    return None


def _decision_rows(runtime_dir):
    """This run's decision records across the existing one-generation rotation."""
    for name in ('hanjuku_decisions.previous.jsonl', 'hanjuku_decisions.jsonl'):
        path = Path(runtime_dir) / name
        if not path.exists() or path.is_symlink():
            continue
        try:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 1024 * 1024))
                lines = stream.read().decode('utf-8', 'ignore').splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get('event') == 'decision':
                yield item


def _recent_events(runtime_dir, start, end):
    records = []
    for record in _decision_rows(runtime_dir):
        at = record.get('at')
        if (type(at) not in (int, float) or not math.isfinite(at)
                or not start <= at <= end or record.get('decision') not in INTERESTING):
            continue
        fact = _event_fact(record)
        if fact:
            records.append((at, record.get('step') if type(record.get('step')) is int else 0, fact))
    records.sort(key=lambda item: (item[0], item[1]))
    # A battle start already followed by its result is history the result
    # describes; keeping both invites a stale "opening HP" sentence.
    results = {(fact.get('ally'), fact.get('enemy'))
               for _at, _step, fact in records if fact['kind'] == 'battle_result'}
    kept = []
    counts = {}
    for at, _step, fact in reversed(records):
        kind = fact['kind']
        if kind == 'battle_start' and (fact.get('ally'), fact.get('enemy')) in results:
            continue
        cap = {'battle_result': 4, 'battle_start': 1,
               'castle_owned_observed': 2, 'castle_lost_observed': 2,
               'order_launched': 2, 'order_start': 1, 'month_plan': 1,
               'buy': 2, 'soldier_refill_receipt': 1,
               'chart_adjust_applied': 1, 'chart_adjust_request': 1}.get(kind, 1)
        if counts.get(kind, 0) >= cap:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        kept.append((at, fact))
        if len(kept) >= MAX_EVENTS:
            break
    return [fact for _at, fact in reversed(kept)]


def _progress(policy, progress):
    chapter = policy.get('chapter')
    chapter = chapter if type(chapter) is int and 1 <= chapter <= 12 else None
    year, month = _month(policy.get('month'))
    cleared = progress.get('cleared') if type(progress.get('cleared')) is int else policy_cleared(policy)
    value = {key: item for key, item in (
        ('cleared', cleared), ('chapter', chapter), ('year', year),
        ('month', month), ('edition', policy.get('chart_edition')
                           if policy.get('chart_edition') in {'speedrun', 'steady', 'ab'} else None))
        if item is not None}
    return value or None


def _current(policy):
    chapter = policy.get('chapter')
    chapter = chapter if type(chapter) is int and 1 <= chapter <= 12 else None
    month_key = policy.get('month')
    current = {}
    battle = policy.get('battle')
    if isinstance(battle, dict):
        ally, enemy = _name(battle.get('ally')), _name(battle.get('enemy'))
        if ally and enemy:
            value = {'ally': ally, 'enemy': enemy,
                     **{key: battle[key] for key in ('ally_hp', 'enemy_hp')
                        if _number(battle.get(key)) is not None}}
            castle = _name(battle.get('castle'))
            if castle:
                value['castle'] = castle
            current['battle'] = value
    order = None
    flow = policy.get('flow') if isinstance(policy.get('flow'), dict) else {}
    attempt = flow.get('sortie_attempt')
    if isinstance(attempt, dict) and not attempt.get('interrupted'):
        step = attempt.get('step')
        probes = flow.get('launch_probe') if isinstance(flow.get('launch_probe'), dict) else {}
        probe = probes.get(step) if isinstance(step, str) else None
        if isinstance(probe, dict):
            general, target = _name(probe.get('general')), _name(probe.get('target'))
            if general and target:
                order = {'general': general, 'target': target}
                source = _name(probe.get('source'))
                if source:
                    order['source'] = source
    if order is None:
        goals = policy.get('goals') if isinstance(policy.get('goals'), list) else []
        for goal in goals:
            if not isinstance(goal, dict):
                continue
            target = _name(goal.get('target'))
            kind = goal.get('kind')
            if target and kind in {'retake', 'attack', 'move'}:
                order = {'goal': kind, 'target': target}
                break
    if order is not None:
        current['order'] = order
    economy = {}
    gold = policy.get('gold')
    if _number(gold) is not None:
        economy['gold'] = gold
    shop = policy.get('shop') if isinstance(policy.get('shop'), dict) else {}
    soldiers = shop.get('soldiers')
    if _number(soldiers) is not None:
        economy['soldiers'] = soldiers
    else:
        observed = policy.get('country_soldier_count')
        if (isinstance(observed, dict) and _number(observed.get('count')) is not None
                and observed.get('scope') == [chapter, month_key]):
            economy['soldiers'] = observed['count']
    generals = policy.get('country_general_count')
    if (isinstance(generals, dict) and _number(generals.get('count')) is not None
            and generals.get('scope') == [chapter, month_key]):
        economy['generals'] = generals['count']
    ownership = policy.get('castle_ownership')
    held = [castle for castle, row in ownership.items()
            if isinstance(castle, str) and _name(castle)
            and isinstance(row, dict) and row.get('owner') == 'own'] if isinstance(
                ownership, dict) else []
    captured = held[:6] if held else _clean_names(policy.get('captured'))
    if captured:
        economy['captured'] = captured
    if economy:
        current['economy'] = economy
    stats = policy.get('stats') if isinstance(policy.get('stats'), dict) else {}
    tally = policy.get('tally') if isinstance(policy.get('tally'), dict) else {}
    totals = {}
    for key in ('wins', 'losses'):
        if _number(stats.get(key)) is not None:
            totals[key] = stats[key]
    if _number(tally.get('battles_started')) is not None:
        totals['battles'] = tally['battles_started']
    if totals:
        current['totals'] = totals
    return current


def situation(runtime_dir, run, state, now):
    """A bounded, whitelisted snapshot; no paths, identities or free-form reasons."""
    try:
        bot = read_record(Path(runtime_dir) / 'hanjuku_bot.json', limit=256 * 1024)
        if any(bot.get(key) is not None and bot.get(key) != value
               for key, value in checked_identity(run).items()):
            bot = {}
    except Exception:
        bot = {}
    policy = bot.get('policy') if isinstance(bot.get('policy'), dict) else {}
    try:
        progress = read_record(Path(runtime_dir) / 'hanjuku_progress.json', limit=8192)
        if any(progress.get(key) not in (None, value) for key, value in checked_identity(run).items()):
            progress = {}
    except Exception:
        progress = {}
    last = state.get('last_delivered_at')
    if not isinstance(last, (int, float)) or not math.isfinite(last) or last > now:
        last = max(state.get('run_started_at') or 0, now - 900.0)
    window_start = max(last, now - 2 * 900.0)
    events = _recent_events(runtime_dir, window_start, now)
    progress_value = _progress(policy, progress)
    current = _current(policy)
    window = {'minutes': max(1, round((now - window_start) / 60))}
    # Aggregate over all decisions in the window, not only the capped event
    # sample, so a busy fight is still counted accurately.
    wins = losses = captures = castle_losses = orders = 0
    for record in _decision_rows(runtime_dir):
        at = record.get('at')
        if type(at) not in (int, float) or not math.isfinite(at) or not window_start <= at <= now:
            continue
        kind = record.get('decision')
        if kind == 'battle_result' and record.get('outcome') in {'win', 'loss'}:
            if record['outcome'] == 'win':
                wins += 1
            else:
                losses += 1
        elif kind == 'castle_owned_observed':
            captures += 1
        elif kind == 'castle_lost_observed':
            castle_losses += 1
        elif kind == 'order_launched':
            orders += 1
    window.update(battles=wins + losses, wins=wins, losses=losses,
                  captures=captures, castle_losses=castle_losses, orders=orders)
    if not events and not current and not progress_value:
        return None
    result = {'window': window, 'events': events, 'current': current}
    if progress_value:
        result['progress'] = progress_value
    return result


def flatten(situation_value):
    """Flatten the nested snapshot into whitelisted facts for validation."""
    facts = []
    if isinstance(situation_value.get('progress'), dict):
        facts.append({'kind': 'progress', **situation_value['progress']})
    if isinstance(situation_value.get('window'), dict):
        facts.append({'kind': 'window', **situation_value['window']})
    for event in situation_value.get('events') or []:
        if isinstance(event, dict) and isinstance(event.get('kind'), str):
            facts.append(event)
    current = situation_value.get('current') if isinstance(situation_value.get('current'), dict) else {}
    for key in ('battle', 'order', 'economy', 'totals'):
        if isinstance(current.get(key), dict):
            facts.append({'kind': f'current_{key}', **current[key]})
    return facts


# ---------------------------------------------------------------- prompt

def build_prompt(situation_value, recent=(), avoid=(), persona=None):
    avoid = [text for text in [*recent, *avoid] if isinstance(text, str)][-8:]
    persona = persona or DEFAULT_PERSONA
    return '\n'.join([
        'あなたはSFC「半熟英雄」の長期戦を一緒に見守る実況者です。',
        'ペルソナ: ' + persona,
        '直近の出来事（window.minutes 分、通常は15分）を、視聴者に語りかけるように4〜8文、'
        '300〜600文字で実況してください。',
        '次の5つを自然につなげてください。',
        '1. この15分の流れと進展（何が起き、どう進んだか）',
        '2. 良かったこと・成長したこと',
        '3. 課題・反省・次に活かせる改善点（事実から言える範囲で）',
        '4. 将軍や城のドラマ、軽いユーモアやジョークを1つ',
        '5. 視聴者への前向きな締め（心に残る一言）',
        '事実と評価を区別してください。評価は「〜に見えます」「〜が課題です」「〜は見事でした」のように話し、'
        '事実を曲げないでください。',
        '下の「状況」に書かれた事実だけを使います。書かれていない数値・将軍名・城名・札名・勝敗・'
        '出来事を作ってはいけません。',
        '勝敗を話せるのはeventsのbattle_resultだけです。勝利は城の占領ではありません。'
        '敗北は将軍の死亡ではありません。死亡・全滅・撃破・ゲームオーバーを断定しないでください。',
        '「勝負」「勝利への道」のような比喩で勝敗を曖昧にせず、勝敗はbattle_resultを指す場合だけ'
        '「〜が勝ちました」「〜は敗れました」と書いてください。',
        '城の占領・奪還・喪失はeventsのcastle_owned_observed／castle_lost_observedだけです。'
        'それ以外から城の出入りを推測しないでください。',
        'battle_startとbattle_resultのHPは観測時点の値です。現在HPと断定せず、'
        '将軍名と「HP」または「体力」、そして「開戦時」「終了時」を添えてください。',
        'current.battleは直近に観測できた戦闘の状態です。current.orderはこれからの予定で、'
        '実行結果ではありません。「次は」「これから」はcurrent.orderがある場合だけ使えます。',
        'eventsは古い順です。過去の出来事は過去形で話します。数字は半角算用数字にし、章・年・月・分・勝敗数・'
        '金額・人数は、状況にある数字と項目を対応させてください。',
        '将軍名・城名・札名以外のカタカナ語は使わないでください。絵文字・Markdown・URLは使わない。',
        'ジョークは状況の範囲で軽く。個人攻撃、下品な表現、死亡・故障の断定はしないでください。',
        '長さは300〜600文字を目安にし、180文字未満や700文字超にしないでください。',
        '出来事を箇条書きのように列挙せず、「何が起きたか→それが何を意味したか→次にどう活かすか」の'
        '一つの物語として話してください。',
        'それぞれの出来事には、意味・評価・感情のどれかを添え、「まず」「次に」「また」で機械的に'
        '並べないでください。',
        '書き出し・リズム・ユーモアの入れ方・締め方を変えた候補を3つ作り、事実の取捨も候補ごとに'
        '変えてください。',
        '同じ言い回しの反復を避けます。特に「直近の実況」と同じ書き出し・同じ文型・同じ締め方に'
        'しないでください。',
        'JSONオブジェクトだけを返してください: {"texts":["候補1","候補2","候補3"]}',
        '## 状況', json.dumps(situation_value, ensure_ascii=False, sort_keys=True),
        '## 直近の実況（この言い回しは使わない）',
        json.dumps(avoid, ensure_ascii=False),
    ])


EVAL_MARKERS = re.compile(
    r'見事|課題|反省|良かった|成長|手応え|悔し|もったい|頼もしい|面白|ユーモア|'
    r'ドラマ|思い|感じ|でしょう|見えます|かもしれません|願い|楽しみ|応援|拍手|'
    r'心配|不安|希望|粘り|踏ん張り|教訓|改善|魅力|見どころ|印象|評価|'
    r'上々|順調|難しさ|難しい|苦しい|厳しい|頼もしく|無駄|白星|黒星|滑り出し|'
    r'見え|いい|よい|惡い|惜しい|立派|素晴らしい|たい|ですね')


def narrative_ok(text):
    """Reject a flat fact list; require an arc and at least one evaluation."""
    if len(re.findall(r'[。！？]', text)) < 3:
        return False
    if not EVAL_MARKERS.search(text):
        return False
    if re.search(r'(?:^|[\n。])\s*(?:[・\-–—*]|[0-9０-９]+[.)、])', text):
        return False
    return True


def parse_candidates(output):
    if not isinstance(output, str) or len(output.encode()) > 16384:
        raise PeriodicError('invalid_output')
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        raise PeriodicError('invalid_output') from None
    if (not isinstance(value, dict) or set(value) != {'texts'}
            or not isinstance(value['texts'], list)
            or not 1 <= len(value['texts']) <= MAX_CANDIDATES):
        raise PeriodicError('invalid_output')
    texts = []
    for text in value['texts']:
        if not isinstance(text, str):
            continue
        text = text.strip()
        if (not MIN_TEXT <= len(text) <= MAX_TEXT
                or re.search(r'[\x00-\x1f<>`{}]|https?://', text)
                or not re.search(r'[ぁ-ゖァ-ヺ一-龯]', text)
                or not narrative_ok(text)
                or text in texts):
            continue
        texts.append(text)
    if not texts:
        raise PeriodicError('invalid_output')
    return texts


# ---------------------------------------------------------------- grounding

def _fact_names(facts):
    names = set()
    for fact in facts:
        for key in ('ally', 'enemy', 'castle', 'card', 'general', 'target',
                    'source', 'item'):
            if _name(fact.get(key)):
                names.add(fact[key])
        for key in ('captured', 'lost', 'cards'):
            if isinstance(fact.get(key), list):
                names.update(_clean_names(fact[key], 64))
        if isinstance(fact.get('orders'), list):
            for order in fact['orders']:
                if isinstance(order, (list, tuple)):
                    names.update(item for item in (_name(value) for value in order) if item)
                elif isinstance(order, dict):
                    names.update(item for item in (_name(order.get(key))
                                                   for key in ('general', 'target')) if item)
    return names


def _last_name(text, names):
    found = [(text.rfind(name), name) for name in names if name in text]
    return max(found)[1] if found else None


def _numbers_match(text, facts):
    for match in re.finditer(r'[0-9]+', text):
        value = int(match.group())
        before = text[max(0, match.start() - 48):match.start()]
        after = text[match.end():match.end() + 8]
        if re.match(r'[-−+＋.]', text[match.end():match.end() + 1]) or \
                re.search(r'[-−+＋.]$', before):
            return False
        if not _number_in_context(value, before, after, facts):
            return False
    return True


def _number_in_context(value, before, after, facts):
    if after.startswith('章'):
        if re.match(r'章(?:を)?(?:クリア|突破|制覇|越え|まで)', after):
            return any(fact.get('cleared') == value for fact in facts
                       if fact.get('kind') == 'progress')
        return any((fact.get('chapter') == value or fact.get('cleared') == value)
                   for fact in facts if fact.get('kind') == 'progress')
    if after.startswith('年'):
        return any(fact.get('year') == value for fact in facts
                   if fact.get('kind') in {'progress', 'month_plan'})
    if after.startswith('月'):
        return any(fact.get('month') == value for fact in facts
                   if fact.get('kind') in {'progress', 'month_plan'})
    if after.startswith('分'):
        return any(fact.get('minutes') == value for fact in facts
                   if fact.get('kind') == 'window')
    if after.startswith('戦') or (after.startswith('つ') and '戦' in after[:6]):
        return any(fact.get('battles') == value for fact in facts
                   if fact.get('kind') in {'window', 'current_totals'})
    if after.startswith('勝'):
        return any(fact.get('wins') == value for fact in facts
                   if fact.get('kind') in {'window', 'current_totals'})
    if after.startswith('敗') or (after.startswith('つ') and '敗' in after[:6]):
        return any(fact.get('losses') == value for fact in facts
                   if fact.get('kind') in {'window', 'current_totals'})
    if re.match(r'(ゴールド|G|円)', after) or re.search(
            r'(?:金|所持金|軍資金|資金)(?:は|が|も|を)?$', before):
        return any(fact.get('gold') == value for fact in facts
                   if fact.get('kind') in {'current_economy', 'month_plan'}) or \
            any(fact.get('price') == value for fact in facts if fact.get('kind') == 'gift') or \
            any(fact.get('price') == value for fact in facts if fact.get('kind') == 'buy')
    if after.startswith('人'):
        if '兵士' in before or re.search(r'兵(?:士)?(?:が|は|も|を)?(?:いま|現在)?$', before):
            return any(fact.get('soldiers') == value for fact in facts
                       if fact.get('kind') in {'current_economy', 'month_plan'}) or \
                any(fact.get('soldiers_after') == value or fact.get('qty') == value
                    for fact in facts if fact.get('kind') == 'soldier_refill_receipt') or \
                (after.startswith('まで') and '補充' in before and any(
                    fact.get('soldiers_after') == value for fact in facts
                    if fact.get('kind') == 'soldier_refill_receipt'))
        if '将軍' in before or '将軍' in after[:8]:
            return any(fact.get('generals') == value for fact in facts
                       if fact.get('kind') == 'current_economy')
        if '補充' in before or '購入' in before:
            return any(fact.get('qty') == value for fact in facts
                       if fact.get('kind') == 'soldier_refill_receipt')
        return False
    if after.startswith(('個', '枚')):
        if any(fact.get('qty') == value for fact in facts if fact.get('kind') == 'buy'):
            return True
        return any(isinstance(fact.get('cards'), list) and len(fact['cards']) == value
                   for fact in facts if fact.get('kind') in {'order_start', 'order_launched'})
    if after.startswith(('回', '件')) and '出撃' in before:
        return any(fact.get('orders') == value for fact in facts if fact.get('kind') == 'window')
    if after.startswith('城') and re.search(r'取|獲得|占領|失|落', before):
        return any(fact.get('captures') == value or fact.get('castle_losses') == value
                   for fact in facts if fact.get('kind') == 'window')
    if re.search(r'兵(?:士)?(?:が|は|も|を)?(?:いま|現在)?$', before):
        return any(fact.get('soldiers') == value for fact in facts
                   if fact.get('kind') in {'current_economy', 'month_plan'})
    if value == 0 and re.search(r'(?:城の出入り|奪った城|失った城|城)(?:は|も|が)?(?:まだ)?$', before):
        return any((fact.get('captures') == 0 or fact.get('castle_losses') == 0)
                   for fact in facts if fact.get('kind') == 'window')
    if '補充' in before and after.startswith(('まで', 'に戻', 'になり', 'に届', 'となり')):
        return any(fact.get('soldiers_after') == value for fact in facts
                   if fact.get('kind') == 'soldier_refill_receipt')
    if re.search(r'兵(?:士)?を$', before) and after.startswith(('補充', '購入')):
        return any(fact.get('qty') == value for fact in facts
                   if fact.get('kind') == 'soldier_refill_receipt')
    if value == 0 and ('終了時' in before or '開戦時' in before) and '敗れ' in before:
        return any(fact.get('outcome') == 'loss' and fact.get('ally_hp') == 0
                   for fact in facts if fact.get('kind') == 'battle_result')
    pair = re.match(r'対([0-9]+)', after)
    if pair:
        other = int(pair.group(1))
        if any((fact.get('ally_hp'), fact.get('enemy_hp')) in {(value, other), (other, value)}
               for fact in facts if fact.get('kind') in {'battle_start', 'battle_result', 'current_battle'}):
            return True
    previous = re.search(r'([0-9]+)対$', before)
    if previous:
        other = int(previous.group(1))
        if any((fact.get('ally_hp'), fact.get('enemy_hp')) in {(value, other), (other, value)}
               for fact in facts if fact.get('kind') in {'battle_start', 'battle_result', 'current_battle'}):
            return True
    if after.startswith('を残') and any(name in before for name in _fact_names(facts)):
        present = [name for name in _fact_names(facts) if name in before]
        for fact in facts:
            if fact.get('kind') not in {'battle_start', 'battle_result', 'current_battle'}:
                continue
            if fact.get('ally') in present and fact.get('ally_hp') == value:
                return True
            if fact.get('enemy') in present and fact.get('enemy_hp') == value:
                return True
    if 'HP' in before or '体力' in before:
        present = [name for name in _fact_names(facts) if name in before]
        if not present:
            return False
        for fact in facts:
            if fact.get('kind') not in {'battle_start', 'battle_result', 'current_battle'}:
                continue
            if len(present) == 1 and present[0] != fact.get('ally') and present[0] != fact.get('enemy'):
                continue
            if fact.get('ally') in present and fact.get('ally_hp') == value:
                return True
            if fact.get('enemy') in present and fact.get('enemy_hp') == value:
                return True
    return False


def _future_claim(text):
    return bool(re.search(r'次は|これから|狙(?:う|い)|目指(?:す|し)|予定', text))


def valid_text(text, facts):
    """Ground a candidate line in the snapshot's names, numbers and results."""
    from .hanjuku_scene_grounding import castle_results_match, outcomes_match

    normalized = unicodedata.normalize('NFKC', text).replace('\u3000', ' ')
    names = _fact_names(facts)
    if any(word not in names and word not in KATAKANA_ALLOWED
           for word in KATAKANA_RE.findall(normalized)):
        return False
    for match in re.finditer(r'([ぁ-ゖァ-ヺ一-龯々ー・]{2,12})城', normalized):
        token = match.group(1)
        if any(token.endswith(name) for name in names):
            continue
        if token in {'自軍', '敵軍', '本城', 'ボス', 'この', 'その', 'あの',
                     '例の', '大きな', '小さな', '目の前'}:
            continue
        if token.endswith(('は', 'が', 'を', 'に', 'へ', 'で', 'と', 'も', 'の',
                           'や', 'から', 'まで', 'より')):
            continue
        return False
    if re.search(r'死亡|戦死|亡くな|全滅|一撃|瞬殺|必ず|確実に|絶対|ゲームオーバー|'
                 r'エンディング|倒した|撃破した?|ゲーム(?:を)?クリア', normalized):
        return False
    for match in re.finditer(r'クリア', normalized):
        if not re.search(r'[0-9]+章', normalized[max(0, match.start() - 16):match.end() + 8]):
            return False
    if not _numbers_match(normalized, facts):
        return False
    battle_results = [fact for fact in facts if fact.get('kind') == 'battle_result']
    # Aggregate window counts ("3勝1敗") are already number-checked against the
    # window fact; they are not a claim about a named battle.
    outcome_text = re.sub(r'[0-9]+勝(?:[0-9]+敗)?|[0-9]+敗|[0-9]+つの敗(?:北)?', '', normalized)
    # Nominal/summary uses are not result claims: "勝ち方", "勝つ形", "連勝で",
    # "勝ち切れない" and similar. They are handled by the surrounding evaluation.
    outcome_text = re.sub(
        r'勝(?:ち方|ち筋|ちパターン|つ形|負|率|利数|利へ|因|敗|ち切れ)|'
        r'連勝(?:で|の|は|が|を)|勝って負けて|勝ったり負けたり|勝ち負け|勝ちを重ね|'
        r'勝ち越し|勝利|勝ち星|勝った次の一戦|勝った次', '', outcome_text)
    if (re.search(r'勝|負け|敗[れ北戦]', outcome_text)
            and not outcomes_match(outcome_text, battle_results)):
        return False
    castle_text = normalized
    if _future_claim(normalized):
        castle_text = re.sub(
            r'(?:占領|奪取|奪還|制圧)(?:を)?(?:狙(?:う|い|います)|目指(?:す|し|します)|'
            r'する予定|の予定|です|だ)', '', castle_text)
        castle_text = re.sub(
            r'(?:占領|奪取|奪還|制圧)(?:を)?(?:へ|に)(?:向け|向か|進|動|挑)', '', castle_text)
        castle_text = re.sub(
            r'(?:占領|奪取|奪還|制圧)(?:を)?(?:です|だ)?(?=[。、]|$)', '', castle_text)
        castle_text = re.sub(
            r'(?:占領|奪取|奪還|制圧)(?:を)?へ(?!成功|失敗|至|到達)', '', castle_text)
    if not castle_results_match(castle_text, facts, allow_unnamed=True):
        return False
    if re.search(r'出撃(?:した|しました|完了)', normalized) and not any(
            fact.get('kind') == 'order_launched' for fact in facts):
        return False
    if re.search(r'(?:購入|買(?:った|いました))(?:した|しました|済み)?', normalized) and not any(
            fact.get('kind') == 'buy' for fact in facts):
        return False
    if re.search(r'補充(?:した|しました|済み|完了)', normalized) and not any(
            fact.get('kind') == 'soldier_refill_receipt' for fact in facts):
        return False
    if re.search(r'城を(?:取|手に入|落と)', normalized):
        before = normalized[:normalized.find('城を')]
        castle = _last_name(before, {fact.get('castle') for fact in facts
                                     if fact.get('kind') in {'castle_owned_observed',
                                                             'castle_lost_observed'}})
        if not castle or not any(fact.get('castle') == castle and
                                 fact.get('kind') in {'castle_owned_observed',
                                                      'castle_lost_observed'}
                                 for fact in facts):
            return False
    if _future_claim(normalized) and not any(fact.get('kind') in {'current_order', 'month_plan'}
                                             for fact in facts):
        return False
    return True


# ---------------------------------------------------------------- novelty

def normalize(text):
    value = unicodedata.normalize('NFKC', text)
    return re.sub(r'[\s、。，．,.!?！？「」『』（）()【】・…:;：；「」]', '', value)


def _shingles(value, size=4):
    return {value[index:index + size] for index in range(max(0, len(value) - size + 1))}


def _lcs(left, right):
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for char in left:
        current = [0]
        for index, other in enumerate(right, 1):
            value = previous[index - 1] + 1 if char == other else 0
            current.append(value)
            if value > best:
                best = value
        previous = current
    return best


def similarity(left, right):
    """Normalized 0..1 measure; >= 1.0 means the new line repeats an old one.

    Long recaps naturally share the date, chapter and battle names.  The
    opening and substring thresholds are therefore relaxed for texts longer
    than a short scene line, while near-identical long text still fails on
    shingle overlap.
    """
    left, right = normalize(left), normalize(right)
    if len(left) < 8 or len(right) < 8:
        return 1.0
    shortest = min(len(left), len(right))
    if shortest < 150 and left[:8] == right[:8]:
        return 1.0
    left_shingles, right_shingles = _shingles(left), _shingles(right)
    union = left_shingles | right_shingles
    jaccard = len(left_shingles & right_shingles) / len(union) if union else 0.0
    containment = (len(left_shingles & right_shingles)
                   / min(len(left_shingles), len(right_shingles)))
    lcs_limit = max(12.0, shortest * 0.12)
    return max(jaccard / 0.35, containment / 0.65, _lcs(left, right) / lcs_limit)


def choose_novel(candidates, recent):
    """The least-similar valid candidate, or None when every one repeats."""
    best = None
    for text in candidates:
        score = max((similarity(text, old) for old in recent), default=0.0)
        if score < 1.0 and (best is None or score < best[0]):
            best = (score, text)
    return best[1] if best else None


# ---------------------------------------------------------------- scheduling

@contextmanager
def _lock(runtime_dir):
    path = Path(runtime_dir) / LOCK
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            yield None
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield None
            return
        yield fd
    finally:
        os.close(fd)


def _spawn(repo_root, runtime_dir, fd, config_path):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(repo_root) / 'src')
    env['DOCICH_CONFIG'] = str(config_path)
    err_fd = os.open(Path(runtime_dir) / ERR_FILE,
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        subprocess.Popen(
            [sys.executable, '-m', 'docich.hanjuku_periodic_worker',
             '--config', str(config_path), '--runtime', str(runtime_dir),
             '--lock-fd', str(fd)],
            cwd=str(repo_root), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=err_fd, start_new_session=True,
            close_fds=True, pass_fds=(fd,),
        )
    finally:
        os.close(err_fd)


def _result(runtime_dir, identity, now):
    value = _read_json(Path(runtime_dir) / RESULT, limit=32768)
    if (type(value.get('schema')) is not int or value['schema'] != 1
            or any(value.get(key) != item for key, item in identity.items())
            or type(value.get('seq')) is not int or value['seq'] < 1
            or type(value.get('at')) not in (int, float) or not math.isfinite(value['at'])
            or value.get('status') not in RETRY_STATUSES | QUIET_STATUSES | {'enqueued'}
            or value['at'] > now + 5):
        return None
    return value


def _apply(runtime_dir, state, result, cfg, now):
    status = result['status']
    changed = True
    if status == 'enqueued' and not (isinstance(result.get('text'), str)
                                     and MIN_TEXT <= len(result['text'].strip()) <= MAX_TEXT):
        status = 'invalid_output'
    if status == 'enqueued':
        recent = [text for text in state['recent_texts'] if text != result['text']]
        state['recent_texts'] = (recent + [result['text'].strip()])[-RECENT:]
        state['last_delivered_at'] = result['at']
        state['next_at'] = max(result['at'] + cfg['interval_s'], now + 60.0)
        state['failures'] = 0
    elif status in RETRY_STATUSES:
        state['failures'] = min(9, state['failures'] + 1)
        state['next_at'] = now + (cfg['interval_s'] if state['failures'] > RETRY_LIMIT
                                  else cfg['retry_s'])
    else:
        state['failures'] = 0
        state['next_at'] = now + cfg['interval_s']
    state['last_result_seq'] = result['seq']
    state['pending_seq'] = None
    state['defer_since'] = None
    _log(runtime_dir, {'event': 'applied', 'status': status, 'seq': result['seq'],
                       'failures': state['failures']})
    return changed


def tick(g, game, runtime_dir, run, identity, *, now=None):
    """Corner-owned scheduler: publish at most one request and start one worker."""
    try:
        ident = checked_identity(identity)
    except ValueError:
        return None
    runtime_dir = Path(runtime_dir)
    if runtime_dir.name != ident['runtime_id']:
        return None
    if any(run.get(key) != value for key, value in ident.items()):
        return None
    if run.get('playing') is not True or run.get('terminal_reason') or run.get('terminal_candidate'):
        return None
    now = time.time() if now is None else now
    state = _state(runtime_dir, ident)
    changed = False
    try:
        cfg = config(game)
    except Exception:
        return None
    if not state['run_started_at']:
        # The first observation initializes the schedule from the live game
        # config, so enabling the section does not require a corner restart
        # before the first 15-minute wait.
        cfg = config(game, _game_config_path(g))
        state['run_started_at'] = now
        state['next_at'] = now + cfg['interval_s']
        changed = True
    result = _result(runtime_dir, ident, now)
    if result is not None and result['seq'] > state['last_result_seq']:
        changed = _apply(runtime_dir, state, result, cfg, now) or changed
    if state['pending_seq'] is not None:
        if state['last_attempt_at'] is None:
            state['pending_seq'] = None
            changed = True
        elif now - state['last_attempt_at'] > PENDING_TIMEOUT_S:
            with _lock(runtime_dir) as fd:
                if fd is None:
                    if changed:
                        _save_state(runtime_dir, state)
                    return None
            state['pending_seq'] = None
            state['failures'] = min(9, state['failures'] + 1)
            state['next_at'] = now + cfg['retry_s']
            _log(runtime_dir, {'event': 'worker_lost', 'seq': state['seq']})
            changed = True
    if now < state['next_at']:
        if changed:
            _save_state(runtime_dir, state)
        return None
    live = config(game, _game_config_path(g))
    if not live['enabled']:
        state['next_at'] = now + live['interval_s']
        _save_state(runtime_dir, state)
        return None
    cfg = live
    if run.get('phase') == 'battle':
        if state['defer_since'] is None:
            state['defer_since'] = now
        if now - state['defer_since'] < DEFER_MAX_S:
            state['next_at'] = now + DEFER_STEP_S
            _save_state(runtime_dir, state)
            _log(runtime_dir, {'event': 'deferred', 'reason': 'battle'})
            return None
    else:
        state['defer_since'] = None
    value = situation(runtime_dir, run, state, now)
    if value is None:
        state['next_at'] = now + NO_MATERIAL_RETRY_S
        _save_state(runtime_dir, state)
        _log(runtime_dir, {'event': 'skipped', 'reason': 'no_material'})
        return None
    with _lock(runtime_dir) as fd:
        if fd is None:
            if changed:
                _save_state(runtime_dir, state)
            return None
        seq = state['seq'] + 1
        request = {'schema': 1, 'version': VERSION, **ident, 'seq': seq,
                   'at': now, 'expires_at': now + REQUEST_TTL_S,
                   'recent_texts': state['recent_texts'],
                   'situation': value}
        try:
            atomic_write_json(runtime_dir / REQUEST, request)
        except Exception:
            state['next_at'] = now + cfg['retry_s']
            _save_state(runtime_dir, state)
            _log(runtime_dir, {'event': 'skipped', 'reason': 'request_write_failed'})
            return None
        state.update(seq=seq, last_attempt_at=now, pending_seq=seq,
                     next_at=now + cfg['interval_s'], defer_since=None)
        _save_state(runtime_dir, state)
        try:
            config_path = getattr(g, 'config_path', None) or (
                Path(g.repo_root) / 'config/docich.toml')
            _spawn(g.repo_root, runtime_dir, fd, config_path)
        except Exception:
            state.update(pending_seq=None, failures=min(9, state['failures'] + 1),
                         next_at=now + cfg['retry_s'])
            _save_state(runtime_dir, state)
            _log(runtime_dir, {'event': 'skipped', 'reason': 'spawn_failed'})
            return None
        _log(runtime_dir, {'event': 'requested', 'seq': seq,
                           'events': len(value['events'])})
        return request
