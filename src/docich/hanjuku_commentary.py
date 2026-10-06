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

COMMENTARY_VERSION = "hanjuku-commentary-v5-terminal-story"

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


def _battle_story_sentence(rec):
    outcome = rec.get('outcome')
    if outcome not in {'win', 'loss'}:
        return None
    enemy = _story_label(rec.get('enemy'))
    castle = _story_label(rec.get('castle'))
    place = f'{castle}城では' if castle else ''
    opponent = f'{enemy}との戦闘に' if enemy else '戦闘に'
    result = '勝っています' if outcome == 'win' else '敗れています'
    return f'{place}{opponent}{result}。'


def recap_body(runtime_dir, run_state) -> str:
    """Turn this run's evidence into a short post-game story.

    The recap deliberately separates plans, confirmed actions, battle results,
    and ownership observations.  It explains the most important contrast in
    the run, labels unsupported causality as unknown, and finishes with a
    concrete next-run focus instead of replaying the event log.
    """
    records = list(_decisions(runtime_dir))
    if not records:
        return (
            '今回の挑戦では、確認できる出来事の記録が残っていません。'
            '次回は終了直前の戦闘結果と城の保有変化を残し、'
            'どこで流れが変わったかを振り返れるようにします。'
        )

    chapters = [
        rec['chapter'] for rec in records
        if type(rec.get('chapter')) is int and 1 <= rec['chapter'] <= 99
    ]
    months = []
    for rec in records:
        key = rec.get('month') if rec.get('decision') == 'month_seen' else None
        if isinstance(key, str) and re.fullmatch(r'[1-9][0-9]?-(?:[1-9]|1[0-2])', key):
            year, month = key.split('-', 1)
            months.append((int(year), int(month)))

    progress = []
    if chapters:
        progress.append(f'第{max(chapters)}章')
    if months:
        year, month = max(months)
        progress.append(f'{year}年{month}月')

    try:
        terminal_reason = (run_state or {}).get('terminal_reason')
    except AttributeError:
        terminal_reason = None
    lead = {
        'game_over': '今回はゲームオーバーとなり、',
        'screen_stalled': '今回は画面停止で終了し、',
        'manual_saved_stop': '今回はセーブして終了し、',
        'manual_forced_stop': '今回は強制終了となり、',
    }.get(terminal_reason, '今回の挑戦は、')
    if progress:
        story = [f"{lead}記録上は{'・'.join(progress)}まで進みました。"]
    elif terminal_reason in {'game_over', 'screen_stalled', 'manual_saved_stop', 'manual_forced_stop'}:
        story = [f'{lead.rstrip("、")}ました。確認できた戦況から振り返ります。']
    else:
        story = ['今回の挑戦を、確認できた戦況から振り返ります。']

    plans = []
    launches = []
    battles = []
    ownership = []
    for index, rec in enumerate(records):
        kind = rec.get('decision')
        if kind == 'order_start':
            general = _story_label(rec.get('general'))
            target = _story_label(rec.get('target'))
            if general and target:
                plans.append((index, general, target))
        elif kind == 'order_launched':
            general = _story_label(rec.get('general'))
            target = _story_label(rec.get('target'))
            if general and target:
                launches.append((index, general, target))
        elif kind == 'battle_result' and rec.get('outcome') in {'win', 'loss'}:
            battles.append((index, rec))
        if kind in {'castle_owned_observed', 'castle_lost_observed', 'world_map_owners'}:
            for event_kind, castle in _ownership_events(rec):
                ownership.append((index, event_kind, castle))

    # Explain the intended attack and what was actually confirmed. Prefer a
    # launch to the planned target, so a substitute general becomes a useful
    # part of the story rather than an unrelated first/last-event sample.
    plan = next(
        (
            candidate for candidate in reversed(plans)
            if any(
                launch[0] >= candidate[0] and launch[2] == candidate[2]
                for launch in launches
            )
        ),
        plans[-1] if plans else None,
    )
    launch = None
    if plan is not None:
        launch = next(
            (item for item in launches if item[0] >= plan[0] and item[2] == plan[2]),
            None,
        )
    if launch is None and launches:
        launch = launches[0]

    if plan and launch and plan[2] == launch[2]:
        if plan[1] == launch[1]:
            story.append(
                f'作戦では{plan[2]}城への進出を狙い、'
                f'{launch[1]}将軍の出撃までは実行できました。'
            )
        else:
            story.append(
                f'作戦では{plan[1]}将軍を{plan[2]}城へ向かわせる予定でしたが、'
                f'実際には{launch[1]}将軍が同じ{launch[2]}城へ出撃しました。'
            )
    elif launch:
        story.append(
            f'{launch[1]}将軍が{launch[2]}城へ出撃したところまでは確認できました。'
        )
    elif plan:
        story.append(
            f'作戦では{plan[1]}将軍を{plan[2]}城へ向かわせる予定でしたが、'
            'この記録からは出撃完了までは確認できません。'
        )

    latest_state = {}
    for event in ownership:
        latest_state[event[2]] = event
    unresolved_losses = [
        event for event in ownership
        if event[1] == 'lost' and latest_state.get(event[2]) == event
    ]
    recovered_losses = [
        event for event in ownership
        if event[1] == 'lost'
        and latest_state.get(event[2], event)[0] > event[0]
        and latest_state.get(event[2], event)[1] == 'captured'
    ]
    latest_win_entry = next(
        (item for item in reversed(battles) if item[1].get('outcome') == 'win'),
        None,
    )
    latest_win = latest_win_entry[1] if latest_win_entry else None
    latest_battle_loss = next(
        (rec for _i, rec in reversed(battles) if rec.get('outcome') == 'loss'),
        None,
    )

    if unresolved_losses:
        latest_loss = unresolved_losses[-1]
        lost_castle = latest_loss[2]
        win_sentence = _battle_story_sentence(latest_win) if latest_win else None
        if win_sentence and latest_win_entry and latest_win_entry[0] > latest_loss[0]:
            story.append(
                f'{lost_castle}城の失陥が確認された一方、その後、{win_sentence}'
                'それでも全体では拠点を失ったまま終えたことが今回の反省点です。'
            )
        elif win_sentence:
            story.append(
                f'{win_sentence}その一方で{lost_castle}城の失陥も確認されており、'
                '局地戦で勝てても、全体では拠点を維持できなかったことが'
                '今回の反省点です。'
            )
        else:
            story.append(
                f'{lost_castle}城を失ったまま終えており、'
                '攻めるだけでなく拠点を維持する判断に課題が残りました。'
            )
        if launches:
            story.append(
                f'ただし、{lost_castle}城の失陥が出撃判断の直接の結果だったかまでは'
                '記録から断定できません。次回は出撃の前後で守備配置と兵力を確認し、'
                '攻撃後も城を維持できる条件を優先します。'
            )
        else:
            story.append(
                '失陥の直接原因までは記録から断定できません。'
                '次回は失陥直前の守備配置・兵力・敵の接近状況を確認し、'
                'どの判断で守りが崩れたのかを絞り込みます。'
            )
    elif recovered_losses:
        lost_castle = recovered_losses[-1][2]
        story.append(
            f'途中で{lost_castle}城を失いましたが、その後の観測では奪回できています。'
            '立て直せた点は成果ですが、一度守りを崩した場面は次回の改善材料です。'
        )
        story.append(
            '次回はその失陥直前の守備配置と出撃判断を見直し、'
            '奪回を前提にしない安定した進軍を狙います。'
        )
    elif latest_battle_loss is not None:
        loss_sentence = _battle_story_sentence(latest_battle_loss)
        if loss_sentence:
            story.append(
                f'{loss_sentence}今回確認できる明確なつまずきはこの戦闘です。'
            )
        story.append(
            '次回はその戦闘の開始時体力と、使える切り札・たまごの選択を見直し、'
            '同じ条件で無理に押し切らない判断を優先します。'
        )
    else:
        if latest_win is not None:
            win_sentence = _battle_story_sentence(latest_win)
            if win_sentence:
                story.append(f'{win_sentence}戦闘で前進できた場面は確認できています。')
        if terminal_reason == 'game_over':
            story.append(
                'ただし、ゲームオーバーへ至った直接の原因はこの記録だけでは特定できません。'
                '次回は終了直前の戦闘結果と城の保有変化を突き合わせ、'
                '戦闘・守備・操作のどこで流れが切れたかを確認します。'
            )
        else:
            story.append(
                '大きな失陥や敗戦はこの記録からは確認できません。'
                '次回も同じ攻勢を続けつつ、終了直前の盤面変化を重点的に残します。'
            )

    return ''.join(story)


def summarize_recap(runtime_dir, run_state) -> tuple[str, str]:
    """A grounded game-over recap shared by voice and chat.

    The body comes only from this run's own decision log and run state; no
    unsupported cause or event is invented.
    """
    return ('game_over_recap',
            f'{recap_body(runtime_dir, run_state)}タイトル画面への復帰を確認し、今回の挑戦はここまでです。')
