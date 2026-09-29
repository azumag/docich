"""Situation-grounded Japanese commentary for Hanjuku decision records.

Deterministic: each line is composed only from fields the policy recorded
from the screen (names, castles, HP, gold, cards, chart step and reason).
When a record carries no verified situation the line is ``None`` and the
candidate is logged as 状況判定保留; nothing is invented to fill silence.
No model or network is used. Delivery is owned by ``hanjuku_narration``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

COMMENTARY_VERSION = "hanjuku-commentary-v3-grounded-month-plan"

_STEP_LABEL = {
    '1-A1': '主人公の初手', '1-V1': 'ヴィーナスの初手', '1-C1': 'ココットの初手',
    '1-A2': '主人公の二手目', '1-V2': 'ヴィーナスの二手目', '1-C2': 'ココットの二手目',
    '1-A3': '主人公の合流', '1-B1': 'ボス戦',
}


def _cards(cards):
    return '、'.join(cards) if cards else '切り札なし'


def compose(rec: dict) -> tuple[str, str | None]:
    """Return (semantic key, text or None). None means 状況判定保留."""
    kind = rec.get('decision')
    step = rec.get('chart_step')
    label = _STEP_LABEL.get(step, 'チャート外')
    if kind == 'name_confirm':
        return 'name', '主人公の名前を「どうし」と入力して、冒険を始めます。'
    if kind == 'order_start':
        return (f'order:{step}',
                f"出撃準備の予定です。{rec['general']}将軍を{rec['source']}から{rec['target']}城へ向かわせる計画です。"
                f"持たせる切り札は{_cards(rec.get('cards'))}です。")
    if kind == 'order_launched':
        return f'launch:{step}', f"{rec['general']}将軍、{rec['target']}城へ出撃しました。"
    if kind == 'order_launched_unconfirmed':
        return f'launch_unconfirmed:{step}', f"{rec['general']}将軍の出撃先選択が中断されました。出撃の成否と行き先を確認します。"
    if kind == 'order_retry':
        if rec.get('strategy_variant') == 'retry_chart_boss_kit':
            return f'retry:{step}', 'ボス戦に敗れたので、主人公とチャートの切り札を確認して再攻撃を準備します。'
        return (f'retry:{step}',
                'チャートどおりの白兵では負けたので、今度は開幕にイッテツーンを2枚使う作戦で同じ城へ攻め直します。')
    if kind == 'order_source_changed':
        return f'source:{step}', '出撃予定の城に将軍が見当たらないので、本城から出し直します。'
    if kind == 'order_substitute':
        return f'substitute:{step}', f"予定の将軍がいないので、{rec.get('observed_metric', ['別の将軍'])[0]}将軍を代わりに向かわせます。"
    if kind == 'attack_observed':
        return (f"attack:{rec.get('castle')}:{rec.get('general')}",
                f"{rec['general']}将軍が{rec['castle']}城に乗り込みました。")
    if kind == 'defense_observed':
        return f"defense:{rec.get('castle')}", f"{rec['castle']}城が攻め込まれています。迎え撃ちます。"
    if kind == 'battle_start':
        ally_hp, enemy_hp = rec.get('ally_hp'), rec.get('enemy_hp')
        key = f"battle:{rec.get('enemy')}:{rec.get('ally')}"
        if any(type(hp) is not int or hp < 0 for hp in (ally_hp, enemy_hp)):
            return key, None  # candidate writer records 状況判定保留
        plan = rec.get('planned_cards') or []
        if plan:
            tail = ('体力は互角です。' if ally_hp == enemy_hp else '')
            prefix = ('再攻撃の作戦として' if rec.get('strategy_variant') == 'retry_with_opening_cards'
                      else 'チャートの予定どおり')
            tail += f"{prefix}{'、'.join(plan)}を使う予定です。"
        elif ally_hp < enemy_hp:
            tail = '体力では負けているので、苦しい白兵戦になりそうです。'
        elif ally_hp > enemy_hp:
            tail = '体力で上回っています。状況を見て使える手を選びます。'
        else:
            tail = '体力は互角です。状況を見て使える手を選びます。'
        return (key,
                f"{rec['ally']}対{rec['enemy']}、体力は{ally_hp}対{enemy_hp}。{tail}")
    if kind == 'battle_survival':
        hp = (rec.get('observed_metric') or {}).get('ally_hp')
        return f'survival:{step}', f'体力が{hp}まで減ったので、切り札とたまごを確認して使える手を選びます。'
    if kind == 'battle_card':
        reason = rec.get('reason') or ''
        if '開幕' in reason:
            why = f"開幕に{rec['card']}を使います。"
        elif 'ぶつかり' in reason:
            why = f"一度ぶつかって敵の体力が{rec['enemy_hp']}になったので、{rec['card']}を使います。"
        else:
            why = f"敵の{rec['enemy']}の体力が{rec['enemy_hp']}まで下がったので、{rec['card']}を使います。"
        return f"card:{rec.get('card')}:{rec.get('enemy')}", why
    if kind == 'battle_result':
        outcome = rec.get('outcome')
        ally, enemy = rec.get('ally'), rec.get('enemy')
        if outcome == 'win':
            tail = f"{rec['castle']}城を確保しました。" if rec.get('castle') and rec.get('side') == 'attack' else ''
            return f'result:{ally}:{enemy}:win', f'{ally}将軍が{enemy}に勝ちました。{tail}'
        if outcome == 'loss':
            return f'result:{ally}:{enemy}:loss', f'{ally}将軍は{enemy}に敗れました。'
        return 'result:held', None
    if kind == 'month_plan':
        plan = rec.get('plan') or {}
        cards = '、'.join(f'{n}{q}個' for n, q in plan.get('cards', []))
        soldiers = plan.get('soldiers')
        text = f"{rec['month'].replace('-', '年')}月、所持金は{rec['gold']}ゴールドです。"
        deviation = rec.get('deviation_reason') or ''
        shortfall = re.search(r'チャート想定(\d+)G', deviation)
        if deviation == 'chart_month_uncovered':
            text += '今月はチャートの購入予定がないので、'
        elif shortfall:
            text += f"チャート想定の{shortfall.group(1)}ゴールドに届かないので、優先順で買える分を買います。"
        elif deviation:
            text += 'チャートの予定と条件が異なるため、買える範囲で進めます。'
        text += f'購入予定は{cards}です。' if cards else '切り札の購入はありません。'
        if type(soldiers) is int and soldiers > 0:
            text += f'兵士を{soldiers}人補充します。'
        return f"plan:{rec['month']}", text
    if kind == 'buy':
        return f"buy:{rec.get('card')}:{rec.get('qty')}", f"{rec['card']}を{rec['qty']}個買いました。"
    if kind == 'soldier_refill':
        return 'soldiers', f"兵士を{rec['qty']}人補充します。"
    if kind == 'poor_harvest':
        return 'harvest', '凶作です。チャートならリセットする場面ですが、このまま進めます。'
    if kind == 'prompt' and rec.get('strategy_variant') == 'accept_duel':
        return 'duel', '一騎打ちの申し出を受けた。青ゲージを消費して勝負します。'
    if kind == 'egg_battle':
        if rec.get('strategy_variant') == 'egg_battle_use_egg':
            return 'egg', '敵が卵で召喚獣を呼び出しました。こちらもたまごで応戦します。'
        return 'egg', '敵が卵で召喚獣を呼び出しました。コマンドはこうげきで応戦します。'
    if kind == 'independent_menu':
        action = (rec.get('strategy_variant') or '').removeprefix('independent_')
        if action == 'use_egg':
            return 'independent_menu', 'チャートに指示がないので、状況判断でたまごを使います。'
        if rec.get('source_pattern') == '③' or rec.get('observed_metric', {}).get('source_pattern') == '③':
            return 'independent_menu', 'チャートに指示がないので、原典の戦術③どおり白兵を続けます。'
        return 'independent_menu', 'チャートに指示がないので、状況判断で白兵を続けます。'
    if kind == 'gift':
        return 'gift', f"おねだりです。チャートならリセットですが、一番安い{rec['item']}を{rec['price']}ゴールドで買って済ませます。"
    if kind == 'prompt' and rec.get('strategy_variant') == 'decline_extra_gift':
        return 'gift_extra', '追加のおねだりは、月一の買い物に備えて断ります。'
    if kind == 'situation_held':
        return 'held', None
    if kind == 'chart_adjust_request':
        reason = rec.get('off_chart_reason') or ''
        why = {'orders_locked': '次の指示が未達で出撃待ち',
               'orders_exhausted': '出撃可能な指示が尽きた',
               'chart_unavailable': '基準チャートが使えない'}.get(reason, '出撃可能な指示がない')
        return 'chart_adjust_request', f'{why}ので、AIに新しい攻略チャートを作らせています。'
    if kind == 'chart_adjust_applied':
        digest = rec.get('order_digest') or []
        heads = [f"{d['general']}→{d['target']}" for d in digest
                 if isinstance(d, dict) and d.get('general') and d.get('target')]
        if not heads:
            steps = rec.get('local_steps') or rec.get('steps') or []
            return 'chart_adjust_applied', f'AIの調整チャートを採用。指示は{len(steps)}手です。'
        if len(heads) > 3:
            return 'chart_adjust_applied', f"AIの調整チャートを採用。{'、'.join(heads[:3])}など{len(heads)}手。"
        return 'chart_adjust_applied', f"AIの調整チャートを採用。{'、'.join(heads)}。"
    if kind == 'chart_interim_order':
        conf = rec.get('confidence')
        conf_s = f'確信度{conf:.2f}。' if type(conf) in (int, float) else ''
        stem, polite, plain = {
            'retake': (f"奪われた{rec['target']}を白兵で奪い", '返します', '返す'),
            'move': (f"{rec.get('source')}から空の{rec['target']}へ", '移ります', '移る'),
        }.get(rec.get('purpose'), (f"{rec['target']}を白兵で", '攻めます', '攻める'))
        if rec.get('strategy_variant') == 'chart_interim_fallback':
            return (f"jev_interim:{rec.get('target')}",
                    f"調整チャートを待つ間、{rec['general']}が{stem}{polite}。")
        return (f"jev_interim:{rec.get('target')}",
                f"JEVは調整チャートを待つ間、{rec['general']}が{stem}{plain}と判断しました。{conf_s}")
    if kind == 'castle_lost_observed':
        return f"lost:{rec.get('castle')}", f"{rec.get('castle')}が敵の城になっているのを確認しました。"
    if kind == 'chart_interim_hold':
        # Only when there is literally nothing left to retake, attack or staff.
        return 'jev_interim_hold', '動かせる将軍と攻め先が無いため、調整チャートを待っています。'
    return f'other:{kind}', None


# Decisions worth speaking. Menu steps and waits are logged, not narrated.
SPOKEN = frozenset({
    'name_confirm', 'order_start', 'order_retry', 'order_source_changed', 'attack_observed',
    'order_launched_unconfirmed', 'defense_observed', 'battle_start', 'battle_survival', 'battle_card', 'battle_result', 'month_plan', 'poor_harvest',
    'prompt', 'situation_held', 'gift', 'egg_battle', 'order_substitute', 'independent_menu',
    'chart_adjust_request', 'chart_adjust_applied', 'chart_interim_order', 'chart_interim_hold',
    'castle_lost_observed'})


# ---------------------------------------------------------------- game over
_MAX_DECISIONS = 200000


def _decisions(runtime_dir):
    for name in ('hanjuku_decisions.jsonl', 'hanjuku_decisions.previous.jsonl'):
        path = Path(runtime_dir) / name
        if not path.exists() or path.is_symlink():
            continue
        with path.open(encoding='utf-8', errors='ignore') as stream:
            for index, line in enumerate(stream):
                if index >= _MAX_DECISIONS:
                    break
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get('event') == 'decision':
                    yield item


def summarize_recap(runtime_dir, run_state) -> tuple[str, str]:
    """A grounded game-over recap for the 实況 (owner rule 2026-09-28).

    Looks back over this run's own decision log and run state: the story
    (how far the chapters went) and the actions (sorties, battles, captures,
    dismissals). Every number is read from the run's records; nothing is
    invented, per the module contract.
    """
    chapter, captured, launches, discharged, months = 1, set(), 0, 0, []
    for rec in _decisions(runtime_dir):
        ch = rec.get('chapter')
        if type(ch) is int and 1 <= ch <= 99:
            chapter = max(chapter, ch)
        kind = rec.get('decision')
        if kind == 'order_launched':
            launches += 1
        elif kind == 'discharge_general':
            discharged += 1
        elif kind == 'month_seen':
            key = rec.get('month')
            if isinstance(key, str) and '-' in key:
                year, month = key.split('-', 1)
                if year.isdigit() and month.isdigit():
                    months.append((int(year), int(month)))
        elif kind in ('castle_owned_observed', 'world_map_owners'):
            events = rec.get('resulting_event')
            if isinstance(events, str):
                events = [events]
            for event in events or ():
                if isinstance(event, str) and event.startswith('captured:'):
                    captured.add(event.split(':', 1)[1])
    try:
        battles = int((run_state or {}).get('battles_finished') or 0)
    except (TypeError, ValueError):
        battles = 0
    label = None
    if months:
        year, month = max(months)
        label = f'{year}年{month}月'
    text = f'ゲームオーバー。第{chapter}章まで進み、{len(captured)}城を獲得、{launches}回出撃と{battles}回戦闘を重ね、'
    text += f'{label}まで戦いました' if label else '進軍を続けました'
    if discharged:
        text += f'（将軍の解雇{discharged}回）'
    text += '。今回の挑戦はここまでです。'
    return 'game_over_recap', text
