"""Situation-grounded Japanese commentary for Hanjuku decision records.

Deterministic: each line is composed only from fields the policy recorded
from the screen (names, castles, HP, gold, cards, chart step and reason).
When a record carries no verified situation the line is ``None`` and the
candidate is logged as 状況判定保留; nothing is invented to fill silence.
No model or network is used. Delivery is owned by ``hanjuku_narration``.
"""
from __future__ import annotations

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
                f"{label}です。{rec['general']}将軍を{rec['source']}から{rec['target']}城へ向かわせます。"
                f"持たせる切り札は{_cards(rec.get('cards'))}です。")
    if kind == 'order_launched':
        return f'launch:{step}', f"{rec['general']}将軍、{rec['target']}城へ出撃しました。"
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
            tail = '体力で上回っているので、切り札を温存して白兵で押します。'
        else:
            tail = '体力は互角です。切り札を温存して白兵で戦います。'
        return (key,
                f"{rec['ally']}対{rec['enemy']}、体力は{ally_hp}対{enemy_hp}。{tail}")
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
        cards = '、'.join(f'{n}{q}個' for n, q in plan.get('cards', [])) or 'なし'
        text = f"{rec['month'].replace('-', '年')}月、所持金は{rec['gold']}ゴールドです。"
        if rec.get('deviation_reason'):
            text += 'チャート想定の214ゴールドに届かないので、ボス用のクースカンとノリウツールを優先して買います。'
        text += f'購入予定は{cards}です。'
        return f"plan:{rec['month']}", text
    if kind == 'buy':
        return f"buy:{rec.get('card')}:{rec.get('qty')}", f"{rec['card']}を{rec['qty']}個買いました。"
    if kind == 'soldier_refill':
        return 'soldiers', f"兵士を{rec['qty']}人補充します。"
    if kind == 'poor_harvest':
        return 'harvest', '凶作です。チャートならリセットする場面ですが、このまま進めます。'
    if kind == 'prompt' and rec.get('strategy_variant') == 'decline_duel':
        return 'duel', '一騎打ちの申し出は、主人公を守るために断ります。'
    if kind == 'egg_battle':
        return 'egg', '敵が卵で召喚獣を呼び出しました。コマンドはこうげきで応戦します。'
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
        if rec.get('strategy_variant') == 'chart_interim_fallback':
            return (f"jev_interim:{rec.get('target')}",
                    f"調整チャートを待つ間、{rec['general']}が{rec['target']}を白兵で再攻撃します。")
        return (f"jev_interim:{rec.get('target')}",
                f"JEVは調整チャートを待つ間、{rec['general']}が{rec['target']}を白兵で再攻撃すると判断しました。{conf_s}")
    if kind == 'chart_interim_hold':
        # Only when there is literally nothing left to attack.
        return 'jev_interim_hold', '再攻撃できる城が無いため、調整チャートを待っています。'
    return f'other:{kind}', None


# Decisions worth speaking. Menu steps and waits are logged, not narrated.
SPOKEN = frozenset({
    'name_confirm', 'order_start', 'order_retry', 'order_source_changed', 'attack_observed',
    'defense_observed', 'battle_start', 'battle_card', 'battle_result', 'month_plan', 'poor_harvest',
    'prompt', 'situation_held', 'gift', 'egg_battle', 'order_substitute',
    'chart_adjust_request', 'chart_adjust_applied', 'chart_interim_order', 'chart_interim_hold'})
