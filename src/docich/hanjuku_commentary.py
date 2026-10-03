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

COMMENTARY_VERSION = "hanjuku-commentary-v4-explicit-evidence"

_STEP_LABEL = {
    '1-A1': '主人公の初手', '1-V1': 'ヴィーナスの初手', '1-C1': 'ココットの初手',
    '1-A2': '主人公の二手目', '1-V2': 'ヴィーナスの二手目', '1-C2': 'ココットの二手目',
    '1-A3': '主人公の合流', '1-B1': 'ボス戦',
}


def _cards(cards):
    return '、'.join(cards) if cards else '切り札なし'


# These records are emitted while choosing actions, before input delivery.
# They must remain intentions, never receipts of a completed game operation.
PLANNED = frozenset({
    'name_confirm', 'order_start', 'order_retry', 'order_source_changed',
    'order_substitute', 'battle_survival', 'battle_card', 'month_plan', 'buy',
    'soldier_refill', 'prompt', 'gift', 'egg_battle', 'independent_menu',
    'chart_adjust_request', 'chart_interim_order', 'chart_interim_hold',
})


def evidence_kind(record):
    return 'plan' if record.get('decision') in PLANNED else 'observation'


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
            return f'retry:{step}', 'ボス戦に敗れたので、待機将軍とチャートの切り札を確認して再攻撃を準備します。'
        return (f'retry:{step}',
                'チャートどおりの白兵では負けたので、今度は開幕にイッテツーンを2枚使う作戦で同じ城へ攻め直します。')
    if kind == 'order_source_changed':
        return f'source:{step}', '出撃予定の城に将軍が見当たらないので、本城から出し直します。'
    if kind == 'hero_priority_selected':
        return f'hero_priority:{step}', f"主人公の危険を避けるため、実一覧で確認した{rec['general']}将軍を先発にします。"
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
                f"{rec['ally']}対{rec['enemy']}、戦闘開始時の体力は{ally_hp}対{enemy_hp}。{tail}")
    if kind == 'battle_survival':
        hp = (rec.get('observed_metric') or {}).get('ally_hp')
        return f'survival:{step}', f'体力が{hp}まで減ったので、切り札とたまごを確認して使える手を選びます。'
    if kind == 'battle_card':
        reason = rec.get('reason') or ''
        if '開幕' in reason:
            why = f"開幕に{rec['card']}を使う予定です。"
        elif 'ぶつかり' in reason:
            why = f"一度ぶつかって敵の体力が{rec['enemy_hp']}になったので、{rec['card']}を使う予定です。"
        else:
            why = f"敵の{rec['enemy']}の体力が{rec['enemy_hp']}まで下がったので、{rec['card']}を使う予定です。"
        return f"card:{rec.get('card')}:{rec.get('enemy')}", why
    if kind == 'battle_egg_dropped':
        return (f"egg_dropped:{rec.get('enemy')}:{rec.get('card')}",
                f"{rec['enemy']}が{rec['card']}で卵を落としたので、"
                f"以後の召喚は使えません。ぶつかり合いで前へ押します。")
    if kind == 'battle_result':
        outcome = rec.get('outcome')
        ally, enemy = rec.get('ally'), rec.get('enemy')
        if outcome == 'win':
            # HP battle outcome is not a receipt for final castle ownership.
            return f'result:{ally}:{enemy}:win', f'{ally}将軍が{enemy}に勝ちました。'
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
        return f"buy:{rec.get('card')}:{rec.get('qty')}", f"{rec['card']}を{rec['qty']}個購入する予定です。決定操作を進めます。"
    if kind == 'soldier_refill':
        return 'soldiers', f"兵士を{rec['qty']}人補充します。"
    if kind == 'poor_harvest':
        return 'harvest', '凶作です。チャートならリセットする場面ですが、このまま進めます。'
    if kind == 'prompt' and rec.get('strategy_variant') == 'accept_duel':
        return 'duel', '一騎打ちを受ける操作を選びます。成立後は青ゲージを使う予定です。'
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
    if kind == 'prompt' and (rec.get('strategy_variant') or '').startswith('summer_bonus_'):
        variant = rec['strategy_variant']
        metric = rec.get('observed_metric') or {}
        if variant == 'summer_bonus_no_cursor':
            return 'summer_bonus', '夏バテのイベントです。選択位置が読めないため、上へ動かして再確認します。'
        option = '兵士半減のバカンス' if variant == 'summer_bonus_vacation' else 'お金半減のボーナス'
        if metric.get('selection') == 'random':
            return 'summer_bonus', f'夏バテのイベントです。{option}を選びます。'
        return ('summer_bonus',
                f'夏バテのイベントです。兵士{metric.get("soldiers")}人と所持金{metric.get("gold")}Gを比べて、'
                f'損失の小さい{option}を選びます。')
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
    if kind == 'castle_owned_observed':
        return f"owned:{rec.get('castle')}", f"{rec.get('castle')}が自軍の城になっているのを確認しました。"
    if kind == 'castle_lost_observed':
        return f"lost:{rec.get('castle')}", f"{rec.get('castle')}が敵の城になっているのを確認しました。"
    if kind == 'chart_interim_hold':
        # Only when there is literally nothing left to retake, attack or staff.
        return 'jev_interim_hold', '動かせる将軍と攻め先が無いため、調整チャートを待っています。'
    return f'other:{kind}', None


# Decisions worth speaking. Menu steps and waits are logged, not narrated.
SPOKEN = frozenset({
    'name_confirm', 'order_start', 'order_retry', 'order_source_changed', 'attack_observed',
    'order_launched_unconfirmed', 'defense_observed', 'battle_start', 'battle_survival', 'battle_card',
    'battle_egg_dropped', 'battle_result', 'month_plan', 'poor_harvest',
    'prompt', 'situation_held', 'gift', 'egg_battle', 'order_substitute', 'independent_menu',
    'chart_adjust_request', 'chart_adjust_applied', 'chart_interim_order', 'chart_interim_hold',
    'castle_lost_observed', 'castle_owned_observed'})


# ---------------------------------------------------------------- game over
_MAX_DECISIONS = 200000


def _decisions(runtime_dir):
    # append_log rotates the active file into .previous before opening a new
    # active file. Read in that same order so a terminal story does not put
    # the latest run segment before its earlier events.
    for name in ('hanjuku_decisions.previous.jsonl', 'hanjuku_decisions.jsonl'):
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


def _story_label(value):
    """A short, single-line label copied from a decision record."""
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split())
    if not value or len(value) > 48 or any(ord(char) < 32 for char in value):
        return None
    return value


def _battle_result_sentence(rec):
    result = {'win': '勝利', 'loss': '敗北'}.get(rec.get('outcome'))
    enemy = _story_label(rec.get('enemy'))
    castle = _story_label(rec.get('castle'))
    if result is None:
        return None
    opponent = f'{enemy}との' if enemy else ''
    place = f'{castle}城で' if castle else ''
    # A battle win is not evidence that a castle changed hands. Ownership is
    # described separately from this battle-only record.
    return f'{place}{opponent}戦闘は{result}と記録されています。'


def _ownership_events(rec):
    kind = rec.get('decision')
    events = rec.get('resulting_event')
    if isinstance(events, str):
        events = [events]
    if not isinstance(events, list):
        events = []
    if kind == 'castle_owned_observed' and not events:
        events = [f"captured:{rec.get('castle', '')}"]
    elif kind == 'castle_lost_observed' and not events:
        events = [f"lost:{rec.get('castle', '')}"]
    for event in events:
        if not isinstance(event, str):
            continue
        event_type, sep, castle = event.partition(':')
        label = _story_label(castle)
        if not sep or not label or event_type not in {'captured', 'lost'}:
            continue
        if kind == 'castle_lost_observed' or event_type == 'lost':
            yield ('lost', label)
        else:
            yield ('captured', label)


def recap_body(runtime_dir, run_state) -> str:
    """A short chronological story grounded in this run's own records.

    The first recorded order is stated as a plan; only ``order_launched`` is
    stated as an actual sortie. Battle outcomes and observed castle ownership
    remain separate facts. The same deterministic text feeds chat and voice.
    """
    records = list(_decisions(runtime_dir))
    if not records:
        return '今回の挑戦では、確認できる出来事の記録が残っていません。'

    chapters = [rec['chapter'] for rec in records
                if type(rec.get('chapter')) is int and 1 <= rec['chapter'] <= 99]
    months = []
    for rec in records:
        key = rec.get('month') if rec.get('decision') == 'month_seen' else None
        if isinstance(key, str) and re.fullmatch(r'[1-9][0-9]?-(?:[1-9]|1[0-2])', key):
            year, month = key.split('-', 1)
            months.append((int(year), int(month)))

    chapter = max(chapters) if chapters else None
    month_label = None
    if months:
        year, month = max(months)
        month_label = f'{year}年{month}月'
    launches = sum(rec.get('decision') == 'order_launched' for rec in records)
    discharged = sum(rec.get('decision') == 'discharge_general' for rec in records)
    captured = set()
    for rec in records:
        for event_kind, castle in _ownership_events(rec):
            if event_kind == 'captured':
                captured.add(castle)

    story = []
    plan = next((rec for rec in records if rec.get('decision') == 'order_start'
                 and _story_label(rec.get('general')) and _story_label(rec.get('target'))), None)
    if plan:
        story.append(
            f"作戦記録には、{_story_label(plan.get('general'))}将軍を"
            f"{_story_label(plan.get('target'))}城へ向かわせる予定が残っています。"
        )

    events = []
    for index, rec in enumerate(records):
        kind = rec.get('decision')
        if kind == 'order_launched':
            general = _story_label(rec.get('general'))
            target = _story_label(rec.get('target'))
            if general and target:
                events.append((index, f'{general}将軍の{target}城への出撃が確認されました。'))
        elif kind == 'battle_result':
            sentence = _battle_result_sentence(rec)
            if sentence:
                events.append((index, sentence))
        elif kind in {'castle_owned_observed', 'castle_lost_observed', 'world_map_owners'}:
            for event_kind, castle in _ownership_events(rec):
                if event_kind == 'captured':
                    sentence = f'{castle}城を自軍が保持していることを観測で確認しました。'
                else:
                    sentence = f'{castle}城の失陥が観測で確認されました。'
                events.append((index, sentence))

    # Retain the first actual sortie plus the latest battle and ownership
    # observations. Fill any remaining slot with the latest recorded event.
    # This keeps the story chronological and gives it a concrete turning
    # point without reading every menu action aloud.
    if len(events) > 3:
        selected = []
        first_sortie = next((event for event in events
                             if '出撃が確認されました。' in event[1]), None)
        latest_battle = next((event for event in reversed(events)
                              if '戦闘は' in event[1]), None)
        latest_ownership = next((event for event in reversed(events)
                                 if '城を自軍が保持' in event[1]
                                 or '城の失陥' in event[1]), None)
        for event in (first_sortie, latest_battle, latest_ownership):
            if event is not None and event not in selected:
                selected.append(event)
        if len(selected) < 3:
            for event in reversed(events):
                if event not in selected:
                    selected.append(event)
                if len(selected) == 3:
                    break
        events = sorted(selected, key=lambda event: event[0])
    story.extend(sentence for _index, sentence in events)

    try:
        battles = (run_state or {}).get('battles_finished')
    except AttributeError:
        battles = None
    if type(battles) is not int or battles < 0:
        battles = None
    progress = []
    if chapter is not None:
        progress.append(f'第{chapter}章まで')
    if month_label:
        progress.append(f'{month_label}まで')
    if launches:
        progress.append(f'出撃{launches}回')
    if battles is not None:
        progress.append(f'戦闘{battles}回')
    if captured:
        progress.append(f'{len(captured)}城の獲得記録')
    if discharged:
        progress.append(f'将軍の解雇{discharged}回')
    if progress:
        story.append('記録では' + '、'.join(progress) + 'の経過が確認できます。')
    elif not story:
        return '今回の挑戦では、確認できる出来事の記録が残っていません。'
    return ''.join(story)


def summarize_recap(runtime_dir, run_state) -> tuple[str, str]:
    """A grounded game-over recap for the 实況 (owner rule 2026-09-28).

    The numbers all come from ``recap_body`` (this run's own decision log and
    run state); nothing is invented, per the module contract.
    """
    return ('game_over_recap',
            f'{recap_body(runtime_dir, run_state)}タイトル画面への復帰を確認し、今回の挑戦はここまでです。')
