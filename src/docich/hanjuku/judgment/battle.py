"""判断層: tactical battle judgment from the observed battle record.

Card damage gates, egg-drop/egg-threat plans, survival and retreat needs,
melee balance and defender last-resort rules. Every function evaluates the
current battle state and returns a judgment or bookkeeping update; none of
them reads the screen or produces pad input (see ``flows.battle``).
"""
from __future__ import annotations

from dataclasses import asdict

from ...hanjuku_egg_reference import GENERAL_EGGS, enemy_egg_triggers, general_max_hp
from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ...hanjuku_egg_stock import observed_uses
from ..decision_log import _record
from ..os_layer.readers import CARD_NAMES
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS
from ..strategy.kit import CARD_IDS, ENEMY_EGG_CARD_ID_SUM, _deploy_cards, _is_boss_order
from ..strategy.orders import _order_for_step, _sortie_step_for, _tactics


ENTRY_RETURN_KINDS = frozenset({'main_menu', 'castle_info', 'castle_menu',
    'general_list', 'card_select', 'sortie_confirm', 'month_menu', 'shop_list',
    'shop_quantity_prompt', 'shop_quantity', 'shop_exit_confirm', 'discharge_menu'})


def _battle_context(mem, ally):
    """Only a matching attack message establishes a battle location and side."""
    attack = mem.get('attack') or {}
    captured = set(mem.get('captured', []))
    if attack.get('side') == 'defense' or (attack.get('general') and attack.get('general') == ally):
        side = attack.get('side')
        if side == 'attack' and attack.get('castle') in captured:
            side = 'defense'          # a battle at a castle we hold is not a capture attempt
        step = attack.get('step')
        if step is None and side == 'attack':
            step = _sortie_step_for(mem, ally, attack.get('castle'))
        return {'castle': attack.get('castle'), 'side': side, 'step': step,
                'entry_evidence': attack.get('entry_evidence')}
    captured = set(mem.get('captured', []))
    en_route = [step for step, sortie in (mem.get('sorties') or {}).items()
                if sortie.get('general') == ally
                and sortie.get('status') in ('en_route', 'arrived', 'launched_unconfirmed')
                and sortie.get('target') not in captured]
    if len(en_route) == 1:
        return {'castle': None, 'side': None, 'step': en_route[0],
                'context': 'unclassified_location'}
    if en_route:
        return {'castle': None, 'side': None, 'step': None, 'context': 'ambiguous_sortie'}
    for castle, info in (mem.get('launched') or {}).items():
        if info.get('general') == ally and castle not in captured:
            return {'castle': None, 'side': None, 'step': info.get('step'),
                    'context': 'unclassified_location'}
    return {'castle': None, 'side': None, 'step': None, 'context': 'unclassified'}


def _boss_tactics_allowed(mem, cur):
    """Gate carried boss cards on entry text or a measured chapter-boss name.

    A boss dialogue may hide/skip the entry message (g484: Venus vs Queen).
    Reading the boss in the battle panel authorizes its carried tactics, but
    does not establish castle ownership, arrival, or a retry context.
    """
    if cur.get('entry_evidence') == 'measured_boss_entry':
        return True
    chapter = mem.get('chapter')
    boss = chart.BOSSES.get(chapter)
    return bool(boss and cur.get('enemy') == boss
                and _is_boss_order(_order_for_step(mem, cur.get('step')), mem))


def _battle_labels(cur):
    return {'chart_step': cur.get('step'),
            'strategy_variant': cur.get('strategy_variant', 'chart'),
            'deviation_reason': cur.get('deviation_reason')}


def _bind_battle_strategy(mem, cur):
    if 'strategy_variant' in cur:
        return
    step = cur.get('step')
    retry = (mem.get('retry_context') or {}).get(step)
    sortie = (mem.get('order_context') or {}).get(step) or {}
    if sortie.get('actual_general') != cur.get('ally'):
        sortie = {}
    if retry or (mem.get('card_override') or {}).get(step):
        cur['strategy_variant'] = (retry or {}).get('strategy_variant', 'retry_with_opening_cards')
        cur['deviation_reason'] = (retry or {}).get('deviation_reason') or '保存済みの再攻撃用切り札計画を適用（元の敗北詳細は未分類）'
        cur['strategy_expected'] = (retry or {}).get('expected_metric') or {'goal': '敵HP減少と戦闘勝利'}
    elif sortie:
        cur['strategy_variant'] = sortie.get('strategy_variant', 'chart')
        cur['deviation_reason'] = sortie.get('deviation_reason')
        cur['strategy_expected'] = sortie.get('expected_metric')
    else:
        cur['strategy_variant'] = mem.get('variant', 'chart')
        cur['deviation_reason'] = None
        cur['strategy_expected'] = {'goal': 'チャート戦術による戦闘勝利'}


def _migrate_card_evidence(mem):
    """Legacy cards_used mixed selections/missing cards with actual use."""
    for scope in ('stats', 'previous_stats'):
        stats = mem.get(scope)
        if not isinstance(stats, dict) or stats.get('card_evidence_version') == 1:
            continue
        had_previous = 'cards_used' in stats
        previous = stats.get('cards_used')
        stats.update(cards_used=None, cards_confirmed=0, card_evidence_version=1)
        if had_previous:
            _record(mem, 'metric_invalidated', metric='cards_used', metric_scope=scope,
                    observed_metric={'previous_inferred_count': previous if type(previous) is int else None,
                                     'cards_used': None},
                    reason='旧切り札集計は予定・欠品を含むため未分類化。以後は実使用証拠のみ別途加算')
    cur = mem.get('battle')
    if not isinstance(cur, dict):
        return
    _bind_battle_strategy(mem, cur)
    if cur.get('card_evidence_version') == 1:
        return
    legacy = list(cur.get('cards_used') or [])
    cur.update(cards_used=[], cards_selected=[], cards_missing=[],
               cards_unclassified=legacy, card_consumption_complete=False, card_evidence_version=1)
    flow = cur.get('card_flow')
    if flow and flow.get('stage') == 'announce':
        pending = flow.get('card')
        if pending and pending not in legacy:
            cur['cards_unclassified'].append(pending)
        cur['card_flow'] = None
    _record(mem, 'battle_card_evidence_migrated', **_battle_labels(cur),
            observed_metric={'legacy_cards_unclassified': legacy},
            reason='旧戦闘の選択済みリストは実使用証拠でないためafter_cardを解禁しない')


def _card_use_unclassified(mem, cur, reason):
    flow = cur.get('card_flow') or {}
    card = flow.get('card')
    selected = (flow.get('selection_planned') is True
                or (flow.get('stage') == 'announce'
                    and card in cur.get('cards_selected', [])))
    if not selected:
        # g496: the B/menu opening expired before any list selection, but the
        # old path consumed an イッテツーン and counted it for after_card.
        # Preserve the carried kit and retry only this tactic, boundedly.
        tid = flow.get('tactic_id')
        done = cur.get('tactics_done', [])
        if tid is None and card:
            matches = [item for item in done if item.endswith(':' + card)]
            if len(matches) == 1:
                tid = matches[0]  # a flow opened by the previous bot version
        failures = cur.setdefault('card_open_failures', {})
        count = int(failures.get(tid, 0)) + 1 if tid else CARD_OPEN_ATTEMPTS
        if tid:
            failures[tid] = count
            if count < CARD_OPEN_ATTEMPTS and tid in done:
                done.remove(tid)
        _record(mem, 'battle_card_open_unclassified', **_battle_labels(cur), card=card,
                expected_metric='実メニューと札一覧の選択',
                observed_metric={'selection_planned': False, 'attempts': count,
                                 'retry_allowed': bool(tid and count < CARD_OPEN_ATTEMPTS)},
                reason='札の選択前に操作が戻ったため携行札を差し引かず、有限回だけ再試行')
        cur['card_flow'] = None
        return
    if card:
        cur.setdefault('cards_unclassified', []).append(card)
        # A selected card with no calibrated receipt still leaves the kit for
        # this sortie: re-planning it would open an empty list next battle.
        step = cur.get('step')
        if step:
            spent = mem.setdefault('kit_spent', {}).setdefault(step, [])
            spent.append(card)
    cur['card_consumption_complete'] = False
    if not cur.get('deviation_reason'):
        cur['strategy_variant'] = 'card_use_unclassified'
        cur['deviation_reason'] = reason
    _record(mem, 'battle_card_unclassified', **_battle_labels(cur), card=card,
            expected_metric='選択した切り札の実使用告知',
            observed_metric={'confirmation': 'unclassified'}, reason=reason)
    cur['card_flow'] = None


def reset_battle_controls(mem):
    mem['egg_battle'] = False
    mem.pop('battle_menu_pending_ticks', None)
    for key in ('egg_action', 'egg_key', 'egg_menu_stage', 'egg_battle_row_dead',
                'egg_retreat_tried', 'egg_retreat_flow', 'egg_choice', 'egg_row_dead',
                'indep_menu', 'indep_menu_key', 'indep_menu_action',
                'monster_menu_key', 'monster_menu_cursor', 'monster_menu_hold',
                'monster_menu_choice', 'monster_menu_choice_key', 'monster_menu_choice_scope', 'monster_menu_cursor_recheck', 'monster_panel',
                'rescue_reentry_count', 'rescue_rearm_count'):
        mem.pop(key, None)


def _strong_cards_carried(mem, cur):
    """この戦闘で実際に携行している強い切り札(消費・破棄済みは除く)。

    計画は携行実績ではない。出撃確認の読めた札一覧だけを正本にし、
    この出撃で消費済みのコピーを差し引いてから戦術にする。
    """
    step = cur.get('step')
    from ..strategy.execution import bound_fields, execution_claimed
    from ..strategy.orders import _order_for_step
    order = _order_for_step(mem, step)
    bound = bound_fields(order, mem)
    if bound is not None:
        if cur.get('ally') != bound['general']:
            return []
        kit = bound['roles']['strong']
        carried = list(bound['cards'])
        for card in (mem.get('kit_spent') or {}).get(step) or ():
            if card in carried:
                carried.remove(card)
        return [kit] if kit is not None and kit in carried else []
    if execution_claimed(mem, step):
        return []
    kit = (mem.get('strong_card_kit') or {}).get(step)
    if not kit or not step:
        return []
    context = (mem.get('order_context') or {}).get(step) or {}
    observed = (context.get('observed_metric') or {}).get('cards')
    if not isinstance(observed, list):
        return []
    carried = list(observed)
    for card in (mem.get('kit_spent') or {}).get(step) or ():
        if card in carried:
            carried.remove(card)
    return [kit] if kit in carried else []


def _strong_enemy(mem, cur):
    """この敵が「強い将軍」か (gcgx shogun.html の戦闘・最大HPを主判定)。

    返り値は (verdict, evidence)。ボスは後期ボスが表 (SFC ID 0..127) に載らない
    ため、表の値に関わらず強い将軍とみなす。判定不能 (表に無い) は False に
    正規化し、積極利用は True の時だけ許可する。
    """
    enemy, ally = cur.get('enemy'), cur.get('ally')
    if ally == NAME:
        ally = 'しゅじんこう'
    verdict, evidence = reference.strong_general(enemy, ally)
    if enemy and enemy in chart.BOSSES.values():
        return True, {**evidence, 'rule': 'boss'}
    return verdict is True, evidence


def _strong_card_tactics(mem, cur, enemy):
    """携行した強い切り札を開幕に使う戦術 (実行は強い将軍と判定された時だけ)。"""
    return [{'enemy': enemy, 'card': card, 'open': True, 'step': cur.get('step'),
             'tactic_id': f"strong:{cur.get('step')}:{card}", 'strong_only': True,
             'note': '出撃時に携行した強い切り札を強い将軍へ開幕で使う'}
            for card in _strong_cards_carried(mem, cur)]


def _card_damage_context(mem, cur):
    """Prediction inputs and a bounded refusal key from current observations."""
    enemy = cur.get('enemy')
    summoned = bool(cur.get('egg_battle') or mem.get('egg_battle')
                    or cur.get('card_summon_observed'))
    if summoned:
        # The saved ordinary enemy HP is not the summoned monster's HP.
        # This gate only prevents a *future* summon; keep its damage unknown.
        target = ('boss_monster' if (enemy in reference.BOSS_GENERALS
                                    or enemy in chart.BOSSES.values() or enemy in reference.BOSS_HP)
                  else 'egg_monster')
    else:
        target = 'boss_general' if enemy in reference.BOSS_GENERALS else 'general'
    if (cur.get('card_context_unclassified')
            or any(type(cur.get(key)) is int and cur[key] <= 0 for key in ('enemy_hp', 'ally_hp'))
            or (summoned and cur.get('card_summon_observed') is not True)):
        target = 'unknown'
    fresh = cur.get('card_soldiers_current') is True and not summoned
    hp_known = not cur.get('card_hp_unread') and not summoned
    return {'target_kind': target, 'enemy_name': enemy,
            'enemy_hp': cur.get('enemy_hp') if hp_known else None,
            'ally_hp': cur.get('ally_hp') if hp_known else None,
            'ally_soldiers': cur.get('ally_soldiers') if fresh else None,
            'enemy_soldiers': cur.get('enemy_soldiers') if fresh else None}


def _card_damage_key(mem, cur):
    context = _card_damage_context(mem, cur)
    # Reevaluate v151 refusals once after hotload, within the existing budget.
    return [2, cur.get('ally'), cur.get('side'), *context.values()]


CARD_CONTROL_SETUP = frozenset({'クースカン', 'ブレイコウ'})


def _control_melee_advantage(cur, enemy_hp, ally_soldiers, enemy_soldiers):
    """A healthy normal army can follow up; this is not a victory guarantee."""
    ally = reference.general_strength('しゅじんこう' if cur.get('ally') == NAME else cur.get('ally'))
    enemy = reference.general_strength(cur.get('enemy'))
    hp = cur.get('ally_hp')
    return (ally is not None and enemy is not None
            and cur.get('enemy') not in reference.BOSS_GENERALS
            and all(type(n) is int and n > 0 for n in (hp, enemy_hp))
            and all(type(n) is int and 0 <= n <= 6 for n in (ally_soldiers, enemy_soldiers))
            and ally_soldiers > 0 and ally_soldiers >= enemy_soldiers
            and ally[1] >= enemy[1]
            and hp > GENERAL_CRITICAL_RETREAT_HP
            and hp * 10 > ally[0] * BEHIND_EGG_RATIO_TENTHS
            and hp >= enemy_hp * 2)


def _card_melee_projection(cur, card, context, estimate):
    # gcgx kirihuda.html: クースカン halves enemy general/soldier HP and
    # stuns. Keep the prospective effect separate from observed live HP.
    hp = context['enemy_hp']
    ours, theirs = context['ally_soldiers'], context['enemy_soldiers']
    if (card != 'クースカン' or estimate['target_kind'] != 'general'
            or type(hp) is not int or hp <= 0
            or not _control_melee_advantage(cur, (hp + 1) // 2, ours, theirs)):
        return None
    return {'enemy_hp_before': hp, 'enemy_hp_after_upper': (hp + 1) // 2,
            'enemy_soldier_hp_after_upper': theirs * ((reference.GENERAL_SOLDIER_HP + 1) // 2),
            'ally_soldiers': ours, 'enemy_soldiers': theirs,
            'effect_confirmation': 'unclassified', 'rule': 'healthy_hp_double_equal_army_combat'}


def _watch_card_melee(mem, cur, gate):
    if (gate['role'] in ('control_then_melee_not_a_kill', 'observed_control_chain_not_a_kill')
            and gate.get('melee_followup')):
        cur['card_melee_followup'] = {**gate['melee_followup'],
            'ally': cur.get('ally'), 'enemy': cur.get('enemy'),
            'selected_tick': int(mem.get('tick') or 0), 'bursts': 0}
    else:
        cur.pop('card_melee_followup', None)


def _card_melee_ready(mem, cur):
    watch = cur.get('card_melee_followup') or {}
    hp = cur.get('enemy_hp')
    return bool(watch and 'クースカン' in cur.get('cards_selected', [])
                and (watch.get('ally'), watch.get('enemy')) == (cur.get('ally'), cur.get('enemy'))
                and 0 <= int(mem.get('tick') or 0) - watch['selected_tick'] <= CARD_MELEE_OBSERVE_LIMIT
                and watch['bursts'] < MELEE_HOLD_LIMIT
                and not (cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed'))
                and not cur.get('card_hp_unread') and cur.get('card_soldiers_current') is True
                and type(hp) is int and 0 < hp <= watch['enemy_hp_after_upper']
                and hp < watch['enemy_hp_before']
                and _control_melee_advantage(cur, hp, cur.get('ally_soldiers'), cur.get('enemy_soldiers')))


# gcgx EggHero 白兵戦闘処理 (X はキャラ左端)。前衛の開始位置と接触間隔、
# 左右の壁、後退初速ごとの後退ドット。将軍は後衛 (前衛の 24 ドット後ろ) に
# 立つとみる: 剣の稽古の初接触 (同じ戦闘14同士) で敵将軍 X=85 は前衛の
# 接触 112 から 24 と押し返しを引いた値に合う。兵士が無くてもそう扱う。
CLASH_ENEMY_FRONT_START = 48
CLASH_ALLY_FRONT_START = 200
CLASH_GAP = 24
CLASH_LEFT_WALL = 4
CLASH_RIGHT_WALL = 240
CLASH_REAR_OFFSET = 24
CLASH_BACKOFF_DOTS = {5: 62, 6: 77, 7: 92, 8: 107, 9: 122, 10: 137}
CLASH_MASH_MAX = 8            # 後退中の連打は 8 回分までしか加速に入らない
CLASH_APTITUDE_SPEED = 6      # 得意地形・季節。城攻・城守・洞窟は速度が上がらない
CLASH_NO_SPEED_APTITUDES = frozenset({'', '城攻', '城守', '洞窟'})
CLASH_MARGIN = 16             # 離散移動・押し返し・壁際の処理順の誤差
CLASH_SAFE_EFFECTS = frozenset({'fixed', 'hp_scaled', 'soldier_clear'})


def _backoff_dots(pusher_soldiers, pushed_soldiers, pusher_combat):
    """押し返された側の後退ドット。初速が表の外なら None (外挿しない)。"""
    a = max(0, 2 * pusher_soldiers - 2 * pushed_soldiers)
    return CLASH_BACKOFF_DOTS.get(max(5, (a + pusher_combat) // 4 + 4))


def _enemy_egg_reach(mem, cur, context, estimate):
    """攻城戦の残りの白兵で、敵の思考タイプの卵判定に届くか。

    gcgx EggHero 卵使用思考: B (瀕死) と D (4の倍数) は敵将軍の壁接触、
    C は接触時の敵将軍 X <= 話数*4+32 で判定する。A は開幕で決まり、当方の
    携行は47以下。押し合いの加速・後退から、敵の加速は下限 (兵士0・連打0・
    城補正0)、当方は上限 (兵士は読めた数か6・連打8・得意補正) で
    敵将軍の最左位置を出す。接触ごとに左へ寄るなら届くとする。
    単発の切り札は位置を動かさないので、届かなければ倒し切れなくても
    この戦闘で卵は出ない。止める・半減・自傷の札は位置に効くので対象外。
    """
    chapter = mem.get('chapter')
    enemy_name = cur.get('enemy')
    ally_name = 'しゅじんこう' if cur.get('ally') == NAME else cur.get('ally')
    ally = reference.general_strength(ally_name)
    enemy = reference.general_strength(enemy_name)
    profile = reference.general_profile(ally_name)
    if (cur.get('side') != 'attack' or type(chapter) is not int or chapter not in range(1, 13)
            or enemy_name in reference.BOSS_GENERALS or enemy_name in chart.BOSSES.values()
            or ally is None or enemy is None or profile is None
            or estimate['effect_kind'] not in CLASH_SAFE_EFFECTS or estimate['self_harm']):
        return None
    triggers = enemy_egg_triggers(enemy_name, player_castle_defense=False)
    ours = context['ally_soldiers']
    ours = ours if type(ours) is int and 0 <= ours <= reference.MAX_BATTLE_SOLDIERS else None
    soldiers = reference.MAX_BATTLE_SOLDIERS if ours is None else ours
    aptitude = 0 if profile['aptitude'] in CLASH_NO_SPEED_APTITUDES else CLASH_APTITUDE_SPEED
    ally_accel = 2 * soldiers + CLASH_MASH_MAX + 16 + ally[1] + aptitude
    enemy_accel = 16 + enemy[1]
    enemy_back = _backoff_dots(soldiers, 0, ally[1])
    ally_back = _backoff_dots(0, soldiers, enemy[1])
    clash_x = chapter * 4 + 32
    bound = {'ally_soldiers_upper': soldiers, 'ally_accel_upper': ally_accel,
             'enemy_accel_lower': enemy_accel, 'enemy_backoff_upper': enemy_back,
             'ally_backoff_lower': ally_back, 'clash_x_threshold': clash_x,
             'clash_checked': triggers.clash_position is not False,
             'enemy_general_x_min': None, 'unreachable': False}
    if enemy_back is None or ally_back is None or enemy_accel * ally_back < ally_accel * enemy_back:
        return bound  # 接触が左へ寄り続け、いずれ敵将軍が壁に当たる
    travel = CLASH_ALLY_FRONT_START - CLASH_ENEMY_FRONT_START - CLASH_GAP
    first = CLASH_ENEMY_FRONT_START + travel * enemy_accel / (enemy_accel + ally_accel)
    # 当方の将軍が右壁で後退を止められると接触はここへ寄っていく。
    settled = (CLASH_RIGHT_WALL - CLASH_REAR_OFFSET - CLASH_GAP
               - enemy_back * ally_accel / enemy_accel)
    general_x = min(first, settled) - CLASH_MARGIN - CLASH_REAR_OFFSET
    bound['enemy_general_x_min'] = round(general_x, 1)
    bound['unreachable'] = (general_x - enemy_back > CLASH_LEFT_WALL
                            and (not bound['clash_checked'] or general_x > clash_x))
    return bound


def _card_damage_gate(mem, cur, card, *, listed_cards=None):
    """A selection gate, separate from use, hit, kill and egg-loss receipts.

    B may inspect a real inventory even when no kill can be proven.  The
    final A needs a fresh evaluation with that inventory. A control card
    may set up observed cards or a healthy, measured normal-army follow-up;
    neither is classified as a guaranteed kill or an egg-loss receipt.
    """
    context = _card_damage_context(mem, cur)
    estimate = reference.card_damage_estimate(card, **context)
    side = cur.get('side')
    triggers = enemy_egg_triggers(cur.get('enemy'), player_castle_defense=(
        True if side == 'defense' else False if side == 'attack' else None))
    summoned = bool(cur.get('egg_battle') or mem.get('egg_battle')
                    or cur.get('card_summon_observed'))
    boss = cur.get('enemy') in reference.BOSS_GENERALS
    threat = True if boss else _egg_threat(triggers)
    drop = _egg_drop_evidence(cur, card) if not summoned and not boss else None
    reach = (_enemy_egg_reach(mem, cur, context, estimate)
             if not summoned and threat is not False else None)
    result = {'allowed': False, 'needs_live_list': False, 'role': 'nonlethal_egg_risk',
              'estimate': estimate, 'enemy_egg_threat': threat,
              'egg_risk_flags': asdict(triggers), 'egg_drop': drop,
              'egg_reach': reach, 'listed_followups': []}
    if (cur.get('card_context_unclassified')
            or any(type(cur.get(key)) is int and cur[key] <= 0 for key in ('enemy_hp', 'ally_hp'))
            or (summoned and cur.get('card_summon_observed') is not True)):
        result['role'] = 'damage_or_egg_risk_unclassified'
    elif summoned:
        result.update(allowed=True, role='summon_already_observed')
    elif estimate['lethal'] is True:
        result.update(allowed=True, role='single_card_lethal')
    elif card == 'エンジェリン':
        result.update(allowed=True, role='healing_not_a_kill')
    elif threat is False:
        result.update(allowed=True, role='no_autonomous_egg_trigger')
    elif reach and reach['unreachable']:
        result.update(allowed=True, role='enemy_egg_triggers_unreachable')
    elif drop and drop.get('drops'):
        result.update(allowed=True, role='egg_drop_candidate_not_a_kill')
    elif listed_cards is None:
        result.update(needs_live_list=True, role='inspect_live_inventory_before_selection')
    elif card in CARD_CONTROL_SETUP:
        # A half/control setup is a conditional chain, not a proven kill.
        # Follow-up cards must be on this screen.  Their actual final damage
        # will be recomputed from the next observed HP/counts, never from the
        # number of クースカン selections or from inferred soldier HP loss.
        followups = []
        for other in dict.fromkeys(listed_cards):
            if other == card or other not in CARD_NAMES or other in CARD_CONTROL_SETUP:
                continue
            predicted = reference.card_damage_estimate(other, **context)
            raw = predicted.get('raw_damage_min')
            if (type(raw) is int and raw > 0
                    and predicted['effect_kind'] not in ('heal', 'retreat', 'half_both_hp')
                    and (not predicted['self_harm'] or other in (cur.get('planned_cards') or []))):
                followups.append(other)
        if followups:
            result.update(allowed=True, role='observed_control_chain_not_a_kill',
                          listed_followups=followups)
            # g749: carrying a weak Footbath must not erase a healthy
            # post-control melee option. The next complete panel still has
            # to prove HP progress and current army/combat advantage; this
            # projection is neither a card-use receipt nor permission now.
            melee = _card_melee_projection(cur, card, context, estimate)
            if melee:
                result['melee_followup'] = melee
        else:
            melee = _card_melee_projection(cur, card, context, estimate)
            if melee:
                result.update(allowed=True, role='control_then_melee_not_a_kill', melee_followup=melee)
            else:
                result['role'] = 'control_has_no_observed_followup'
    elif estimate['lethal'] is None:
        result['role'] = 'damage_or_egg_risk_unclassified'
    cur['card_assessment'] = {
        'card': card, 'enemy_hp': context['enemy_hp'],
        'ally_soldiers': context['ally_soldiers'], 'enemy_soldiers': context['enemy_soldiers'],
        'target_kind': estimate['target_kind'], 'raw_damage_min': estimate['raw_damage_min'],
        'enemy_soldier_hp_upper': estimate['enemy_soldier_hp_upper'],
        'damage_lower_bound': estimate['damage_lower_bound'],
        'remaining_hp_upper': estimate['remaining_hp_upper'], 'lethal': estimate['lethal'],
        'egg_drop_fit': drop.get('drops') if drop else None,
        'allowed': result['allowed'], 'reason': result['role'],
        **({'melee_followup': result['melee_followup']} if result.get('melee_followup') else {})}
    mem['last_card_assessment'] = {**cur['card_assessment'], 'tick': mem.get('tick')}
    return result


def _card_gate_refused(mem, cur, card, gate, *, listed_cards):
    """Remember a refusal without consuming the card or looping B unchanged."""
    signature = _card_damage_key(mem, cur)
    refused = cur.setdefault('card_damage_refusals', {})
    if refused.get(card) != signature:
        _record(mem, 'battle_card_damage_rejected', **_battle_labels(cur), card=card,
                expected_metric='敵兵士の吸収後も単発で倒せるか、独立した卵封じ・回復・連携根拠',
                observed_metric={**gate, 'listed_cards': list(listed_cards)},
                resulting_event='card_not_selected',
                reason='単発で倒し切れる保証がなく、次の接触・壁判定で敵に召喚される危険があるため実一覧の代替手段を確認')
    refused[card] = signature
    # #2896: suppress rescue-needed latch in favourable battles (branch-local).
    if not _full_hp_melee_advantage(cur):
        cur['card_damage_rescue_needed'] = True
    flow = cur.get('card_flow') or {}
    tid = flow.get('tactic_id')
    if flow.get('card') == card and tid in cur.get('tactics_done', []):
        cur['tactics_done'].remove(tid)  # may reconsider after actual HP/counts change


def _card_gate_alternatives(mem, cur, names, *, omit=None):
    allowed = []
    for candidate in SURVIVAL_CARDS:
        if (candidate in names and candidate != omit
                and candidate not in (cur.get('cards_selected') or [])):
            gate = _card_damage_gate(mem, cur, candidate, listed_cards=names)
            if gate['allowed']:
                allowed.append(candidate)
    return allowed


def _card_healing_needed(cur):
    maximum = general_max_hp('しゅじんこう' if cur.get('ally') == NAME else cur.get('ally'))
    full = max((value for value in (maximum, cur.get('ref_ally_hp'), cur.get('start_ally_hp'))
                if type(value) is int and value > 0), default=None)
    hp = cur.get('ally_hp')
    return type(hp) is not int or full is None or hp < full


def _card_gate_choice(mem, cur, candidates, names):
    """Healing when needed, proven lethal, drop candidate, then control setup."""
    if not candidates:
        return None
    gates = {card: _card_damage_gate(mem, cur, card, listed_cards=names) for card in candidates}
    if 'エンジェリン' in candidates and _card_healing_needed(cur):
        choice = 'エンジェリン'
    else:
        choice = None
        for role in ('single_card_lethal', 'egg_drop_candidate_not_a_kill',
                     'observed_control_chain_not_a_kill', 'control_then_melee_not_a_kill'):
            choices = [card for card in candidates if gates[card]['role'] == role]
            if choices:
                choice = _rescue_card(choices, cur)
                # Conserve firepower only between already-proven finishers.
                # Critical/unknown HP keeps the existing emergency order;
                # healing above, explicit chart choices, and final A gates
                # remain unchanged. Do not rank guessed or self-harming hits.
                if (role == 'single_card_lethal'
                        and type(cur.get('ally_hp')) is int
                        and cur['ally_hp'] > GENERAL_CRITICAL_RETREAT_HP):
                    safe = [card for card in choices
                            if gates[card]['estimate']['self_harm'] is False
                            and type(gates[card]['estimate']['raw_damage_min']) is int
                            and gates[card]['estimate']['raw_damage_min'] > 0]
                    if safe:
                        minimum = min(gates[card]['estimate']['raw_damage_min'] for card in safe)
                        choice = _rescue_card([card for card in safe
                            if gates[card]['estimate']['raw_damage_min'] == minimum], cur)
                break
        if choice is None:
            choice = _rescue_card(candidates, cur)
    _card_damage_gate(mem, cur, choice, listed_cards=names)  # last assessment is the choice
    return choice


def _card_damage_probe_exhausted(cur):
    """Use the fight's existing count/HP budget, never a per-refusal reset."""
    holds = max(int(cur.get('melee_holds') or 0), int(cur.get('card_damage_probe_ticks') or 0))
    holds += int(cur.get('card_damage_rechecks') or 0)
    hp, start = cur.get('ally_hp'), cur.get('start_ally_hp')
    if type(hp) is not int or type(start) is not int or hp <= 0 or start <= 0:
        return True
    return (bool(cur.get('melee_forced')) or holds >= MELEE_HOLD_LIMIT
            or start - hp >= max(MELEE_HP_HOLD_FLOOR, start // MELEE_HP_HOLD_DIVISOR))


def _card_damage_recheck_resources(mem, cur):
    """Changed readings can recheck withheld cards within the fight's budget.

    Readable/unread soldier counts may oscillate without any game progress.
    Charge each inventory recheck to the same budget as a short melee probe,
    and grant the chart loop permission only for this accepted observation.
    """
    blocked = cur.get('card_damage_list_blocked')
    if not blocked or not cur.get('card_damage_rescue_needed'):
        return
    signature = _card_damage_key(mem, cur)
    if blocked.get('key') == signature:
        return
    exhausted = _card_damage_probe_exhausted(cur)
    if exhausted:
        # The count/HP budget stops further probing.  At that boundary a
        # newly *observed* zero soldier count may already make a known real
        # card lethal: permit one final inventory check, not another probe
        # budget and not an A from the old list.
        if cur.get('card_damage_final_recheck'):
            return
        names = blocked.get('cards') or []
        finishing = False
        for card in names:
            gate = _card_damage_gate(mem, cur, card, listed_cards=names)
            if (gate['allowed'] and (gate['role'] in ('single_card_lethal', 'control_then_melee_not_a_kill')
                    or (gate['role'] == 'healing_not_a_kill' and _card_healing_needed(cur)))):
                finishing = True
                break
        if not finishing:
            return
        cur['card_damage_final_recheck'] = True
    else:
        cur['card_damage_rechecks'] = int(cur.get('card_damage_rechecks') or 0) + 1
    blocked['key'] = signature
    cur['card_damage_recheck_key'] = signature
    rescue = cur.get('survival')
    if rescue:
        rescue.update(cards_exhausted=False, exhausted=False, cards_checked=0,
                      pending_opens=0, menu_ticks=0)
        rescue.pop('conserving_melee', None)  # a changed panel permits this bounded real-card recheck
    _record(mem, 'battle_card_damage_recheck', **_battle_labels(cur),
            observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp'),
                             'ally_soldiers': cur.get('ally_soldiers'),
                             'enemy_soldiers': cur.get('enemy_soldiers'),
                             'probe_ticks': cur.get('card_damage_probe_ticks', 0),
                             'inventory_rechecks': cur.get('card_damage_rechecks', 0),
                             'final_inventory_check': exhausted},
            reason='HP・兵数の読取変化から保留した実在札を再確認。可読性の変化も戦闘通算の保持予算へ計上')


CARD_OPEN_ATTEMPTS = 2        # initial chart opening plus one bounded retry
CARD_MENU_OPEN_RETRIES = 4    # B repeats while the command menu has not opened yet
CARD_LIST_CURSOR_LIMIT = 10  # same finite list budget as survival selections
CARD_ANNOUNCE_LIMIT = 6      # shared by chart and survival confirmation waits
MELEE_HOLD_LIMIT = 8          # egg-safe holds in one fight before melee proceeds
CARD_MELEE_OBSERVE_LIMIT = (CARD_ANNOUNCE_LIMIT + CARD_LIST_CURSOR_LIMIT
                            + 2 * CARD_MENU_OPEN_RETRIES + MELEE_HOLD_LIMIT)
# g692: animation/menus took 26 observations before a fresh advantageous
# panel. Keep their bounded read budget separate from the eight power bursts.
# A hold that bleeds the general past this share of the fight's opening HP
# stops before the count bound (g530 09:52 defense: 90 -> 81 by the 7th hold,
# the count alone let it reach 73, and the forced push that followed fought
# the summoned モーグリ at 73 and died with it at 4 HP; entering with the 8 HP
# the budget saves wins that trade, by a thin margin). The floor keeps a
# small opening HP from tripping on the first scratch.
MELEE_HP_HOLD_DIVISOR = 10
MELEE_HP_HOLD_FLOOR = 3


def _charted_melee(mem, cur) -> bool:
    """The chart needs this fight's melee to progress toward a card.

    Holding input under the clash egg risk never reaches ``after_clash`` or
    the HP gate of a ``when_hp_at_most`` card (g454 08:19: the queen battle
    held, どうし fell to 43 and the rescue used its own egg -> ヒュドラ; g456
    14:13: どうし 90 vs ガルバンゾー 30 held until the フットバース gate at
    enemy HP 24 was unreachable, and the hero died).
    """
    if cur.get('card_damage_rescue_needed'):
        return False  # a rejected finishing plan cannot justify unbounded chart pushing
    return any((t.get('after_clash') or t.get('when_hp_at_most') is not None)
               and t.get('enemy') in (None, cur.get('enemy'))
               and t.get('step') in (None, cur.get('step'))
               for t in _tactics(mem, cur.get('step')))


def _melee_balance(cur: dict) -> dict:
    """Comparable general/army estimate, not the game's damage formula.

    The fixed named-general combat stat affects attack, while the native
    panel supplies current HP and (only when proven current) soldier counts.
    Weight each side's existing HP pool by its own combat stat. Missing
    stats never borrow the other side's value or an optimistic default.
    Summoned-monster HP/attack and castle/terrain speed are not estimated.
    """
    combat = {}
    for side in ('ally', 'enemy'):
        name = cur.get(side)
        name = 'しゅじんこう' if side == 'ally' and name == NAME else name
        row = reference.general_strength(name) if isinstance(name, str) else None
        value = row[1] if isinstance(row, (tuple, list)) and len(row) >= 2 else None
        combat[side] = value if type(value) is int and 1 <= value <= 15 else None
    measured = (cur.get('card_soldiers_current') is True
                and not cur.get('card_hp_unread')
                and not cur.get('card_context_unclassified')
                and all(type(cur.get(side + '_soldiers')) is int
                        and 0 <= cur[side + '_soldiers'] <= 6 for side in ('ally', 'enemy')))
    out = {'assessment_method': 'hp_pool_times_combat_heuristic',
           'assessment_scope': 'general_matchup', 'soldiers_current': measured}
    for side in ('ally', 'enemy'):
        hp = cur.get(side + '_hp')
        force = (hp + (10 * cur[side + '_soldiers'] if measured else 0)
                 if type(hp) is int and hp > 0 else None)
        out[side + '_combat'] = combat[side]
        out[side + '_force'] = force
        out[side + '_melee_score'] = (force * combat[side]
                                     if force is not None and combat[side] is not None else None)
    return out


def _combat_behind(cur):
    if (cur.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('enemy') in chart.BOSSES.values()):
        return False  # a general's attack is not a summoned monster's attack
    balance = _melee_balance(cur)
    ours, theirs = balance['ally_melee_score'], balance['enemy_melee_score']
    return (ours is not None and theirs is not None
            and ours * 10 <= theirs * BEHIND_EGG_RATIO_TENTHS)


def _behind(cur: dict) -> bool:
    if not (cur.get('egg_battle') or cur.get('card_summon_observed')):
        balance = _melee_balance(cur)
        ours, theirs = balance['ally_melee_score'], balance['enemy_melee_score']
        if ours is not None and theirs is not None:
            return ours < theirs
        if (isinstance(cur.get('ally'), str) and isinstance(cur.get('enemy'), str)
                and all(type(cur.get(k)) is int and cur[k] > 0 for k in ('ally_hp', 'enemy_hp'))):
            return True  # unknown attack cannot license ordinary melee conservation
    # Monster-menu comparisons intentionally supply HP alone, not generals.
    enemy_hp, ally_hp = cur.get('enemy_hp'), cur.get('ally_hp')
    if type(enemy_hp) is not int or type(ally_hp) is not int:
        return False
    return ally_hp < enemy_hp


# Only non-sacrificial cards are eligible for automatic survival use. Rank
# healing first, then control/damage; this is not a guaranteed damage model.
# Effects: https://gcgx.games/hanjuku/kirihuda.html
SURVIVAL_CARDS = ('エンジェリン', 'キャトルミュー', 'ミックミー', 'マグネガキン', 'クースカン',
                  'グリンボー', 'ゼンマイン', 'ブラッキー', 'ファイアーボイス',
                  'ハリケーン', 'ブンシーン', 'ピッグローラー', 'イッテツーン',
                  'カンケリン', 'フットバース', 'ダイチスイム', 'ノリウツール')
# 実在庫の札が順位表に無くても ValueError にしない (全32札を表に持つようになったため)。
SURVIVAL_RANK = {card: index for index, card in enumerate(SURVIVAL_CARDS)}


def _egg_max_hp(cur):
    """(enemy max HP, ally max HP, exclusion reason) for this battle (#2364).

    The fixed char.csv HP is the max HP; the battle panel's current HP is not
    used. The hero uses the remembered full-strength ``ref_ally_hp`` when it
    exists. A boss/normal-general boundary or an unknown general returns a
    reason and no arithmetic, so the display can say 対象外/判定不能 instead of
    filling a missing max HP with the current HP or 0.
    """
    enemy = cur.get('enemy')
    if isinstance(enemy, str) and (enemy in reference.BOSS_GENERALS
                                   or enemy in chart.BOSSES.values()):
        return None, None, 'boss'
    ally_max = cur.get('ref_ally_hp')
    if type(ally_max) is not int or ally_max <= 0:
        ally_name = 'しゅじんこう' if cur.get('ally') == NAME else cur.get('ally')
        ally_max = general_max_hp(ally_name)
    enemy_max = general_max_hp(enemy)
    if type(enemy_max) is not int or enemy_max <= 0:
        return None, ally_max, 'enemy_unknown'
    if type(ally_max) is not int or ally_max <= 0:
        return enemy_max, None, 'ally_unknown'
    return enemy_max, ally_max, None


def _egg_drop_evidence(cur, card):
    """This battle's 卵落 fit for ``card``, or None when max HP is unknown.

    gcgx: a card drops the enemy egg when its 卵落 exceeds the two generals'
    *max* HP sum mod 16. A wounded battle reading is not max HP, so the
    comparison uses the fixed char.csv HP (hero: the remembered full-strength
    ``ref_ally_hp``) and fails closed to the fixed priority when the general
    is unknown.
    """
    enemy_max, ally_max, _ = _egg_max_hp(cur)
    value = reference.egg_drop_value(card)
    if value is None or any(type(n) is not int or n <= 0 for n in (enemy_max, ally_max)):
        return None
    total = enemy_max + ally_max
    return {'value': value, 'threshold': reference.egg_drop_threshold(total),
            'max_hp_sum': total, 'drops': reference.can_drop_egg(card, total)}


def _egg_threat(triggers):
    """敵がこの戦闘で卵を使える能力か。未読は None (安全と推定しない)。

    開幕の「携行ID合計 >= 48」判定は当方の携行が47以下なので起きない
    (``_cap_card_ids``)。残るのは激突・壁の判定で、思考タイプ0の敵には無い。
    """
    if triggers.has_egg is None:
        return None
    if triggers.has_egg is False:
        return False
    flags = (triggers.clash_position, triggers.wall_critical, triggers.wall_mod4)
    if any(flag is True for flag in flags):
        return True
    return None if any(flag is None for flag in flags) else False


def _carried_kit(mem, cur):
    """この戦闘で実際に携行している切り札。出撃注文が特定できない戦闘は None。"""
    order = _order_for_step(mem, cur.get('step'))
    return None if order is None else _deploy_cards(order, mem)


def _egg_inventory(mem, cur, kit):
    """Bounded display/decision rows for the observed cards (#2364).

    ``card_stock`` is the observed inventory, ``kit`` the cards this sortie
    actually carried. A card in stock but not in the kit is *not* usable in
    this battle, while an unobserved stock entry stays null instead of 0.
    Duplicate carried cards are counted, and a card already used/selected is
    not offered again. One shared row feeds both the card and the tactic.
    """
    stock = mem.get('card_stock') if isinstance(mem.get('card_stock'), dict) else {}
    # 1回の使用は選択時 (cards_selected) と結果確定時 (cards_used /
    # cards_unclassified) の両方に載るため、札ごとに多い方の件数を使う。
    selected = cur.get('cards_selected') or []
    settled = [*(cur.get('cards_used') or []), *(cur.get('cards_unclassified') or [])]
    sortie_spent = (mem.get('kit_spent') or {}).get(cur.get('step')) or []
    ordered = []
    for name in [*(kit or []), *sorted(stock, key=lambda n: (CARD_IDS.get(n, 99), str(n)))]:
        if isinstance(name, str) and name and name not in ordered:
            ordered.append(name[:24])
    rows = []
    for name in ordered[:40]:
        carried = (kit or []).count(name)
        count = stock.get(name)
        count = count if type(count) is int and 0 <= count <= 99 else None
        if carried <= 0 and count == 0:
            continue                      # known absent, not an owned card
        # _deploy_cards already removed settled sortie uses. Only this
        # battle's overlapping settled entries may be credited: a prior
        # battle's use must not cancel a new pending selection.
        credited = min(settled.count(name), sortie_spent.count(name))
        spent = max(selected.count(name), settled.count(name)) - credited
        if kit is None:
            available, unavailable = None, 'kit_unknown'
        elif carried <= 0:
            available, unavailable = 0, 'not_carried'
        elif carried > spent:
            available, unavailable = carried - spent, None
        else:
            available, unavailable = 0, 'used'
        prediction = reference.card_drop_prediction(name, None)
        rows.append({'name': name, 'id': prediction['id'],
                     'egg_drop': prediction['egg_drop'], 'drops': None,
                     'stock': count, 'carried': carried,
                     'available': available, 'usable': (available or 0) > 0,
                     'unavailable': unavailable})
    return rows


def _egg_plan(mem, cur):
    """将軍の最大HPと携行切り札の卵落を組み合わせた、この戦闘の卵ディニアル計画。

    owner 2026-10-03: 「全ての将軍と切り札のデータをちゃんと内部でデータとして
    持って、その組み合わせで卵を使わせないか、落とさせるようにたたかう」。
    gcgx: 携行札のID合計が48以上だと敵が先に卵を使い、「卵落 > 敵・味方将軍の
    最大HP合計 mod 16」で命中した札は敵の卵を落として以後の召喚を封じる。
    将軍名・最大HP・携行が判明しない項目は fail-closed で None のまま残す。

    #2364: 右ステータス表示と開幕戦術が同じ計算を参照するよう、両将軍の
    最大HP・余り・札ごとの予測・卵の状態も同じ plan に残す。チャートが指す
    札も「卵を落とせる携行札」としては除外しない (HPゲートや after_clash の
    札を開幕の卵落へ回すかは flows.battle の優先順位で決める)。
    """
    enemy = cur.get('enemy')
    side = cur.get('side')
    defense = True if side == 'defense' else False if side == 'attack' else None
    triggers = enemy_egg_triggers(enemy, player_castle_defense=defense)
    kit = _carried_kit(mem, cur)
    ids = None if kit is None else [CARD_IDS.get(card) for card in kit]
    enemy_max, ally_max, excluded = _egg_max_hp(cur)
    total = (enemy_max + ally_max
             if type(enemy_max) is int and type(ally_max) is int else None)
    # 敵卵の状態: 静的な保有能力と、この戦闘で観測した使用/落下推定を分ける。
    # どちらも「実際に落とした」確定証跡ではない (既存の egg_drop_watch と同じ)。
    if triggers.has_egg is False:
        egg_state = 'none'
    elif triggers.has_egg is None:
        egg_state = 'unknown'
    elif cur.get('enemy_egg_drop_expected') is True:
        egg_state = 'dropped_unconfirmed'
    elif cur.get('egg_battle') or cur.get('card_summon_observed'):
        egg_state = 'used'
    else:
        egg_state = 'available'
    plan = {'enemy': enemy, 'ally': cur.get('ally'),
            'threat': _egg_threat(triggers), 'has_egg': triggers.has_egg,
            'egg_state': egg_state,
            'kit_known': kit is not None, 'carried': list(kit or []),
            'id_sum': None if ids is None or any(i is None for i in ids) else sum(ids),
            'deny_opening_egg': None,
            'enemy_max_hp': enemy_max, 'ally_max_hp': ally_max,
            'max_hp_sum': total,
            'remainder': total % reference.EGG_DROP_MOD if total is not None else None,
            'threshold': reference.egg_drop_threshold(total) if total is not None else None,
            'cards': [], 'droppers': [], 'dropper': None, 'charted': [],
            'excluded': excluded,
            'egg_drop': None, 'sortie_dropper': None, 'triggers': asdict(triggers),
            'enemy_profile': reference.general_profile(enemy if isinstance(enemy, str) else None),
            'ally_profile': reference.general_profile(
                'しゅじんこう' if cur.get('ally') == NAME else
                (cur.get('ally') if isinstance(cur.get('ally'), str) else None))}
    # The display validates profile identity against the live battle name.
    # Hero stats use the canonical table row, but its label and carried max
    # HP must match the value used by the battle plan.
    if cur.get('ally') == NAME and plan['ally_profile'] is not None:
        plan['ally_profile'].update(name=NAME, max_hp=ally_max)
    plan['verdict'] = ('excluded' if excluded == 'boss'
                       else 'unknown' if excluded or total is None or egg_state == 'unknown'
                       else 'no_egg' if egg_state == 'none'
                       else 'used' if egg_state == 'used'
                       else 'dropped_unconfirmed' if egg_state == 'dropped_unconfirmed'
                       else 'not_threat' if plan['threat'] is False
                       else 'unknown' if plan['threat'] is None else 'not_droppable')
    # 出撃時の卵落計画 (#1553): 守備将軍の実測記録から出撃時に加えた卵落札。
    # 実測した敵が記録と一致する時だけ計画を残し、同じ算出 (max_hp_sum /
    # threshold) になったことを 'agreement' で残す。一致しない戦闘では計画を
    # 使わず (fail-closed)、矛盾を戦闘ごとに1回だけ記録する。
    sortie_kit = (mem.get('dropper_card_kit') or {}).get(cur.get('step'))
    if isinstance(sortie_kit, dict) and isinstance(sortie_kit.get('card'), str):
        if kit is not None and sortie_kit['card'] in kit and sortie_kit.get('defender') == enemy:
            plan['sortie_dropper'] = {'card': sortie_kit['card'], 'defender': enemy,
                                      'max_hp_sum': sortie_kit.get('max_hp_sum'),
                                      'threshold': sortie_kit.get('threshold')}
        elif kit is not None and sortie_kit.get('defender') not in (None, enemy) and not cur.get('sortie_dropper_mismatch_seen'):
            cur['sortie_dropper_mismatch_seen'] = True
            _record(mem, 'sortie_dropper_mismatch', **_battle_labels(cur), enemy=enemy,
                    expected_metric={'defender': sortie_kit.get('defender'),
                                     'card': sortie_kit['card']},
                    observed_metric={'battle_enemy': enemy},
                    reason='出撃時の卵落計画は別の守備将軍向けのため、この戦闘では計画を使わず実測した敵で算出する')
    rows = _egg_inventory(mem, cur, kit)
    if total is not None:
        for row in rows:
            prediction = reference.card_drop_prediction(row['name'], total)
            row['drops'] = prediction['drops']
    plan['cards'] = rows
    # 携行IDの合計が48以上だと敵が開幕で卵を使う (gcgx ai.html)。不明IDは失敗扱い。
    if kit is not None:
        plan['deny_opening_egg'] = plan['id_sum'] is not None and plan['id_sum'] < ENEMY_EGG_CARD_ID_SUM
    if isinstance(plan['sortie_dropper'], dict):
        # 出撃時と戦闘時で同じ対戦相手なら同じ算出になるはず (#1553)。
        plan['sortie_dropper']['agreement'] = (
            plan['sortie_dropper'].get('max_hp_sum') == plan['max_hp_sum']
            and plan['sortie_dropper'].get('threshold') == plan['threshold'])
    if total is None or kit is None:
        cur['egg_plan'] = plan
        return plan
    plan['droppers'] = [row['name'] for row in rows
                        if row['usable'] and row['drops'] is True]
    # この戦闘のチャートが指す札。除外には使わないが、選択理由として残す。
    plan['charted'] = sorted({t['card'] for t in _tactics(mem, cur.get('step'))
                              if t.get('step') in (None, cur.get('step'))
                              and t.get('enemy') in (None, enemy)})
    if not (plan['threat'] is True and plan['droppers']
            and egg_state == 'available'):
        cur['egg_plan'] = plan
        return plan
    # Owner 2026-10-21: 成立する開幕卵落を後続チャートの札予約より優先する。
    free = list(plan['droppers'])
    # 卵落の強い札を先に、同値は将軍へのダメージが小さい札を (主力を温存)。
    plan['dropper'] = None if not free else min(free, key=lambda card: (
        -reference.egg_drop_value(card),
        reference.CARDS.get(card, {}).get('general_damage') or 0,
        SURVIVAL_RANK.get(card, len(SURVIVAL_CARDS)), CARD_IDS.get(card, 99)))
    if plan['dropper'] is not None:
        plan['egg_drop'] = _egg_drop_evidence(cur, plan['dropper'])
        plan['verdict'] = 'droppable'
    plan['egg_drop_free'] = bool(free)
    cur['egg_plan'] = plan
    return plan


def _egg_drop_tactics(mem, cur, enemy, plan=None):
    """成立する未使用の携行札を、後続チャートより先に開幕で使う。

    未知・不成立・卵なし・使用不能は発動しない。実メニューで再確認する。
    """
    plan = _egg_plan(mem, cur) if plan is None else plan
    card = plan.get('dropper')
    if not card:
        return []
    return [{'enemy': enemy, 'card': card, 'open': True, 'step': cur.get('step'),
             'tactic_id': f"eggdrop:{cur.get('step')}:{card}", 'egg_drop_only': True,
             'note': ('携行札の卵落が敵味方将軍の最大HP合計の余りを超えるため開幕に使い、'
                      '敵の卵を落として召喚を封じる')}]


def _watch_egg_drop(cur, card, hp, evidence):
    """Track an egg-loss candidate only after selecting its measured row."""
    cur.pop('egg_drop_watch', None)
    if evidence and evidence.get('drops'):
        cur['egg_drop_watch'] = {'card': card, 'hp': hp, 'value': evidence['value'],
                                 'threshold': evidence['threshold'],
                                 'max_hp_sum': evidence['max_hp_sum'],
                                 'evidence_version': 2}


def _observe_egg_drop_candidate(mem, cur):
    """Keep selection/damage evidence without declaring the enemy disarmed.

    A damage decrease can be melee, a failed selection, or an earlier hit.
    Unlike a bounded chart follow-up, skipping all egg-risk checks requires
    an actual egg-loss receipt. No such screen signature is calibrated yet.
    """
    if cur.pop('enemy_egg_dropped', None) is True:
        cur['enemy_egg_drop_expected'] = True
        _record(mem, 'battle_egg_drop_invalidated', **_battle_labels(cur),
                observed_metric={'previous_inferred_egg_drop': True,
                                 'egg_drop_confirmation': 'unclassified'},
                reason='旧版の選択とHP低下だけによる卵落確定を取り消し、敵召喚への警戒を戻す')
    watch = cur.get('egg_drop_watch')
    if not watch:
        return
    if watch.get('evidence_version') != 2:
        cur.pop('egg_drop_watch', None)  # old watchers started before selection
        return
    hp, at_open = cur.get('enemy_hp'), watch.get('hp')
    if type(hp) is not int or hp <= 0 or type(at_open) is not int or hp >= at_open:
        return
    if watch['card'] not in (cur.get('cards_selected') or []):
        return                      # 札一覧の選択に達していない＝使用の根拠がない
    cur['enemy_egg_drop_expected'] = True
    cur.pop('egg_drop_watch', None)
    _record(mem, 'battle_egg_drop_unconfirmed', **_battle_labels(cur), card=watch['card'],
            enemy=cur.get('enemy'), enemy_hp=hp, ally_hp=cur.get('ally_hp'),
            egg_drop={'value': watch['value'], 'threshold': watch['threshold'],
                      'max_hp_sum': watch['max_hp_sum']},
            expected_metric='卵落札が実際に命中すれば召喚を封じる。実使用と卵落は未確認',
            observed_metric={'enemy_hp': hp, 'hp_at_card': at_open,
                             'selected': True, 'egg_drop_confirmation': 'unclassified'},
            reason='卵落札の選択後にHP低下を観測したが、白兵ダメージと区別できないため召喚不能とは扱わない')


def _rescue_card(candidates, cur):
    """The rescue card: a visible heal first, else the best egg dropper.

    ``SURVIVAL_CARDS`` stays the damage/control fallback. A card that would
    drop this battle's egg goes before it: the enemy summon is what a
    low-HP rescue is usually racing. HPs are the battle's start readings
    (the hero's remembered full strength); without them the fixed order stands.
    """
    if not candidates:
        return None
    if 'エンジェリン' in candidates:
        return 'エンジェリン'                     # heal always goes first
    if 'キャトルミュー' in candidates:
        return 'キャトルミュー'                   # instant general kill / 224 EM / 90 boss
    if enemy_egg_triggers(cur.get('enemy')).has_egg is False:
        return candidates[0]                      # no egg to drop
    droppers = [card for card in candidates
                if (_egg_drop_evidence(cur, card) or {}).get('drops')]
    if not droppers:
        return candidates[0]
    return min(droppers, key=lambda card: (-reference.egg_drop_value(card),
                                           SURVIVAL_RANK.get(card, len(SURVIVAL_CARDS))))


BEHIND_EGG_RATIO_TENTHS = 7    # ally HP at or below 70% of the enemy's: rescue (egg) now
GENERAL_CRITICAL_RETREAT_HP = 12


def _unarmed_clash_risk(cur):
    """Check resources before a non-boss egg clash, rather than idle into it."""
    if (cur.get('planned_cards')
            or cur.get('enemy') in chart.BOSSES.values()):
        return False
    side = cur.get('side')
    defense = True if side == 'defense' else False if side == 'attack' else None
    triggers = enemy_egg_triggers(cur.get('enemy'), player_castle_defense=defense)
    return triggers.has_egg is True and triggers.clash_position is True


def _survival_needed(cur):
    hp, enemy, start = (cur.get(k) for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))
    if any(type(n) is not int or n <= 0 for n in (hp, enemy)):
        return False
    # Sword practice has no resource menu. Preserve its released A bursts.
    if (cur.get('enemy'), cur.get('ally'), start, cur.get('start_enemy_hp'), cur.get('step')) == (
            'だいじん', 'どうし', 90, 90, None):
        return False
    if _critical_survival_needed(cur):
        return True  # current danger is known; historical HP is not required
    if type(start) is not int or start <= 0:
        return False
    # Far behind the enemy from the start: the old 40%-of-start rule fired
    # only at HP ~10, after the melee had already decided the fight, and
    # generals died with an unused egg (g421 15:09 26 vs 48, 15:13 27 vs 38;
    # owner: eggs unused while dying).
    # Behind from the very start (g438 03:31: ココット 22 vs キッシュ 26 went
    # 22 -> 10 in one observation of melee before any card was used, then
    # died). The rescue (cards first, then the egg) opens before the melee.
    # Not for boss fights or battles with charted cards: their plan runs.
    # A start gap within 10% is no rescue signal for a general whose combat-
    # weighted melee is ahead (g1075 2026-10-30 08:56: ゼウス 85x13 vs the
    # eggless バーミセリ 89x12 spent ちきゅうちゃん; owner: no egg was
    # needed). g438 ココット 22 vs キッシュ 26 (85%) still opens.
    balance = _melee_balance(cur)
    ours, theirs = balance['ally_melee_score'], balance['enemy_melee_score']
    behind_start = (type(cur.get('start_enemy_hp')) is int and start < cur['start_enemy_hp']
                    and not cur.get('planned_cards') and cur.get('enemy') not in chart.BOSSES.values()
                    and not (ours is not None and theirs is not None and ours >= theirs
                             and start * 10 > cur['start_enemy_hp'] * 9))
    return (hp <= GENERAL_CRITICAL_RETREAT_HP or (hp < enemy and hp * 5 <= start * 2)
            or hp * 10 <= enemy * BEHIND_EGG_RATIO_TENTHS or behind_start
            or cur.get('card_damage_rescue_needed') is True
            or _unarmed_clash_risk(cur) or _unarmed_wall_risk(cur)
            or (not cur.get('planned_cards') and _combat_behind(cur)))


def _unarmed_wall_risk(cur):
    """Inspect initially eggless attackers before a wall-triggered summon.

    g684 Trevis66 vs Alfalfa38 carried a card but had no timed chart tactic.
    Clash=False allowed unchecked melee until Sandworm202 was already out.
    Check the real resource menu first; a carried kit is not a usable-card
    receipt. Initially eggless generals can acquire an いっぱつエッグ after
    a victory: a named positive count overrides that initial metadata.
    Initial metadata requests inspection, never proves live availability:
    only the actual menu can authorize a resource selection.
    """
    ally = GENERAL_EGGS.get('しゅじんこう' if cur.get('ally') == NAME else cur.get('ally'))
    if (cur.get('side') != 'attack' or cur.get('planned_cards') or cur.get('egg_battle')
            or cur.get('own_egg_positive_observed') is True
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('enemy') in reference.BOSS_GENERALS
            or ally is None or not (ally[0] is False or cur.get('own_egg_zero_observed') is True)):
        return False
    triggers = enemy_egg_triggers(cur.get('enemy'), player_castle_defense=False)
    return (triggers.has_egg is True and triggers.clash_position is False
            and (triggers.wall_critical is True or triggers.wall_mod4 is True))


def _survival_state(mem, cur):
    if 'survival' not in cur:
        cur['survival'] = {'opens': 0, 'menu_ticks': 0, 'cards_checked': 0,
                           'cards_attempted': [], 'egg_attempted': False}
        _record(mem, 'battle_survival', **_battle_labels(cur),
                observed_metric={'ally_hp': cur.get('ally_hp'), 'enemy_hp': cur.get('enemy_hp'),
                                 'melee_balance': _melee_balance(cur)},
                expected_metric='使用可能な切り札・たまごを確認して選択',
                reason=('携行戦術のない卵持ち敵との衝突前に、戦闘メニューで救済手段を確認'
                        if _unarmed_clash_risk(cur)
                        else '初期卵なしまたは観測残数0の攻撃将軍は、敵の壁判定召喚より先に実資源を確認'
                        if _unarmed_wall_risk(cur)
                        else 'HP・兵士数・将軍の戦闘力で不利なため、戦闘メニューで救済手段を確認'
                        if _combat_behind(cur)
                        else 'HP低下のため温存を中止し、戦闘メニューで救済手段を確認'))
    return cur['survival']


def _bound_summoner_general(mem, owner, ally, enemy):
    """Return only a valid, same-runtime general reading for this battle."""
    from ..os_layer.roster import run_identity
    battle = mem.get('battle') or {}
    reading = battle.get('summon_general_reading')
    if (owner != 'ally' or ally is None or enemy is None
            or ally.name not in reference.MONSTER_SKILLS or enemy.name not in reference.MONSTER_SKILLS
            or any(type(hp) is not int or hp <= 0 for hp in (ally.hp, enemy.hp))
            or not isinstance(reading, dict)
            or run_identity(reading.get('identity')) is None
            or run_identity(reading['identity']) != run_identity(mem.get('_run_identity'))
            or not battle.get('ally') or not battle.get('enemy')
            or (reading.get('ally'), reading.get('enemy')) != (battle['ally'], battle['enemy'])
            or reading.get('hp') != battle.get('ally_hp')):
        return None
    hp, full = reading.get('hp'), reading.get('max_hp')
    return reading if type(hp) is int and type(full) is int and 0 < hp <= full else None


def _wounded_summoner(mem, owner, ally, enemy):
    """A living owned summon still shields its measured wounded general."""
    reading = _bound_summoner_general(mem, owner, ally, enemy)
    return bool(reading and (reading['hp'] <= GENERAL_CRITICAL_RETREAT_HP
                            or reading['hp'] * 2 <= reading['max_hp']))


def _summoner_return_risk(mem, owner, ally, enemy):
    """HP-pool exposure risk, not a prediction of monster skill damage."""
    reading = _bound_summoner_general(mem, owner, ally, enemy)
    if reading is None:
        return None
    hp, full = reading['hp'], reading['max_hp']
    wounded = hp <= GENERAL_CRITICAL_RETREAT_HP or hp * 2 <= full
    if not wounded and hp * 2 > enemy.hp:
        return None
    return {'rule': 'wounded_general' if wounded else 'general_vs_monster_hp',
            'general': reading['ally'], 'general_hp': hp, 'general_max_hp': full,
            'enemy_monster': enemy.name, 'enemy_monster_hp': enemy.hp}


def _critical_survival_needed(cur):
    hp, enemy = cur.get('ally_hp'), cur.get('enemy_hp')
    return (not cur.get('card_hp_unread') and type(hp) is int
            and 0 < hp <= GENERAL_CRITICAL_RETREAT_HP
            and type(enemy) is int and enemy > 0)


GENERAL_EGG_REOPEN_LIMIT = 2


def _general_egg_rescue_recheck(mem, cur):
    """Reopen an unobserved rescue menu before a known egg holder collapses.

    Three B attempts landing in dialogue are not evidence of an empty egg.
    Only a bound, worsening HP trade grants two extra finite episodes; each
    needs further HP loss. The visible menu still owns resource selection.
    """
    from ..os_layer.roster import run_identity
    rescue = cur.get('survival')
    if (not isinstance(rescue, dict)
            or cur.get('ally') in (None, NAME, 'しゅじんこう', 'だいじん')
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('enemy') in reference.BOSS_GENERALS
            or cur.get('egg_battle') or mem.get('egg_battle')
            or cur.get('card_summon_observed') or cur.get('card_flow') or cur.get('planned_cards')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('resource_menu_observed') or cur.get('okunote_only_observed')
            or cur.get('independent') or cur.get('cards_selected')
            or rescue.get('egg_attempted') or rescue.get('egg_pending')
            or rescue.get('cards_checked') or rescue.get('cards_attempted')
            or rescue.get('conserving_melee')
            or (cur.get('hero_retreat') or {}).get('selected')
            or not _own_egg_positive(mem, cur) or _own_egg_empty(mem, cur)):
        return False
    identity = run_identity(mem.get('_run_identity'))
    hp, enemy, start, enemy_start = (cur.get(k) for k in
                                    ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    maximum, enemy_maximum = general_max_hp(cur.get('ally')), general_max_hp(cur.get('enemy'))
    scope = [identity, cur.get('ally'), cur.get('enemy'), start, enemy_start]
    if (identity is None or cur.get('melee_opening_scope') != scope
            or any(type(n) is not int or n <= 0 for n in
                   (hp, enemy, start, enemy_start, maximum, enemy_maximum))
            or not hp < start <= maximum or not enemy <= enemy_start <= enemy_maximum
            or hp >= enemy or hp * 3 > start * 2
            or hp * enemy_start >= enemy * start):
        return False
    opens, pending, ticks, episodes = (rescue.get(k, 0) for k in
        ('opens', 'pending_opens', 'menu_ticks', 'general_egg_rechecks'))
    last_hp = rescue.get('general_egg_recheck_hp')
    if (any(type(n) is not int for n in (opens, pending, ticks, episodes))
            or not 3 <= opens < 12 or pending != 3 or ticks != 0
            or not 0 <= episodes < GENERAL_EGG_REOPEN_LIMIT
            or (episodes > 0 and (type(last_hp) is not int or not hp < last_hp <= start))):
        return False
    rescue.update(general_egg_rechecks=episodes + 1, general_egg_recheck_hp=hp,
                  exhausted=False, egg_priority=True, opens=opens + 1,
                  pending_opens=1, menu_ticks=0)
    _record(mem, 'battle_general_egg_recheck', **_battle_labels(cur),
            general=cur.get('ally'), enemy=cur.get('enemy'),
            observed_metric={'ally_hp': hp, 'enemy_hp': enemy,
                             'ally_opening_hp': start, 'enemy_opening_hp': enemy_start,
                             'egg_uses': (mem.get('egg_uses') or {}).get(cur.get('ally')),
                             'episode': episodes + 1, 'limit': GENERAL_EGG_REOPEN_LIMIT,
                             'opens': opens + 1, 'menu_observed': False},
            reason='卵残数が正数の一般将軍が押し負けているため、開けなかった実メニューを有限回だけ再確認する')
    return True


HERO_SURVIVAL_REOPEN_LIMIT = 2    # extra unopened-menu episodes for a losing hero
RESCUE_REENTRY_LIMIT = HERO_SURVIVAL_REOPEN_LIMIT  # bounded summon-battle re-entry
RESCUE_REARM_LIMIT = HERO_SURVIVAL_REOPEN_LIMIT    # bounded re-arm after exhausted


def _hero_losing_melee(cur):
    """A hero melee the white trade itself proves we are losing.

    どうしの戦死はゲームオーバー（run全体の喪失）で、防衛戦には退却が無い。
    g749 08:37 どうし90 vs アテナ66（防衛戦・携行札なし・卵1）: the three
    opening B presses landed on the enemy's battle dialogue, so the resource
    menu never appeared; the hold budget then cut to ``power_mash`` and the
    hero died 54 -> 6 while アテナ's panel never left 66. The static
    combat×HP score still called the fight even (a measured 896 vs 1276 needs
    the soldiers), so the usable evidence is the measured trade: our HP pool
    has lost a third of its opening while the enemy's opening panel has not
    moved at all. 卵落・激突のリスクより、主人公の死亡のほうが重い。
    """
    if (cur.get('ally') != NAME or cur.get('egg_battle') or cur.get('card_flow')
            or cur.get('planned_cards')):
        return False
    hp, enemy, start, enemy_start = (cur.get(k) for k in
                                     ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    if any(type(n) is not int or n <= 0 for n in (hp, enemy, start, enemy_start)):
        return False
    return hp < enemy and enemy >= enemy_start and hp * 3 <= start * 2


def _melee_continuation_evidence(mem, cur):
    """An ordinary general may conserve an overwhelming current advantage.

    A scoped opening need not be above 70% of maximum HP: that percentage
    alone does not erase a measured twofold advantage in remaining force.
    Once our HP falls, require actual enemy HP/troop progress as before.
    This is a remaining-force heuristic, never a promised win or egg loss.
    """
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    if not all(isinstance(cur.get(k), str) for k in ('ally', 'enemy')):
        return None
    opening = cur.get('melee_opening_scope')
    if (not isinstance(opening, list) or len(opening) != 5
            or run_identity(opening[0]) != identity):
        return None
    hp, enemy, start, enemy_start = (cur.get(k) for k in
                                    ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    maximum = general_max_hp(cur.get('ally'))
    enemy_maximum = general_max_hp(cur.get('enemy'))
    scope = [identity, cur.get('ally'), cur.get('enemy'), start, enemy_start]
    if (identity is None or cur.get('melee_opening_scope') != scope
            or cur.get('ally') in (NAME, 'しゅじんこう') or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_flow') or cur.get('card_damage_rescue_needed')
            or any(type(n) is not int or n <= 0 for n in
                   (hp, enemy, start, enemy_start, maximum, enemy_maximum))
            or not GENERAL_CRITICAL_RETREAT_HP < hp <= start <= maximum
            or not enemy <= enemy_start <= enemy_maximum):
        return None
    balance = _melee_balance(cur)
    values = [balance[k] for k in ('ally_force', 'enemy_force', 'ally_melee_score', 'enemy_melee_score')]
    if (balance['soldiers_current'] is not True
            or any(type(n) is not int or n <= 0 for n in values)
            or values[0] <= 2 * values[1] or values[2] <= 2 * values[3]):
        return None
    # Low general HP alone must not spend an egg when both measured troop
    # count and combat stat dominate. Keep the existing twofold total-force
    # and combat-score margins, and the critical-HP floor above.
    if hp <= enemy * 2 and not (
            cur['ally_soldiers'] > cur['enemy_soldiers']
            and balance['ally_combat'] > balance['enemy_combat']):
        return None
    opening_army = cur.get('melee_opening_army')
    army_known = (isinstance(opening_army, list) and len(opening_army) == 2
                  and all(type(n) is int and 0 <= n <= 6 for n in opening_army))
    soldier_progress = army_known and cur['enemy_soldiers'] < opening_army[1]
    if hp < start and enemy >= enemy_start and not soldier_progress:
        return None
    return {'rule': 'melee_finish_after_damage' if hp < start else 'melee_advantage_at_opening',
            'ally_hp': hp, 'enemy_hp': enemy,
            'ally_opening_hp': start, 'enemy_opening_hp': enemy_start,
            'opening_soldiers': list(opening_army) if army_known else None,
            'enemy_progress': ('hp' if enemy < enemy_start else
                               'soldiers' if soldier_progress else 'not_observed'),
            'ally_max_hp': maximum, **balance}


def _healthy_melee_margin_evidence(mem, cur):
    """A scoped, healthy advantage even under pessimistic unread troop bounds.

    A resource inspection is not evidence that an egg must be spent. Unknown
    CURRENT troops range from zero to six: credit us with the lower bound
    and the enemy with the upper bound, never store either as an observation.
    Require a 3:2 combat-weighted margin, healthy HP and no losing HP trade.
    These are conservative policy thresholds, not a damage/win prediction.
    """
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    if _enemy_transformed(mem, cur):
        return None
    ally, enemy_name = cur.get('ally'), cur.get('enemy')
    if identity is None or not all(isinstance(n, str) for n in (ally, enemy_name)):
        return None
    hp, enemy, start, enemy_start = (cur.get(k) for k in
                                    ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    maximum = general_max_hp('しゅじんこう' if ally == NAME else ally)
    enemy_maximum = general_max_hp(enemy_name)
    if (cur.get('melee_opening_scope') != [identity, ally, enemy_name, start, enemy_start]
            or cur.get('card_soldiers_current') is not True
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed')
            or enemy_name in chart.BOSSES.values() or enemy_name in reference.BOSS_GENERALS
            or cur.get('card_flow') or cur.get('planned_cards') or cur.get('card_damage_rescue_needed')
            or _own_egg_empty(mem, cur)
            or any(type(n) is not int or n <= 0 for n in
                   (hp, enemy, start, enemy_start, maximum, enemy_maximum))
            or not GENERAL_CRITICAL_RETREAT_HP < hp <= start <= maximum
            or not enemy <= enemy_start <= enemy_maximum
            or hp <= enemy or hp * 10 <= maximum * BEHIND_EGG_RATIO_TENTHS):
        return None
    # A high starting score must not excuse a one-sided or losing live trade.
    if hp < start and (enemy >= enemy_start
                       or (start - hp) * enemy_start > (enemy_start - enemy) * start):
        return None
    ours, theirs = cur.get('ally_soldiers'), cur.get('enemy_soldiers')
    if any(n is not None and (type(n) is not int or not 0 <= n <= 6)
           for n in (ours, theirs)):
        return None
    balance = _melee_balance(cur)
    ac, ec = balance['ally_combat'], balance['enemy_combat']
    if any(type(n) is not int or not 1 <= n <= 15 for n in (ac, ec)):
        return None
    own_min, enemy_max = (ours if ours is not None else 0), (theirs if theirs is not None else 6)
    own_force, enemy_force = hp + 10 * own_min, enemy + 10 * enemy_max
    own_score, enemy_score = own_force * ac, enemy_force * ec
    if (own_force * 10 <= enemy_force * BEHIND_EGG_RATIO_TENTHS
            or own_score * 2 <= enemy_score * 3):
        return None
    return {'rule': 'healthy_melee_margin', 'ally_hp': hp, 'enemy_hp': enemy,
            'ally_max_hp': maximum, 'ally_opening_hp': start, 'enemy_opening_hp': enemy_start,
            'soldiers_observed': [ours, theirs], 'ally_soldiers_min': own_min,
            'enemy_soldiers_max': enemy_max, 'ally_force_min': own_force,
            'enemy_force_max': enemy_force, 'ally_melee_score_min': own_score,
            'enemy_melee_score_max': enemy_score, 'score_margin': [3, 2],
            'army_basis': 'observed' if ours is not None and theirs is not None else 'pessimistic_bounds'}


def _enemy_transformed(mem, cur):
    """Detect that the enemy used an egg / transformed during this battle.

    The opening melee scope pins the enemy name and HP. A different enemy
    name, an enemy HP that recovered above its opening watermark, or a
    recorded transform observation means the opening conservation premise no
    longer applies.
    """
    if not isinstance(cur, dict):
        return False
    if cur.get('enemy_transform_observed') is True:
        return True
    scope = cur.get('melee_opening_scope')
    if isinstance(scope, (list, tuple)) and len(scope) >= 5:
        opening_enemy = scope[2]
        opening_enemy_hp = scope[4]
        enemy = cur.get('enemy')
        enemy_hp = cur.get('enemy_hp')
        if (isinstance(enemy, str) and isinstance(opening_enemy, str)
                and enemy != opening_enemy):
            return True
        if (type(enemy_hp) is int and type(opening_enemy_hp) is int
                and enemy_hp > opening_enemy_hp):
            return True
    return False


def _hold_melee_egg(mem, cur, labels):
    """Preserve a healthy advantage or a scoped, overwhelming ordinary melee.

    Compare current HP pools weighted by each named general's combat stat.
    Unknown/old counts or stats do not prove advantage; summons/critical HP win.
    """
    if (_enemy_transformed(mem, cur)
            or _critical_survival_needed(cur)
            or _own_egg_empty(mem, cur)
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_soldiers_current') is not True
            or cur.get('egg_battle') or mem.get('egg_battle')
            or cur.get('card_summon_observed')
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('enemy') in reference.BOSS_GENERALS):
        return False
    if _healthy_melee_margin_evidence(mem, cur) is not None:
        return True
    hp, enemy = cur.get('ally_hp'), cur.get('enemy_hp')
    ours, theirs = cur.get('ally_soldiers'), cur.get('enemy_soldiers')
    maximum = max((n for n in (general_max_hp('しゅじんこう' if cur.get('ally') == NAME
                                             else cur.get('ally')),
                              cur.get('ref_ally_hp'), cur.get('start_ally_hp'))
                   if type(n) is int and n > 0), default=None)
    balance = _melee_balance(cur)
    scores = balance['ally_melee_score'], balance['enemy_melee_score']
    healthy = (all(type(n) is int and n > 0 for n in (hp, enemy, maximum, *scores))
            and all(type(n) is int and 0 <= n <= 6 for n in (ours, theirs))
            and hp * 10 > maximum * BEHIND_EGG_RATIO_TENTHS
            and (hp + 10 * ours) * 10 > (enemy + 10 * theirs) * BEHIND_EGG_RATIO_TENTHS
            and scores[0] > scores[1])
    if 'きりふだ' not in labels and cur.get('ally') in (NAME, 'しゅじんこう'):
        # No cards is not itself a reason to spend the hero's last egg.
        # Keep his full-health/game-over protection and demand a larger
        # current margin than for an ordinary healthy general.
        return (healthy and hp <= maximum and hp > enemy * 2
                and hp + 10 * ours > 2 * (enemy + 10 * theirs)
                and scores[0] > 2 * scores[1]
                and not cur.get('card_flow') and not cur.get('card_damage_rescue_needed')
                and not _hero_losing_melee(cur))
    return healthy or _melee_continuation_evidence(mem, cur) is not None


def _conserved_army_recheck(mem, cur):
    """Briefly reobserve unread troops after a real healthy conservation menu.

    g745 Coco/Semillon1436 conserved; 1446's clipped army invalidated both
    counts and immediately reopened the egg. This grants two observations
    per battle, not stale troop counts or permission to continue melee.
    HP danger, a summon, a pending tactic or a spent egg bypasses the wait.
    """
    from ..os_layer.roster import run_identity
    rescue = cur.get('survival') or {}
    identity = run_identity(mem.get('_run_identity'))
    hp, enemy = cur.get('ally_hp'), cur.get('enemy_hp')
    maximum = general_max_hp('しゅじんこう' if cur.get('ally') == NAME else cur.get('ally'))
    balance = _melee_balance(cur)
    ac, ec = balance.get('ally_combat'), balance.get('enemy_combat')
    if (identity is None or rescue.get('conserving_melee') is not True
            or rescue.get('exhausted') or rescue.get('egg_attempted')
            or _critical_survival_needed(cur) or _own_egg_empty(mem, cur)
            or cur.get('card_flow') or cur.get('command_open_pending')
            or cur.get('card_damage_rescue_needed')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('card_soldiers_current') is not True
            or any(cur.get(k) is not None and
                   (type(cur.get(k)) is not int or not 0 <= cur[k] <= 6)
                   for k in ('ally_soldiers', 'enemy_soldiers'))
            or all(type(cur.get(k)) is int for k in ('ally_soldiers', 'enemy_soldiers'))
            or any(type(n) is not int or n <= 0 for n in (hp, enemy, maximum, ac, ec))
            or hp > maximum or hp * 10 <= maximum * BEHIND_EGG_RATIO_TENTHS
            or hp * ac <= enemy * ec):
        return False
    scope = [identity, cur.get('ally'), cur.get('enemy'),
             cur.get('start_ally_hp'), cur.get('start_enemy_hp')]
    claim = cur.get('conserved_army_recheck')
    if claim is None:
        claim = cur['conserved_army_recheck'] = {'scope': scope, 'observations': 0}
    if (not isinstance(claim, dict) or claim.get('scope') != scope
            or type(claim.get('observations')) is not int
            or not 0 <= claim['observations'] < 2):
        cur['conserved_army_recheck'] = {'scope': scope, 'observations': 2}
        return False  # corrupt/foreign/exhausted claims never refill on animation
    claim['observations'] += 1
    return True


def _critical_safe_card(mem, cur, rescue, *, for_retreat=False):
    # Only the observed list, not a chart kit, licenses delaying an active
    # egg for a heal/proven finishing blow. Selection is still not a use.
    if for_retreat and (rescue.get('cards_checked', 0) >= 3
            or rescue.get('cards_exhausted')
            or (rescue.get('exhausted') and rescue.get('critical_menu_recheck'))):
        return False  # stale inventory must not suppress retreat indefinitely
    names = rescue.get('listed_cards') or []
    for card in names:
        if (card not in SURVIVAL_CARDS or card in rescue.get('cards_attempted', [])
                or card in cur.get('cards_selected', [])):
            continue
        gate = _card_damage_gate(mem, cur, card, listed_cards=names)
        if gate['allowed'] and (gate['role'] == 'single_card_lethal'
                or (gate['role'] == 'healing_not_a_kill' and _card_healing_needed(cur))):
            return True
    return False


# Monthly duel events: 武芸者の挑戦 (「…ごあいてねがえぬか?」), はたしあい and
# 裏切り. Owner (2026-10-11): these fights barely used the blue gauge.
DUEL_EVENT_TOKENS = ('ごあいて', 'はたしあい', 'うらぎ', 'ぶげいしゃ', 'いっきうち')
DUEL_EVENT_WINDOW = 40          # observations (~60 s) from the event text to the battle


def _bind_event_duel(mem, cur):
    """A battle right after a duel event, outside any castle attack/defense.

    gcgx battle.html: ぶつかった際にバーが青くなるので、その間にAを連打すると
    踏ん張って前進しやすくなる. The egg-safe hold sends nothing at exactly that
    clash, so a duel held through its blue windows (owner 2026-10-11).
    """
    event = mem.get('duel_event')
    if (not isinstance(event, dict) or cur.get('side') or cur.get('castle')
            or int(mem.get('tick') or 0) - int(event.get('tick') or 0) > DUEL_EVENT_WINDOW):
        return
    cur['event_duel'] = True
    mem.pop('duel_event', None)
    _record(mem, 'battle_event_duel', **_battle_labels(cur),
            observed_metric={'event_text': event.get('text')},
            reason='月一イベントの一騎打ちのため、卵の激突保留をせず青ゲージでA連打する')


def _tactic_enemy_ok(tactic, enemy, cur):
    """A tactic applies to this enemy, or to the defender standing in for it."""
    if tactic.get('enemy') in (None, enemy):
        return True
    sub = cur.get('tactic_substitute') if isinstance(cur, dict) else None
    return bool(tactic.get('open') and isinstance(sub, dict)
                and sub.get('expected') == tactic.get('enemy') and sub.get('actual') == enemy)


def _bind_substitute_defender(mem, cur, b):
    """Apply a step's opening cards when another, at-least-as-strong general defends.

    g684 12:01: 1-C2 sends ココット (max 24) with ダイチスイム×2・ブラッキー for
    the chart's opening against タピオカ (50). ソーピニヨン (48) defended
    スペンソニア instead, the enemy-keyed tactic never matched, no card was
    used and ココット died 24->18->0. The kit (general damage 6+6+22, gcgx
    kirihuda) is the chart's answer to that matchup, not to the name.
    """
    step = cur.get('step')
    if not step or not b.enemy or type(b.enemy_hp) is not int or type(b.ally_hp) is not int:
        return
    tactics = [t for t in _tactics(mem, step) if t.get('step') == step]
    if any(t.get('enemy') in (None, b.enemy) for t in tactics):
        return
    expected = {t['enemy'] for t in tactics if t.get('open') and t.get('enemy')}
    # Only a clear mismatch (g684: 48 vs 24). A near-even substitute keeps
    # the egg-drop opener and melee (キッシュ 26 vs ココット 24).
    if len(expected) != 1 or b.enemy_hp * 2 < b.ally_hp * 3:
        return
    cur['tactic_substitute'] = {'expected': next(iter(expected)), 'actual': b.enemy}
    _record(mem, 'tactic_substitute_defender', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
            observed_metric={'expected': cur['tactic_substitute']['expected'],
                             'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp},
            reason='チャート想定と別の将軍が守るが、HPが自軍の1.5倍以上のため同じ手順の開幕切り札を使う')


def _egg_before_retreat(mem, cur, *, menu=False):
    """Inspect this actor's untried positive egg before fleeing ordinary melee.

    A count requests the menu; only its usable row licenses selection.
    Exhausted/failed attempts and summons keep the existing retreat route.
    """
    rescue = cur.get('survival') or {}
    # A real conservation menu already inspected the egg. Recheck current
    # strength before reopening it; danger cancels this permission.
    if (rescue.get('conserving_melee') is True
            and _hold_melee_egg(mem, cur, set(rescue.get('conserved_labels') or ()))):
        return False
    return (not cur.get('egg_battle') and _survival_needed(cur)
            and (_critical_survival_needed(cur) or not cur.get('planned_cards'))
            and _own_egg_positive(mem, cur) and not _own_egg_empty(mem, cur)
            and not rescue.get('egg_attempted') and not rescue.get('exhausted')
            and (menu or (int(rescue.get('opens') or 0) < 12
                          and int(rescue.get('pending_opens') or 0) < 3))
            and not (cur.get('hero_retreat') or {}).get('selected'))


def _egg_rescue_pending(cur):
    """Allow the one selected egg a bounded fade without toggling its menu."""
    rescue = cur.get('survival') or {}
    if not (rescue.get('egg_priority') and rescue.get('egg_pending')):
        return False
    rescue['egg_wait'] = int(rescue.get('egg_wait') or 0) + 1
    if rescue['egg_wait'] <= 3:
        return True
    rescue['egg_pending'] = False
    return False


def _default_retreat_needed(cur):
    """Any general's retreat threshold in an attack battle.

    The hero keeps his remembered full-strength reference; another general
    uses this battle's start HP (g460 16:11: ココット 24 vs タピオカ 50 spent
    the whole fight in card menus, fell 24->12->0 and died with no retreat).
    """
    if (cur.get('ally') and cur.get('egg_battle') and cur.get('egg_retreat_needed')
            and all(type(cur.get(k)) is int and cur[k] > 0
                    for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))):
        return True
    if not cur.get('ally') or not _survival_needed(cur):
        return False
    if _critical_survival_needed(cur):
        return True
    hp, enemy, start = (cur.get(k) for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))
    ref = (max(start, int(cur.get('ref_ally_hp') or 0)) if cur.get('ally') == NAME else start)
    rescue = cur.get('survival') or {}
    if ((((_unarmed_clash_risk(cur) or _unarmed_wall_risk(cur))
          and not cur.get('card_damage_rescue_needed'))
         or (cur.get('card_damage_rescue_needed') and _card_damage_probe_exhausted(cur)))
            and rescue.get('exhausted')
            and not rescue.get('cards_uncertain')
            and not (_healthy_melee_advantage(cur) or _pre_summon_hp_advantage(cur)
                    or _full_hp_melee_advantage(cur))):
        return True  # no observed rescue remains; retreat before the summon
    if cur.get('egg_battle'):
        # An enemy summon we cannot answer (no egg left, no cards) is not a
        # winnable melee: retreat before the general dies (owner 2026-09-29;
        # g460 16:46: ヴィーナス 82 vs アルファルファ 38 summoned and killed
        # her while the bot chose こうげき and the retreat came too late).
        return hp <= GENERAL_CRITICAL_RETREAT_HP or hp * 2 <= enemy or hp * 2 <= ref
    return hp <= GENERAL_CRITICAL_RETREAT_HP or (hp < enemy and hp * 4 <= ref)


def _hero_retreat_needed(cur):
    """Value-aware ordinary retreat with the existing special-case guards."""
    from .retreat import retreat_needed
    return retreat_needed(cur)


def _pre_summon_hp_advantage(cur):
    """Owner policy: a clear HP lead can keep melee after a resource probe.

    Reverse the existing 70%-HP disadvantage boundary. This is a willingness
    to continue before a summon, not a combat estimate or a promise of a win.
    Unknown armies stay unknown; no soldier count is needed or manufactured.
    """
    from .retreat import _resources_exhausted, _strength, retreat_profile, STRONG_RETREAT_HP

    profile, enemy_stats = retreat_profile(cur), _strength(cur.get('enemy'))
    if (profile['class'] in ('hero', 'unknown') or enemy_stats is None
            or cur.get('side') != 'attack'
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('enemy') in reference.BOSS_GENERALS
            or cur.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_damage_rescue_needed') or cur.get('event_duel')
            or (cur.get('hero_retreat') or {}).get('selected')
            or not _resources_exhausted(cur)):
        return False
    hp, enemy = cur.get('ally_hp'), cur.get('enemy_hp')
    floor = STRONG_RETREAT_HP if profile['class'] == 'strong' else GENERAL_CRITICAL_RETREAT_HP
    return (type(hp) is int and floor < hp <= profile['max_hp']
            and type(enemy) is int and 0 < enemy <= enemy_stats[0]
            and enemy * 10 <= hp * BEHIND_EGG_RATIO_TENTHS)


def _healthy_melee_advantage(cur):
    """A current ordinary panel can refute resource exhaustion as defeat.

    Do not use remembered HP alone, a monster panel, unknown soldiers or a
    hero's advantage to relax the hero's game-over protection.
    """
    if (cur.get('ally') == NAME or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_soldiers_current') is not True):
        return False
    hp, enemy, start = (cur.get(k) for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))
    ours, theirs = cur.get('ally_soldiers'), cur.get('enemy_soldiers')
    balance = _melee_balance(cur)
    scores = balance['ally_melee_score'], balance['enemy_melee_score']
    return (all(type(n) is int and n > 0 for n in (hp, enemy, start, *scores))
            # The native g713 Zeus frame has seven silhouettes (the general
            # can be counted too). Keep its old bounded retreat guard, but
            # _melee_balance never credits a seven-count as seven soldiers.
            and all(type(n) is int and 0 <= n <= 7 for n in (ours, theirs))
            and hp + 10 * ours > 2 * (enemy + 10 * theirs)
            and scores[0] > 2 * scores[1]
            and hp > GENERAL_CRITICAL_RETREAT_HP
            and hp > enemy * 2 and hp * 10 > start * 7)


# Measured B-to-menu latency in production (g1059-g1075, n=47): median
# 1.9 s, p90 2.8 s, max 5.0 s, at ~0.65 s per battle observation. Two waits
# (~1.3 s) re-pressed B while the box was still sliding in and closed it: 34
# of 81 openings failed and generals died with eggs and cards unused (g1075
# ドンダム 28->2 HP, 2026-10-28).
COMMAND_OPEN_WAITS = 4


def _full_hp_melee_advantage(cur):
    if (cur.get('ally') == NAME or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_soldiers_current') is not True):
        return False
    hp, enemy, start = (cur.get(k) for k in ('ally_hp', 'enemy_hp', 'start_ally_hp'))
    ours, theirs = cur.get('ally_soldiers'), cur.get('enemy_soldiers')
    balance = _melee_balance(cur)
    scores = balance['ally_melee_score'], balance['enemy_melee_score']
    max_hp = general_max_hp('しゅじんこう' if cur.get('ally') == NAME else cur.get('ally'))
    return (all(type(n) is int and n > 0 for n in (hp, enemy, start, *scores))
            and all(type(n) is int and 0 <= n <= 7 for n in (ours, theirs))
            and type(max_hp) is int and hp == max_hp
            and hp > GENERAL_CRITICAL_RETREAT_HP
            and hp > enemy
            and hp * 10 > start * 7
            and scores[0] > scores[1])


# (aliases removed; callers use _full_hp_melee_advantage directly)


def _command_open_wait(mem, cur):
    pending = cur.get('command_open_pending')
    if not pending:
        return None
    owner_flow = cur.get({'survival': 'survival', 'retreat': 'hero_retreat',
                          'defender_egg': 'okunote_egg_preempt'}.get(pending.get('owner'), '')) or {}
    if owner_flow.get('exhausted') or owner_flow.get('unavailable'):
        cur.pop('command_open_pending', None)
        return None
    # A complete HP panel can survive while the command box slides in.
    # Repeated B there can close the menu just requested. Current HP is still
    # read before this short wait, and the old opening budgets are retained.
    pending['waits'] = pending.get('waits', 0) + 1
    if pending['waits'] > COMMAND_OPEN_WAITS:
        cur.pop('command_open_pending', None)
        return None
    _record(mem, 'battle_command_open_wait', **_battle_labels(cur),
            observed_metric={'owner': pending['owner'], 'waits': pending['waits'],
                             'ally_hp': cur.get('ally_hp'), 'enemy_hp': cur.get('enemy_hp')},
            reason='戦闘メニューの表示を短く再観測し、未確認の再BやAを送らない')
    return [{'type': 'wait', 'ms': 50}]



def _available_rare_tactic(mem, cur):
    return next((t for t in _tactics(mem, cur.get('step'))
                 if t['card'] == 'キャトルミュー' and t.get('step') == cur.get('step')
                 and t.get('tactic_id') not in cur.get('tactics_done', [])
                 and (not t.get('boss_only') or _boss_tactics_allowed(mem, cur))), None)


OKUNOTE_MAX_SELF_DAMAGE = 88  # gcgx: ヤケクソ at castle Lv1; higher levels reduce it
# A non-finishing okunote is drawn again before the enemy summons (owner
# 2026-10-30). gcgx okunote.html: each draw shows 3 of 16 random choices and
# only the もどき (10..125) or だいじんアタック reliably damage the general.
OKUNOTE_REDRAW_LIMIT = 4         # draws per fight, the first one included
OKUNOTE_REDRAW_HP_FLOOR = 48     # しんだフリ at Lv1: a best-of-3 this bad is ~5%
EGG_WALL_DAMAGE = 4          # gcgx battle.html: a hit against the edge costs 4 HP
DEFENDER_EGG_OPEN_LIMIT = 3


def _defender_egg_window(mem, cur, *, menu_open=False):
    """A measured eggless defender's deadline before the next enemy summon.

    gcgx ai.html: every ordinary attacker of our castle uses thought 1,
    regardless of its roster thought. After wall damage, living HP < chapter+3
    triggers its egg; contact at internal X <= chapter*4+32 can trigger earlier.
    No calibrated front X or egg-loss receipt exists, so neither a low enemy
    HP nor a selected dropper proves a safe finishing blow. Unknown generals,
    bosses and unobserved resource availability do not get an invented rule.
    A known castle name and observed defense suffice here: no navigation uses
    this decision, so later chapters do not need guessed map coordinates.

    menu_open: the rescue menu on screen now shows おくのて as the only usable
    command (#2989). g1075 2026-10-29/30: that menu was open about 1-3 s
    into defenses that waited for these deadlines; where measured, the enemy
    summoned 10-28 s in, mostly far above the HP window. 8 such defenses
    were lost, while all 6 that selected おくのて before any summon were
    won. A non-hero defender takes it from the open menu; HP is then no
    lower than at any later deadline, so the self-damage lottery is not
    worse. The hero keeps the HP and position deadlines.
    """
    chapter = mem.get('chapter')
    guard = cur.get('okunote_egg_preempt') or {}
    if (type(chapter) is not int or chapter not in range(1, 13)
            or cur.get('side') != 'defense'
            or cur.get('castle') not in (*chart.castles(chapter), *chart.CASTLE_NAMES.get(chapter, ()))
            or cur.get('ally') in (None, 'だいじん') or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_flow')
            or not cur.get('okunote_only_observed')
            or guard.get('selected') or guard.get('exhausted') or guard.get('unavailable')):
        return None
    triggers = enemy_egg_triggers(cur.get('enemy'), player_castle_defense=True)
    if triggers.has_egg is not True or triggers.wall_critical is not True:
        return None
    hp, enemy, start, enemy_start = (cur.get(k) for k in
        ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    ally_max = general_max_hp('しゅじんこう' if cur.get('ally') == NAME else cur.get('ally'))
    enemy_max = general_max_hp(cur.get('enemy'))
    if (any(type(n) is not int or n <= 0 for n in (hp, enemy, start, enemy_start, ally_max, enemy_max))
            or max(hp, start) > ally_max or max(enemy, enemy_start) > enemy_max):
        return None
    observed_drop = cur.get('defender_enemy_hp_drop')
    observed_drop = observed_drop if type(observed_drop) is int and observed_drop > 0 else 0
    # Leave one wall hit in addition to the largest observed inter-frame loss
    # for capture/input/menu latency. This cannot bound an unseen X trigger.
    margin = EGG_WALL_DAMAGE + max(EGG_WALL_DAMAGE, observed_drop)
    critical_hp = chapter + 2
    holds = int(cur.get('melee_holds') or 0)
    hp_budget = max(MELEE_HP_HOLD_FLOOR, start // MELEE_HP_HOLD_DIVISOR)
    position_guard_exhausted = (bool(cur.get('melee_forced')) or holds >= MELEE_HOLD_LIMIT
                                or start - hp >= hp_budget)
    # g1075 2026-10-30 08:59 アルカ: キール's first draw (いあつする/うそなき/
    # しんだフリ) left リーキ at 30; back in melee it fell 30→24→14→8 in 5 s
    # and the window's reopening lost the race to its egg. A drawn,
    # non-finishing okunote is drawn again at once while HP allows.
    draws = cur.get('okunote_draws') or 0
    redraw = (0 < draws < OKUNOTE_REDRAW_LIMIT and hp > OKUNOTE_REDRAW_HP_FLOOR
              and cur.get('ally') not in (NAME, 'しゅじんこう'))
    reason = ('wall_hp_window' if enemy <= critical_hp + margin
              else 'okunote_redraw' if redraw
              else 'unmeasured_clash_guard_exhausted' if position_guard_exhausted
              else 'menu_open_contact_unmeasured'
              if menu_open and cur.get('ally') not in (NAME, 'しゅじんこう') else None)
    if guard.get('reason'):
        reason = guard['reason']  # a menu opening keeps its chosen rescue route
    return {'thought': 1, 'thought_basis': 'player_castle_defense',
            'enemy_egg_basis': 'static_general_data', 'own_egg_usable': False,
            'resources_basis': 'observed_okunote_only', 'enemy_hp': enemy, 'ally_hp': hp,
            'wall_critical_hp': critical_hp, 'wall_damage': EGG_WALL_DAMAGE,
            'observed_hp_drop': observed_drop, 'input_margin_hp': margin,
            'open_at_hp': critical_hp + margin,
            'clash_internal_x_threshold': chapter * 4 + 32, 'clash_position_measured': False,
            'hold_count': holds, 'hold_hp_bled': start - hp, 'hold_hp_budget': hp_budget,
            'okunote_draws': draws, 'reason': reason, 'due': reason is not None,
            'short_burst': enemy <= critical_hp + 2 * margin}


def _defender_last_resort(mem, cur):
    """A losing non-hero human defender may take the irreversible last chance.

    HP defeat is not certain death; neither is the lottery safe. This only
    replaces repeated losing melee after *measured* resource unavailability.
    Boss kits, the hero, uncertain locations and healthy fights stay excluded.
    """
    if (cur.get('ally') in (None, NAME, 'しゅじんこう', 'だいじん')
            or cur.get('side') != 'defense'
            or cur.get('castle') not in chart.castles(mem.get('chapter') or 0)
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('planned_cards') or cur.get('card_flow') or cur.get('egg_battle')
            or cur.get('okunote_last_resort_selected')
            or (cur.get('okunote_egg_preempt') or {}).get('selected')
            or not cur.get('okunote_only_observed')):
        return False
    hp, enemy, start, enemy_start = (cur.get(k) for k in
        ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    ally_max = general_max_hp(cur.get('ally'))
    enemy_max = general_max_hp(cur.get('enemy'))
    if any(type(v) is not int or v <= 0 for v in
           (hp, enemy, start, enemy_start, ally_max, enemy_max)):
        return False
    # #2864: the 70% melee rule that opened this rescue menu already measures
    # a losing fight; waiting for HP to halve came too late. g1075 10-30 11:04
    # デュオニソス 28/54 vs アンディーブ 36 (228 vs 528 with soldiers) declined
    # here, the one recheck at HP 14 never reached the menu, and フヌイヌム
    # fell. The lottery's table self-damage did not show on the panel either:
    # 15 g1075 picks of negative choices moved it 0..14, しんだフリ (-48)
    # at HP 8 left 8.
    return (hp <= start <= ally_max and enemy <= enemy_start <= enemy_max
            and enemy * 5 >= enemy_start * 4
            and (hp * 2 <= start or _combat_behind(cur)))


def _defender_summon_last_resort(mem, cur, reading):
    """The current exposed defender can take the same risky last chance.

    Monster HP is kept separate from both generals' ordinary HP. This uses
    a current bound reading, never the stale opponent HP from before summon.
    """
    if (not reading or not cur.get('okunote_only_observed')
            or (cur.get('side') != 'defense' and not _own_egg_empty(mem, cur))
            or cur.get('ally') in (None, 'だいじん')
            or cur.get('castle') not in chart.castles(mem.get('chapter') or 0)
            or cur.get('enemy') in chart.BOSSES.values() or cur.get('planned_cards') or cur.get('card_flow')):
        return False
    hp, enemy = reading.get('ally_hp'), reading.get('enemy_monster_hp')
    starts = (cur.get('start_ally_hp'), cur.get('start_enemy_hp'))
    return (type(hp) is int and type(enemy) is int and 0 < hp <= OKUNOTE_MAX_SELF_DAMAGE
            and all(type(v) is int and v > 0 for v in starts)
            and type(general_max_hp(cur.get('enemy'))) is int
            and 0 < enemy <= 999 and reading.get('enemy_monster') in reference.MONSTER_SKILLS
            and (reading['enemy_monster'] == 'ダークエルフ' or hp * 10 <= enemy * BEHIND_EGG_RATIO_TENTHS))


def _hold_general_loss_metric(mem):
    """HP defeat is not proof of death; invalidate old inferred counters once.

    No death/roster-loss observer exists yet. Preserve HP battle counters and
    original JSONL history, but mark both current and archived in-memory loss
    totals unknown on the next observation, even if the old total was zero.
    """
    for scope in ('stats', 'previous_stats'):
        old_stats = mem.get(scope)
        if not isinstance(old_stats, dict):
            continue
        previous = old_stats.get('generals_lost')
        mem[scope] = {**old_stats, 'generals_lost': None}
        if previous is not None:
            bounded_previous = previous if type(previous) is int and 0 <= previous <= 10**6 else None
            _record(mem, 'metric_invalidated', metric='generals_lost', metric_scope=scope,
                    expected_metric='将軍の死亡または喪失を直接確認する証拠',
                    observed_metric={'previous_inferred_count': bounded_previous,
                                     'generals_lost': None, 'status': 'unclassified'},
                    reason='HP敗北から将軍喪失は確定できず、喪失観測器がないため旧集計を未分類化')


HERO_RECALL_RATIO_TENTHS = 4   # hero HP at or below 40% of his full strength after a win
HERO_RECALL_MIN_HP = 20        # and never below this when his full strength is unknown


def _rescue_rearm(mem, cur, context):
    """Bounded re-arm of an exhausted rescue state on tide change."""
    if not isinstance(cur, dict):
        return False
    rescue = cur.get('survival') or {}
    if not rescue.get('exhausted'):
        return False
    count = int(cur.get('rescue_rearm_count') or 0)
    if count >= RESCUE_REARM_LIMIT:
        return False
    context = context or {}
    hp = cur.get('ally_hp')
    prev = cur.get('_rescue_eval_hp')
    # Record HP at the exhausted latch on first evaluation so a later tide
    # change (HP drop) can be detected (design §2.2 ①).
    if prev is None and type(hp) is int:
        cur['_rescue_eval_hp'] = hp
        return False
    rearm = (type(prev) is int and type(hp) is int
             and prev - hp >= max(1, GENERAL_CRITICAL_RETREAT_HP // 2))
    if not rearm:
        return False
    rescue['exhausted'] = False
    cur['rescue_rearm_count'] = count + 1
    cur['_rescue_eval_hp'] = hp
    try:
        _record(mem, 'battle_rescue_rearmed', **_battle_labels(cur),
                reason='tide change re-armed exhausted rescue',
                observed_metric={'hp': hp, 'previous_hp': prev,
                                'episode': count + 1, 'limit': RESCUE_REARM_LIMIT})
    except Exception:
        pass
    return True

def _hero_marching(mem):
    now = int(mem.get('tick') or 0)
    return [step for step, s in (mem.get('sorties') or {}).items()
            if s.get('general') == NAME and s.get('status') in ('en_route', 'launched_unconfirmed')
            and s.get('tick') is not None and now - int(s['tick']) < SORTIE_BUSY_TICKS]


def _own_egg_empty(mem, battle):
    """Only this named general's observed integer zero excludes its egg.

    A missing or unread count is unknown. Selection alone is not a use
    receipt, so neither this gate nor _egg_recheck invents a decrement.
    """
    ally = battle.get('ally') if isinstance(battle, dict) else None
    uses = observed_uses((mem.get('egg_uses') or {}).get(ally)) if ally else None
    if uses is not None and uses > 0:
        if ally in (mem.get('egg_zero_owners') or []):
            mem['egg_zero_owners'] = [n for n in mem['egg_zero_owners'] if n != ally]
        return False
    if ally and ally in (mem.get('egg_zero_owners') or []):
        return True
    if uses is None and ally:
        # A bulk recovery/unknown reading does not prove this actor has uses.
        # A later named positive quantity remains the only release here.
        uses = ((mem.get('house_eggs') or {}).get(ally) or {}).get('uses')
    return type(uses) is int and uses == 0


def _egg_quantities_unknown(mem):
    """An aggregate receipt invalidates counts but cannot release a zero."""
    counts = mem.get('egg_uses') or {}
    blocked = {n for n in mem.get('egg_zero_owners') or []
               if observed_uses(counts.get(n)) in (None, 0)}
    blocked.update(n for n, value in counts.items() if type(value) is int and value == 0)
    mem['egg_zero_owners'] = sorted(blocked)[:32]
    mem['egg_uses'] = {}


def _own_egg_positive(mem, battle):
    """A named, observed 1..5 count overrides initially eggless metadata.

    No selection or victory infers an acquisition/count. This reads the
    same named observation ledger as the zero-count guard and never alters it.
    """
    ally = battle.get('ally') if isinstance(battle, dict) else None
    if ally in (mem.get('egg_recheck') or []):
        return False  # a selection dirties the old count until this owner is read
    uses = observed_uses((mem.get('egg_uses') or {}).get(ally)) if ally else None
    return uses is not None and uses > 0


def _own_egg_needed(mem, battle, general_reading=None):
    """自軍の卵の必要性を現在HP・兵士数・両将軍の戦闘値で判定する。

    正典 (docs/hanjuku_script_bot.md ⑥) は「HP不利かつチャートに切り札指示
    なし → use_egg」。合戦力は gcgx battle.html「兵士は1人ずつHP10を持っている」
    に従う HP + 10×兵士へ、両将軍本人の戦闘値をそれぞれ掛ける。
    判断用の近似スコアであり、ゲームのダメージ式や勝利保証ではない。
    兵士未読は両側ともHPのみ、戦闘値不明は温存を許可しない。
    加えて自軍のHPが最大の7割を超えていること（`general_reading`）も要る。
    オーナー確認 (2026-10-03): 30/82 のように自軍が大破していれば、敵将軍の
    絶対値HPが低くても勝てそうにはならず卵を使う。読み取れない時も温存しない。
    強い将軍 (shogun.html の評・ボス) は常に要る。HP不明でも要る (従来どおり)。
    """
    evidence = {'strong_general': None, 'strong_rule': None, 'ally_hp': None,
                'ally_max_hp': None,
                'enemy_hp': None, 'ally_soldiers': None, 'enemy_soldiers': None,
                'ally_force': None, 'enemy_force': None, 'healthy': None,
                'ratio_tenths': BEHIND_EGG_RATIO_TENTHS, 'rule': 'unknown_battle'}
    if not isinstance(battle, dict):
        return True, evidence
    balance = _melee_balance(battle)
    combat_known = all(type(balance[side + '_combat']) is int for side in ('ally', 'enemy'))
    strong, strong_evidence = (_strong_enemy(mem, battle) if combat_known else
                               (False, {'rule': 'unknown'}))
    if battle.get('enemy') in chart.BOSSES.values():
        strong, strong_evidence = True, {**strong_evidence, 'rule': 'boss'}
    reading = general_reading if isinstance(general_reading, (tuple, list)) else None
    healthy = (bool(reading) and len(reading) == 2
               and type(reading[0]) is int and type(reading[1]) is int and reading[1] > 0
               and reading[0] * 10 > reading[1] * BEHIND_EGG_RATIO_TENTHS)
    ally_hp, enemy_hp = battle.get('ally_hp'), battle.get('enemy_hp')
    ally_soldiers, enemy_soldiers = battle.get('ally_soldiers'), battle.get('enemy_soldiers')
    # Use the same current-panel evidence as learned tactics (ctx3). A
    # partial/foreign panel invalidates its marker but retains old counts.
    # Missing or invalid armies fall back to HP only, never one-sided credit.
    measured = balance['soldiers_current']
    ally_force, enemy_force = balance['ally_force'], balance['enemy_force']
    ours, theirs = balance['ally_melee_score'], balance['enemy_melee_score']
    comfortable = (healthy and type(ours) is int and type(theirs) is int
                   and ours * 10 > theirs * BEHIND_EGG_RATIO_TENTHS)
    if not healthy:
        rule = 'ally_wounded'    # 大破中は敵より上でも温存しない
    elif not combat_known:
        rule = 'combat_unknown'
    elif measured:
        rule = 'soldier_force'
    else:
        rule = 'hp_only'
    return (strong or not comfortable), {
        **strong_evidence,
        **balance,
        'strong_rule': strong_evidence.get('rule'),
        'strong_general': strong, 'ally_hp': ally_hp,
        'ally_max_hp': reading[1] if isinstance(reading, (tuple, list)) and len(reading) == 2
        and type(reading[1]) is int else None,
        'enemy_hp': enemy_hp,
        'ally_soldiers': ally_soldiers if measured else None,
        'enemy_soldiers': enemy_soldiers if measured else None,
        'ally_force': ally_force, 'enemy_force': enemy_force, 'healthy': healthy,
        'ratio_tenths': BEHIND_EGG_RATIO_TENTHS, 'rule': rule,
    }
