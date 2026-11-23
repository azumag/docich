"""Record complete in-game months with no enemy-directed sortie.

The bot can spend a whole game month in the monthly scan, battles, camp recalls
or a failed plan and never launch a sortie.  month_sorties (docich#4642)
already counts confirmed departures toward enemy castles, but it resets on the
next month scope, so the zero month leaves no durable record.  This module
watches the policy state around decide (the caller passes the previous
observation and the new one), closes the month at the boundary and writes a
bounded, secret-free incident to the runtime directory.

The audit is evidence only.  It never sends input, never edits the plan and
never changes a decision.  Writes are best-effort: a failure is reported as a
fixed code by the caller and the observation continues.

Metric (owner decision 2026-11-20):

- confirmed: month_sorties.launches for the closed month.
- departures: distinct chart steps with order_launched,
  sortie_departed_observed or sortie_launch_confirmed in the month.
- zero_sortie: both are 0 and the month was observed from its start.
- move (own-castle transfer) and house repairs are reported separately and
  never make a month non-zero.

Artifacts:

- hanjuku_month_audit.json: open month + pending incident (small, atomic).
- hanjuku_month_audit.jsonl: sealed incidents (append-only, rotate 4 MB).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter

SCHEMA = 1
AUDIT_FILE = 'hanjuku_month_audit.json'
LOG_NAME = 'hanjuku_month_audit'
TITLE_PREFIX = '[半熟英雄] 月内出撃ゼロ'
AFTER_WINDOW_SECONDS = 300
BOSS_NO_BATTLE_SECONDS = 600
CAMP_ISSUE_COOLDOWN_SECONDS = 900
SORTIE_ISSUE_COOLDOWN_SECONDS = 900
BATTLE_RESOURCE_ISSUE_COOLDOWN_SECONDS = 900
CAMP_ABANDON_MONTHS = 3
RECALL_KIKAN_COOLDOWN_SECONDS = 900
BATTLE_MENU_ACTIONS = frozenset({
    'battle_egg_general_attack', 'battle_egg_break_bait',
    'battle_egg_retreat_attempt', 'battle_egg_opening_preempt',
    'battle_card_selected', 'battle_card', 'battle_card_effect_confirmed',
    'battle_hero_retreat_select', 'battle_retreat_preempted_card',
    'battle_okunote_last_resort', 'battle_okunote_egg_preempt',
    'battle_okunote_summon_preempt', 'battle_okunote_select',
    'battle_survival_select',
})
BATTLE_MENU_CLOSES = frozenset({
    'battle_menu_close', 'battle_one_hit_menu_close', 'battle_egg_conserve',
    'battle_survival_unavailable', 'battle_menu_rescue_exhausted',
    'battle_menu_rescue_reclose', 'battle_card_remaining_unconfirmed',
    'battle_egg_actor_unconfirmed', 'battle_menu_context_recheck',
    'battle_menu_recheck_unconfirmed', 'battle_command_incomplete',
    'battle_card_menu_skipped_confirmed_kit', 'battle_okunote_egg_wait',
    'battle_disabled_defense_probe',
})
BATTLE_MENU_IGNORED_CLOSES = frozenset({
    'battle_menu_close', 'battle_one_hit_menu_close',
})

BATTLE_MENU_ISSUE_COOLDOWN_SECONDS = 900
SORTIE_CARDS_ISSUE_COOLDOWN_SECONDS = 900
SORTIE_CARD_PURPOSES = frozenset({'attack', 'retake', 'boss', 'invade'})
NON_SORTIE_CARD_PURPOSES = frozenset({'move', 'house', 'repair', 'defend', 'recall'})

CHIKUJOU_ISSUE_COOLDOWN_SECONDS = 900
MIN_MONTH_SECONDS = 120
MAX_GAP_SECONDS = 60
MIN_OBSERVATIONS = 20
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 32 * 1024
MAX_STATE_BYTES = 128 * 1024
MAX_TEXT = 160
MAX_LIST = 20
COMMENT_COOLDOWN_SECONDS = 3600
MAX_ISSUES = 200
DEFAULT_REPO = 'azumag/docich'
BT = chr(96)
LAUNCH_DECISIONS = frozenset({
    'order_launched', 'sortie_departed_observed', 'sortie_launch_confirmed',
    # A kept march is a real departure even when the ▲ unit could not be
    # confirmed on the Y view (g1190-ch1v4: マサラ イデ！→ボス marched while
    # the audit read departures=0 — docich#4826). Months that only have
    # unconfirmed launches still classify as 'unconfirmed_launches'.
    'sortie_launch_unconfirmed_kept',
})
MONTH_RE = re.compile(r'^([0-9]{1,2})-([0-9]{1,2})$')
SIGNAL_LABELS = {
    'order_start_without_launch': '出撃開始後に発射されない',
    'launch_confirmation_missing': '出撃成立を確認できない',
    'departure_cancelled': '出撃が取り消される',
    'monthly_scan_hold': '月次処理がマップを占有',
    'monthly_scan_yield': '月次処理を出撃へ譲ったが発射なし',
    'camp_recall_loop': '野営帰還を繰り返す',
    'battle_grind': '戦闘が長引く',
    'unread_hold': '未読画面で保留',
    'plan_all_settled': '計画の指示が尽きる',
    'no_orders': '開始可能な指示がない',
    'chart_adjust_stale': '調整チャート待ち',
    'no_source': '出撃元の将軍がいない',
    'stuck_march': '行軍が停滞',
    'ledger_unknown': '月内出撃数を確認できない',
    'boss_no_battle': 'ボス戦闘が発生しない',
    'boss_cell_missing': 'ボス位置が未測定',
    'boss_hp_missing': 'ボスHPが未観測',
    'boss_pursuit_idle': 'ボス追跡が動いていない',
    'camp_recall_failed': 'キャンプ帰還失敗',
    'camp_recall_cancelled': 'キャンプ帰還キャンセル',
    'camp_recall_unconfirmed': 'キャンプ帰還未確認',
    'camp_no_roof': '帰還先の城が見えない',
    'camp_menu_unread': '野営メニューを読めない',
    'camp_cursor_unread': '野営メニューのカーソルを読めない',
    'camp_recall_limit': '帰還操作が上限に達する',
    'camp_boss_redirect': 'ボス再出撃の帰還が失敗',
    'camp_unclassified_screen': '未分類画面で帰還を取り消す',
    'camp_arrival_unconfirmed': '帰還到着を確認できない',
    'camp_battle_interrupt': '戦闘で帰還が中断',
    'camp_in_flight': '帰還操作が残っている',
    'sortie_failed': '出撃失敗',
    'sortie_cancelled': '出撃キャンセル',
    'sortie_launch_not_observed': '出撃後に部隊が現れない',
    'sortie_source_menu': '出撃元の城メニューが開かない',
    'sortie_no_source': '出撃元の将軍がいない',
    'sortie_general_missing': '出撃一覧に将軍がいない',
    'sortie_card_menu': '切り札一覧から抜けられない',
    'sortie_card_unread': '携行札を読めない',
    'sortie_no_spare': '移動元に残す将軍がいない',
    'sortie_status_screen': '城ステータスが開き続ける',
    'sortie_hero_egg': '主人公の卵破損で出撃できない',
    'sortie_retry_exhausted': '出撃再試行の上限',
    'sortie_unit_stayed': '将軍が在城のまま',
    'sortie_order_failed': '指示が失敗',
    'battle_retreat_with_resources': '卵/切り札を残して退却',
    'battle_loss_with_resources': '卵/切り札を残して敗北',
    'battle_retreat_with_egg': '卵を残して退却',
    'battle_retreat_with_card': '切り札を残して退却',
    'battle_loss_with_egg': '卵を残して敗北',
    'battle_loss_with_card': '切り札を残して敗北',
    'camp_abandoned': '野営放置',
    'camp_abandon_3months': '3ヶ月以上野営',
    'chikujou_available_at_end': '築城可能なまま月一終了',
    'chikujou_cancelled_with_resources': '築城可能なのにキャンセル',
    'chikujou_candidate': '未試行の築城候補',
    'boss_month_zero_sortie': 'ボスフェーズで月内ボス出撃ゼロ',
    'boss_sortie_missing': 'ボスへの出撃がない',
    'recall_kikan_selected': '帰還で「きかん」を選択',
    'recall_y_jump_expected': 'Yジャンプが必要',
    'corner_stalled': '画面停滞でコーナー停止',
    'screen_stalled': '画面が停止した',
    'input_stalled': '入力が停滞した',
    'battle_menu_wasted': '戦闘Bメニューの無駄な開閉',
    'battle_menu_no_resource': '卵・切り札・退却・奥の手を実行せず閉じた',
    'sortie_without_cards': '切り札在庫があるのに非携行で出撃',
    'card_inventory_ignored': '切り札在庫を携行していない',
    'unknown': '原因未分類',
}
SIGNAL_ORDER = tuple(SIGNAL_LABELS)
# One stable issue signature per month: the first matching primary cause.
# The full signal list stays in the record for the timeline and evidence.
PRIMARY_LABELS = {
    'plan_starved': '計画の指示が尽きる',
    'departure_failure': '出撃開始後に発射されない',
    'monthly_scan': '月次処理がマップを占有',
    'camp_recall': '野営帰還を繰り返す',
    'battle': '戦闘が長引く',
    'unread': '未読画面で保留',
    'no_source': '出撃元の将軍がいない',
    'stuck_march': '行軍が停滞',
    'ledger_unknown': '月内出撃数を確認できない',
    'boss_no_battle': 'ボス戦闘が発生しない',
    'boss_cell_missing': 'ボス位置が未測定',
    'boss_hp_missing': 'ボスHPが未観測',
    'boss_pursuit_idle': 'ボス追跡が動いていない',
    'camp_recall_failed': 'キャンプ帰還失敗',
    'camp_recall_cancelled': 'キャンプ帰還キャンセル',
    'camp_recall_unconfirmed': 'キャンプ帰還未確認',
    'camp_no_roof': '帰還先の城が見えない',
    'camp_menu_unread': '野営メニューを読めない',
    'camp_cursor_unread': '野営メニューのカーソルを読めない',
    'camp_recall_limit': '帰還操作が上限に達する',
    'camp_boss_redirect': 'ボス再出撃の帰還が失敗',
    'camp_unclassified_screen': '未分類画面で帰還を取り消す',
    'camp_arrival_unconfirmed': '帰還到着を確認できない',
    'camp_battle_interrupt': '戦闘で帰還が中断',
    'camp_in_flight': '帰還操作が残っている',
    'sortie_failed': '出撃失敗',
    'sortie_cancelled': '出撃キャンセル',
    'sortie_launch_not_observed': '出撃後に部隊が現れない',
    'sortie_source_menu': '出撃元の城メニューが開かない',
    'sortie_no_source': '出撃元の将軍がいない',
    'sortie_general_missing': '出撃一覧に将軍がいない',
    'sortie_card_menu': '切り札一覧から抜けられない',
    'sortie_card_unread': '携行札を読めない',
    'sortie_no_spare': '移動元に残す将軍がいない',
    'sortie_status_screen': '城ステータスが開き続ける',
    'sortie_hero_egg': '主人公の卵破損で出撃できない',
    'sortie_retry_exhausted': '出撃再試行の上限',
    'sortie_unit_stayed': '将軍が在城のまま',
    'sortie_order_failed': '指示が失敗',
    'battle_retreat_with_resources': '卵/切り札を残して退却',
    'battle_loss_with_resources': '卵/切り札を残して敗北',
    'battle_retreat_with_egg': '卵を残して退却',
    'battle_retreat_with_card': '切り札を残して退却',
    'battle_loss_with_egg': '卵を残して敗北',
    'battle_loss_with_card': '切り札を残して敗北',
    'camp_abandoned': '野営放置',
    'camp_abandon_3months': '3ヶ月以上野営',
    'chikujou_available_at_end': '築城可能なまま月一終了',
    'chikujou_cancelled_with_resources': '築城可能なのにキャンセル',
    'chikujou_candidate': '未試行の築城候補',
    'boss_month_zero_sortie': 'ボスフェーズで月内ボス出撃ゼロ',
    'boss_sortie_missing': 'ボスへの出撃がない',
    'recall_kikan_selected': '帰還で「きかん」を選択',
    'recall_y_jump_expected': 'Yジャンプが必要',
    'corner_stalled': '画面停滞でコーナー停止',
    'screen_stalled': '画面が停止した',
    'input_stalled': '入力が停滞した',
    'battle_menu_wasted': '戦闘Bメニューの無駄な開閉',
    'battle_menu_no_resource': '卵・切り札・退却・奥の手を実行せず閉じた',
    'sortie_without_cards': '切り札在庫があるのに非携行で出撃',
    'card_inventory_ignored': '切り札在庫を携行していない',
    'unknown': '原因未分類',
}
PRIMARY_SIGNALS = (
    ('sortie_without_cards', ('sortie_without_cards',)),
    ('battle_menu_wasted', ('battle_menu_wasted',)),
    ('corner_stalled', ('corner_stalled',)),
    ('recall_kikan_selected', ('recall_kikan_selected',)),
    ('boss_month_zero_sortie', ('boss_month_zero_sortie',)),
    ('chikujou_available_at_end', ('chikujou_available_at_end',)),
    ('chikujou_cancelled_with_resources', ('chikujou_cancelled_with_resources',)),
    ('camp_abandoned', ('camp_abandoned',)),
    ('battle_retreat_with_resources', ('battle_retreat_with_resources',)),
    ('battle_loss_with_resources', ('battle_loss_with_resources',)),
    ('sortie_failed', ('sortie_failed',)),
    ('sortie_cancelled', ('sortie_cancelled',)),
    ('camp_recall_failed', ('camp_recall_failed',)),
    ('camp_recall_cancelled', ('camp_recall_cancelled',)),
    ('camp_recall_unconfirmed', ('camp_recall_unconfirmed',)),
    ('boss_no_battle', ('boss_no_battle',)),
    ('plan_starved', ('plan_all_settled', 'no_orders')),
    ('departure_failure', ('order_start_without_launch', 'launch_confirmation_missing',
                           'departure_cancelled')),
    ('monthly_scan', ('monthly_scan_hold', 'monthly_scan_yield')),
    ('camp_recall', ('camp_recall_loop',)),
    ('battle', ('battle_grind',)),
    ('unread', ('unread_hold',)),
    ('no_source', ('no_source',)),
    ('stuck_march', ('stuck_march',)),
    ('ledger_unknown', ('ledger_unknown',)),
)


class AuditError(Exception):
    """Fixed-code audit failure; never carries paths or raw exceptions."""


def _bounded_text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return None
    text = ''.join(ch for ch in value if ch >= ' ' and ch != '\x7f').strip()
    return text[:limit] if text else None


def _bounded_int(value, minimum=0, maximum=10 ** 9):
    return value if type(value) is int and minimum <= value <= maximum else None


def _names(value, limit=12):
    if not isinstance(value, list):
        return None
    names = [_bounded_text(item, 48) for item in value[:limit]]
    return [name for name in names if name]


def _text_map(value, limit=12):
    if not isinstance(value, dict):
        return None
    out = {}
    for key, item in list(value.items())[:limit]:
        key = _bounded_text(key, 48) if isinstance(key, str) else None
        item = _bounded_text(item, 48)
        if key and item:
            out[key] = item
    return out or None


def _next_month(month):
    match = MONTH_RE.fullmatch(month) if isinstance(month, str) else None
    if match is None:
        return None
    year, number = int(match.group(1)), int(match.group(2))
    if not 1 <= number <= 12:
        return None
    return f'{year + 1}-1' if number == 12 else f'{year}-{number + 1}'


def _scope(state):
    mem = state.get('policy') if isinstance(state, dict) else None
    if not isinstance(mem, dict):
        return None
    chapter, month = mem.get('chapter'), mem.get('month')
    if type(chapter) is not int or not isinstance(month, str) or not month:
        return None
    return chapter, month


def _identity(state, mem=None):
    mem = mem if isinstance(mem, dict) else {}
    trace = state.get('decision_trace') if isinstance(state, dict) else None
    source = trace if isinstance(trace, dict) else mem.get('_run_identity')
    keys = ('game', 'runtime_id', 'generation', 'lease_id')
    if not isinstance(source, dict):
        return {}
    out = {name: source.get(name) for name in keys}
    if not isinstance(out.get('runtime_id'), str):
        out['runtime_id'] = None
    if type(out.get('generation')) is not int:
        out['generation'] = None
    if not isinstance(out.get('lease_id'), str):
        out['lease_id'] = None
    return out


def _identity_key(identity):
    identity = identity if isinstance(identity, dict) else {}
    return (identity.get('runtime_id'), identity.get('generation'), identity.get('lease_id'))


def _summarize_plan(plan):
    plan = plan if isinstance(plan, dict) else {}
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    chart = plan.get('chart_plan') if isinstance(plan.get('chart_plan'), dict) else {}
    chart_orders = chart.get('orders') if isinstance(chart.get('orders'), list) else []
    return {
        'active': _bounded_text(plan.get('active'), 64),
        'orders': {_bounded_text(step, 64) or '?': _bounded_text(status, 24) or '?'
                   for step, status in list(orders.items())[:60]
                   if isinstance(step, str) and isinstance(status, str)},
        'chart_plan': {
            'source': _bounded_text(chart.get('source'), 32),
            'request_id': _bounded_text(chart.get('request_id'), 32),
            'orders': [{
                'step': _bounded_text(order.get('step'), 64),
                'purpose': _bounded_text(order.get('purpose'), 32),
                'source': _bounded_text(order.get('source'), 48),
                'target': _bounded_text(order.get('target'), 48),
                'general': _bounded_text(order.get('general'), 48),
                'note': _bounded_text(order.get('note'), 120),
                'after': _bounded_text(order.get('after'), 48),
            } for order in chart_orders[:MAX_LIST] if isinstance(order, dict)],
        },
    }


def _summarize_world(world, tick):
    world = world if isinstance(world, dict) else {}
    tick = tick if type(tick) is int else None
    garrison = world.get('garrison') if isinstance(world.get('garrison'), dict) else {}
    sorties = world.get('sorties') if isinstance(world.get('sorties'), dict) else {}
    launched = world.get('launched_orders') if isinstance(world.get('launched_orders'), dict) else {}
    history = world.get('sortie_history') if isinstance(world.get('sortie_history'), list) else []
    ownership = world.get('castle_ownership') if isinstance(world.get('castle_ownership'), dict) else {}

    def age(value):
        started = value.get('tick') if isinstance(value, dict) else None
        if tick is None or type(started) is not int or started < 0 or tick < started:
            return None
        return min(tick - started, 10 ** 7)

    return {
        'garrison': {_bounded_text(castle, 48) or '?': _names(names, 12)
                     for castle, names in list(garrison.items())[:12]
                     if isinstance(castle, str)},
        'sorties': {_bounded_text(step, 64) or '?': {
            'status': _bounded_text(value.get('status'), 32),
            'general': _bounded_text(value.get('general'), 48),
            'source': _bounded_text(value.get('source'), 48),
            'target': _bounded_text(value.get('target') or value.get('planned_target'), 48),
            'purpose': _bounded_text(value.get('purpose'), 32),
            'age_ticks': age(value),
        } for step, value in list(sorties.items())[:MAX_LIST] if isinstance(value, dict)},
        'launched_orders': {_bounded_text(step, 64) or '?': {
            'target': _bounded_text(value.get('target'), 48),
            'source': _bounded_text(value.get('source'), 48),
            'general': _bounded_text(value.get('general'), 48),
            'purpose': _bounded_text(value.get('purpose'), 32),
            'age_ticks': age(value),
        } for step, value in list(launched.items())[:MAX_LIST] if isinstance(value, dict)},
        'captured': _names(world.get('captured'), 20),
        'lost': _names(world.get('lost'), 20),
        'castle_ownership': _text_map(ownership, 16),
        'gold': _bounded_int(world.get('gold'), 0, 10 ** 7),
        'sortie_history': [{
            'outcome': _bounded_text(row.get('outcome'), 24),
            'general': _bounded_text(row.get('general'), 48),
            'target': _bounded_text(row.get('target'), 48),
            'tick': _bounded_int(row.get('tick'), 0, 10 ** 9),
            'age_ticks': age(row),
        } for row in history[-8:] if isinstance(row, dict)],
    }


def _summarize_flow(flow):
    flow = flow if isinstance(flow, dict) else {}
    keys = {
        'month_sub': ('stage', 'kind', 'step', 'attempts', 'reads'),
        'post_month_status': ('stage', 'kind', 'phase_reads', 'floor_yields', 'attempts'),
        'house': ('stage', 'goal', 'kind'),
        'recall': ('stage', 'steps', 'target', 'kind'),
        'departure_request': ('status', 'step', 'general', 'target'),
        'scene_hold': ('stage', 'kind'),
        'yes_no_unread': ('stage', 'kind'),
        'army_count_check': ('stage', 'result'),
    }
    out = {}
    for name, fields in keys.items():
        value = flow.get(name)
        if not isinstance(value, dict):
            continue
        row = {}
        for field in fields:
            item = value.get(field)
            if isinstance(item, str):
                row[field] = _bounded_text(item, 48)
            elif type(item) is int:
                row[field] = _bounded_int(item, 0, 10 ** 7)
            elif type(item) is bool:
                row[field] = item
        if row:
            out[name] = row
    for name, value in list(flow.items())[:24]:
        if name in out or not isinstance(name, str) or not isinstance(value, dict):
            continue
        row = {}
        for field in ('stage', 'kind', 'status', 'result', 'attempts', 'reads', 'steps'):
            item = value.get(field)
            if isinstance(item, str):
                row[field] = _bounded_text(item, 48)
            elif type(item) is int:
                row[field] = _bounded_int(item, 0, 10 ** 7)
        if row:
            out[name] = row
    return out


def _summarize_stats(mem):
    stats = mem.get('stats') if isinstance(mem.get('stats'), dict) else {}
    tally = mem.get('tally') if isinstance(mem.get('tally'), dict) else {}
    return {
        'wins': _bounded_int(stats.get('wins'), 0, 10 ** 6),
        'losses': _bounded_int(stats.get('losses'), 0, 10 ** 6),
        'unclassified': _bounded_int(stats.get('unclassified'), 0, 10 ** 6),
        'cards_confirmed': _bounded_int(stats.get('cards_confirmed'), 0, 10 ** 6),
        'battles_started': _bounded_int(tally.get('battles_started'), 0, 10 ** 6),
        'battles_judged': _bounded_int(tally.get('battles_judged'), 0, 10 ** 6),
        'castle_losses': _bounded_int(tally.get('castle_losses'), 0, 10 ** 6),
    }


def _world_snapshot(mem):
    """WORLD region plus un-migrated top-level observation keys.

    gold, captured, lost, castle_ownership and sortie_history still live at
    the policy top level in long runs (the region migration is gradual).  A
    record that read only mem['world'] showed gold=None and no captures even
    though the state had them.
    """
    from .state.regions import WORLD, region
    data = dict(region(mem, WORLD))
    for key in ('gold', 'captured', 'lost', 'castle_ownership', 'sortie_history'):
        if data.get(key) is None:
            data[key] = mem.get(key)
    return data



def _boss_phase(mem):
    """The boss is the only remaining target, or None.

    Chapter 9's castles exclude the moving ボス tile, while chapters 1-3 and
    5-12 keep it in the castle table and chapter 4 has four 四季王 sites.
    The phase starts only when every non-boss castle is owned and at least
    one boss site is still not ours. A lost normal castle leaves the phase.
    """
    from .. import hanjuku_chart as chart
    chapter = mem.get('chapter')
    if type(chapter) is not int or chapter <= 0:
        return None
    castles = set(chart.castles(chapter) or ())
    bosses = set(chart.boss_sites(chapter) or ())
    if not castles or not bosses or mem.get('boss_defeated') == chapter:
        return None
    owned = set(mem.get('captured') or [])
    owned -= set(mem.get('lost') or [])
    if not mem.get('home_lost'):
        home = chart.home_castle(chapter)
        if home:
            owned.add(home)
    normal = castles - bosses
    if not normal or not normal <= owned:
        return None
    remaining = bosses - owned
    if not remaining:
        return None
    return {
        'chapter': chapter,
        'boss_name': chart.BOSSES.get(chapter),
        'bosses': sorted(remaining),
        'normal_castles': len(normal),
        'owned_count': len(owned),
    }


def _boss_battle_seen(mem, records, chapter, boss_name):
    """True when this boss phase produced any observed boss fight."""
    from .. import hanjuku_chart as chart
    from .. import hanjuku_reference as reference
    if mem.get('boss_defeated') == chapter:
        return True
    victories = mem.get('boss_victories')
    if isinstance(victories, dict) and victories:
        return True
    if mem.get('boss_hp_seen') is not None:
        return True
    history = mem.get('boss_hp_history')
    if isinstance(history, list) and history:
        return True
    boss_sites = set(chart.boss_sites(chapter) or ())
    for row in mem.get('sortie_history') or ():
        if isinstance(row, dict) and (row.get('target') in boss_sites
                                      or row.get('castle') in boss_sites):
            return True
    boss_names = {boss_name} | set(chart.BOSSES.values()) | set(reference.BOSS_GENERALS)
    for record in records or ():
        if not isinstance(record, dict):
            continue
        if record.get('decision') == 'battle_start':
            enemy = record.get('enemy')
            if enemy in boss_names or reference.is_boss_target(enemy):
                return True
        elif record.get('decision') == 'sortie_outcome':
            metric = record.get('observed_metric')
            castle = record.get('castle') or (metric.get('castle')
                                              if isinstance(metric, dict) else None)
            if castle in boss_sites:
                return True
        elif record.get('decision') in ('boss_defeated', 'boss_victory', 'boss_defeated_observed'):
            return True
    return False


def _rows_boss_battle(rows, chapter, boss_name):
    """Boss battle evidence in a decision window (persisted across observations)."""
    from .. import hanjuku_chart as chart
    from .. import hanjuku_reference as reference
    boss_sites = set(chart.boss_sites(chapter) or ())
    boss_names = {boss_name} | set(chart.BOSSES.values()) | set(reference.BOSS_GENERALS)
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        if row.get('decision') == 'battle_start':
            enemy = row.get('enemy')
            if enemy in boss_names or reference.is_boss_target(enemy):
                return True
        elif row.get('decision') == 'sortie_outcome':
            metric = row.get('observed_metric')
            castle = row.get('castle') or (metric.get('castle')
                                           if isinstance(metric, dict) else None)
            if castle in boss_sites:
                return True
        elif row.get('decision') in ('boss_defeated', 'boss_victory', 'boss_defeated_observed'):
            return True
    return False


def _boss_incident_id(identity, current):
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|boss|"
           f"{current.get('chapter')}|{current.get('started_at')}")
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]


def _build_boss_incident(runtime, state, after, records, now, current, rows=None):
    mem = after.get('policy') or {}
    context = _context(after)
    if rows is None:
        rows = _read_decisions(runtime, current.get('started_at') or now, now,
                               state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    battle_starts = counts.get('battle_start', 0)
    from .. import hanjuku_reference as reference
    boss_battles = sum(1 for row in rows if row.get('decision') == 'battle_start'
                       and (row.get('enemy') == current.get('boss_name')
                            or row.get('enemy') in reference.BOSS_GENERALS))
    signals = ['boss_no_battle']
    if not mem.get('boss_cell'):
        signals.append('boss_cell_missing')
    if not (mem.get('boss_hp_seen') or mem.get('boss_hp_history')):
        signals.append('boss_hp_missing')
    if not any(name.startswith('boss_pursuit') for name in counts):
        signals.append('boss_pursuit_idle')
    for name in _signals(context, counts, 0, 0, battle_starts):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    confidence = 'high'
    reasons = []
    if float(current.get('max_gap_s') or 0) > MAX_GAP_SECONDS:
        reasons.append('observation_gap')
    if int(current.get('observations') or 0) < 20:
        reasons.append('few_observations')
    if context is None:
        reasons.append('context_missing')
    if reasons:
        confidence = 'medium' if reasons == ['observation_gap'] else 'low'
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': current.get('chapter'), 'month': mem.get('month'),
                 'first_at': current.get('started_at'), 'last_at': now,
                 'closed_at': now, 'duration_s': max(0.0, now - current.get('started_at')),
                 'first_tick': current.get('first_tick'), 'last_tick': current.get('last_tick'),
                 'duration_ticks': None, 'observations': current.get('observations'),
                 'max_gap_s': current.get('max_gap_s'), 'started_clean': True}
    move_launches = sum(1 for row in rows if row.get('decision') == 'order_launched'
                        and row.get('purpose') == 'move')
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    identity = state.get('identity') or {}
    signal_text = '+'.join(signals) if signals else 'boss_no_battle'
    record = {
        'schema': SCHEMA,
        'event': 'boss_phase_no_battle',
        'kind': 'boss_phase_no_battle',
        'confidence': confidence,
        'confidence_reasons': reasons,
        'chapter': current.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'boss_phase': {
            'chapter': current.get('chapter'),
            'boss_name': current.get('boss_name'),
            'bosses': list(current.get('bosses') or []),
            'started_at': current.get('started_at'),
            'first_tick': current.get('first_tick'),
            'last_at': now,
            'last_tick': current.get('last_tick'),
            'observed_s': max(0.0, now - (current.get('started_at') or now)),
            'observations': current.get('observations'),
            'max_gap_s': current.get('max_gap_s'),
            'battle_count': 0,
            'battle_starts': battle_starts,
            'boss_hp_seen': mem.get('boss_hp_seen'),
            'boss_hp_history_count': len(mem.get('boss_hp_history') or []),
            'boss_cell': mem.get('boss_cell'),
            'captured_count': len(mem.get('captured') or []),
        },
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
            'battle_starts': battle_starts,
            'boss_battles': boss_battles,
            'moves': move_launches,
            'house_dispatches': counts.get('goal_dispatch', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_starts, 'boss_battles': boss_battles},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': current.get('bot_version') or after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    record['incident_id'] = _boss_incident_id(identity, current)
    record['signature'] = 'boss_no_battle'
    record['fingerprint'] = _fingerprint(current.get('chapter'), 'boss_phase_no_battle',
                                          ['boss_no_battle'])
    record['auto_file'] = confidence == 'high'
    return _shrink(record)



CAMP_TERMINAL = {
    'camp_recall_aborted': ('camp_recall_failed', 'failed'),
    'camp_boss_redirect_aborted': ('camp_recall_failed', 'failed'),
    'camp_recall_skipped': ('camp_recall_cancelled', 'cancelled'),
    'camp_recall_unconfirmed': ('camp_recall_unconfirmed', 'unconfirmed'),
}
CAMP_OUTCOME_RANK = {'failed': 0, 'cancelled': 1, 'unconfirmed': 2}


def _camp_reason_code(decision, event, reason):
    text = f"{event or ''} {reason or ''}"
    if 'camp_menu_not_opened' in text or 'メニューが開かない' in text:
        return 'camp_menu_unread'
    if 'cursor_unconfirmed' in text or 'カーソル' in text:
        return 'camp_cursor_unread'
    if '上限' in text:
        return 'camp_recall_limit'
    if '屋根' in text or 'no_roof' in text:
        return 'camp_no_roof'
    if 'unclassified_screen' in text or '未分類' in text:
        return 'camp_unclassified_screen'
    if 'arrival_unconfirmed' in text or '到着' in text:
        return 'camp_arrival_unconfirmed'
    if decision == 'camp_boss_redirect_aborted':
        return 'camp_boss_redirect'
    return 'other'


def _camp_event(record):
    """One terminal camp-recall failure/cancel/unconfirmed decision, or None."""
    if not isinstance(record, dict):
        return None
    decision = record.get('decision')
    if decision not in CAMP_TERMINAL and decision != 'camp_group_screen':
        return None
    event = record.get('resulting_event') if isinstance(record.get('resulting_event'), str) else None
    if decision == 'camp_group_screen' and event != 'unclassified_screen_closed':
        return None
    metric = record.get('observed_metric')
    metric = metric if isinstance(metric, dict) else {}
    if decision in CAMP_TERMINAL:
        kind, outcome = CAMP_TERMINAL[decision]
    else:
        kind, outcome = 'camp_recall_cancelled', 'cancelled'
    reason = record.get('reason') if isinstance(record.get('reason'), str) else None
    camp = metric.get('camp')
    camp = list(camp) if (isinstance(camp, list) and len(camp) == 2
                          and all(type(value) is int for value in camp)) else None
    castle = _bounded_text(metric.get('castle'), 48)
    general = _bounded_text(record.get('general') or metric.get('general'), 48)
    return {
        'kind': kind,
        'outcome': outcome,
        'decision': decision,
        'reason_code': _camp_reason_code(decision, event, reason),
        'reason': _bounded_text(reason, 200),
        'resulting_event': _bounded_text(event, 64),
        'general': general,
        'camp': camp,
        'castle': castle,
        'stage': _bounded_text(metric.get('stage'), 32),
        'steps': _bounded_int(metric.get('steps'), 0, 10 ** 6),
        'input_sent': metric.get('input_sent') is True,
        'interrupted': metric.get('interrupted') is True,
        'at': record.get('at') if type(record.get('at')) in (int, float) else None,
        'screen': _bounded_text(record.get('screen_kind'), 32),
    }


def _build_camp_incident(runtime, state, after, event, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    signals = [event['kind']]
    if event.get('reason_code') and event['reason_code'] != 'other':
        signals.append(event['reason_code'])
    flow = (context or {}).get('flow') or {}
    if flow.get('recall'):
        signals.append('camp_in_flight')
    if counts.get('camp_menu_unread'):
        signals.append('camp_menu_unread')
    if counts.get('camp_recall_battle') or counts.get('camp_recall_interrupted'):
        signals.append('camp_battle_interrupt')
    seen, unique = set(), []
    for name in signals:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    signals = unique[:8]
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                 'first_at': now, 'last_at': now, 'closed_at': now,
                 'duration_s': 0.0, 'first_tick': mem.get('tick'),
                 'last_tick': mem.get('tick'), 'duration_ticks': None,
                 'observations': 0, 'max_gap_s': 0.0, 'started_clean': True}
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    identity = state.get('identity') or {}
    record = {
        'schema': SCHEMA,
        'event': event['kind'],
        'kind': event['kind'],
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'camp_event': dict(event),
        'sorties': {
            'confirmed': confirmed,
            'departures': None,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"{event['kind']}|{event['reason_code']}|{event.get('camp')}|"
           f"{event.get('general')}|{int(float(now))}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = event['kind']
    record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
    record['auto_file'] = True
    return _shrink(record)



SORTIE_TERMINAL = {
    'sortie_launch_failed': ('sortie_failed', 'failed'),
    'order_failed': ('sortie_failed', 'failed'),
    'sortie_cancelled_observed': ('sortie_cancelled', 'cancelled'),
}


def _sortie_reason_code(decision, record):
    text = ' '.join(str(record.get(key) or '') for key in
                    ('deviation_reason', 'reason', 'resulting_event'))
    if decision == 'sortie_launch_failed':
        return 'sortie_launch_not_observed'
    if decision == 'sortie_cancelled_observed':
        return 'sortie_unit_stayed'
    if decision == 'situation_held':
        return 'sortie_retry_exhausted'
    if '城メニュー' in text or '開かず' in text:
        return 'sortie_source_menu'
    if '本城も出せない' in text:
        return 'sortie_no_source'
    if '出撃一覧に見えない' in text:
        return 'sortie_general_missing'
    if '切り札一覧' in text:
        return 'sortie_card_menu'
    if '携行札を読めない' in text:
        return 'sortie_card_unread'
    if '移動元に残す将軍がいない' in text:
        return 'sortie_no_spare'
    if 'ステータス表示が開き続けた' in text:
        return 'sortie_status_screen'
    if '卵破損' in text:
        return 'sortie_hero_egg'
    if '出撃要求が送信・受理されない' in text:
        return 'sortie_order_unaccepted'
    return 'sortie_order_failed'


def _sortie_event(record):
    """One terminal sortie failure/cancel decision, or None."""
    if not isinstance(record, dict):
        return None
    decision = record.get('decision')
    variant = record.get('strategy_variant')
    if decision == 'situation_held':
        if variant not in ('attack_retry_exhausted', 'boss_retry_exhausted'):
            return None
        kind, outcome = 'sortie_failed', 'failed'
    elif decision in SORTIE_TERMINAL:
        kind, outcome = SORTIE_TERMINAL[decision]
    else:
        return None
    metric = record.get('observed_metric')
    metric = metric if isinstance(metric, dict) else {}
    step = _bounded_text(record.get('chart_step') or metric.get('step'), 64)
    general = _bounded_text(record.get('general') or metric.get('general'), 48)
    source = _bounded_text(record.get('source') or metric.get('source'), 48)
    target = _bounded_text(record.get('target') or metric.get('target'), 48)
    purpose = _bounded_text(record.get('purpose') or metric.get('purpose'), 32)
    reason = record.get('deviation_reason') or record.get('reason')
    return {
        'kind': kind,
        'outcome': outcome,
        'decision': decision,
        'reason_code': _sortie_reason_code(decision, record),
        'reason': _bounded_text(reason, 200),
        'step': step,
        'step_local': step.rsplit(':', 1)[-1] if step else None,
        'general': general,
        'source': source,
        'target': target,
        'purpose': purpose,
        'misses': _bounded_int(metric.get('misses'), 0, 10 ** 6),
        'strategy_variant': _bounded_text(variant, 40),
        'at': record.get('at') if type(record.get('at')) in (int, float) else None,
        'screen': _bounded_text(record.get('screen_kind'), 32),
    }


def _build_sortie_incident(runtime, state, after, event, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = [event['kind']]
    if event.get('reason_code'):
        signals.append(event['reason_code'])
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                 'first_at': now, 'last_at': now, 'closed_at': now,
                 'duration_s': 0.0, 'first_tick': mem.get('tick'),
                 'last_tick': mem.get('tick'), 'duration_ticks': None,
                 'observations': 0, 'max_gap_s': 0.0, 'started_clean': True}
    # docich#5162 (owner 2026-11-23): only the explicit "order was not sent or
    # accepted" reason is transient when the month has other confirmed launches.
    # Unknown order failures, confirmed launch failures, and exhausted retries
    # remain high so an unrelated failure cannot be silently downgraded.
    confidence, confidence_reasons = 'high', []
    if (event.get('decision') == 'order_failed'
            and event.get('reason_code') == 'sortie_order_unaccepted'
            and type(confirmed) is int and confirmed > 0):
        confidence = 'medium'
        confidence_reasons = ['sortie_month_launched']
    identity = state.get('identity') or {}
    record = {
        'schema': SCHEMA,
        'event': event['kind'],
        'kind': event['kind'],
        'confidence': confidence,
        'confidence_reasons': confidence_reasons,
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'sortie_event': dict(event),
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
            'sortie_launch_failed': counts.get('sortie_launch_failed', 0),
            'sortie_cancelled_observed': counts.get('sortie_cancelled_observed', 0),
            'sortie_launch_unverified': counts.get('sortie_launch_unverified', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"{event['kind']}|{event['reason_code']}|{event.get('step_local')}|"
           f"{event.get('general')}|{event.get('target')}|{int(float(now))}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = event['kind']
    record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
    record['auto_file'] = True
    return _shrink(record)


SORTIE_NON_PURPOSES = frozenset({'move', 'house', 'repair', 'recall', 'defend'})


def _context_order(after, step):
    context = _context(after)
    chart = ((context or {}).get('plan') or {}).get('chart_plan') or {}
    orders = chart.get('orders') if isinstance(chart.get('orders'), list) else []
    for order in orders:
        if isinstance(order, dict) and order.get('step') == step:
            return order
    return {}



def _remaining_cards(selected, used):
    used = [card for card in (used or []) if isinstance(card, str)]
    remaining = []
    for card in selected or ():
        if not isinstance(card, str):
            continue
        if card in used:
            used.remove(card)
        else:
            remaining.append(card)
    return remaining[:12]


def _battle_resources(record):
    metric = record.get('observed_metric')
    metric = metric if isinstance(metric, dict) else {}
    egg = _bounded_int(metric.get('egg_uses'), 0, 10 ** 6)
    selected = [card for card in (metric.get('cards_selected') or [])
                if isinstance(card, str)][:12]
    used = [card for card in (metric.get('cards_used') or [])
            if isinstance(card, str)][:12]
    pending = record.get('card')
    if not isinstance(pending, str):
        pending = metric.get('card_pending') if isinstance(metric.get('card_pending'), str) else None
    remaining = _remaining_cards(selected, used)
    if pending and pending not in remaining and pending not in used:
        remaining.append(pending)
    # owner 決定 2026-11-23 (#5117): the live list proved this card missing
    # (battle_card_missing), so it is not carried and not a remaining resource.
    missing = {card for card in (metric.get('cards_missing') or ())
               if isinstance(card, str)}
    remaining = [card for card in remaining if card not in missing]
    # docich#5101: keep the remaining-card signature unchanged (the retreat with
    # a card left is the alert either way), but expose whether that card had
    # already been tried, so triage can tell "the spend attempt failed" from
    # "the card was never given a chance".
    attempted = {card for card in (metric.get('cards_attempted') or ())
                 if isinstance(card, str)}
    retried = {card for card in (metric.get('cards_spend_retry') or ())
               if isinstance(card, str)}
    resources = []
    if egg:
        resources.append('egg')
    if remaining:
        resources.append('card')
    return {
        'resources': resources,
        'egg_uses': egg,
        'cards_selected': selected,
        'cards_used': used,
        'cards_remaining': remaining[:12],
        'cards_missing': sorted(missing)[:12],
        'card_pending': pending,
        'cards_attempted': sorted(attempted)[:12],
        'cards_spend_retry': sorted(retried)[:12],
        'cards_unconfirmed_egg_drop': _egg_drop_cards(remaining)[:12],
    }


def _egg_drop_cards(cards):
    """Remaining cards whose consumption this audit cannot confirm (#5165).

    Egg-drop cards act by dropping the enemy's egg. v129 forbids treating an
    enemy HP drop as a confirmed consumption without a calibrated screen
    signature, so a remaining egg-drop card is *structurally unconfirmed*
    rather than evidence that the bot skipped a usable card.
    """
    from .. import hanjuku_reference as reference
    out = []
    for card in cards:
        row = reference.CARDS.get(card) if isinstance(card, str) else None
        if isinstance(row, dict) and (row.get('egg_drop') or 0) > 0:
            out.append(card)
    return out


# Must match judgment.battle.RETREAT_RESOURCE_FLOOR_HP (a test guards the
# equality). At or below this HP the battle flow deliberately stops every
# card/egg detour and retreats: g460 lost the general 24 -> 12 -> 0 while
# waiting in card menus. A retreat there is the designed safety valve, not a
# resource left behind (owner 2026-11-23, docich#5130).
SAFETY_FLOOR_HP = 4


def _battle_resource_event(record):
    """Loss/retreat while eggs or cards remain, or None."""
    if not isinstance(record, dict):
        return None
    decision = record.get('decision')
    metric = record.get('observed_metric')
    metric = metric if isinstance(metric, dict) else {}
    if decision == 'battle_hero_retreat_select':
        if record.get('choice') != 'たいきゃく':
            return None
        outcome = 'retreat'
    elif decision == 'battle_retreat_preempted_card':
        outcome = 'retreat'
    elif decision == 'battle_result':
        if record.get('outcome') != 'loss':
            return None
        outcome = 'loss'
    else:
        return None
    if outcome == 'retreat':
        hp = _bounded_int(metric.get('ally_hp'), -1, 10 ** 6)
        if hp is not None and 0 < hp <= SAFETY_FLOOR_HP:
            return None
    resources = _battle_resources(record)
    if not resources['resources']:
        return None
    general = _bounded_text(record.get('general') or record.get('ally')
                            or metric.get('general'), 48)
    return {
        'kind': ('battle_loss_with_resources' if outcome == 'loss'
                 else 'battle_retreat_with_resources'),
        'outcome': outcome,
        'decision': decision,
        'resources': resources['resources'],
        'egg_uses': resources['egg_uses'],
        'cards_selected': resources['cards_selected'],
        'cards_used': resources['cards_used'],
        'cards_remaining': resources['cards_remaining'],
        'cards_unconfirmed_egg_drop': resources['cards_unconfirmed_egg_drop'],
        'card_pending': resources['card_pending'],
        'general': general,
        'enemy': _bounded_text(record.get('enemy') or metric.get('enemy'), 48),
        'castle': _bounded_text(record.get('castle') or metric.get('castle'), 48),
        'side': _bounded_text(record.get('side') or metric.get('side'), 16),
        'step': _bounded_text(record.get('chart_step') or metric.get('step'), 64),
        'ally_hp': _bounded_int(metric.get('ally_hp'), 0, 10 ** 6),
        'enemy_hp': _bounded_int(metric.get('enemy_hp'), 0, 10 ** 6),
        'retreat_selected': metric.get('hero_retreat_selected') is True,
        'reason': _bounded_text(record.get('reason'), 200),
        'at': record.get('at') if type(record.get('at')) in (int, float) else None,
    }


def _build_battle_resource_incident(runtime, state, after, event, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = [event['kind']]
    for resource in event['resources']:
        signals.append(f"{event['kind']}_with_{resource}")
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                 'first_at': now, 'last_at': now, 'closed_at': now,
                 'duration_s': 0.0, 'first_tick': mem.get('tick'),
                 'last_tick': mem.get('tick'), 'duration_ticks': None,
                 'observations': 0, 'max_gap_s': 0.0, 'started_clean': True}
    identity = state.get('identity') or {}
    record = {
        'schema': SCHEMA,
        'event': event['kind'],
        'kind': event['kind'],
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'battle_event': dict(event),
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"{event['kind']}|{','.join(event['resources'])}|"
           f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = event['kind']
    record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
    record['auto_file'] = True
    return _shrink(record)


def _track_battle_resources(runtime, state, after, records, now, on_sealed=None):
    events = [event for event in (_battle_resource_event(record) for record in records or ())
              if event]
    if not events:
        return
    events.sort(key=lambda event: 0 if event['outcome'] == 'loss' else 1)
    event = events[0]
    chapter = (after.get('policy') or {}).get('chapter')
    key = '|'.join(str(part or '') for part in (
        chapter, event['kind'], ','.join(event['resources']),
        event.get('general'), event.get('step')))
    table = state.setdefault('battle_resource_events', {})
    if not isinstance(table, dict):
        table = state['battle_resource_events'] = {}
    last = table.get(key)
    if isinstance(last, dict) and float(now) - float(last.get('at') or 0) < BATTLE_RESOURCE_ISSUE_COOLDOWN_SECONDS:
        last['count'] = int(last.get('count') or 1) + 1
        last['last_at'] = float(now)
        return
    incident = _build_battle_resource_incident(runtime, state, after, event, now)
    _append_incident(runtime, incident, on_sealed)
    table[key] = {'at': float(now), 'kind': event['kind'],
                  'resources': list(event['resources']), 'count': 1}
    if len(table) > 30:
        for stale in sorted(table, key=lambda name: table[name].get('at') or 0)[:-30]:
            table.pop(stale, None)


def _track_sortie(runtime, state, after, records, now, on_sealed=None):
    events = [event for event in (_sortie_event(record) for record in records or ()) if event]
    if not events:
        return
    events.sort(key=lambda event: 0 if event['outcome'] == 'failed' else 1)
    event = events[0]
    if event['decision'] in ('order_failed', 'situation_held'):
        order = _context_order(after, event.get('step'))
        purpose = event.get('purpose') or (order or {}).get('purpose')
        if purpose in SORTIE_NON_PURPOSES:
            return
        if purpose:
            event['purpose'] = purpose
        for key in ('general', 'source', 'target'):
            if not event.get(key) and order.get(key):
                event[key] = _bounded_text(order.get(key), 48)
    chapter = (after.get('policy') or {}).get('chapter')
    if event['kind'] == 'sortie_cancelled':
        # docich#5136 (owner 2026-11-23): a cancellation the bot retries is not a
        # stalled month. Once the month has confirmed launches, the cancelled
        # attempt was transient; filing it as a high-confidence bug buries the
        # real stalls. #5107 already bounds the retry gate, so the month itself
        # proves whether the retry worked.
        observed = state.get('observed') or {}
        launches = observed.get('ledger_launches')
        month = (after.get('policy') or {}).get('month')
        if (type(launches) is int and launches > 0
                and observed.get('ledger_scope') == [chapter, month]):
            return
    key = '|'.join(str(part or '') for part in (
        chapter, event['kind'], event['reason_code'], event.get('step_local'),
        event.get('general'), event.get('target')))
    table = state.setdefault('sortie_events', {})
    if not isinstance(table, dict):
        table = state['sortie_events'] = {}
    last = table.get(key)
    if isinstance(last, dict) and float(now) - float(last.get('at') or 0) < SORTIE_ISSUE_COOLDOWN_SECONDS:
        last['count'] = int(last.get('count') or 1) + 1
        last['last_at'] = float(now)
        return
    incident = _build_sortie_incident(runtime, state, after, event, now)
    _append_incident(runtime, incident, on_sealed)
    table[key] = {'at': float(now), 'kind': event['kind'],
                  'reason': event['reason_code'], 'count': 1}
    if len(table) > 30:
        for stale in sorted(table, key=lambda name: table[name].get('at') or 0)[:-30]:
            table.pop(stale, None)



def _camp_generals(mem):
    statuses = ((mem.get('general_statuses') or {}).get('statuses') or {})
    current = {}
    for name, row in statuses.items():
        if not isinstance(name, str) or not isinstance(row, dict):
            continue
        if row.get('location') != 'camp':
            continue
        if row.get('location_observed') is False:
            continue
        hp = row.get('hp')
        if type(hp) is int and hp <= 0:
            continue
        current[name] = row
    return current


def _camp_month_starts(mem):
    start = mem.get('month_start_tick')
    if type(start) is not int or start <= 0:
        return []
    durations = mem.get('month_durations')
    durations = durations if isinstance(durations, list) else []
    starts = [start]
    for duration in reversed(durations[-6:]):
        if type(duration) is not int or duration <= 0:
            break
        previous = starts[-1] - duration
        if previous < 0:
            break
        starts.append(previous)
    return starts


def _camp_age_months(mem, general):
    seen = ((mem.get('camp_status_seen') or {}).get('actors') or {})
    entry = seen.get(general) if isinstance(seen, dict) else None
    first_tick = entry.get('tick') if isinstance(entry, dict) else None
    if type(first_tick) is not int or first_tick <= 0:
        return None
    starts = _camp_month_starts(mem)
    if not starts:
        return None
    return min(1 + sum(1 for start in starts if start > first_tick), 24)


def _build_camp_abandon_incident(runtime, state, after, stay, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = ['camp_abandoned', 'camp_abandon_3months']
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                 'first_at': now, 'last_at': now, 'closed_at': now,
                 'duration_s': 0.0, 'first_tick': mem.get('tick'),
                 'last_tick': mem.get('tick'), 'duration_ticks': None,
                 'observations': 0, 'max_gap_s': 0.0, 'started_clean': True}
    identity = state.get('identity') or {}
    row = stay.get('row') or {}
    record = {
        'schema': SCHEMA,
        'event': 'camp_abandoned',
        'kind': 'camp_abandoned',
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'camp_stay': {
            'general': stay.get('general'),
            'months': stay.get('months'),
            'first_tick': stay.get('first_tick'),
            'first_month': stay.get('first_month'),
            'hp': row.get('hp'),
            'max_hp': row.get('max_hp'),
            'egg': row.get('egg'),
            'broken': row.get('broken'),
            'status_text': _bounded_text(row.get('status_text'), 80),
            'castle_candidate': _bounded_text(
                (row.get('marker') or {}).get('castle_candidate'), 48),
            'camp_observed': row.get('camp_observed') is True,
            'last_tick': _bounded_int(row.get('tick'), 0, 10 ** 9),
        },
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"camp_abandoned|{stay.get('general')}|{stay.get('first_tick')}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = 'camp_abandoned'
    record['fingerprint'] = _fingerprint(mem.get('chapter'), 'camp_abandoned',
                                         ['camp_abandoned'])
    record['auto_file'] = True
    return _shrink(record)



def _chikujou_staffed(mem):
    world = _world_snapshot(mem)
    garrison = world.get('garrison') if isinstance(world.get('garrison'), dict) else {}
    staffed = {name for name, roster in garrison.items()
               if isinstance(name, str) and isinstance(roster, list) and roster}
    statuses = ((mem.get('general_statuses') or {}).get('statuses') or {})
    for name, row in statuses.items():
        if (isinstance(name, str) and isinstance(row, dict)
                and row.get('location') == 'castle' and row.get('castle')):
            staffed.add(row['castle'])
    return staffed


_CHIKUJOU_HOLD_LABELS = {
    'egg_unverified': '卵回復の支払いが未確認',
    'recruit_pending': '将軍募集が未決',
}


def _chikujou_hold(shop, records, month):
    """Why this month never opened 築城, when the hold is by design.

    docich#5045: an unresolved egg payment or recruit question freezes the
    card/castle rows (flows.month._hold_month_cards) until it settles.
    That is a fail-closed hold, not a silent loss, so the audit names the
    reason and keeps the loss countable instead of reporting an unexplained
    month end (docich#5094). A released hold sets chikujou back to check,
    so a skipped row here still means nobody got to open it.
    """
    if not isinstance(shop, dict) or shop.get('chikujou') not in ('skipped', None):
        return None
    held = bool(shop.get('cards_held') or shop.get('chikujou_held'))
    for row in reversed(records or ()):
        if not isinstance(row, dict) or row.get('decision') != 'month_cards_held_for_recruit':
            continue
        if month is not None and row.get('month') not in (None, month):
            continue
        held = True
        break
    if not held:
        return None
    egg, recruit = shop.get('egg'), shop.get('recruit')
    if egg == 'unverified' or (recruit == 'deferred_egg'
                               and egg not in ('done', 'not_needed', 'skipped', None)):
        return 'egg_unverified'
    return 'recruit_pending'


_CHIKUJOU_COST_DECLINE = '築城の実費が不明か必要資金を割るため、支払わず見送る'


def _chikujou_cost_declined(records, month):
    """The native quote was declined as unaffordable this month (docich#5081).

    `affordable` only compares gold with reserve + a fixed spare; it cannot
    see the real per-castle price the bot read in the うむッ/いかんッ frame.
    A month that ended after the bot declined that price is not a missed
    build, the same way a month skipped for an observed refusal is not
    (#5061). Only a decline recorded for this month counts: decisions are
    read in a 120 s window that can span a month boundary.
    """
    last_decline = None
    for row in records or ():
        if not isinstance(row, dict) or row.get('decision') != 'chikujou_declined':
            continue
        if row.get('reason') != _CHIKUJOU_COST_DECLINE:
            continue
        if month is not None and row.get('month') not in (None, month):
            continue
        at = row.get('at')
        if type(at) in (int, float) and (last_decline is None or at > last_decline):
            last_decline = at
        if last_decline is None:
            return True  # undated record: the decline still stands
    if last_decline is None:
        return False
    # A later quote/confirm in the same month means the bot reopened 築城
    # after the decline; that month can still be a real missed build.
    for row in records or ():
        if not isinstance(row, dict) or row.get('decision') not in (
                'chikujou_confirm', 'chikujou_continue'):
            continue
        if month is not None and row.get('month') not in (None, month):
            continue
        at = row.get('at')
        if type(at) in (int, float) and at > last_decline:
            return False
    return True


def _chikujou_menu_unreadable(records, month):
    """The ちくじょう row stayed unreadable until the month was closed.

    docich#5079 makes the bounded wait and the final skipped month visible
    in `chikujou_menu_unreadable`; the audit must name that cause instead of
    reporting a reason-less high-confidence missed build (docich#5097).
    Only the terminal `skipped` record counts, not the retry observations.
    """
    for row in records or ():
        if not isinstance(row, dict) or row.get('decision') != 'chikujou_menu_unreadable':
            continue
        if month is not None and row.get('month') not in (None, month):
            continue
        metric = row.get('observed_metric')
        if isinstance(metric, dict) and metric.get('skipped') is True:
            return True
    return False


def _chikujou_snapshot(after, before, record, records):
    from .strategy import economy
    month = record.get('month') if isinstance(record.get('month'), str) else None
    chosen = None
    for state in (after, before):
        if not isinstance(state, dict):
            continue
        mem = state.get('policy')
        if not isinstance(mem, dict):
            continue
        shop = mem.get('shop')
        if not isinstance(shop, dict):
            continue
        if month is not None and shop.get('key') not in (None, month):
            continue
        chosen = (state, mem, shop)
        if shop.get('key') == month:
            break
    if chosen is None:
        return None
    state, mem, shop = chosen
    metric = record.get('observed_metric') if isinstance(record.get('observed_metric'), dict) else {}
    gold = next((value for value in (metric.get('gold_end'), metric.get('gold_after'),
                                     metric.get('gold'), state.get('gold'))
                 if type(value) is int), None)
    if type(gold) is not int:
        gold = shop.get('gold_end') if type(shop.get('gold_end')) is int else shop.get('gold_start')
    reserve = economy._chikujou_reserve(mem, shop)
    if type(gold) is not int or reserve is None:
        return None
    tried = {name for name in (shop.get('chikujou_tried') or []) if isinstance(name, str)}
    # docich#5026: the shop ledger merges a chikujou sub's rows only when the
    # sub finishes, so a live sub's refusals and level-5 sightings (each
    # recorded in an earlier observation, not in this batch) were counted as
    # still-available candidates. Read the same exclusions the flow uses.
    sub = mem.get('month_sub')
    if (isinstance(sub, dict) and sub.get('kind') == 'chikujou'
            and sub.get('key') in (None, shop.get('key'))):
        tried.update(name for name in (sub.get('rows_tried') or [])
                     if isinstance(name, str))
        if isinstance(sub.get('chosen'), str):
            tried.add(sub['chosen'])
    upgrades = {entry.get('castle') for entry in (shop.get('chikujou_upgrades') or [])
                if isinstance(entry, dict)}
    maxed, unavailable = set(), set()
    for row in records or ():
        if not isinstance(row, dict):
            continue
        metric = row.get('observed_metric')
        metric = metric if isinstance(metric, dict) else {}
        if row.get('decision') == 'chikujou_max_level_seen':
            maxed.update(name for name in (metric.get('castles') or []) if isinstance(name, str))
        if row.get('decision') == 'chikujou_castle_unavailable':
            name = metric.get('castle')
            if isinstance(name, str):
                unavailable.add(name)
    staffed = _chikujou_staffed(mem)
    raw_candidates = economy._chikujou_candidates(mem)
    candidates = [name for name in raw_candidates
                  if name not in tried and name not in upgrades
                  and name not in maxed and name not in unavailable]
    staffed_candidates = [name for name in candidates if name in staffed]
    # month.py skips 築城 for up to two months after it read a refusal from the
    # native list with this exact castle set (mem['chikujou_refused']). The
    # refusal is the observation that these candidates cannot be built right
    # now, so a month that skipped for it is not a missed build. A changed
    # castle set no longer matches, so the alert still fires then (docich#5061).
    refused = mem.get('chikujou_refused')
    refused_castles = refused.get('castles') if isinstance(refused, dict) else None
    refusal_covered = (isinstance(refused_castles, list) and bool(refused_castles)
                       and sorted(refused_castles) == sorted(raw_candidates))
    affordable = economy._chikujou_affordable(gold, reserve)
    cost_declined = _chikujou_cost_declined(records, month)
    return {
        'month': month or shop.get('key'),
        'gold': gold,
        'reserve': reserve,
        'shop_status': shop.get('chikujou'),
        'candidates': candidates[:8],
        'staffed_candidates': staffed_candidates[:8],
        'tried': sorted(tried)[:8],
        'upgrades': sorted(name for name in upgrades if isinstance(name, str))[:8],
        'maxed': sorted(maxed)[:8],
        'unavailable': sorted(unavailable)[:8],
        'affordable': affordable,
        'refusal_covered': refusal_covered,
        # docich#5151: name the refusal state the flow used to skip, so a month
        # that ended with candidates can be told apart from one that skipped
        # inside the two-month refusal window without re-reading the run.
        'refused_skips': int(refused.get('skips') or 0) if isinstance(refused, dict) else 0,
        'refused_castles': (sorted(name for name in refused_castles if isinstance(name, str))[:8]
                            if isinstance(refused_castles, list) else []),
        'cost_declined': cost_declined,
        'menu_unreadable': _chikujou_menu_unreadable(records, month),
        'held_for': _chikujou_hold(shop, records, month),
        'buildable': bool(affordable and staffed_candidates and not refusal_covered
                          and not cost_declined),
    }


def _chikujou_event(record, after, before, records):
    if not isinstance(record, dict):
        return None
    decision = record.get('decision')
    if decision == 'month_confirm' and record.get('choice') not in (None, 'うむッ!'):
        return None
    if decision not in ('month_done', 'month_confirm', 'chikujou_declined',
                         'chikujou_leftover'):
        return None
    snapshot = _chikujou_snapshot(after, before, record, records)
    if not snapshot or not snapshot.get('buildable'):
        return None
    if decision == 'chikujou_declined':
        reason = record.get('reason') if isinstance(record.get('reason'), str) else ''
        if '候補' not in reason and snapshot.get('shop_status') not in ('done', 'skipped'):
            return None
        outcome = 'cancelled'
    elif decision == 'chikujou_leftover':
        outcome = 'cancelled'
    else:
        outcome = 'ended'
    snapshot['outcome'] = outcome
    snapshot['decision'] = decision
    snapshot['reason'] = _bounded_text(record.get('reason'), 200)
    return snapshot


def _build_chikujou_incident(runtime, state, after, event, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    kind = ('chikujou_available_at_end' if event.get('outcome') == 'ended'
            else 'chikujou_cancelled_with_resources')
    # docich#5045 option 3: a month that ended while the card/castle rows were
    # held for an unresolved egg/recruit question is a fail-closed hold, not a
    # silent loss. Keep the occurrence in the ledger so the redesign can be
    # judged on counts, but do not file it as a high-confidence alarm (the
    # issue filer defaults to min_confidence=high).
    held_for = event.get('held_for') if event.get('outcome') == 'ended' else None
    menu_unreadable = (event.get('menu_unreadable')
                       if event.get('outcome') == 'ended' else None)
    confidence, confidence_reasons = 'high', []
    if held_for:
        confidence = 'medium'
        confidence_reasons = [f'chikujou_held_for_{held_for}']
    elif menu_unreadable:
        # docich#5097: the row was unreadable, not a reason-less missed build.
        confidence = 'medium'
        confidence_reasons = ['chikujou_menu_unreadable']
    signals = [kind, 'chikujou_candidate']
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                 'first_at': now, 'last_at': now, 'closed_at': now,
                 'duration_s': 0.0, 'first_tick': mem.get('tick'),
                 'last_tick': mem.get('tick'), 'duration_ticks': None,
                 'observations': 0, 'max_gap_s': 0.0, 'started_clean': True}
    identity = state.get('identity') or {}
    record = {
        'schema': SCHEMA,
        'event': kind,
        'kind': kind,
        'confidence': confidence,
        'confidence_reasons': confidence_reasons,
        'chikujou_held_for': held_for,
        'chikujou_menu_unreadable': menu_unreadable,
        'chikujou_refused_skips': event.get('refused_skips'),
        'chikujou_refused_castles': list(event.get('refused_castles') or []),
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'chikujou_event': dict(event),
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"{kind}|{event.get('month')}|{mem.get('chapter')}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = kind
    record['fingerprint'] = _fingerprint(mem.get('chapter'), kind, [kind])
    record['auto_file'] = True
    return _shrink(record)


def _track_chikujou(runtime, state, before, after, records, now, on_sealed=None):
    mem = after.get('policy') or {}
    chapter, month = mem.get('chapter'), mem.get('month')
    # A decline or terminal unreadable row is emitted in an earlier observe()
    # call than month_done.  Keep the current observation as the only source
    # of terminal events, but classify it using recent same-month evidence.
    history = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                              state.get('identity'))
    evidence = [row for row in history
                if row.get('month') == month
                and row.get('chapter') in (None, chapter)]
    evidence.extend(records or ())
    events = [event for event in
              (_chikujou_event(record, after, before, evidence) for record in records or ())
              if event]
    if not events:
        return
    events.sort(key=lambda event: 0 if event.get('outcome') == 'ended' else 1)
    event = events[0]
    event_month = event.get('month') or month
    watch = state.get('chikujou_watch')
    if (not isinstance(watch, dict) or watch.get('chapter') != chapter
            or watch.get('month') != event_month):
        watch = {'chapter': chapter, 'month': event_month, 'reported': False}
        state['chikujou_watch'] = watch
    if watch.get('reported'):
        return
    incident = _build_chikujou_incident(runtime, state, after, event, now)
    _append_incident(runtime, incident, on_sealed)
    watch['reported'] = True
    watch['reported_at'] = float(now)
    watch['kind'] = incident.get('kind')



def _recall_kikan_event(record):
    if not isinstance(record, dict) or record.get('decision') != 'camp_recall_cursor':
        return None
    metric = record.get('observed_metric')
    metric = metric if isinstance(metric, dict) else {}
    if metric.get('choice') != 'きかん':
        return None
    if metric.get('move') not in (None, 'here'):
        return None
    current = metric.get('current')
    if not (isinstance(current, list) and len(current) == 3
            and all(type(value) is int for value in current[:2])
            and isinstance(current[2], str)):
        current = None
    return {
        'decision': 'camp_recall_cursor',
        'choice': 'きかん',
        'current': current,
        'move': metric.get('move'),
        'at': record.get('at') if type(record.get('at')) in (int, float) else None,
        'reason': _bounded_text(record.get('reason'), 200),
    }


def _build_recall_kikan_incident(runtime, state, after, event, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    recall = ((mem.get('flow') or {}).get('recall') or {})
    recall = recall if isinstance(recall, dict) else {}
    signals = ['recall_kikan_selected', 'recall_y_jump_expected']
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                 'first_at': now, 'last_at': now, 'closed_at': now,
                 'duration_s': 0.0, 'first_tick': mem.get('tick'),
                 'last_tick': mem.get('tick'), 'duration_ticks': None,
                 'observations': 0, 'max_gap_s': 0.0, 'started_clean': True}
    identity = state.get('identity') or {}
    record = {
        'schema': SCHEMA,
        'event': 'recall_kikan_selected',
        'kind': 'recall_kikan_selected',
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'recall_event': {
            **event,
            'goal': _bounded_text(recall.get('goal'), 48),
            'camp': (list(recall['target']) if isinstance(recall.get('target'), (list, tuple))
                     and len(recall['target']) == 2 else None),
            'general': _bounded_text(recall.get('general') or recall.get('actor'), 48),
            'stage': _bounded_text(recall.get('stage'), 32),
            'fort': recall.get('fort') is True,
            'boss': recall.get('boss') is True,
            'y_jumps': copy.deepcopy(mem.get('y_jumps') or {}),
            'y_jumped': copy.deepcopy(mem.get('y_jumped') or {}),
        },
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"recall_kikan_selected|{record['recall_event'].get('goal')}|"
           f"{record['recall_event'].get('general')}|{int(float(now))}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = 'recall_kikan_selected'
    record['fingerprint'] = _fingerprint(mem.get('chapter'), 'recall_kikan_selected',
                                         ['recall_kikan_selected'])
    record['auto_file'] = True
    return _shrink(record)


def _track_recall_kikan(runtime, state, after, records, now, on_sealed=None):
    events = [event for event in (_recall_kikan_event(record) for record in records or ())
              if event]
    if not events:
        return
    event = events[0]
    mem = after.get('policy') or {}
    chapter = mem.get('chapter')
    recall = ((mem.get('flow') or {}).get('recall') or {})
    recall = recall if isinstance(recall, dict) else {}
    key = '|'.join(str(part or '') for part in (
        chapter, recall.get('goal'), recall.get('general') or recall.get('actor'),
        tuple(recall.get('target') or ())))
    table = state.setdefault('recall_kikan_events', {})
    if not isinstance(table, dict):
        table = state['recall_kikan_events'] = {}
    last = table.get(key)
    if isinstance(last, dict) and float(now) - float(last.get('at') or 0) < RECALL_KIKAN_COOLDOWN_SECONDS:
        last['count'] = int(last.get('count') or 1) + 1
        last['last_at'] = float(now)
        return
    incident = _build_recall_kikan_incident(runtime, state, after, event, now)
    _append_incident(runtime, incident, on_sealed)
    table[key] = {'at': float(now), 'kind': 'recall_kikan_selected', 'count': 1}
    if len(table) > 30:
        for stale in sorted(table, key=lambda name: table[name].get('at') or 0)[:-30]:
            table.pop(stale, None)



def _battle_menu_key(after):
    mem = after.get('policy') if isinstance(after, dict) else None
    mem = mem if isinstance(mem, dict) else {}
    cur = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    return (cur.get('general') or cur.get('ally'), cur.get('step'))


def _battle_menu_actions(records):
    return [row for row in records or ()
            if isinstance(row, dict) and row.get('decision') in BATTLE_MENU_ACTIONS]


def _battle_menu_close(records):
    rows = [row for row in records or ()
            if isinstance(row, dict) and row.get('decision') in BATTLE_MENU_CLOSES]
    if not rows:
        return None
    for row in rows:
        if row.get('decision') not in BATTLE_MENU_IGNORED_CLOSES:
            return row
    return rows[0]


def _build_battle_menu_incident(runtime, state, after, watch, close, now):
    mem = after.get('policy') or {}
    cur = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = ['battle_menu_wasted', 'battle_menu_no_resource']
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    identity = state.get('identity') or {}
    observed = state.get('observed') if isinstance(state.get('observed'), dict) else None
    if observed is not None:
        month = _month_summary(observed, now)
    else:
        month = None
    record = {
        'schema': SCHEMA,
        'event': 'battle_menu_wasted',
        'kind': 'battle_menu_wasted',
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': month,
        'battle_menu': {
            'general': _bounded_text(watch.get('general'), 48),
            'step': _bounded_text(watch.get('step'), 64),
            'opened_at': watch.get('opened_at'),
            'closed_at': float(now),
            'duration_s': round(max(0.0, float(now) - float(watch.get('opened_at') or now)), 1),
            'ally_hp': watch.get('ally_hp'),
            'enemy_hp': watch.get('enemy_hp'),
            'side': _bounded_text(cur.get('side'), 16),
            'close_decision': _bounded_text(close.get('decision'), 64),
            'close_reason': _bounded_text(close.get('reason'), 200),
            'close_metric': copy.deepcopy(close.get('observed_metric') or {}),
            'executed_actions': [],
        },
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"battle_menu_wasted|{watch.get('general')}|{watch.get('step')}|"
           f"{int(float(watch.get('opened_at') or now))}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = 'battle_menu_wasted'
    record['fingerprint'] = _fingerprint(mem.get('chapter'), 'battle_menu_wasted',
                                         ['battle_menu_wasted'])
    record['auto_file'] = True
    return _shrink(record)


def _track_battle_menu(runtime, state, before, after, records, now, on_sealed=None):
    is_menu = after.get('screen_kind') == 'battle_menu'
    watch = state.get('battle_menu_watch')
    if not is_menu:
        if not isinstance(watch, dict):
            return
        actions = list(watch.get('actions') or []) + _battle_menu_actions(records)
        close = _battle_menu_close(records)
        state['battle_menu_watch'] = None
        if not close or actions or close.get('decision') in BATTLE_MENU_IGNORED_CLOSES:
            return
        chapter = (after.get('policy') or {}).get('chapter')
        key = '|'.join(str(part or '') for part in (
            chapter, watch.get('general'), watch.get('step')))
        table = state.setdefault('battle_menu_events', {})
        if not isinstance(table, dict):
            table = state['battle_menu_events'] = {}
        last = table.get(key)
        if (isinstance(last, dict)
                and float(now) - float(last.get('at') or 0) < BATTLE_MENU_ISSUE_COOLDOWN_SECONDS):
            last['count'] = int(last.get('count') or 1) + 1
            return
        incident = _build_battle_menu_incident(runtime, state, after, watch, close, now)
        _append_incident(runtime, incident, on_sealed)
        table[key] = {'at': float(now), 'kind': 'battle_menu_wasted', 'count': 1}
        if len(table) > 30:
            for stale in sorted(table, key=lambda name: table[name].get('at') or 0)[:-30]:
                table.pop(stale, None)
        return
    if not isinstance(watch, dict):
        key = _battle_menu_key(after)
        mem = after.get('policy') or {}
        cur = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
        watch = {
            'opened_at': float(now),
            'general': key[0],
            'step': key[1],
            'ally_hp': cur.get('ally_hp'),
            'enemy_hp': cur.get('enemy_hp'),
            'actions': [],
        }
        state['battle_menu_watch'] = watch
    elif _battle_menu_key(after) != (watch.get('general'), watch.get('step')):
        state['battle_menu_watch'] = None
        return
    for row in _battle_menu_actions(records):
        action = row.get('decision')
        if isinstance(action, str) and action not in watch.setdefault('actions', []):
            watch['actions'].append(action)



def _positive_card_stock(mem):
    stock = mem.get('card_stock') if isinstance(mem.get('card_stock'), dict) else {}
    positive = {}
    for name, count in stock.items():
        if isinstance(name, str) and type(count) is int and count > 0:
            positive[name] = count
    return positive


def _sortie_plan_order(mem, step):
    plan = mem.get('plan') if isinstance(mem.get('plan'), dict) else {}
    chart = plan.get('chart_plan') if isinstance(plan.get('chart_plan'), dict) else {}
    orders = chart.get('orders') if isinstance(chart.get('orders'), list) else []
    for order in orders:
        if isinstance(order, dict) and order.get('step') == step:
            return order
    return {}


def _sortie_carried_cards(mem, step, record):
    carried = []
    rare = (mem.get('rare_card_kit') or {}).get(step)
    if isinstance(rare, list):
        carried += [card for card in rare if isinstance(card, str)]
    elif isinstance(rare, str):
        carried.append(rare)
    strong = (mem.get('strong_card_kit') or {}).get(step)
    if isinstance(strong, str):
        carried.append(strong)
    dropper = (mem.get('dropper_card_kit') or {}).get(step)
    if isinstance(dropper, dict) and isinstance(dropper.get('card'), str):
        carried.append(dropper['card'])
    for key in ('cards', 'card', 'cards_selected'):
        value = record.get(key)
        if isinstance(value, str):
            carried.append(value)
        elif isinstance(value, list):
            carried += [card for card in value if isinstance(card, str)]
    metric = record.get('observed_metric')
    metric = metric if isinstance(metric, dict) else {}
    for key in ('cards', 'card', 'cards_selected'):
        value = metric.get(key)
        if isinstance(value, str):
            carried.append(value)
        elif isinstance(value, list):
            carried += [card for card in value if isinstance(card, str)]
    return carried


def _sortie_cards_event(record, mem):
    if not isinstance(record, dict) or record.get('decision') not in LAUNCH_DECISIONS:
        return None
    step = record.get('chart_step')
    if not isinstance(step, str) or not step:
        return None
    order = _sortie_plan_order(mem, step)
    purpose = record.get('purpose') or order.get('purpose')
    if purpose in NON_SORTIE_CARD_PURPOSES:
        return None
    if purpose not in SORTIE_CARD_PURPOSES and order.get('target') is None:
        return None
    stock = _positive_card_stock(mem)
    if not stock:
        return None
    carried = _sortie_carried_cards(mem, step, record)
    if carried:
        return None
    return {
        'decision': record.get('decision'),
        'step': step,
        'purpose': _bounded_text(purpose, 32),
        'general': _bounded_text(record.get('general') or order.get('general'), 48),
        'source': _bounded_text(record.get('source') or order.get('source'), 48),
        'target': _bounded_text(record.get('target') or order.get('target'), 48),
        'inventory': dict(sorted(stock.items())[:12]),
        'planned_cards': [card for card in (order.get('cards') or [])
                          if isinstance(card, str)][:12],
        'carried': [],
        'reason': _bounded_text(record.get('reason'), 200),
        'at': record.get('at') if type(record.get('at')) in (int, float) else None,
    }


def _build_sortie_cards_incident(runtime, state, after, event, now):
    mem = after.get('policy') or {}
    context = _context(after)
    rows = _read_decisions(runtime, max(0.0, float(now) - 120), now,
                           state.get('identity'))
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    month_sorties = (context or {}).get('month_sorties') or {}
    confirmed = month_sorties.get('launches') if isinstance(month_sorties, dict) else None
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = ['sortie_without_cards', 'card_inventory_ignored']
    for name in _signals(context, counts, confirmed, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    identity = state.get('identity') or {}
    record = {
        'schema': SCHEMA,
        'event': 'sortie_without_cards',
        'kind': 'sortie_without_cards',
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'month_summary': _month_summary(state.get('observed'), now)
        if isinstance(state.get('observed'), dict) else None,
        'sortie_event': dict(event),
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': after.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'created_at': float(now),
    }
    raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
           f"sortie_without_cards|{event.get('step')}|{event.get('general')}|"
           f"{int(float(now))}")
    record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
    record['signature'] = 'sortie_without_cards'
    record['fingerprint'] = _fingerprint(mem.get('chapter'), 'sortie_without_cards',
                                         ['sortie_without_cards'])
    record['auto_file'] = True
    return _shrink(record)


def _track_sortie_cards(runtime, state, after, records, now, on_sealed=None):
    mem = after.get('policy') or {}
    events = [event for event in
              (_sortie_cards_event(record, mem) for record in records or ())
              if event]
    if not events:
        return
    event = events[0]
    chapter = mem.get('chapter')
    key = '|'.join(str(part or '') for part in (
        chapter, event.get('step'), event.get('general')))
    table = state.setdefault('sortie_cards_events', {})
    if not isinstance(table, dict):
        table = state['sortie_cards_events'] = {}
    last = table.get(key)
    if (isinstance(last, dict)
            and float(now) - float(last.get('at') or 0) < SORTIE_CARDS_ISSUE_COOLDOWN_SECONDS):
        last['count'] = int(last.get('count') or 1) + 1
        return
    incident = _build_sortie_cards_incident(runtime, state, after, event, now)
    _append_incident(runtime, incident, on_sealed)
    table[key] = {'at': float(now), 'kind': 'sortie_without_cards', 'count': 1}
    if len(table) > 30:
        for stale in sorted(table, key=lambda name: table[name].get('at') or 0)[:-30]:
            table.pop(stale, None)


def _track_camp_abandon(runtime, state, after, now, on_sealed=None):
    mem = after.get('policy') or {}
    chapter, month = mem.get('chapter'), mem.get('month')
    if type(chapter) is not int or not isinstance(month, str):
        return
    current = _camp_generals(mem)
    if not current and not isinstance(state.get('camp_watch'), dict):
        return
    complete = (mem.get('general_statuses') or {}).get('complete') is True
    watch = state.get('camp_watch')
    if (not isinstance(watch, dict) or watch.get('chapter') != chapter
            or not isinstance(watch.get('generals'), dict)):
        watch = {'chapter': chapter, 'month': month, 'generals': {}, 'departed': []}
        state['camp_watch'] = watch
    generals = watch['generals']
    departed = watch.get('departed')
    if not isinstance(departed, list):
        departed = watch['departed'] = []
    if watch.get('month') != month:
        for name, entry in list(generals.items()):
            if name in current:
                entry['months'] = int(entry.get('months') or 1) + 1
                entry['last_month'] = month
            elif complete:
                generals.pop(name, None)
                if name not in departed:
                    departed.append(name)
        watch['month'] = month
    seen = ((mem.get('camp_status_seen') or {}).get('actors') or {})
    for name, row in current.items():
        entry = generals.get(name)
        age = _camp_age_months(mem, name)
        first_tick = seen.get(name, {}).get('tick') if isinstance(seen.get(name), dict) else None
        if entry is None:
            if name in departed:
                departed.remove(name)
                months = 1
                first_tick = mem.get('tick') if type(mem.get('tick')) is int else first_tick
            else:
                months = max(1, age or 1)
            entry = {'months': months, 'first_month': month,
                     'first_tick': first_tick, 'reported': False,
                     'last_month': month}
            generals[name] = entry
        else:
            entry['months'] = max(int(entry.get('months') or 1), age or 1)
            if not entry.get('first_tick'):
                entry['first_tick'] = first_tick
        entry['hp'] = row.get('hp')
    if complete:
        for name in list(generals):
            if name not in current:
                generals.pop(name, None)
                if name not in departed:
                    departed.append(name)
    if len(departed) > 50:
        del departed[:-50]
    for name, row in current.items():
        entry = generals.get(name)
        if not isinstance(entry, dict) or entry.get('reported'):
            continue
        if int(entry.get('months') or 0) < CAMP_ABANDON_MONTHS:
            continue
        incident = _build_camp_abandon_incident(
            runtime, state, after,
            {'general': name, 'months': entry.get('months'),
             'first_tick': entry.get('first_tick'),
             'first_month': entry.get('first_month'), 'row': row}, now)
        _append_incident(runtime, incident, on_sealed)
        entry['reported'] = True
        entry['reported_at'] = float(now)
    if len(generals) > 30:
        for stale in sorted(generals, key=lambda name: generals[name].get('first_tick') or 0)[:-30]:
            generals.pop(stale, None)


def _track_camp(runtime, state, after, records, now, on_sealed=None):
    events = [event for event in (_camp_event(record) for record in records or ()) if event]
    if not events:
        return
    events.sort(key=lambda event: CAMP_OUTCOME_RANK.get(event['outcome'], 9))
    event = events[0]
    chapter = (after.get('policy') or {}).get('chapter')
    key = '|'.join(str(part or '') for part in (
        chapter, event['kind'], event['reason_code'], event.get('camp'),
        event.get('general'), event.get('castle')))
    table = state.setdefault('camp_events', {})
    if not isinstance(table, dict):
        table = state['camp_events'] = {}
    last = table.get(key)
    if isinstance(last, dict) and float(now) - float(last.get('at') or 0) < CAMP_ISSUE_COOLDOWN_SECONDS:
        last['count'] = int(last.get('count') or 1) + 1
        last['last_at'] = float(now)
        return
    incident = _build_camp_incident(runtime, state, after, event, now)
    _append_incident(runtime, incident, on_sealed)
    table[key] = {'at': float(now), 'kind': event['kind'],
                  'reason': event['reason_code'], 'count': 1}
    if len(table) > 30:
        for stale in sorted(table, key=lambda name: table[name].get('at') or 0)[:-30]:
            table.pop(stale, None)


def _track_boss(runtime, state, after, records, now, on_sealed=None):
    mem = after.get('policy') or {}
    phase = _boss_phase(mem)
    current = state.get('boss') if isinstance(state.get('boss'), dict) else None
    if phase is None:
        if current:
            state['last_boss'] = {**current, 'ended_at': now}
            state['boss'] = None
        return
    restart = (not current or current.get('chapter') != phase['chapter']
               or list(current.get('bosses') or []) != list(phase['bosses']))
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    if restart:
        current = {
            'chapter': phase['chapter'],
            'boss_name': phase['boss_name'],
            'bosses': list(phase['bosses']),
            'started_at': float(now),
            'first_tick': tick,
            'last_at': float(now),
            'last_tick': tick,
            'observations': 1,
            'max_gap_s': 0.0,
            'battle_count': 0,
            'battle_at': None,
            'reported': False,
            'incident_id': None,
            'bot_version': _bounded_text(after.get('bot_version'), 80),
        }
        state['boss'] = current
    else:
        gap = max(0.0, float(now) - float(current.get('last_at') or now))
        current['max_gap_s'] = max(float(current.get('max_gap_s') or 0.0), gap)
        current['last_at'] = float(now)
        current['last_tick'] = tick
        current['observations'] = int(current.get('observations') or 0) + 1
        version = _bounded_text(after.get('bot_version'), 80)
        if version:
            current['bot_version'] = version
    if _boss_battle_seen(mem, records, phase['chapter'], phase['boss_name']):
        if not current.get('battle_at'):
            current['battle_count'] = 1
            current['battle_at'] = float(now)
            current['battle_tick'] = tick
        return
    if current.get('battle_at') or current.get('reported'):
        return
    if float(now) - float(current.get('started_at') or now) < BOSS_NO_BATTLE_SECONDS:
        return
    rows = _read_decisions(runtime, current.get('started_at') or now, now,
                           state.get('identity'))
    if _rows_boss_battle(rows, phase['chapter'], phase['boss_name']):
        current['battle_count'] = 1
        current['battle_at'] = float(now)
        current['battle_source'] = 'decision_log'
        return
    incident = _build_boss_incident(runtime, state, after, records, now, current, rows=rows)
    _append_incident(runtime, incident, on_sealed)
    current['reported'] = True
    current['incident_id'] = incident.get('incident_id')
    current['reported_at'] = float(now)


def _context(state):

    """Bounded game context from one observation; never copies the whole mem."""
    if not isinstance(state, dict):
        return None
    mem = state.get('policy')
    if not isinstance(mem, dict):
        return None
    scope = _scope(state)
    if scope is None:
        return None
    from .state.regions import FLOW, PLAN, region
    world = _world_snapshot(mem)
    plan = region(mem, PLAN)
    flow = region(mem, FLOW)
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    durations = mem.get('month_durations')
    durations = durations if isinstance(durations, list) else []
    durations = [value for value in durations[-6:]
                 if type(value) is int and 0 <= value <= 10 ** 7]
    return {
        'chapter': scope[0],
        'month': scope[1],
        'tick': tick,
        'month_start_tick': _bounded_int(mem.get('month_start_tick'), 0, 10 ** 9),
        'month_durations': durations,
        'bot_version': _bounded_text(state.get('bot_version'), 80),
        'screen_kind': _bounded_text(state.get('screen_kind'), 32),
        'phase': _bounded_text(state.get('phase'), 32),
        'plan': _summarize_plan(plan),
        'world': _summarize_world(world, tick),
        'flow': _summarize_flow(flow),
        'stats': _summarize_stats(mem),
        'month_sorties': _copy_month_sorties(mem.get('month_sorties')),
    }


def _copy_month_sorties(value):
    if not isinstance(value, dict):
        return None
    scope = value.get('scope')
    scope = [scope[0], scope[1]] if (isinstance(scope, list) and len(scope) == 2
                                     and type(scope[0]) is int and isinstance(scope[1], str)) else None
    launches = _bounded_int(value.get('launches'), 0, 10 ** 6)
    return {'scope': scope, 'launches': launches}


def capture(state):
    """Bounded snapshot of the pre-decide state, or None when not ready."""
    try:
        return _context(state)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return None


def _new_state(identity):
    return {
        'schema': SCHEMA,
        'identity': identity or {},
        'observed': None,
        'boss': None,
        'pending': None,
        'last_close': None,
        'last_sealed': None,
        'write_tick': 0,
    }


def _read_state(runtime):
    path = Path(runtime) / AUDIT_FILE
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_STATE_BYTES:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        return None
    return data


def _write_state(runtime, state):
    path = Path(runtime) / AUDIT_FILE
    if path.is_symlink():
        raise AuditError('unsafe_audit_state')
    from ..game_switch import atomic_write_json
    atomic_write_json(path, state)


def _open_month(before, after, now, *, started_clean):
    mem = after.get('policy') or {}
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    month_sorties = _copy_month_sorties(mem.get('month_sorties'))
    scope = [mem.get('chapter'), mem.get('month')]
    ledger_ok = (month_sorties is not None and month_sorties.get('scope') == scope
                 and month_sorties.get('launches') is not None)
    return {
        'chapter': mem.get('chapter'),
        'month': mem.get('month'),
        'first_at': float(now),
        'first_tick': tick,
        'last_at': float(now),
        'last_tick': tick,
        'observations': 1,
        'max_gap_s': 0.0,
        'started_clean': bool(started_clean),
        'ledger_scope': month_sorties.get('scope') if ledger_ok else None,
        'ledger_launches': month_sorties.get('launches') if ledger_ok else None,
        'bot_version': _bounded_text(after.get('bot_version'), 80),
    }


def _touch_month(observed, after, now):
    mem = after.get('policy') or {}
    gap = max(0.0, float(now) - float(observed.get('last_at') or now))
    observed['max_gap_s'] = max(float(observed.get('max_gap_s') or 0.0), gap)
    observed['last_at'] = float(now)
    tick = _bounded_int(mem.get('tick'), 0, 10 ** 9)
    if tick is not None:
        observed['last_tick'] = tick
    observed['observations'] = int(observed.get('observations') or 0) + 1
    version = _bounded_text(after.get('bot_version'), 80)
    if version:
        observed['bot_version'] = version
    month_sorties = _copy_month_sorties(mem.get('month_sorties'))
    scope = [observed.get('chapter'), observed.get('month')]
    if (month_sorties is not None and month_sorties.get('scope') == scope
            and month_sorties.get('launches') is not None):
        observed['ledger_scope'] = month_sorties['scope']
        observed['ledger_launches'] = month_sorties['launches']


def _read_decisions(runtime, since, until, identity):
    runtime = Path(runtime)
    rows = []
    for name in ('hanjuku_decisions.previous.jsonl', 'hanjuku_decisions.jsonl'):
        path = runtime / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            offset = max(0, size - MAX_READ_BYTES)
            with path.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read(MAX_READ_BYTES + 1)
            if offset:
                raw = raw.partition(b'\n')[2]
            lines = raw.decode('utf-8', errors='replace').splitlines()
        except (OSError, ValueError):
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get('event') != 'decision':
                continue
            at = row.get('at')
            if type(at) not in (int, float) or not since <= at <= until:
                continue
            if identity and any(row.get(name) not in (None, identity.get(name))
                                for name in ('runtime_id', 'generation', 'lease_id')):
                continue
            rows.append(row)
    rows.sort(key=lambda row: row.get('at') or 0)
    return rows


def _coverage(rows, since, until):
    if not rows:
        return 0.0
    first = rows[0].get('at')
    last = rows[-1].get('at')
    if type(first) not in (int, float) or type(last) not in (int, float):
        return 0.0
    span = max(1.0, float(until) - float(since))
    covered = max(0.0, min(float(until), float(last)) - max(float(since), float(first)))
    return min(1.0, covered / span)


def _departures(rows):
    keys = set()
    events = 0
    for row in rows:
        decision = row.get('decision')
        if decision not in LAUNCH_DECISIONS:
            continue
        # A move into our own castle is a transfer, not an enemy sortie.
        if row.get('purpose') == 'move':
            continue
        events += 1
        step = row.get('chart_step')
        if isinstance(step, str) and step:
            keys.add(step)
        else:
            keys.add(f"{decision}:{row.get('at')}")
    return len(keys), events


def _boss_departures(rows, chapter):
    from .. import hanjuku_chart as chart
    bosses = set(chart.boss_sites(chapter) or ())
    keys = set()
    events = 0
    for row in rows:
        decision = row.get('decision')
        if decision not in LAUNCH_DECISIONS:
            continue
        if row.get('purpose') == 'move':
            continue
        metric = row.get('observed_metric')
        metric = metric if isinstance(metric, dict) else {}
        target = row.get('target') or metric.get('target')
        purpose = row.get('purpose') or metric.get('purpose')
        if purpose != 'boss' and target not in bosses:
            continue
        events += 1
        step = row.get('chart_step')
        if isinstance(step, str) and step:
            keys.add(step)
        else:
            keys.add(f"{decision}:{row.get('at')}")
    return len(keys), events


def _decision_counts(rows):
    counts = Counter(row.get('decision') for row in rows
                     if isinstance(row.get('decision'), str))
    return {name: count for name, count in counts.most_common(30)}


def _decision_rows(rows, limit):
    out = []
    for row in rows[-limit:]:
        out.append({
            'at': row.get('at') if type(row.get('at')) in (int, float) else None,
            'decision': _bounded_text(row.get('decision'), 64),
            'chart_step': _bounded_text(row.get('chart_step'), 64),
            'screen_kind': _bounded_text(row.get('screen_kind'), 32),
            'general': _bounded_text(row.get('general'), 48),
            'source': _bounded_text(row.get('source'), 48),
            'target': _bounded_text(row.get('target'), 48),
            'purpose': _bounded_text(row.get('purpose'), 32),
            'reason': _bounded_text(row.get('reason'), 120),
        })
    return out


def _outcomes(rows):
    outcomes = Counter()
    for row in rows:
        if row.get('decision') != 'sortie_outcome':
            continue
        metric = row.get('observed_metric')
        outcome = metric.get('outcome') if isinstance(metric, dict) else None
        if isinstance(outcome, str):
            outcomes[outcome] += 1
    return dict(outcomes)


def _signals(context, counts, confirmed, departures, battle_count):
    out = []
    flow = (context or {}).get('flow') or {}
    world = (context or {}).get('world') or {}
    plan = (context or {}).get('plan') or {}
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    if counts.get('order_start') and not departures:
        out.append('order_start_without_launch')
    if counts.get('sortie_launch_unverified') and not departures:
        out.append('launch_confirmation_missing')
    if counts.get('sortie_cancelled_observed'):
        out.append('departure_cancelled')
    if counts.get('post_month_sortie_floor'):
        out.append('monthly_scan_yield')
    if counts.get('post_month_status') or counts.get('post_month_status_close_probe') \
            or counts.get('month_sub_step') or flow.get('post_month_status') or flow.get('month_sub'):
        out.append('monthly_scan_hold')
    camp = sum(counts.get(name, 0) for name in counts if 'camp' in name or name.startswith('recall'))
    if camp >= 10:
        out.append('camp_recall_loop')
    if battle_count >= 20 or counts.get('battle_draw_hold', 0) >= 10:
        out.append('battle_grind')
    unread = counts.get('unknown_hold_advance', 0) + counts.get('situation_held', 0)
    if unread >= 30 or flow.get('scene_hold') or flow.get('yes_no_unread'):
        out.append('unread_hold')
    if orders:
        if all(status in ('failed', 'done') for status in orders.values()):
            out.append('plan_all_settled')
    else:
        out.append('no_orders')
    if counts.get('chart_adjust_request'):
        out.append('chart_adjust_stale')
    garrison = world.get('garrison') if isinstance(world.get('garrison'), dict) else {}
    staffed = [len(names) for names in garrison.values() if isinstance(names, list)]
    if not staffed or max(staffed) <= 1:
        out.append('no_source')
    sorties = world.get('sorties') if isinstance(world.get('sorties'), dict) else {}
    if any(isinstance(row, dict) and (row.get('age_ticks') or 0) > 3000
           for row in sorties.values()):
        out.append('stuck_march')
    if confirmed is None:
        out.append('ledger_unknown')
    seen, result = set(), []
    for name in out:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    result.sort(key=lambda name: SIGNAL_ORDER.index(name) if name in SIGNAL_ORDER else len(SIGNAL_ORDER))
    return result[:8]


def _confidence(observed, context, ledger_ok, chapter_changed, irregular, coverage, duration_s):
    reasons = []
    if not observed.get('started_clean'):
        reasons.append('first_partial_month')
    if chapter_changed:
        reasons.append('chapter_change')
    if irregular:
        reasons.append('month_jump')
    if not ledger_ok:
        reasons.append('ledger_scope_mismatch')
    if float(observed.get('max_gap_s') or 0) > MAX_GAP_SECONDS:
        reasons.append('observation_gap')
    if duration_s < MIN_MONTH_SECONDS:
        reasons.append('short_month')
    if int(observed.get('observations') or 0) < MIN_OBSERVATIONS:
        reasons.append('few_observations')
    if coverage < 0.9:
        reasons.append('decision_log_coverage')
    if context is None:
        reasons.append('context_missing')
    soft = {'observation_gap', 'decision_log_coverage'}
    if not reasons:
        return 'high', reasons
    if len(reasons) == 1 and reasons[0] in soft:
        return 'medium', reasons
    return 'low', reasons


def _month_summary(observed, close_at):
    first_at = float(observed.get('first_at') or close_at)
    last_at = float(observed.get('last_at') or close_at)
    first_tick = observed.get('first_tick')
    last_tick = observed.get('last_tick')
    return {
        'chapter': observed.get('chapter'),
        'month': observed.get('month'),
        'first_at': first_at,
        'last_at': last_at,
        'closed_at': float(close_at),
        'duration_s': max(0.0, last_at - first_at),
        'first_tick': first_tick,
        'last_tick': last_tick,
        'duration_ticks': (last_tick - first_tick
                           if type(first_tick) is int and type(last_tick) is int
                           and last_tick >= first_tick else None),
        'observations': int(observed.get('observations') or 0),
        'max_gap_s': float(observed.get('max_gap_s') or 0.0),
        'started_clean': bool(observed.get('started_clean')),
    }


def _primary_signal(signals):
    for name, members in PRIMARY_SIGNALS:
        if any(signal in members for signal in signals):
            return name
    return 'unknown'


def _incident_id(identity, chapter, month):
    raw = f"{identity.get('runtime_id')}|{identity.get('generation')}|{chapter}|{month}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]


def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _shrink(record):
    """Drop the largest optional evidence until the record fits the bound."""
    while len(json.dumps(record, ensure_ascii=False).encode('utf-8')) > MAX_RECORD_BYTES:
        node = None
        path = None
        for candidate in (('decisions', 'before'), ('after', 'rows'),
                          ('plan', 'chart_plan', 'orders'), ('world', 'sortie_history'),
                          ('world', 'garrison')):
            node = record
            for key in candidate:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, (list, dict)) and node:
                path = candidate
                break
        if path is None:
            raise AuditError('incident_exceeds_record_bound')
        node = record
        for key in path[:-1]:
            node = node[key]
        current = node[path[-1]]
        if isinstance(current, list):
            node[path[-1]] = current[:len(current) // 2]
        else:
            node[path[-1]] = dict(list(current.items())[:len(current) // 2])
        record['truncated'] = True
    return record


def _build_incident(runtime, observed, context, rows, close_at, identity, *, kind, confirmed,
                    departures, launch_events, coverage, confidence, reasons,
                    chapter_changed, irregular, boss_month=None):
    counts = _decision_counts(rows)
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    signals = _signals(context, counts, confirmed, departures, battle_count)
    if kind in ('boss_month_zero_sortie', 'boss_month_zero_unverified'):
        signals = ['boss_month_zero_sortie'] + [name for name in signals
                                               if name != 'boss_month_zero_sortie']
    move_launches = sum(1 for row in rows if row.get('decision') == 'order_launched'
                        and row.get('purpose') == 'move')
    record = {
        'schema': SCHEMA,
        'event': {'zero_sortie': 'month_zero_sortie',
                  'zero_unverified': 'month_zero_unverified',
                  'boss_month_zero_sortie': 'month_boss_zero_sortie',
                  'boss_month_zero_unverified': 'month_boss_zero_unverified',
                  'unconfirmed_launches': 'month_unconfirmed_launches'}.get(
                      kind, 'month_unconfirmed_launches'),
        'kind': kind,
        'confidence': confidence,
        'confidence_reasons': reasons,
        'chapter': observed.get('chapter'),
        'month': observed.get('month'),
        'month_summary': _month_summary(observed, close_at),
        'sorties': {
            'confirmed': confirmed,
            'departures': departures,
            'launch_events': launch_events,
            'order_start': counts.get('order_start', 0),
            'order_launched': counts.get('order_launched', 0),
            'sortie_launch_confirmed': counts.get('sortie_launch_confirmed', 0),
            'sortie_launch_unverified': counts.get('sortie_launch_unverified', 0),
            'sortie_launch_failed': counts.get('sortie_launch_failed', 0),
            'sortie_departure_requested': counts.get('sortie_departure_requested', 0),
            'sortie_cancelled_observed': counts.get('sortie_cancelled_observed', 0),
            'sortie_outcomes': _outcomes(rows),
            'moves': move_launches,
            'house_dispatches': counts.get('goal_dispatch', 0),
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'boss_month': boss_month or {},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': round(coverage, 3),
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(Path(runtime).name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': observed.get('bot_version'),
            'identity': identity,
        },
        'context_missing': context is None,
        'chapter_changed': bool(chapter_changed),
        'irregular_month': bool(irregular),
        'created_at': float(close_at),
    }
    record['incident_id'] = _incident_id(identity, observed.get('chapter'), observed.get('month'))
    primary = _primary_signal(signals)
    record['signature'] = primary
    record['fingerprint'] = _fingerprint(observed.get('chapter'), kind, [primary])
    record['auto_file'] = (kind in ('zero_sortie', 'boss_month_zero_sortie')
                            and confidence == 'high')
    return _shrink(record)


def _after_summary(rows, close_at, window_end):
    departures, events = _departures(rows)
    first = min((row.get('at') for row in rows
                 if row.get('decision') in LAUNCH_DECISIONS
                 and type(row.get('at')) in (int, float)), default=None)
    return {
        'window_start_at': float(close_at),
        'window_end_at': float(window_end),
        'complete': float(window_end) >= float(close_at) + AFTER_WINDOW_SECONDS,
        'decisions': len(rows),
        'counts': _decision_counts(rows),
        'departures': departures,
        'launch_events': events,
        'recovered': departures > 0,
        'first_launch_seconds': (round(float(first) - float(close_at), 1)
                                 if first is not None else None),
        'outcomes': _outcomes(rows),
        'rows': _decision_rows(rows, 20),
    }


def _append_incident(runtime, record, on_sealed=None):
    from ..hanjuku_run import append_log
    append_log(Path(runtime), LOG_NAME, record)
    if callable(on_sealed):
        on_sealed(record)


ISSUE_LOG = 'hanjuku_month_audit_issue.log'
ISSUE_LOG_LIMIT = 1024 * 1024


def spawn_issue_filer(runtime, record):
    """Start the gh filing CLI for one sealed incident, without waiting.

    Called from the command bot's effect loop only while the corner is
    running. The child is detached and writes its result to the runtime log;
    the game observation never waits for gh.
    """
    if not isinstance(record, dict) or record.get('auto_file') is not True:
        return False
    if os.environ.get('DOCICH_DISABLE_ISSUE_FILER') == '1':
        return False
    incident_id = record.get('incident_id')
    if not isinstance(incident_id, str) or not incident_id:
        return False
    root = Path(__file__).resolve().parents[3]
    runtime = Path(runtime)
    log_path = runtime / ISSUE_LOG
    if log_path.is_symlink():
        return False
    try:
        if log_path.exists() and log_path.stat().st_size > ISSUE_LOG_LIMIT:
            os.replace(log_path, runtime / (ISSUE_LOG + '.previous'))
    except OSError:
        pass
    env = os.environ.copy()
    pythonpath = str(root / 'src')
    if env.get('PYTHONPATH'):
        pythonpath += os.pathsep + env['PYTHONPATH']
    env['PYTHONPATH'] = pythonpath
    path_parts = ['/usr/local/bin', '/usr/bin', '/bin', '/snap/bin',
                  str(Path.home() / '.local' / 'bin')]
    current = [part for part in env.get('PATH', '').split(os.pathsep) if part]
    env['PATH'] = os.pathsep.join(path_parts + [part for part in current
                                                if part not in path_parts])
    command = [sys.executable, '-m', 'docich.hanjuku.month_audit',
               '--runtime', str(runtime), 'file',
               '--incident', incident_id, '--repo', 'azumag/docich']
    try:
        fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'ab') as output:
            subprocess.Popen(command, cwd=str(root), env=env,
                             stdin=subprocess.DEVNULL, stdout=output,
                             stderr=subprocess.STDOUT, start_new_session=True,
                             close_fds=True)
    except OSError:
        return False
    return True


def _seal(runtime, state, now, on_sealed=None):
    pending = state.get('pending')
    if not isinstance(pending, dict):
        return False
    record = pending.get('incident')
    if not isinstance(record, dict):
        state['pending'] = None
        return False
    window_end = min(float(now), float(pending.get('window_end_at') or now))
    rows = _read_decisions(runtime, pending.get('window_start_at') or now,
                           window_end, state.get('identity'))
    record['after'] = _after_summary(rows, pending.get('window_start_at') or now, window_end)
    record['sealed_at'] = float(now)
    record['after']['early'] = float(now) < float(pending.get('window_end_at') or now)
    record = _shrink(record)
    _append_incident(runtime, record, on_sealed)
    state['pending'] = None
    state['last_sealed'] = {
        'incident_id': record.get('incident_id'),
        'kind': record.get('kind'),
        'month': record.get('month'),
        'at': float(now),
    }
    _write_state(runtime, state)
    return True


def _seal_if_due(runtime, state, now, on_sealed=None):
    pending = state.get('pending')
    if not isinstance(pending, dict):
        return False
    if float(now) < float(pending.get('window_end_at') or 0):
        return False
    return _seal(runtime, state, now, on_sealed)


def _close_month(runtime, state, before, after, now, on_sealed=None):
    observed = state.get('observed')
    if not isinstance(observed, dict):
        return
    chapter, month = observed.get('chapter'), observed.get('month')
    close_at = float(now)
    context = before if (isinstance(before, dict)
                         and (before.get('chapter'), before.get('month')) == (chapter, month)) else None
    ledger_scope = observed.get('ledger_scope')
    confirmed = observed.get('ledger_launches')
    if context is not None:
        month_sorties = context.get('month_sorties')
        if isinstance(month_sorties, dict):
            ledger_scope = month_sorties.get('scope')
            confirmed = month_sorties.get('launches')
    ledger_ok = ledger_scope == [chapter, month] and type(confirmed) is int
    if not ledger_ok:
        confirmed = None
    after_mem = after.get('policy') or {}
    after_chapter = after_mem.get('chapter')
    after_month = after_mem.get('month')
    chapter_changed = type(after_chapter) is int and after_chapter != chapter
    if type(after_chapter) is not int:
        chapter_changed = True
    next_month = _next_month(month)
    irregular = bool(next_month is not None and isinstance(after_month, str)
                     and after_month != next_month)
    if next_month is None:
        irregular = True
    observed = dict(observed)
    # The gap between the last in-month observation and the boundary counts:
    # a launch or capture can hide in it, so the month is not fully observed.
    observed['max_gap_s'] = max(float(observed.get('max_gap_s') or 0.0),
                                max(0.0, close_at - float(observed.get('last_at') or close_at)))
    since = float(observed.get('first_at') or close_at)
    until = float(observed.get('last_at') or close_at)
    rows = _read_decisions(runtime, since, until, state.get('identity'))
    coverage = _coverage(rows, since, until)
    departures, launch_events = _departures(rows)
    boss_phase = _boss_phase(after_mem)
    boss_departures, boss_events = _boss_departures(rows, chapter)
    boss_month = None
    if boss_phase is not None:
        tracker = state.get('boss') if isinstance(state.get('boss'), dict) else {}
        boss_month = {
            'boss_name': boss_phase.get('boss_name'),
            'bosses': boss_phase.get('bosses'),
            'departures': boss_departures,
            'launch_events': boss_events,
            'started_at': tracker.get('started_at'),
            'observations': tracker.get('observations'),
        }
    duration_s = max(0.0, until - since)
    confidence, reasons = _confidence(observed, context, ledger_ok, chapter_changed,
                                      irregular, coverage, duration_s)
    if not observed.get('started_clean'):
        kind = 'skipped'
    elif chapter_changed:
        kind = 'skipped'
    elif irregular:
        kind = 'skipped'
    elif boss_month is not None and boss_departures == 0:
        kind = ('boss_month_zero_sortie' if confidence == 'high'
                else 'boss_month_zero_unverified')
    elif departures == 0 and confirmed == 0 and confidence == 'high':
        kind = 'zero_sortie'
    elif departures == 0 and (confirmed is None or confidence != 'high'):
        kind = 'zero_unverified'
    elif departures > 0 and confirmed == 0:
        kind = 'unconfirmed_launches'
    else:
        kind = 'ok'
    state['last_close'] = {
        'chapter': chapter,
        'month': month,
        'kind': kind,
        'confidence': confidence,
        'confirmed': confirmed,
        'departures': departures,
        'at': close_at,
    }
    if kind in ('zero_sortie', 'zero_unverified', 'unconfirmed_launches',
                'boss_month_zero_sortie', 'boss_month_zero_unverified'):
        if state.get('pending'):
            _seal(runtime, state, close_at, on_sealed)
        record = _build_incident(
            runtime, observed, context, rows, close_at, state.get('identity'), kind=kind,
            confirmed=confirmed, departures=departures, launch_events=launch_events,
            coverage=coverage, confidence=confidence, reasons=reasons,
            chapter_changed=chapter_changed, irregular=irregular,
            boss_month=boss_month)
        state['pending'] = {
            'incident': record,
            'window_start_at': close_at,
            'window_end_at': close_at + AFTER_WINDOW_SECONDS,
        }
    # The boundary we just saw is the next month's start, so it is fully
    # observed even when the month that just closed was the first (partial)
    # month of this run. A chapter change or a month jump is different.
    state['_next_started_clean'] = not chapter_changed and not irregular


def observe(runtime, before, after, records=None, *, now=None, on_sealed=None):
    """Update the audit for one observation; never raises into the game loop."""
    runtime = Path(runtime)
    now = time.time() if now is None else float(now)
    if not isinstance(after, dict):
        return None
    mem = after.get('policy')
    if not isinstance(mem, dict):
        return None
    chapter, month = mem.get('chapter'), mem.get('month')
    if type(chapter) is not int or not isinstance(month, str) or not month:
        return None
    identity = _identity(after, mem)
    state = _read_state(runtime)
    if (not isinstance(state, dict) or state.get('schema') != SCHEMA
            or _identity_key(state.get('identity')) != _identity_key(identity)):
        state = _new_state(identity)
    force = False
    if _seal_if_due(runtime, state, now, on_sealed):
        force = True
    observed = state.get('observed')
    if not isinstance(observed, dict):
        state['observed'] = _open_month(before, after, now, started_clean=False)
        force = True
    elif (observed.get('chapter'), observed.get('month')) != (chapter, month):
        _close_month(runtime, state, before, after, now, on_sealed)
        started = bool(state.pop('_next_started_clean', False))
        state['observed'] = _open_month(before, after, now, started_clean=started)
        force = True
    else:
        _touch_month(observed, after, now)
    _track_boss(runtime, state, after, records, now, on_sealed)
    _track_camp(runtime, state, after, records, now, on_sealed)
    _track_sortie(runtime, state, after, records, now, on_sealed)
    _track_battle_resources(runtime, state, after, records, now, on_sealed)
    _track_camp_abandon(runtime, state, after, now, on_sealed)
    _track_chikujou(runtime, state, before, after, records, now, on_sealed)
    _track_recall_kikan(runtime, state, after, records, now, on_sealed)
    _track_battle_menu(runtime, state, before, after, records, now, on_sealed)
    _track_sortie_cards(runtime, state, after, records, now, on_sealed)
    state['write_tick'] = int(state.get('write_tick') or 0) + 1
    # The open-month counters only advance if every observation persists them:
    # a write every tenth observation restarted from the last file and could
    # never accumulate past one. The record is about one kilobyte, far below
    # the bot state the same process already writes.
    _write_state(runtime, state)
    return state



def record_corner_stall(runtime, *, reason, run, identity, rotation_request_id=None,
                        terminal_evidence=None, now=None, spawn=True):
    """Append one screen/input-stall corner stop incident and file it.

    Called by the corner owner before the slot is released to rotation. The
    helper is deliberately independent of the open-month state: the corner is
    already terminal, and the bot will not observe another month.
    """
    if reason not in ('screen_stalled', 'input_stalled'):
        return None
    now = time.time() if now is None else float(now)
    runtime = Path(runtime)
    run = run if isinstance(run, dict) else {}
    identity = identity if isinstance(identity, dict) else {}
    mem = run.get('policy') if isinstance(run.get('policy'), dict) else {}
    evidence = terminal_evidence if isinstance(terminal_evidence, dict) else run.get('terminal_evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    raw_id = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
              f"{reason}|{evidence.get('frame_sha256')}")
    incident_id = hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:20]
    for existing in load_incidents(runtime):
        if existing.get('incident_id') == incident_id:
            return existing
    context = _context(run) if run else None
    rows = _read_decisions(runtime, max(0.0, now - 120), now, identity)
    counts = _decision_counts(rows)
    departures, launch_events = _departures(rows)
    battle_count = sum(counts.get(name, 0) for name in counts
                       if name.startswith('battle_') or name == 'monster_menu_choice')
    kind = 'corner_stalled'
    signals = [kind, reason]
    for name in _signals(context, counts, None, departures, battle_count):
        if name not in signals:
            signals.append(name)
    signals = signals[:8]
    record = {
        'schema': SCHEMA,
        'event': 'corner_stalled',
        'kind': kind,
        'confidence': 'high',
        'confidence_reasons': [],
        'chapter': mem.get('chapter') if isinstance(mem, dict) else None,
        'month': mem.get('month') if isinstance(mem, dict) else None,
        'corner_stall': {
            'reason': reason,
            'unchanged_seconds': _bounded_int(run.get('unchanged_seconds'), 0, 10 ** 7),
            'actions_sent': _bounded_int(run.get('actions_sent'), 0, 10 ** 9),
            'phase': _bounded_text(run.get('phase'), 32),
            'bot_version': _bounded_text(run.get('bot_version'), 80),
            'chapter': mem.get('chapter') if isinstance(mem, dict) else None,
            'month': mem.get('month') if isinstance(mem, dict) else None,
            'runtime_id': _bounded_text(identity.get('runtime_id'), 64),
            'generation': _bounded_int(identity.get('generation'), 0, 10 ** 9),
            'rotation_request_id': _bounded_text(rotation_request_id, 64),
            'terminal_evidence': {
                'reason': _bounded_text(evidence.get('terminal_reason'), 32),
                'frame_sha256': _bounded_text(evidence.get('frame_sha256'), 64),
                'tick': _bounded_int(evidence.get('tick'), 0, 10 ** 9),
            } if evidence else None,
        },
        'plan': copy.deepcopy((context or {}).get('plan') or {}),
        'world': copy.deepcopy((context or {}).get('world') or {}),
        'flow': copy.deepcopy((context or {}).get('flow') or {}),
        'stats': copy.deepcopy((context or {}).get('stats') or {}),
        'battles': {'decisions': battle_count},
        'signals': signals,
        'decisions': {
            'total': len(rows),
            'counts': counts,
            'coverage': None,
            'before': _decision_rows(rows, 30),
            'after': None,
        },
        'evidence': {
            'runtime_dir': str(runtime.name),
            'decisions': 'hanjuku_decisions.jsonl',
            'events': 'hanjuku_events.jsonl',
            'frames': 'hanjuku_frames/',
            'bot_version': run.get('bot_version'),
            'identity': identity,
        },
        'created_at': now,
    }
    record['incident_id'] = incident_id
    record['signature'] = kind
    record['fingerprint'] = _fingerprint(record.get('chapter'), kind, [reason])
    record['auto_file'] = True
    record = _shrink(record)
    _append_incident(runtime, record)
    if spawn:
        spawn_issue_filer(runtime, record)
    return record


def load_incidents(runtime):
    """Sealed incidents, oldest first; never raises on a malformed line."""
    runtime = Path(runtime)
    rows = []
    for name in (f'{LOG_NAME}.jsonl', f'{LOG_NAME}.previous.jsonl'):
        path = runtime / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open(encoding='utf-8', errors='ignore') as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get('schema') == SCHEMA:
                        rows.append(row)
        except OSError:
            continue
    rows.sort(key=lambda row: row.get('created_at') or 0)
    return rows


def _md(value):
    if value is None:
        return ''
    return str(value).replace('|', '\\|').replace('\n', ' ')[:160]


def _clock(value):
    if type(value) not in (int, float):
        return ''
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%H:%M:%SZ')


def _cell(value):
    text = _md(value)
    return f'{BT}{text}{BT}' if text else ''


def _timeline(record):
    rows = (record.get('decisions') or {}).get('before') or []
    noisy = {'situation_held', 'unknown_hold_advance', 'battle_draw_hold'}
    useful = [row for row in rows if row.get('decision') not in noisy]
    if useful:
        return useful[-20:], len(useful) != len(rows)
    return rows[-3:], True


def render(record):
    """Deterministic Markdown for one incident."""
    if not isinstance(record, dict):
        raise AuditError('invalid_incident')
    month = record.get('month_summary') or {}
    sorties = record.get('sorties') or {}
    signals = record.get('signals') or []
    labels = [SIGNAL_LABELS.get(name, name) for name in signals]
    labels_text = ' / '.join(labels) if labels else '未分類'
    identity = (record.get('evidence') or {}).get('identity') or {}
    kind = record.get('kind')
    confidence = record.get('confidence')
    camp = record.get('camp_event') or {}
    sortie = record.get('sortie_event') or {}
    battle = record.get('battle_event') or {}
    stay = record.get('camp_stay') or {}
    chikujou = record.get('chikujou_event') or {}
    boss_month = record.get('boss_month') or {}
    boss = record.get('boss_phase') or {}
    recall = record.get('recall_event') or {}
    stall = record.get('corner_stall') or {}
    battle_menu = record.get('battle_menu') or {}
    sortie_cards = record.get('sortie_event') or {}
    body = [
        f"<!-- hanjuku-month-audit fingerprint={record.get('fingerprint')} -->",
        f"<!-- hanjuku-month-audit occurrence={record.get('incident_id')} -->",
        f"## {_issue_title(record)}",
        '',
        f"- 判定: **{kind}** (confidence: {BT}{confidence}{BT})",
        f"- シグネチャ: {labels_text}",
        f"- ラン: {BT}{identity.get('runtime_id')}{BT} / generation {BT}{identity.get('generation')}{BT} / {BT}{(record.get('evidence') or {}).get('bot_version')}{BT}",
        f"- 月: ch{month.get('chapter')} {month.get('month')}（観測 {month.get('observations')}回 / {round(float(month.get('duration_s') or 0), 1)}秒 / max gap {round(float(month.get('max_gap_s') or 0), 1)}秒）",
        f"- 判定理由: {', '.join(record.get('confidence_reasons') or []) or 'なし'}",
    ]
    if not camp:
        body.append(f"- 出撃: 確定 {sorties.get('confirmed')} / 発射 {sorties.get('departures')} / order_start {sorties.get('order_start')} / order_launched {sorties.get('order_launched')}")
    body.append('')
    if stall:
        body += [
            '### コーナー停止',
            '',
            f"- 理由: {stall.get('reason')} / 無変化 {stall.get('unchanged_seconds')}秒"
            f" / actions {stall.get('actions_sent')} / phase {stall.get('phase') or '不明'}",
            f"- ラン: {stall.get('runtime_id')} / generation {stall.get('generation')}"
            f" / bot {stall.get('bot_version') or '不明'}",
            f"- 章/月: ch{stall.get('chapter')} {stall.get('month')}"
            f" / rotation_request_id {stall.get('rotation_request_id') or 'なし'}",
            f"- terminal evidence: {stall.get('terminal_evidence')}",
            '',
        ]
    if recall:
        body += [
            '### 帰還手段',
            '',
            f"- 選択: {recall.get('choice')} / 期待: 全体マップ（Y）で自軍城へ直接戻る",
            f"- 現在: {recall.get('current')} / move: {recall.get('move')}",
            f"- 将軍: {recall.get('general') or '不明'} / 帰還先: {recall.get('goal') or '不明'}"
            f" / 野営: {recall.get('camp')} / stage: {recall.get('stage') or '不明'}",
            f"- Yジャンプ: y_jumps {recall.get('y_jumps')} / y_jumped {recall.get('y_jumped')}",
            f"- 理由: {recall.get('reason') or '不明'}",
            '',
        ]
    if boss_month:
        body += [
            '### ボスフェーズ月次',
            '',
            f"- ボス: {boss_month.get('boss_name') or 'ボス'}"
            f" / 残り: {', '.join(boss_month.get('bosses') or [])}",
            f"- 月内ボス出撃: {boss_month.get('departures')}"
            f" / launch_events {boss_month.get('launch_events')}",
            f"- フェーズ開始: {boss_month.get('started_at')}"
            f" / 観測 {boss_month.get('observations')}回",
            '',
        ]
    if chikujou:
        body += [
            '### 築城',
            '',
            f"- 結果: {chikujou.get('outcome')} / 決定: {BT}{chikujou.get('decision')}{BT}"
            f" / 月: {chikujou.get('month')}",
            f"- 所持金: {chikujou.get('gold')} / 必要残し: {chikujou.get('reserve')}"
            f" / 状態: {chikujou.get('shop_status')}",
            f"- 未試行の候補: {chikujou.get('staffed_candidates') or chikujou.get('candidates')}",
            f"- 試行済み: {chikujou.get('tried')} / 増築済み: {chikujou.get('upgrades')}",
            f"- 理由: {chikujou.get('reason') or '不明'}",
            *([f"- 保留: {_CHIKUJOU_HOLD_LABELS.get(chikujou['held_for'], chikujou['held_for'])}"
                '（卵/募集の未決で築城を開かない fail-closed 設計・docich#5045）']
               if chikujou.get('held_for') else []),
            *(['- 築城行の読取り: 不可（有限回の再観測後も未読・docich#5079/#5097）']
               if chikujou.get('menu_unreadable') else []),
            '',
        ]
    if stay:
        body += [
            '### 野営放置',
            '',
            f"- 将軍: {stay.get('general')} / 継続: {stay.get('months')}ヶ月"
            f" / 初回: {stay.get('first_month')} ({stay.get('first_tick')})",
            f"- HP: {stay.get('hp')}/{stay.get('max_hp')} / 卵: {stay.get('egg')}"
            f" / 壊れ: {bool(stay.get('broken'))} / 状態: {stay.get('status_text') or '不明'}",
            f"- 城候補: {stay.get('castle_candidate') or '不明'}"
            f" / 最終観測tick: {stay.get('last_tick')}",
            '',
        ]
    if sortie_cards:
        body += [
            '### 切り札の非携行',
            '',
            f"- 将軍: {sortie_cards.get('general') or '不明'} / step: {sortie_cards.get('step') or '不明'}"
            f" / purpose: {sortie_cards.get('purpose') or '不明'}",
            f"- 出撃元: {sortie_cards.get('source') or '不明'}"
            f" / 対象: {sortie_cards.get('target') or '不明'}",
            f"- 在庫: {sortie_cards.get('inventory')} / 携行: なし"
            f" / 計画cards: {sortie_cards.get('planned_cards')}",
            f"- 決定: {sortie_cards.get('decision')} / 理由: {sortie_cards.get('reason') or '不明'}",
            '',
        ]
    if battle_menu:
        body += [
            '### 戦闘Bメニュー',
            '',
            f"- 将軍: {battle_menu.get('general') or '不明'} / step: {battle_menu.get('step') or '不明'}"
            f" / side: {battle_menu.get('side') or '不明'}",
            f"- 開閉: {battle_menu.get('opened_at')} → {battle_menu.get('closed_at')}"
            f"（{battle_menu.get('duration_s')}秒） / 実行: なし",
            f"- HP: ally {battle_menu.get('ally_hp')} / enemy {battle_menu.get('enemy_hp')}",
            f"- 閉じた決定: {battle_menu.get('close_decision')}"
            f" / 理由: {battle_menu.get('close_reason') or '不明'}",
            '',
        ]
    if battle:
        body += [
            '### 戦闘結果',
            '',
            f"- 結果: {battle.get('outcome')} / 決定: {BT}{battle.get('decision')}{BT}",
            f"- 将軍: {battle.get('general') or '不明'} / 敵: {battle.get('enemy') or '不明'}"
            f" / 城: {battle.get('castle') or '不明'} / side: {battle.get('side') or '不明'}",
            f"- HP: ally {battle.get('ally_hp')} / enemy {battle.get('enemy_hp')}",
            f"- 卵残数: {battle.get('egg_uses')} / 切り札: 選択 {battle.get('cards_selected')}"
            f" / 使用 {battle.get('cards_used')} / 残り {battle.get('cards_remaining')}"
            f" / pending {battle.get('card_pending')}",
            *([f"- 残資源（消費確定不能・署名未校正）: {battle.get('cards_unconfirmed_egg_drop')}"]
              if battle.get('cards_unconfirmed_egg_drop') else []),
            f"- 退却選択: {bool(battle.get('retreat_selected'))} / 理由: {battle.get('reason') or '不明'}",
            '',
        ]
    if sortie:
        body += [
            '### 出撃失敗 / キャンセル',
            '',
            f"- 結果: {sortie.get('outcome')} / 決定: {BT}{sortie.get('decision')}{BT}",
            f"- step: {sortie.get('step') or '不明'}",
            f"- 将軍: {sortie.get('general') or '不明'} / 出撃元: {sortie.get('source') or '不明'}"
            f" / 対象: {sortie.get('target') or '不明'} / 目的: {sortie.get('purpose') or '不明'}",
            f"- 理由: {sortie.get('reason') or '不明'} ({sortie.get('reason_code')})"
            f" / misses: {sortie.get('misses')}",
            '',
        ]
    if camp:
        body += [
            '### キャンプ帰還',
            '',
            f"- 結果: {camp.get('outcome')} / 決定: {BT}{camp.get('decision')}{BT}",
            f"- 将軍: {camp.get('general') or '不明'} / 野営: {camp.get('camp')} / 帰還先: {camp.get('castle') or '不明'}",
            f"- 理由: {camp.get('reason') or '不明'} ({camp.get('reason_code')})",
            f"- stage: {camp.get('stage') or '不明'} / steps: {camp.get('steps')}"
            f" / input_sent: {bool(camp.get('input_sent'))} / interrupted: {bool(camp.get('interrupted'))}",
            '',
        ]
    if boss:
        body += [
            '### ボスフェーズ',
            '',
            f"- ボス: {boss.get('boss_name') or 'ボス'} / 対象: {', '.join(boss.get('bosses') or [])}",
            f"- 継続: {round(float(boss.get('observed_s') or 0), 1)}秒 / 観測 {boss.get('observations')}回"
            f" / max gap {round(float(boss.get('max_gap_s') or 0), 1)}秒",
            f"- ボス戦闘: {boss.get('battle_count')} / battle_start 全体: {boss.get('battle_starts')}",
            f"- ボスHP観測: {bool(boss.get('boss_hp_seen') or boss.get('boss_hp_history_count'))}"
            f" / 残りボス城: {', '.join(boss.get('bosses') or [])}",
            '',
        ]
    body.append('### 直前の決定' if (camp or sortie or battle or stay or chikujou or recall
                                     or stall or battle_menu or sortie_cards) else
                ('### ボスフェーズの決定' if kind in ('boss_month_zero_sortie',
                                                     'boss_month_zero_unverified')
                 else '### フェーズ中の決定' if kind == 'boss_phase_no_battle'
                 else '### 月内の決定'))
    body.append('')
    rows, filtered = _timeline(record)
    if filtered:
        body.append('定常的な保留（situation_held など）は決定内訳に集計し、ここでは省略する。')
        body.append('')
    body += [
        '| 時刻 | 決定 | step | 将軍 | 対象 | 理由 |',
        '| --- | --- | --- | --- | --- | --- |',
    ]
    for row in rows:
        body.append(f"| {_clock(row.get('at'))} | {_cell(row.get('decision'))} | {_cell(row.get('chart_step'))} | {_md(row.get('general'))} | {_md(row.get('target'))} | {_md(row.get('reason'))} |")
    body += ['', '### 現在の計画' if (camp or sortie or battle or stay or chikujou or recall
                                     or stall or battle_menu or sortie_cards or boss_month
                                     or kind == 'boss_phase_no_battle')
             else '### 前月の計画', '',
             '| step | 状態 | 目的 | 出撃元 | 対象 | 将軍 |',
             '| --- | --- | --- | --- | --- | --- |']
    plan = record.get('plan') or {}
    orders = plan.get('orders') if isinstance(plan.get('orders'), dict) else {}
    chart_orders = ((plan.get('chart_plan') or {}).get('orders') or [])
    detail = {row.get('step'): row for row in chart_orders if isinstance(row, dict)}
    for step, status in list(orders.items())[:30]:
        row = detail.get(step) or {}
        body.append(f"| {_cell(step)} | {_md(status)} | {_md(row.get('purpose'))} | {_md(row.get('source'))} | {_md(row.get('target'))} | {_md(row.get('general'))} |")
    body += ['', '### 世界状態', '']
    world = record.get('world') or {}
    body.append(f"- 所持金: {world.get('gold')}")
    body.append(f"- 奪取: {', '.join(world.get('captured') or []) or 'なし'}")
    body.append(f"- 失城: {', '.join(world.get('lost') or []) or 'なし'}")
    garrison = world.get('garrison') or {}
    body.append('- 在城: ' + (' / '.join(f"{castle}({len(names)})"
                                         for castle, names in garrison.items()) or '未確認'))
    in_flight = world.get('sorties') or {}
    if in_flight:
        body.append('- 行軍中: ' + ', '.join(
            f"{step}:{row.get('status')}({row.get('age_ticks')}t)"
            for step, row in list(in_flight.items())[:8]))
    body += ['', '### 決定内訳', '']
    counts = (record.get('decisions') or {}).get('counts') or {}
    body.append(' / '.join(f"{BT}{name}{BT}:{count}" for name, count in list(counts.items())[:20]))
    after = record.get('after') or {}
    if after:
        body += ['', '### 翌5分', '',
                 f"- 決定 {after.get('decisions')}件 / 出撃 {after.get('departures')}件 / 回復: {'yes' if after.get('recovered') else 'no'} / 初出撃まで {after.get('first_launch_seconds')}秒{'（early seal）' if after.get('early') else ''}"]
    body += ['', '### 証拠', '',
             f"- runtime: {BT}{(record.get('evidence') or {}).get('runtime_dir')}{BT}",
             f"- decisions: {BT}{(record.get('evidence') or {}).get('decisions')}{BT} / events: {BT}{(record.get('evidence') or {}).get('events')}{BT}",
             f"- frames: {BT}{(record.get('evidence') or {}).get('frames')}{BT}",
             f"- incident_id: {BT}{record.get('incident_id')}{BT}",
             '', '<details><summary>JSON</summary>', '',
             f"{BT * 3}json",
             json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1)[:24000],
             f"{BT * 3}", '', '</details>']
    return '\n'.join(body)


def render_comment(record):
    """Compact recurrence comment for an existing open issue."""
    month = record.get('month_summary') or {}
    sorties = record.get('sorties') or {}
    labels = [SIGNAL_LABELS.get(name, name) for name in record.get('signals') or []]
    after = record.get('after') or {}
    boss = record.get('boss_phase') or {}
    lines = [
        f"<!-- hanjuku-month-audit fingerprint={record.get('fingerprint')} -->",
        f"<!-- hanjuku-month-audit occurrence={record.get('incident_id')} -->",
        f"### 再発: ch{record.get('chapter')} {record.get('month')}",
        '',
        f"- 判定: **{record.get('kind')}** ({BT}{record.get('confidence')}{BT})",
        f"- 出撃: 確定 {sorties.get('confirmed')} / 発射 {sorties.get('departures')}"
        f" / order_start {sorties.get('order_start')}",
        f"- シグナル: {' / '.join(labels) if labels else '未分類'}",
        f"- 月: 観測 {month.get('observations')}回 / {round(float(month.get('duration_s') or 0), 1)}秒"
        f" / max gap {round(float(month.get('max_gap_s') or 0), 1)}秒",
        f"- incident_id: {BT}{record.get('incident_id')}{BT}",
    ]
    camp = record.get('camp_event') or {}
    sortie = record.get('sortie_event') or {}
    battle = record.get('battle_event') or {}
    stay = record.get('camp_stay') or {}
    chikujou = record.get('chikujou_event') or {}
    recall = record.get('recall_event') or {}
    stall = record.get('corner_stall') or {}
    battle_menu = record.get('battle_menu') or {}
    sortie_cards = record.get('sortie_event') or {}
    boss_month = record.get('boss_month') or {}
    if record.get('kind') == 'sortie_without_cards':
        lines.insert(5, f"- 切り札: 在庫 {sortie_cards.get('inventory')}"
                        f" / 携行なし / {sortie_cards.get('general') or '不明'}")
    elif record.get('kind') == 'battle_menu_wasted':
        lines.insert(5, f"- 戦闘Bメニュー: {battle_menu.get('duration_s')}秒"
                        f" / 実行なし / {battle_menu.get('general') or '不明'}"
                        f" / close {battle_menu.get('close_decision')}")
    elif record.get('kind') == 'corner_stalled':
        lines.insert(5, f"- 停止: {stall.get('reason')}"
                        f" / 無変化 {stall.get('unchanged_seconds')}秒"
                        f" / g{stall.get('generation')}")
    elif record.get('kind') == 'recall_kikan_selected':
        lines.insert(5, f"- 帰還: {recall.get('choice')} / goal {recall.get('goal')}"
                        f" / {recall.get('general') or '不明'}")
    elif record.get('kind') in ('boss_month_zero_sortie', 'boss_month_zero_unverified'):
        lines.insert(5, f"- ボス月次: {boss_month.get('departures')}"
                        f" / {boss_month.get('boss_name') or 'ボス'}")
    elif record.get('kind') in ('chikujou_available_at_end', 'chikujou_cancelled_with_resources'):
        lines.insert(5, f"- 築城: {chikujou.get('outcome')} / gold {chikujou.get('gold')}"
                        f" / reserve {chikujou.get('reserve')}"
                        f" / 候補 {chikujou.get('staffed_candidates')}"
                        + (f" / 保留 {chikujou.get('held_for')}" if chikujou.get('held_for') else ''))
    elif record.get('kind') == 'camp_abandoned':
        lines.insert(5, f"- 野営: {stay.get('months')}ヶ月 / {stay.get('general')}"
                        f" / first {stay.get('first_tick')}")
    elif record.get('kind') in ('battle_retreat_with_resources', 'battle_loss_with_resources'):
        lines.insert(5, f"- 戦闘: {battle.get('outcome')} / 資源 {battle.get('resources')}"
                        f" / {battle.get('general') or '不明'}")
    elif record.get('kind') in ('sortie_failed', 'sortie_cancelled'):
        lines.insert(5, f"- 出撃: {sortie.get('outcome')} / {sortie.get('reason_code')}"
                        f" / {sortie.get('general') or sortie.get('step_local') or '不明'}")
    elif record.get('kind') in ('camp_recall_failed', 'camp_recall_cancelled',
                                'camp_recall_unconfirmed'):
        lines.insert(5, f"- キャンプ: {camp.get('outcome')} / {camp.get('reason_code')}"
                        f" / 将軍 {camp.get('general') or '不明'}")
    elif record.get('kind') == 'boss_phase_no_battle':
        lines.insert(5, f"- ボス: {boss.get('boss_name') or 'ボス'}"
                        f" / 継続 {round(float(boss.get('observed_s') or 0), 1)}秒")
    else:
        lines.insert(-1, f"- 翌5分: 出撃 {after.get('departures')}"
                         f" / 回復 {'yes' if after.get('recovered') else 'no'}")
    return '\n'.join(lines)


def _issue_title(record):
    kind = record.get('kind')
    if kind == 'sortie_without_cards':
        event = record.get('sortie_event') or {}
        who = event.get('general') or event.get('step') or '将軍不明'
        return (f"[半熟英雄] 切り札在庫があるのに非携行で出撃: ch{record.get('chapter')} {who}")[:100]
    if kind == 'battle_menu_wasted':
        menu = record.get('battle_menu') or {}
        who = menu.get('general') or menu.get('step') or '将軍不明'
        return (f"[半熟英雄] 戦闘Bメニューを無駄に開閉: ch{record.get('chapter')} {who}")[:100]
    if kind == 'corner_stalled':
        stall = record.get('corner_stall') or {}
        label = ('画面停滞' if stall.get('reason') == 'screen_stalled' else '入力停滞')
        return (f"[半熟英雄] {label}でコーナー停止: ch{record.get('chapter')} "
                f"g{stall.get('generation')}")[:100]
    if kind == 'recall_kikan_selected':
        recall = record.get('recall_event') or {}
        who = recall.get('general') or recall.get('goal') or '将軍不明'
        return (f"[半熟英雄] 帰還で「きかん」を選択: ch{record.get('chapter')} {who}")[:100]
    if kind in ('boss_month_zero_sortie', 'boss_month_zero_unverified'):
        boss_month = record.get('boss_month') or {}
        boss = boss_month.get('boss_name') or 'ボス'
        return (f"[半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch{record.get('chapter')} "
                f"{boss}")[:100]
    if kind in ('chikujou_available_at_end', 'chikujou_cancelled_with_resources'):
        label = ('築城可能なまま月一終了' if kind == 'chikujou_available_at_end'
                 else '築城可能なのにキャンセル')
        event = record.get('chikujou_event') or {}
        return (f"[半熟英雄] {label}: ch{record.get('chapter')} {event.get('month') or ''}")[:100]
    if kind == 'camp_abandoned':
        stay = record.get('camp_stay') or {}
        return (f"[半熟英雄] 野営放置: ch{record.get('chapter')} "
                f"{stay.get('general') or '将軍不明'} ({stay.get('months')}ヶ月)")[:100]
    if kind in ('battle_retreat_with_resources', 'battle_loss_with_resources'):
        battle = record.get('battle_event') or {}
        resources = battle.get('resources') or []
        text = 'と'.join('卵' if name == 'egg' else '切り札' for name in resources) or '資源'
        label = f"{text}を残して{'敗北' if kind == 'battle_loss_with_resources' else '退却'}"
        who = battle.get('general') or battle.get('step') or '不明'
        return f"[半熟英雄] {label}: ch{record.get('chapter')} {who}"[:100]
    if kind in ('sortie_failed', 'sortie_cancelled'):
        sortie = record.get('sortie_event') or {}
        label = '出撃失敗' if kind == 'sortie_failed' else '出撃キャンセル'
        who = sortie.get('general') or sortie.get('step_local') or '不明'
        return f"[半熟英雄] {label}: ch{record.get('chapter')} {who}"[:100]
    if kind in ('camp_recall_failed', 'camp_recall_cancelled', 'camp_recall_unconfirmed'):
        camp = record.get('camp_event') or {}
        label = {'camp_recall_failed': 'キャンプ帰還失敗',
                 'camp_recall_cancelled': 'キャンプ帰還キャンセル',
                 'camp_recall_unconfirmed': 'キャンプ帰還未確認'}[kind]
        who = camp.get('general') or camp.get('castle') or '将軍不明'
        return f"[半熟英雄] {label}: ch{record.get('chapter')} {who}"[:100]
    if kind == 'boss_phase_no_battle':
        boss = (record.get('boss_phase') or {}).get('boss_name') or 'ボス'
        return f"[半熟英雄] ボスフェーズでボス戦闘ゼロ: ch{record.get('chapter')} {boss}"[:100]
    label = PRIMARY_LABELS.get(record.get('signature'), '原因未分類')
    return f"{TITLE_PREFIX}: ch{record.get('chapter')} {label}"[:100]


class GhError(AuditError):
    pass


def _run_gh(args, *, gh=None):
    if gh is not None:
        result = gh(args)
        if isinstance(result, tuple):
            code, out, err = result
        else:
            code, out, err = 0, result or '', ''
        if code != 0:
            raise GhError((err or 'gh failed')[:200])
        return out
    if shutil.which('gh') is None:
        raise GhError('gh_not_found')
    proc = subprocess.run(['gh', *args], text=True, capture_output=True)
    if proc.returncode != 0:
        raise GhError((proc.stderr or '').strip()[:200] or 'gh_failed')
    return proc.stdout


def _gh_json(args, *, gh=None):
    raw = _run_gh(args, gh=gh)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise GhError('gh_bad_json') from exc


def _secret_scanner():
    env = os.environ.get('AGENT_GUARDS')
    candidates = []
    if env:
        candidates.append(Path(env))
    try:
        root = Path(__file__).resolve().parents[3]
        candidates += [root.parent / 'agent-guards',
                       root.parent.parent / 'agent-guards',
                       root.parent.parent.parent / 'agent-guards',
                       # Vendored copy for the production VM timer; the
                       # workspace agent-guards above stays preferred.
                       root / 'ops' / 'vm_actions',
                       Path.cwd().parent / 'agent-guards',
                       Path.cwd() / 'agent-guards']
    except (IndexError, OSError):
        pass
    for directory in candidates:
        path = directory / 'secret_scan.py'
        if path.is_file():
            return path
    return None


def _scan_clean(path, *, scanner=None):
    if scanner is not None:
        return bool(scanner(path))
    script = _secret_scanner()
    if script is None:
        raise AuditError('secret_scan_unavailable')
    proc = subprocess.run([sys.executable, str(script), '--quiet', str(path)],
                          text=True, capture_output=True)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise AuditError('secret_scan_failed')


def _gh_auth(gh=None):
    _run_gh(['auth', 'status'], gh=gh)


def _issues(repo, *, gh=None):
    data = _gh_json(['issue', 'list', '--repo', repo, '--state', 'open',
                     '--limit', str(MAX_ISSUES), '--json', 'number,title,body'], gh=gh)
    return data if isinstance(data, list) else []


def _issue_comments(repo, number, *, gh=None):
    data = _gh_json(['issue', 'view', str(number), '--repo', repo,
                     '--json', 'comments'], gh=gh)
    comments = data.get('comments') if isinstance(data, dict) else None
    return comments if isinstance(comments, list) else []


def _find_issue(issues, fingerprint):
    marker = f'hanjuku-month-audit fingerprint={fingerprint}'
    for issue in issues:
        if marker in (issue.get('body') or ''):
            return issue
    return None


def _occurrence_present(comments, incident_id):
    marker = f'hanjuku-month-audit occurrence={incident_id}'
    return any(marker in (comment.get('body') or '') for comment in comments)


def file_issue(runtime, *, repo=DEFAULT_REPO, incident_id=None, dry_run=False,
               min_confidence='high', gh=None, scanner=None, now=None):
    """File or comment one sealed incident through gh; returns a result dict."""
    now = time.time() if now is None else float(now)
    records = load_incidents(runtime)
    if incident_id:
        records = [row for row in records if row.get('incident_id') == incident_id]
    else:
        records = [row for row in records if row.get('auto_file') is True]
    order = {'high': 3, 'medium': 2, 'low': 1}
    records = [row for row in records
               if order.get(row.get('confidence'), 0) >= order.get(min_confidence, 3)]
    if not records:
        return {'status': 'no_incident'}
    record = records[-1]
    fingerprint = record.get('fingerprint')
    title = _issue_title(record)
    result = {'status': 'dry_run' if dry_run else 'unknown',
              'incident_id': record.get('incident_id'), 'fingerprint': fingerprint}
    if dry_run:
        result.update(title=title, body=render(record))
        return result
    _gh_auth(gh=gh)
    issues = _issues(repo, gh=gh)
    existing = _find_issue(issues, fingerprint)
    number = existing.get('number') if existing else None
    text = render_comment(record) if existing else render(record)
    if existing:
        comments = _issue_comments(repo, number, gh=gh)
        if _occurrence_present([existing, *comments], record.get('incident_id')):
            return {**result, 'status': 'already_commented', 'issue': number}
        latest = max((comment.get('createdAt') or '' for comment in comments), default='')
        if latest:
            try:
                age = now - dt.datetime.fromisoformat(latest.replace('Z', '+00:00')).timestamp()
            except (TypeError, ValueError):
                age = None
            if age is not None and 0 <= age < COMMENT_COOLDOWN_SECONDS:
                return {**result, 'status': 'cooldown', 'issue': number}
    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False,
                                     encoding='utf-8') as stream:
        stream.write(text)
        body_path = Path(stream.name)
    try:
        if not _scan_clean(body_path, scanner=scanner):
            raise AuditError('secret_scan_detected')
        if existing:
            out = _run_gh(['issue', 'comment', str(number), '--repo', repo,
                           '--body-file', str(body_path)], gh=gh)
            return {**result, 'status': 'commented', 'issue': number, 'url': out.strip()}
        out = _run_gh(['issue', 'create', '--repo', repo, '--title', title,
                       '--body-file', str(body_path), '--label', 'bug',
                       '--label', 'auto-generated'], gh=gh)
        return {**result, 'status': 'created', 'url': out.strip()}
    finally:
        try:
            body_path.unlink()
        except OSError:
            pass


def watch_once(state_dir, *, repo=DEFAULT_REPO, gh=None, scanner=None, now=None,
               dry_run=False, seen=None):
    """File new sealed incidents for every runtime under one state directory."""
    root = Path(state_dir) / 'runtimes'
    seen = seen if isinstance(seen, dict) else {}
    results = []
    try:
        if root.is_symlink() or not root.is_dir():
            return results
        entries = sorted(root.iterdir())
    except OSError:
        return results
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            log = entry / f'{LOG_NAME}.jsonl'
            if log.is_symlink() or not log.is_file():
                continue
            stat = log.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if seen.get(str(entry)) == signature:
                continue
        except OSError:
            continue
        try:
            result = file_issue(entry, repo=repo, gh=gh, scanner=scanner,
                                now=now, dry_run=dry_run)
        except (AuditError, GhError, OSError) as exc:
            result = {'status': 'error', 'error': str(exc)}
        if result.get('status') not in ('error', 'cooldown'):
            seen[str(entry)] = signature
        if result.get('status') != 'no_incident':
            results.append({'runtime_id': entry.name, **result})
    return results


def watch(state_dir, *, repo=DEFAULT_REPO, interval=60.0, once=False, gh=None,
          scanner=None, dry_run=False):
    """Poll runtime audit logs and file new incidents; never starts a service."""
    seen = {}
    while True:
        now = time.time()
        for result in watch_once(state_dir, repo=repo, gh=gh, scanner=scanner,
                                 now=now, dry_run=dry_run, seen=seen):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if once:
            return 0
        time.sleep(max(5.0, float(interval)))


def _latest_pending(state):
    pending = state.get('pending') if isinstance(state, dict) else None
    return pending.get('incident') if isinstance(pending, dict) else None


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m docich.hanjuku.month_audit')
    parser.add_argument('--runtime', default=os.environ.get('HANJUKU_RUNTIME'),
                        help='Hanjuku runtime directory (default: HANJUKU_RUNTIME)')
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show')
    show.add_argument('--pending', action='store_true')
    sub.add_parser('list')
    render_cmd = sub.add_parser('render')
    render_cmd.add_argument('--incident')
    render_cmd.add_argument('--out')
    file_cmd = sub.add_parser('file')
    file_cmd.add_argument('--repo', default=DEFAULT_REPO)
    file_cmd.add_argument('--incident')
    file_cmd.add_argument('--dry-run', action='store_true')
    file_cmd.add_argument('--min-confidence', default='high', choices=('high', 'medium', 'low'))
    sub.add_parser('finalize')
    watch_cmd = sub.add_parser('watch')
    watch_cmd.add_argument('--state-dir')
    watch_cmd.add_argument('--repo', default=DEFAULT_REPO)
    watch_cmd.add_argument('--interval', type=float, default=60.0)
    watch_cmd.add_argument('--once', action='store_true')
    watch_cmd.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'watch':
        state_dir = args.state_dir
        if not state_dir and args.runtime:
            state_dir = str(Path(args.runtime).parent.parent)
        if not state_dir:
            state_dir = os.environ.get('HANJUKU_STATE_DIR')
        if not state_dir:
            parser.error('--state-dir, --runtime or HANJUKU_STATE_DIR is required')
        try:
            return watch(state_dir, repo=args.repo, interval=args.interval,
                         once=args.once, dry_run=args.dry_run)
        except (AuditError, GhError) as exc:
            print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
            return 2
    if not args.runtime:
        parser.error('--runtime or HANJUKU_RUNTIME is required')
    runtime = Path(args.runtime)
    try:
        if args.command == 'show':
            state = _read_state(runtime)
            record = _latest_pending(state) if args.pending else None
            if record is None:
                records = load_incidents(runtime)
                record = records[-1] if records else None
            if record is None:
                print('no incident')
                return 1
            print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == 'list':
            for record in load_incidents(runtime):
                print(f"{record.get('incident_id')}\t{record.get('kind')}\t"
                      f"{record.get('confidence')}\tch{record.get('chapter')} {record.get('month')}\t"
                      f"confirmed={((record.get('sorties') or {}).get('confirmed'))}\t"
                      f"departures={((record.get('sorties') or {}).get('departures'))}")
            return 0
        if args.command == 'render':
            records = load_incidents(runtime)
            if args.incident:
                records = [row for row in records if row.get('incident_id') == args.incident]
            if not records:
                print('no incident', file=sys.stderr)
                return 1
            body = render(records[-1])
            if args.out:
                Path(args.out).write_text(body, encoding='utf-8')
            else:
                print(body)
            return 0
        if args.command == 'file':
            result = file_issue(runtime, repo=args.repo, incident_id=args.incident,
                                dry_run=args.dry_run, min_confidence=args.min_confidence)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == 'finalize':
            state = _read_state(runtime)
            if not isinstance(state, dict) or not state.get('pending'):
                print('no pending')
                return 1
            _seal(runtime, state, time.time())
            print('sealed')
            return 0
    except (AuditError, GhError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 2
    return 2


if __name__ == '__main__':
    raise SystemExit(main())