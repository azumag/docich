"""手順層: the live battle loop and its end.

Reads the battle panel into the battle record, asks the judgment layer which
tactic or rescue is due, opens the command menu for it and settles the record
when the battle ends.
"""
from __future__ import annotations
from ..state.regions import world as _world, flow as _flow, journal as _journal, sorties as _sorties, plan as _plan_region

import re
import time
from dataclasses import asdict

from ..judgment.battle import _general_egg_rescue_recheck
from . import egg_bait
from ..os_layer.screen import Screen
from ..os_layer.font import UNKNOWN
from ... import hanjuku_chart as chart, hanjuku_experience as experience
from ... import hanjuku_reference as reference
from ...hanjuku_egg_reference import enemy_egg_triggers, general_max_hp
from ..decision_log import _record, _tally
from ..judgment.battle import (EGG_WALL_DAMAGE, DEFENDER_SUMMON_OPEN_LIMIT, CARD_MENU_OPEN_RETRIES, DEFENDER_EGG_OPEN_LIMIT, DUEL_EVENT_TOKENS,
                               _conserved_army_recheck,
                               ENTRY_RETURN_KINDS, GENERAL_CRITICAL_RETREAT_HP, HERO_RECALL_MIN_HP,
                               HERO_RECALL_RATIO_TENTHS, MELEE_HOLD_LIMIT, MELEE_HP_HOLD_DIVISOR,
                               MELEE_HP_HOLD_FLOOR, _battle_context, _battle_labels, _bind_battle_strategy,
                               _bind_event_duel, _bind_substitute_defender, _boss_tactics_allowed,
                               _card_damage_gate, _card_damage_key, _card_damage_recheck_resources,
                               _card_melee_ready, _card_use_unclassified, _charted_melee, _command_open_wait,
                               _critical_safe_card, _critical_survival_needed, _defender_egg_window, _defender_last_resort,
                               _defender_summon_preempt,
                               _egg_before_retreat, _egg_rescue_pending, _egg_drop_evidence, _egg_drop_tactics, _egg_plan, _hero_marching,
                               _hero_losing_melee, _hero_retreat_needed, _hold_general_loss_metric, _hold_melee_egg, _melee_balance,
                               _migrate_card_evidence, _observe_egg_drop_candidate, _one_hit_lethal, _own_egg_empty,
                               _own_egg_positive, _strong_card_tactics, _strong_enemy, _survival_needed,
                               _survival_state, _tactic_enemy_ok, reset_battle_controls,
                               HERO_SURVIVAL_REOPEN_LIMIT)
from ..os_layer.commands import _human_menu_open, pad
from ..os_layer.readers import STATUS_NAMES
from ..os_layer.world import NAME, CAPTURE_READ_DELAY, garrison_lost
from ..strategy.campaign import _castle_label, observe_chapter_castle, observe_chapter_general
from ..strategy.kit import _is_boss_order
from ..strategy.execution import bound_fields
from ..strategy.orders import (LONG_MARCH_TICKS, SITE_LATE_ENTRY_TICKS, _bind_sortie,
                                drop_retired_step_context,
                                _garrison_move, _match_sortie, _order_for_step, _tactics)


def observe_entry_return(screen, mem):
    """A measured management menu ends an entry that never reached combat.

    A field/map frame can flash before combat, so it is not a receipt. Keep
    active battles and their pending first panel until battle_end finishes.
    Never manufacture a loss, death, or castle owner from a skipped entry.
    """
    entry = _plan_region(mem).get('attack')
    if not isinstance(entry, dict) or not entry or mem.get('battle'):
        return
    if screen.kind not in ENTRY_RETURN_KINDS:
        return
    _record(mem, 'battle_entry_expired', castle=entry.get('castle'),
            side=entry.get('side'), general=entry.get('general'), screen=screen.kind,
            resulting_event='unclassified_entry_closed',
            reason='戦闘未開始のまま管理画面へ戻ったため古い城情報を解除')
    _plan_region(mem)['attack'] = None
    mem.pop('battle_seen', None)


# The fight's own dialogue (the king's lines, the end message) is the only
# measured trace between the last stacked panel and the map; keep a few pages.
SEASON_KING_TEXT_LIMIT = 12


def _season_king_rows(screen):
    """The general's and the 四季王's rows of one stacked summon panel."""
    from ...hanjuku_map_sources import SEASON_KINGS
    kings = [r for r in screen.egg_rows if r.side == 'enemy' and r.name in SEASON_KINGS]
    allies = [r for r in screen.egg_rows if r.side == 'ally']
    if len(kings) != 1 or len(allies) != 1 or len(screen.egg_rows) != 2:
        return None
    return allies[0], kings[0]


def observe_season_king_panel(screen, mem):
    """#3063: a 四季王 fight never shows the two-general panel battle_step reads.

    g1075 2026-10-30 08:27:56 opened straight on the stacked summon panel
    (アルテミス 49 / サマカンテ 614) and 08:44-08:45 went ヴィーナス 82→26 /
    スプリミョーネ 425→382 on it; neither fight left battle_start or
    battle_result, so no 四季王 win could ever reach boss_victories. Only a
    measured boss entry of the same general opens the record, after two equal
    readings. The general's and the king's rows then keep its HP for
    battle_end's rule; an end that never draws a 0 stays unclassified. The
    fight's dialogue pages are recorded so its end message can be measured.
    """
    cur = mem.get('battle')
    if cur and cur.get('panel') == 'season_king_stack':
        if screen.kind == 'text' and screen.text:
            texts = cur.setdefault('texts', [])
            if screen.text not in texts and len(texts) < SEASON_KING_TEXT_LIMIT:
                texts.append(screen.text)
                _record(mem, 'season_king_battle_text', **_battle_labels(cur), enemy=cur['enemy'],
                        ally=cur['ally'], castle=cur.get('castle'),
                        observed_metric={'text': screen.text, 'ally_hp': cur.get('ally_hp'),
                                         'enemy_hp': cur.get('enemy_hp')},
                        reason='四季王戦の台詞を勝敗の実測証拠として記録')
        rows = _season_king_rows(screen)
        if rows:
            ally, king = rows
            if king.name == cur['enemy']:
                cur['enemy_hp'] = king.hp
                cur['away'] = 0
                if ally.name == cur['ally']:
                    cur['ally_hp'] = ally.hp
        return
    attack = _plan_region(mem).get('attack') or {}
    rows = _season_king_rows(screen) if not cur and mem.get('chapter') == 4 else None
    if (not rows or attack.get('side') != 'attack' or attack.get('entry_evidence') != 'measured_boss_entry'
            or rows[0].name != attack.get('general')):
        mem.pop('season_king_seen', None)
        return
    ally, king = rows
    reading = [king.name, ally.name]
    if mem.get('season_king_seen') != reading:
        mem['season_king_seen'] = reading
        return
    mem.pop('season_king_seen', None)
    cur = mem['battle'] = {'enemy': king.name, 'ally': ally.name, 'start_enemy_hp': king.hp,
                           'start_ally_hp': ally.hp, 'enemy_hp': king.hp, 'ally_hp': ally.hp,
                           'cards_used': [], 'plan': None, 'cards_selected': [], 'cards_missing': [],
                           'cards_unclassified': [], 'card_consumption_complete': True,
                           'card_evidence_version': 1, 'panel': 'season_king_stack', 'away': 0,
                           **_battle_context(mem, ally.name)}
    _bind_battle_strategy(mem, cur)
    _record(mem, 'battle_start', **_battle_labels(cur), enemy=king.name, ally=ally.name,
            expected_metric=cur.get('strategy_expected'),
            observed_metric={'enemy_hp': king.hp, 'ally_hp': ally.hp, 'panel': 'season_king_stack'},
            context=cur.get('context', 'message'), enemy_hp=king.hp, ally_hp=ally.hp,
            castle=cur.get('castle'), reason='四季王戦の召喚獣戦パネルで将軍名と四季王のHPを確認')
    _tally(mem, 'battles_started')


def _defender_successor(mem, cur, b):
    """Two stable living-general panels can continue a known castle defense.

    The successor may finish the attacker at once, so the confirming panels can
    show the enemy at 0 (g1075 14:20: クランベリー 51 vs カシュー 1, then 0;
    requiring HP left recorded the won defense as a lost home castle).
    """
    maximum = general_max_hp('しゅじんこう' if b.ally == NAME else b.ally)
    eligible = (cur.get('side') == 'defense' and bool(cur.get('castle'))
                and cur.get('ally_hp') == 0 and (cur.get('enemy_hp') or 0) > 0
                and b.enemy == cur.get('enemy') and b.ally != cur.get('ally')
                and maximum is not None and 0 < b.ally_hp <= maximum)
    reading = [b.enemy, b.ally]
    if not eligible:
        cur.pop('successor_seen', None)
        return False
    if cur.get('successor_seen') != reading:
        cur['successor_seen'] = reading
        return False
    context = {'castle': cur['castle'], 'side': 'defense', 'step': None,
               'entry_evidence': cur.get('entry_evidence')}
    predecessor = cur['ally']
    battle_end(mem, 'defender_successor', defense_continues=True)
    reset_battle_controls(mem)
    _plan_region(mem)['attack'] = context
    mem['battle_seen'] = reading
    _record(mem, 'defender_successor', ally=b.ally, enemy=b.enemy,
            castle=context['castle'], previous_ally=predecessor,
            observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
            reason='同じ敵と戦う次の守備将軍を連続した完全パネルで確認')
    return True


def _attack_defender_successor(mem, cur, b):
    """The castle's next defender meets the same attacker after a won fight.

    g1075 18:12:58 (#3028): ココット beat フヌイヌム's リースリング (37) and
    バーミセリ (89) came out next. No new battle was opened, so the loss to
    バーミセリ was judged from the old panel (24 vs 0) and recorded as
    captured:フヌイヌム. The new defender stands at HP above 0 where the
    beaten one read 0, so a misread of the old name cannot qualify.
    """
    maximum = general_max_hp(b.enemy)
    eligible = (cur.get('side') == 'attack' and bool(cur.get('castle'))
                and type(cur.get('enemy_hp')) is int and cur['enemy_hp'] == 0
                and type(cur.get('ally_hp')) is int and cur['ally_hp'] > 0
                and b.ally == cur.get('ally') and b.enemy != cur.get('enemy')
                and maximum is not None and 0 < b.enemy_hp <= maximum
                and b.ally_hp <= cur['ally_hp'])
    reading = [b.enemy, b.ally]
    if not eligible:
        cur.pop('attack_successor_seen', None)
        return False
    if cur.get('attack_successor_seen') != reading:
        cur['attack_successor_seen'] = reading
        return False
    context = {'castle': cur['castle'], 'side': 'attack', 'general': cur['ally'],
               'step': cur.get('step'), 'entry_evidence': cur.get('entry_evidence')}
    beaten = cur['enemy']
    battle_end(mem, 'attack_defender_successor', attack_continues=True)
    reset_battle_controls(mem)
    _plan_region(mem)['attack'] = context
    mem['battle_seen'] = reading
    _record(mem, 'attack_defender_successor', ally=b.ally, enemy=b.enemy,
            castle=context['castle'], previous_enemy=beaten,
            observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
            reason='守備将軍を倒した直後に同じ城の次の守備将軍が出たため、城は奪っていないとして次の戦闘へ移行')
    return True


def _unlocated_scope(mem, cur):
    """Only this run's measured ordinary opening can bind a neutral handoff."""
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    if not all(isinstance(cur.get(k), str) for k in ('ally', 'enemy')):
        return None
    start, enemy_start = cur.get('start_ally_hp'), cur.get('start_enemy_hp')
    maximum, enemy_maximum = general_max_hp(cur.get('ally')), general_max_hp(cur.get('enemy'))
    if (identity is None or type(mem.get('tick')) is not int
            or type(mem.get('chapter')) is not int or mem['chapter'] <= 0
            or cur.get('side') is not None or cur.get('castle') is not None
            or cur.get('ally') in (NAME, 'しゅじんこう') or cur.get('event_duel')
            or any(type(n) is not int or n <= 0 for n in
                   (start, enemy_start, maximum, enemy_maximum))
            or start > maximum or enemy_start > enemy_maximum
            or cur.get('melee_opening_scope') != [identity, cur.get('ally'), cur.get('enemy'), start, enemy_start]):
        return None
    return [identity, mem['chapter'], cur['ally'], cur['enemy'], start, enemy_start]


def _observe_unlocated_zero(mem, cur, b):
    scope = _unlocated_scope(mem, cur)
    previous = cur.pop('unlocated_zero_candidate', None)
    if (scope is None or b.ally_hp != 0 or b.enemy_hp <= 0
            or b.enemy_hp > cur['start_enemy_hp']
            or cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed')):
        cur.pop('unlocated_zero', None)
        return
    now = mem['tick']
    cur['unlocated_zero_candidate'] = [scope, now]
    if previous == [scope, now - 1]:
        cur['unlocated_zero'] = {'scope': scope, 'tick': now, 'enemy_hp': b.enemy_hp}


def _unlocated_successor(mem, cur, b):
    """Accept an observed replacement without manufacturing a castle defense.

    Two current HP0 panels, then two living known-general panels against the
    same enemy, bound to this run and opening. Old hotloaded/unbound records
    cannot acquire this receipt from their saved HP alone.
    """
    scope = _unlocated_scope(mem, cur)
    proof = cur.get('unlocated_zero')
    maximum = general_max_hp('しゅじんこう' if b.ally == NAME else b.ally)
    now = mem.get('tick')
    eligible = (scope is not None and isinstance(proof, dict) and proof.get('scope') == scope
                and type(proof.get('tick')) is int and 0 < now - proof['tick'] <= 32
                and type(proof.get('enemy_hp')) is int
                and type(cur.get('ally_hp')) is int and cur['ally_hp'] == 0
                and type(cur.get('enemy_hp')) is int and cur['enemy_hp'] > 0
                and b.enemy == cur['enemy'] and b.ally != cur['ally']
                and type(maximum) is int and 0 < b.ally_hp <= maximum
                and 0 < b.enemy_hp <= proof['enemy_hp'] <= cur['start_enemy_hp']
                and not (cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed')))
    previous = cur.pop('unlocated_successor_seen', None)
    if not eligible:
        return False
    reading = [scope, b.ally, b.enemy, now]
    cur['unlocated_successor_seen'] = reading
    if previous != [scope, b.ally, b.enemy, now - 1]:
        return False
    predecessor = cur['ally']
    battle_end(mem, 'unlocated_successor', actor_continues=True)
    reset_battle_controls(mem)
    # Match only the new actor and deliberately carry no location or route.
    _plan_region(mem)['attack'] = {'general': b.ally, 'side': None, 'castle': None, 'step': None}
    mem['battle_seen'] = [b.enemy, b.ally]
    mem['melee_opening_candidate'] = [scope[0], b.ally, b.enemy]
    _record(mem, 'unlocated_successor', ally=b.ally, enemy=b.enemy,
            previous_ally=predecessor, castle=None, side=None,
            observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
            reason='同じランの実HP0と連続した新将軍パネルから操作対象を交代。城と攻守は未分類')
    return True


def _entered_battle_successor(mem, cur, b):
    """A measured new entry and two complete panels release a finished win.

    g510: only one attack-entry frame separated Gasupacho/Cinnamon from
    the hero/Venus. The old zero-enemy-HP record then blocked every input.
    Never infer a transition from changed names alone or from a living enemy.
    """
    entry = _plan_region(mem).get('attack')
    maximum = general_max_hp('しゅじんこう' if b.ally == NAME else b.ally)
    eligible = (isinstance(entry, dict) and entry.get('side') == 'attack'
                and entry.get('general') == b.ally and b.ally != cur.get('ally')
                and entry.get('castle') in chart.castles(mem.get('chapter') or 0)
                and type(cur.get('enemy_hp')) is int and cur['enemy_hp'] == 0
                and type(cur.get('ally_hp')) is int and cur['ally_hp'] > 0
                and type(cur.get('away')) is int and cur['away'] >= 1
                and maximum is not None and 0 < b.ally_hp <= maximum and b.enemy_hp > 0)
    reading = [b.enemy, b.ally]
    if not eligible:
        cur.pop('entry_successor_seen', None)
        return False
    if cur.get('entry_successor_seen') != reading:
        cur['entry_successor_seen'] = reading
        return False
    context = dict(entry)
    previous_ally, previous_enemy = cur['ally'], cur['enemy']
    battle_end(mem, 'confirmed_new_entry')
    reset_battle_controls(mem)
    _plan_region(mem)['attack'] = context
    mem['battle_seen'] = reading
    _record(mem, 'battle_entry_successor', ally=b.ally, enemy=b.enemy,
            castle=context['castle'], previous_ally=previous_ally, previous_enemy=previous_enemy,
            observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
            reason='前戦の敵HP0・新しい突入記録・連続した完全パネルから次の戦闘へ移行')
    return True


def _remember_general_for_summon(mem, cur, name, hp):
    """Bind a real general panel before a monster can hide its HP."""
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    full = mem.get('hero_max_hp') if name == NAME else general_max_hp(name)
    if (identity is None or name != cur.get('ally') or not cur.get('enemy')
            or type(hp) is not int or type(full) is not int or not 0 < hp <= full):
        cur.pop('summon_general_reading', None)
        return
    from ..os_layer.roster import observed_member
    observed_member(mem, [name])
    cur['summon_general_reading'] = {
        'identity': dict(identity), 'ally': name, 'enemy': cur['enemy'],
        'hp': hp, 'max_hp': full}


def _card_battle_reading(screen, cur, mem=None):
    """Refresh the inputs used by damage prediction, never soldier HP guesses.

    A menu may still expose the ordinary battle panel.  Use that panel when
    present; an unread ordinary frame invalidates previous counts.  A menu
    without a panel retains the last battle HP, not a general's maximum HP.
    Old hotloaded counts lack the current-reading marker and remain unknown.
    """
    b = screen.battle
    if b is None and screen.kind != 'battle':
        # 通常戦闘メニューでは HP パネルが隠れるが兵士はフィールドに残る。
        # パネル保持契約 (HP・文脈は直近の読みを保持) と同じく、実測できた
        # 側の兵士数だけを更新し、未読の側は前回の読みを保持する
        # (docich#4048)。部分読みで既存の計測を消すと、g745-1436 のように
        # (6,None) が直前の (4,1) を上書きして卵温存を壊す。誤読は採らない。
        counts = getattr(screen, 'field_soldiers', None)
        if (screen.kind == 'battle_menu'
                and isinstance(counts, (tuple, list)) and len(counts) == 2):
            measured = False
            for key, value in zip(('ally_soldiers', 'enemy_soldiers'), counts):
                if type(value) is int and 0 <= value <= 6:
                    cur[key] = value
                    measured = True
            # 「現在値として証明済み」の印は、実際にどちらかの側を計測
            # できたときだけ立てる。両側とも未読 (None, None) のフレームで
            # 立てると、未読前提の保護 (奥の手の温存など、#3358) が実測
            # ありと誤認して崩れる (g482 の回帰で検出)。
            if measured:
                cur['card_soldiers_current'] = True
        return
    if (b is None or b.enemy != cur.get('enemy') or b.ally != cur.get('ally')
            or any(type(hp) is not int or hp < 0 for hp in (b.enemy_hp, b.ally_hp))):
        cur['card_hp_unread'] = True
        cur['card_context_unclassified'] = True
        cur['card_combatants_changed'] = (b is not None and (b.enemy != cur.get('enemy')
                                                             or b.ally != cur.get('ally')))
        cur['card_soldiers_current'] = False
        if mem is not None:
            egg_bait.cancel(mem, cur, '本人HPまたは戦闘パネルが未確認になったため召喚待ちを終了')
        return
    previous = cur.get('enemy_hp')
    maximum = general_max_hp(cur.get('enemy'))
    if (cur.get('side') == 'defense' and type(previous) is int
            and type(maximum) is int and 0 < b.enemy_hp < previous <= maximum):
        # Preserve the v131 observation-delay margin even when the first
        # lower HP panel is already behind an open card/command menu.
        cur['defender_enemy_hp_drop'] = max(int(cur.get('defender_enemy_hp_drop') or 0),
                                            previous - b.enemy_hp)
    cur['card_hp_unread'] = False
    cur['card_context_unclassified'] = False
    cur['card_combatants_changed'] = False
    cur['card_summon_observed'] = False
    cur['egg_battle'] = False
    if mem is not None:
        mem['egg_battle'] = False
    cur['enemy_hp'], cur['ally_hp'] = b.enemy_hp, b.ally_hp
    if mem is not None:
        _remember_general_for_summon(mem, cur, b.ally, b.ally_hp)
    # Trigger B: HP re-observation invalidates a stale conserve latch.
    rescue = cur.get('survival') if isinstance(cur, dict) else None
    if isinstance(rescue, dict) and rescue.get('conserving_melee'):
        try:
            from ..judgment.battle import _enemy_transformed as _et
            _enemy_transformed = _et
            if _enemy_transformed(mem, cur):
                broken = True
            elif _critical_survival_needed(cur):
                broken = True
            elif _own_egg_empty(mem, cur):
                broken = True
            elif cur.get('card_hp_unread') or cur.get('card_context_unclassified'):
                broken = True
            elif cur.get('card_soldiers_current') is not True:
                broken = True
            elif cur.get('egg_battle') or mem.get('egg_battle') or cur.get('card_summon_observed'):
                broken = True
            else:
                enemy_name = cur.get('enemy')
                if enemy_name in chart.BOSSES.values() or enemy_name in reference.BOSS_GENERALS:
                    broken = True
                else:
                    hp = cur.get('ally_hp')
                    start = cur.get('start_ally_hp')
                    enemy_hp = cur.get('enemy_hp')
                    enemy_start = cur.get('start_enemy_hp')
                    if (type(hp) is int and type(start) is int and hp < start
                          and type(enemy_hp) is int and type(enemy_start) is int
                          and (enemy_hp >= enemy_start
                               or (start - hp) * enemy_start > (enemy_start - enemy_hp) * start)):
                        broken = True
            if broken:
                rescue.pop('conserving_melee', None)
                rescue.pop('conserved_labels', None)
                rescue.pop('conserved_army_recheck', None)
        except Exception:
            pass
    cur['hp_observed_at'] = time.time()
    counts = getattr(screen, 'field_soldiers', None)
    if not isinstance(counts, (tuple, list)) or len(counts) != 2:
        counts = (None, None)
    cur['ally_soldiers'], cur['enemy_soldiers'] = counts
    cur['card_soldiers_current'] = True


def _card_gate_close(mem, cur, names):
    cur['card_flow'] = None
    cur['card_damage_list_blocked'] = {'key': _card_damage_key(mem, cur), 'cards': list(names)}
    rescue = _survival_state(mem, cur)
    # These cards still exist; they are unsafe for this observation.  This
    # flag only advances to a real egg/retreat.  It never proves おくのて exists.
    rescue['cards_exhausted'] = True
    rescue['cards_uncertain'] = False
    return [pad('b')]


def _observe_clash_gauge(mem, cur, screen):
    """Keep the measured 連打ゲージ beside the battle, and contrast it with
    the predicted egg state the first time a calibrated blue window appears.

    対比は記録だけ (docich#1553): 青窓と卵落予測の一致・矛盾の判定は、青の
    実機校正が済むまで 'unclassified' に留める。測定が予測を書き換えることは
    なく、power_mash / egg_safe_hold の切替も ASSIST_PULSE も追加しない
    (#1180 Phase 1 の契約)。confidence 基準: state 'blue' は校正済み
    CLASH_GAUGE_BLUE のパレットが枠内の最頻色であることだけを根拠にする。
    未校正 (表が空) の間は blue は一度も立たない。
    """
    gauges = getattr(screen, 'clash_gauges', None) or []
    cur['clash_gauge'] = ({g.side: {'state': g.state, 'filled': g.filled,
                                    'box': list(g.box), 'tones': [list(t) for t in g.tones]}
                           for g in gauges} or None)
    blue = next((g for g in gauges if g.state == 'blue'), None)
    if blue is None or cur.get('clash_gauge_blue_seen'):
        return
    cur['clash_gauge_blue_seen'] = True
    _record(mem, 'battle_clash_gauge_cross_check', **_battle_labels(cur),
            enemy=cur.get('enemy'), enemy_hp=cur.get('enemy_hp'), ally_hp=cur.get('ally_hp'),
            observed_metric={'clash_gauge': cur['clash_gauge'], 'blue_side': blue.side},
            expected_metric={'enemy_egg_drop_expected': cur.get('enemy_egg_drop_expected') is True,
                             'egg_drop_watch': cur.get('egg_drop_watch'),
                             'dropper_plan': (cur.get('plan') or {}).get('egg_drop')},
            verdict='unclassified',
            reason='接触の青ゲージを実測した。卵落予測との一致・矛盾は青の校正後に判定し、制御は変更しない')


def _observe_defender(mem, cur):
    """Remember who a castle's battle panel showed defending it (#1553).

    The sortie menus never display the opponent, so the only measured way a
    sortie can know its defender is a previous battle's panel at the same
    castle — typically the retry after a failed attack. Recording is
    display-only evidence for the deploy layer; the battle layer still
    decides from the enemy it observes (a mismatch just leaves the sortie
    plan unused and the record refreshed by this battle).
    """
    if (cur.get('side') != 'attack' or not cur.get('castle')
            or not isinstance(cur.get('enemy'), str)
            or cur.get('enemy') in reference.BOSS_GENERALS
            or cur.get('enemy') in chart.BOSSES.values()
            or general_max_hp(cur.get('enemy')) is None
            or cur.get('castle') not in chart.castles(mem.get('chapter') or 0)):
        return
    record = {'general': cur['enemy'], 'chapter': mem.get('chapter') or 0,
              'evidence': 'battle_panel', 'step': cur.get('step'),
              # The panel also reads the defender's HP: keeping it lets the
              # march see which castle has a defender already worn down
              # (g1163 2026-11-13 01:1x オオキク read 74/74/3 across the wave).
              'hp': cur.get('enemy_hp')}
    defenders = mem.setdefault('castle_defenders', {})
    if defenders.get(cur['castle']) != record:
        defenders[cur['castle']] = record
        _record(mem, 'castle_defender_seen', **_battle_labels(cur), castle=cur['castle'],
                observed_metric=record,
                reason='戦闘パネルの対戦相手をこの城の守備将軍として記録し、再出撃の卵落計画に使う')


def battle_finished(cur):
    """A panel HP of 0 on either side: the fight this record was read from is over."""
    return (isinstance(cur, dict) and type(cur.get('enemy_hp')) is int
            and type(cur.get('ally_hp')) is int and 0 in (cur['enemy_hp'], cur['ally_hp']))


def _finished_record_replaced(mem, cur, b):
    """A new panel can close a finished record once two identical readings agree.

    g1075 15:13 (#3009): ヘパイストス fell to 0 and the 帰還先ピッカー covered the
    return. アポロン's next panel showed クランベリー, nothing closed the old
    record, and the new fight never opened. A new fight starts with both sides
    above 0, and two matching readings keep a misread zero-HP panel from
    closing a fight that is still on the field.
    """
    if not (isinstance(cur, dict)
            and type(cur.get('enemy_hp')) is int and cur['enemy_hp'] == 0
            and type(cur.get('ally_hp')) is int and cur['ally_hp'] > 0):
        return False
    if not (type(b.enemy_hp) is int and type(b.ally_hp) is int
            and b.enemy_hp > 0 and b.ally_hp > 0
            and isinstance(b.enemy, str) and isinstance(b.ally, str)
            and b.enemy and b.ally):
        cur.pop('finished_replacement_seen', None)
        return False
    reading = [b.enemy, b.ally]
    if cur.get('finished_replacement_seen') != reading:
        cur['finished_replacement_seen'] = reading
        return False
    battle_end(mem, 'battle', panel_changed=True)
    reset_battle_controls(mem)
    return True


def _drop_defender_record(mem, castles):
    """Capture or loss makes a castle's remembered defender stale."""
    defenders = mem.get('castle_defenders') or {}
    for castle in castles:
        defenders.pop(castle, None)


def _carried_opening_tactics(mem, cur, existing):
    """Spend otherwise-unused, *confirmed carried* cards at the fight opening.

    The battle chart still owns the timings of its named cards. An observed
    sortie kit may contain additional cards that otherwise sit unused until
    retreat. Never invent cards from the shop stock or an unconfirmed plan.
    """
    step = cur.get('step')
    order = _order_for_step(mem, step)
    bound = bound_fields(order, mem)
    if not bound or bound['general'] != cur.get('ally'):
        return []
    boss_entry = _boss_tactics_allowed(mem, cur)
    if _is_boss_order(order, mem) and not boss_entry:
        return []  # reserve the boss kit on the road to the boss
    covered = {}
    for tactic in existing:
        card = tactic['card']
        covered[card] = covered.get(card, 0) + 1
    carried = list(bound['cards'])
    for card in (mem.get('kit_spent') or {}).get(step) or ():
        if card in carried:
            carried.remove(card)
    added = []
    for slot, card in enumerate(carried):
        if card in reference.BOSS_ONLY_CARDS and not boss_entry:
            continue
        if covered.get(card, 0) > 0:
            covered[card] -= 1
            continue
        added.append({'enemy': cur.get('enemy'), 'card': card, 'open': True,
                      'step': step, 'tactic_id': f'carried:{step}:{card}:{slot}',
                      'note': '実携行を確認した未指定の切り札を温存せず開幕に使用'})
    return added


def _planned_battle_cards(mem, cur, enemy):
    """The cards this fight can actually spend: the only rescue resources.

    A card the sortie left behind (the measured departure kit) or one the
    battle rules forbid before a measured boss entry (``BOSS_ONLY_CARDS``)
    cannot be spent. Counting it as a pending plan kept ``_resources_exhausted``
    false while the general fell with it in hand (g1153 2026-11-08 13:21
    ココット: 在庫0のイッテツーン2枚と使用禁止のバルムンクが planned_cards に
    残り、白兵9.8倍差でも退却しなかった).
    """
    planned = [t['card'] for t in _tactics(mem, cur.get('step'))
               if _tactic_enemy_ok(t, enemy, cur) and t.get('step') in (None, cur.get('step'))
               and not (t.get('boss_only')
                        and not _boss_tactics_allowed(mem, cur))]
    planned += list((_plan_region(mem).get('card_override') or {}).get(cur.get('step')) or ())
    bound = bound_fields(_order_for_step(mem, cur.get('step')), mem)
    if (isinstance(bound, dict) and isinstance(bound.get('cards'), list)
            and bound.get('general') in (None, cur.get('ally'))):
        planned = [card for card in planned if card in bound['cards']]
    if not _boss_tactics_allowed(mem, cur):
        planned = [card for card in planned if card not in reference.BOSS_ONLY_CARDS]
    return planned


def _mark_defense_seen(mem, cur):
    """A defence battle is the threat itself: record it like せめこまれました.

    g1163 2026-11-13 21:2x: the enemy attacked ドンドン while the finish
    sortie was out; only the toast set defense_seen, so an attack started
    without it kept the strongest general out front.
    """
    if isinstance(cur, dict) and cur.get('side') == 'defense' and cur.get('castle'):
        mem.setdefault('defense_seen', {})[cur['castle']] = int(mem.get('tick') or 0)


def battle_step(screen: Screen, mem):
    b = screen.battle
    if (b is None or not b.enemy or not b.ally or UNKNOWN in b.enemy or UNKNOWN in b.ally
            or any(type(hp) is not int or hp < 0 for hp in (b.enemy_hp, b.ally_hp))):
        mem['battle_seen'] = None
        mem.pop('melee_opening_candidate', None)
        if mem.get('battle'):
            _card_battle_reading(screen, mem['battle'], mem)
            mem['battle'].pop('successor_seen', None)
            mem['battle'].pop('entry_successor_seen', None)
            mem['battle'].pop('attack_successor_seen', None)
            mem['battle'].pop('unlocated_successor_seen', None)
            mem['battle'].pop('unlocated_zero_candidate', None)
            mem['battle'].pop('finished_replacement_seen', None)
        return []  # partial panel must not replace the last clear HP/context
    _migrate_card_evidence(mem)
    cur = mem.get('battle')
    if cur:
        cur.pop('defeat_owner', None)  # a later complete combat panel invalidates the post-combat flag
    if cur and (b.enemy, b.ally) != (cur['enemy'], cur['ally']):
        if not (_entered_battle_successor(mem, cur, b) or _defender_successor(mem, cur, b)
                or _attack_defender_successor(mem, cur, b) or _unlocated_successor(mem, cur, b)):
            if not _finished_record_replaced(mem, cur, b):
                _card_battle_reading(screen, cur, mem)
                return []
        cur = None
    elif cur:
        cur.pop('successor_seen', None)
        cur.pop('entry_successor_seen', None)
        cur.pop('attack_successor_seen', None)
        cur.pop('unlocated_successor_seen', None)
        cur.pop('finished_replacement_seen', None)
        mem.pop('battle_seen', None)
    if cur is None:
        # Fades dim the panel and can drop dakuten; open a battle record only
        # after two consecutive identical readings of both names.
        reading = [b.enemy, b.ally]
        from ..os_layer.roster import run_identity
        identity = run_identity(mem.get('_run_identity'))
        candidate = [identity, b.ally, b.enemy] if identity is not None else None
        if mem.get('battle_seen') != reading:
            mem['battle_seen'] = reading
            mem['melee_opening_candidate'] = candidate
            return []
        mem['battle_seen'] = None
        previous_candidate = mem.pop('melee_opening_candidate', None)
        # The panel's opponent is a chapter fact: a general whose debut
        # chapter is later than ours proves the game advanced (g454: ピオーネ/
        # ヘラ after the chapter 1 boss). This resets the chapter state, so it
        # runs before the new battle record is built.
        observe_chapter_general(mem, b.enemy)
        context = _battle_context(mem, b.ally)
        cur = mem['battle'] = {'enemy': b.enemy, 'ally': b.ally, 'start_enemy_hp': b.enemy_hp,
                               'start_ally_hp': b.ally_hp, 'cards_used': [], 'plan': None,
                               'cards_selected': [], 'cards_missing': [], 'cards_unclassified': [],
                               'card_consumption_complete': True, 'card_evidence_version': 1,
                               **context}
        if (identity is not None and isinstance(previous_candidate, list)
                and len(previous_candidate) == 3
                and run_identity(previous_candidate[0]) == identity
                and previous_candidate == candidate):
            # Captured only at the two-panel battle opening. A hotloaded old
            # battle cannot invent an earlier healthy opening from current HP.
            cur['melee_opening_scope'] = [identity, b.ally, b.enemy, b.ally_hp, b.enemy_hp]
            army = screen.field_soldiers
            if (isinstance(army, (tuple, list)) and len(army) == 2
                    and all(type(n) is int and 0 <= n <= 6 for n in army)):
                cur['melee_opening_army'] = list(army)
        if b.ally == NAME and type(b.ally_hp) is int:
            # The hero's full strength, remembered across battles: a hero who
            # starts a fight already weak never looks "far down" against his
            # own start HP (g436 23:17: 14 HP after a road battle, then died).
            mem['hero_max_hp'] = max(int(mem.get('hero_max_hp') or 0), b.ally_hp)
            cur['ref_ally_hp'] = mem['hero_max_hp']
        _bind_battle_strategy(mem, cur)
        if cur.get('step') and not (_plan_region(mem).get('attack') or {}).get('step'):
            # The order id stayed unknown (ambiguous entry): only the marching
            # sortie's kit is bound, and the record says so.
            _record(mem, 'battle_step_resolved', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                    castle=cur.get('castle'), side=cur.get('side'),
                    observed_metric={'sortie_step': cur['step']},
                    reason='出撃注文を特定できない突入のため、同じ将軍の進軍中出撃の携行札だけを結び付け')
        _bind_substitute_defender(mem, cur, b)
        _bind_event_duel(mem, cur)
        planned = _planned_battle_cards(mem, cur, b.enemy)
        cur['planned_cards'] = list(planned)
        if isinstance(b.ally, str) and type(b.ally_hp) is int:
            # The HP this general brought into the fight is the only measured
            # strength the plan can see: g1163 2026-11-12 16:4x attacked オオキク
            # with 18/22/27-HP generals against a 50-68-HP defender fourteen
            # times and lost every one. Keeping the reading lets the attack
            # pick a healthy general instead of the highest maximum.
            name = 'しゅじんこう' if b.ally == NAME else b.ally
            known = _world(mem).setdefault('general_hp', {})
            row = known.get(b.ally) if isinstance(known.get(b.ally), dict) else {}
            maximum = row.get('max') or general_max_hp(name) or b.ally_hp
            known[b.ally] = {'hp': int(b.ally_hp), 'max': int(maximum),
                             'tick': int(mem.get('tick') or 0)}
        # 開戦時の兵士数も記録する (docich#4048)。フィールド計測が未読の
        # 側は None のまま残し、誤読を戦闘判断へ流さない。
        army = screen.field_soldiers
        if not isinstance(army, (tuple, list)) or len(army) != 2:
            army = (None, None)
        _record(mem, 'battle_start', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                expected_metric=cur['strategy_expected'],
                observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp,
                                 'ally_soldiers': army[0], 'enemy_soldiers': army[1]},
                planned_cards=planned, context=cur.get('context', 'message'),
                enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, castle=cur['castle'],
                ally_soldiers=army[0], enemy_soldiers=army[1],
                reason='戦闘パネルの将軍名とHPを確認')
        _mark_defense_seen(mem, cur)
        _tally(mem, 'battles_started')
        # 将軍 (最大HP) と携行切り札 (卵落・ID合計) の組み合わせで、この戦闘の
        # 「卵を使わせない／落とさせる」方針を先に決めて記録する。卵を使える能力が
        # ない・不明な敵では方針もないので記録しない (卵の有無自体は battle_melee の
        # egg_risk_flags が毎回残す)。
        egg_plan = _egg_plan(mem, cur)
        if egg_plan['threat']:
            _record(mem, 'battle_egg_plan', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                    expected_metric='携行ID47以下による開幕卵の抑止と、卵落札での卵の落下',
                    observed_metric=egg_plan,
                    reason='将軍の最大HPと携行切り札の卵落・ID合計から卵ディニアル方針を決定')
    if (b.enemy, b.ally) != (cur['enemy'], cur['ally']):
        return []            # faded/partial panel: keep the last clear reading
    cur['away'] = 0
    # Keep the display projection on the same arithmetic as this observation:
    # consumed cards and the enemy-egg state must not stay at the opening
    # snapshot (#2364). This only rewrites cur['egg_plan']; it records nothing
    # new except the existing one-shot sortie mismatch.
    _egg_plan(mem, cur)
    _observe_clash_gauge(mem, cur, screen)
    _observe_defender(mem, cur)
    from . import okunote
    okunote.combat_return(screen, mem, cur)
    cur.pop('okunote_flow', None)  # the command finished; a later use is a new attempt
    preempt_guard = cur.get('okunote_egg_preempt') or {}
    if preempt_guard.get('selected'):
        # A complete human-battle panel ends the menu episode. Keep the
        # one-lottery receipt, but never claim subsequent dialogue as pending.
        preempt_guard['stage'] = 'completed'
        preempt_guard.pop('close_pending', None)
        preempt_guard.pop('unread_ticks', None)
    _observe_unlocated_zero(mem, cur, b)
    _card_battle_reading(screen, cur, mem)
    cur['own_egg_zero_observed'] = _own_egg_empty(mem, cur)
    cur['own_egg_positive_observed'] = _own_egg_positive(mem, cur)
    from ..os_layer.roster import dirty_status
    dirty_status(mem, [cur.get('ally')])
    if b.enemy_hp is not None and cur.get('start_enemy_hp') is not None and b.enemy_hp < cur['start_enemy_hp']:
        cur['clashed'] = True
    if (_boss_tactics_allowed(mem, cur) and type(b.ally_hp) is int
            and type(cur.get('start_ally_hp')) is int and b.ally_hp < cur['start_ally_hp']):
        # The first clash can hurt only our general (g506 Queen 70 stays
        # unchanged while hero 66->55). Do not wait for an enemy HP drop
        # before starting the carried post-clash boss sequence.
        cur['clashed'] = True
    if b.enemy_hp == 0 or b.ally_hp == 0:
        if (cur.get('card_flow') or {}).get('stage') == 'menu':
            # A panel at zero is the end of the fight: drop the opening card
            # that has not reached the command menu yet instead of pushing B
            # after a resolution the chart cannot follow up on.
            cur['card_flow'] = None
        return []
    # The opening taunt can coexist with a complete HP panel. B is ignored
    # while it is visible, so do not spend any opening budget or pending wait.
    # Match only the reported phrase (including its typing prefix); other
    # dialogue and unread OCR do not establish this particular foreground.
    # The wait is bounded per stay: the counter restarts only after a complete
    # panel shows no taunt. At the cap the existing judgment runs again, so a
    # taunt that never leaves the OCR text cannot hold input forever.
    if (not cur.get('egg_battle') and not mem.get('egg_battle')
            and 'ここがおまえ' in screen.text.replace(' ', '').replace('\n', '')):
        waits = cur.get('opening_dialogue_waits', 0) + 1
        cur['opening_dialogue_waits'] = waits
        if waits == 1:
            _record(mem, 'battle_opening_dialogue_wait', **_battle_labels(cur),
                    observed_metric={'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp},
                    reason='敵の開始台詞を読めたため、消えるまで戦闘入力を保留')
        if waits <= OPENING_DIALOGUE_WAIT_LIMIT:
            return [{'type': 'wait', 'ms': 50}]
        if waits == OPENING_DIALOGUE_WAIT_LIMIT + 1:
            _record(mem, 'battle_opening_dialogue_exhausted', **_battle_labels(cur),
                    observed_metric={'waits': waits - 1, 'limit': OPENING_DIALOGUE_WAIT_LIMIT},
                    reason='開始台詞の保留が上限に達したため、既存の戦闘判断へ戻す')
    else:
        cur.pop('opening_dialogue_waits', None)
    # Card selection and subsequent damage do not prove that the card hit:
    # the A input may not have reached the game, and melee also lowers HP.
    _observe_egg_drop_candidate(mem, cur)
    _card_damage_recheck_resources(mem, cur)
    egg_bait.observe_melee(screen, mem, cur)
    melee_followup = _card_melee_ready(mem, cur)
    # An eligible carried egg-drop card opens before an easy melee finish:
    # the owner's rule is unconditional once the enemy egg and max-HP
    # remainder are known. A prior selection is not a new opening.
    if _one_hit_lethal(cur) and not _egg_plan(mem, cur).get('dropper'):
        return _one_hit_finish_step(mem, cur)
    opening_wait = _command_open_wait(mem, cur)
    if opening_wait is not None:
        return opening_wait
    if _egg_rescue_pending(cur):
        return [{'type': 'wait', 'ms': 50}]
    dropper_due = (bool((cur.get('egg_plan') or {}).get('dropper'))
                   and not cur.get('card_flow'))
    if not dropper_due and not melee_followup and _general_egg_rescue_recheck(mem, cur):
        return _human_menu_open(cur, 'survival', mem)
    if not dropper_due and not melee_followup and _egg_before_retreat(mem, cur):
        if (cur.get('card_flow') or {}).get('stage') == 'announce':
            _card_use_unclassified(mem, cur, '危険HPで本人の卵を確認するため、未確認の切り札使用は未分類として保持')
        else:
            cur['card_flow'] = None
        rescue = _survival_state(mem, cur)
        rescue.update(egg_priority=True, opens=rescue['opens'] + 1,
                      pending_opens=rescue.get('pending_opens', 0) + 1, menu_ticks=0)
        _record(mem, 'battle_egg_before_retreat', **_battle_labels(cur),
                general=cur.get('ally'), enemy=cur.get('enemy'),
                observed_metric={'ally_hp': cur.get('ally_hp'), 'enemy_hp': cur.get('enemy_hp'),
                                 'egg_uses': (mem.get('egg_uses') or {}).get(cur.get('ally'))},
                reason='本人の卵残数が正数で未試行のため、退却より先に実メニューの召喚可否を確認')
        return _human_menu_open(cur, 'survival', mem)
    retreat = None if melee_followup or dropper_due else _hero_retreat_open(mem, cur)
    if retreat is not None:
        return retreat
    if (_critical_survival_needed(cur) and cur.get('side') == 'defense'
            and (cur.get('card_flow') or {}).get('stage') == 'announce'):
        # Defense has no retreat. An uncalibrated card-use receipt cannot
        # keep a critically wounded general waiting in live melee.
        _card_use_unclassified(mem, cur, '危険HPの防衛戦では未確認の使用告知を待たず、実救済メニューを確認')
        return [pad('b')]
    if cur.get('card_flow'):
        flow = cur['card_flow']
        opening_hp = flow.get('enemy_hp_at_open')
        # g484: Queen 68->34 after selected クースカン, but two idle
        # observations before reopening B let the Queen summon. A measured
        # HP drop after selection is enough to continue an existing chain;
        # the uncalibrated consumption receipt remains unclassified.
        fast_chain = (flow.get('stage') == 'announce'
                      and flow.get('card') in cur.get('cards_selected', [])
                      and type(opening_hp) is int and type(b.enemy_hp) is int
                      and b.enemy_hp < opening_hp
                      and _boss_tactics_allowed(mem, cur)
                      and any(t.get('after_card') == flow.get('card')
                              and t.get('enemy') in (None, b.enemy)
                              and t.get('step') in (None, cur.get('step'))
                              for t in _tactics(mem, cur.get('step'))))
        if fast_chain or melee_followup:
            _card_use_unclassified(mem, cur, ('選択後の敵HP低下と白兵復帰を実測したため、使用告知は未確認のまま白兵で追撃'
                                             if melee_followup else
                                             '選択後の敵HP低下と白兵復帰を実測したため、使用告知は未確認のまま後続札へ進む'))
        else:
            flow['battle_frames_without_receipt'] = flow.get('battle_frames_without_receipt', 0) + 1
            waited = flow['battle_frames_without_receipt']
            opening = flow.get('stage') == 'menu'
            if opening and waited <= CARD_MENU_OPEN_RETRIES:
                return [pad('b')]
            if waited >= (CARD_MENU_OPEN_RETRIES + 1 if opening else 2):
                _card_use_unclassified(mem, cur, '実使用告知を確認できないまま白兵戦へ復帰')
            return []
    if melee_followup:
        watch = cur['card_melee_followup']
        watch['bursts'] += 1
        _record(mem, 'battle_card_melee_followup', **_battle_labels(cur),
                observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp,
                                 'ally_soldiers': cur['ally_soldiers'], 'enemy_soldiers': cur['enemy_soldiers'],
                                 'bursts': watch['bursts'], 'limit': MELEE_HOLD_LIMIT,
                                 'effect_confirmation': 'unclassified'},
                reason='クースカン選択後の敵HP低下と健全な白兵優位を実測したため、保持予算内でA連打して追撃')
        return _power_mash(mem, cur)
    extra = [{'enemy': b.enemy, 'card': card, 'open': True, 'step': cur.get('step'),
              'note': '再攻撃の開幕切り札(チャート逸脱)'}
             for card in ([] if (mem.get('rare_card_kit') or {}).get(cur.get('step')) is not None
                          else _plan_region(mem).get('card_override', {}).get(cur.get('step')) or [])]
    # 元の並びはtactic ID用に保ち、実行順は成立する開幕卵落→
    # 再攻撃extra→チャート/強敵札（2026-11-06オーナー指定）。
    egg_plan = _egg_plan(mem, cur)
    tactics = [*extra, *_tactics(mem, cur.get('step')),
               *_egg_drop_tactics(mem, cur, b.enemy, egg_plan),
               *_strong_card_tactics(mem, cur, b.enemy)]
    tactics.extend(_carried_opening_tactics(mem, cur, tactics))
    # Even old chart/override tactics cannot spend a boss-only card on an ordinary foe.
    if not _boss_tactics_allowed(mem, cur):
        tactics = [t for t in tactics if t['card'] not in reference.BOSS_ONLY_CARDS]
    done = cur.setdefault('tactics_done', [])

    # Preserve original indexes (chart tactic IDs) while prioritizing egg drop.
    # The unverified default "use any carried card at the opening" (tactic id
    # 'd:') is a last resort: ranked above the strong-only tactic it opened
    # with the dropper and the carried strong card never fired against a strong
    # general (g1133 2026-11-04 16:00:50, キール vs マンゴスチン).
    def _tactic_rank(item):
        index, tactic = item
        if tactic.get('egg_drop_only'):
            return 0                   # any measured carried egg drop opens first
        if index < len(extra):
            return 1                   # retry/override opener only after egg drop
        if str(tactic.get('tactic_id') or '').startswith('d:'):
            return 3                   # unverified default: only when nothing else is due
        return 2                       # verified chart tactic / strong card

    ordered = sorted(enumerate(tactics), key=_tactic_rank)
    for index, tactic in ordered:
        tid = tactic.get('tactic_id') or f"{'x' if index < len(extra) else 'c'}{index}:{tactic['card']}"
        if not _tactic_enemy_ok(tactic, b.enemy, cur) or tid in done:
            continue
        if tactic.get('step') and tactic['step'] != cur.get('step'):
            continue
        # A card consumed by the opening tactic cannot be spent again by the
        # later chart; another carried copy remains eligible.
        inventory = next((row for row in egg_plan.get('cards', [])
                          if row['name'] == tactic['card']), None)
        consumed = [*(cur.get('cards_selected') or []),
                    *((mem.get('kit_spent') or {}).get(cur.get('step')) or [])]
        if (tactic['card'] in consumed and egg_plan.get('kit_known')
                and (inventory is None or inventory.get('available') == 0)):
            continue
        if (tactic.get('boss_only')
                and not _boss_tactics_allowed(mem, cur)):
            continue          # do not spend the boss kit in an unmeasured road fight
        strong_evidence = None
        if tactic.get('strong_only'):
            # Owner 2026-11-04: 切り札はピンチの救済ではなく、状況を有利にする
            # ために使う。不利でなくても、携行した強い札は開幕で使う。強い
            # 将軍かどうかは判断の根拠として記録するだけで、使用の条件にしない。
            _, strong_evidence = _strong_enemy(mem, cur)
        # No calibrated use receipt exists, so a selected-then-unclassified card
        # is the best evidence there is. Without chaining on it the charted boss
        # strategy never fires its second card (1-B1: クースカン→ノリウツール)
        # and the queen summons. The chain is recorded as a deviation.
        attempted = [*cur['cards_used'], *[card for card in (cur.get('cards_unclassified') or [])
                                          if card in cur.get('cards_selected', [])]]
        due = (tactic.get('open')
               or (tactic.get('when_hp_at_most') is not None and b.enemy_hp is not None
                   and b.enemy_hp <= tactic['when_hp_at_most'])
               or (tactic.get('after_clash') and cur.get('clashed'))
               or (tactic.get('after_card') and tactic['after_card'] in attempted))
        if due:
            # A prior rejection may be retried only after the measured HP or
            # soldier counts change.  A planned card is not proof that it can
            # finish the enemy, and B is only an inventory inspection.
            refused = (cur.get('card_damage_refusals') or {}).get(tactic['card'])
            signature = _card_damage_key(mem, cur)
            if refused is not None and (refused == signature
                    or (cur.get('card_damage_rescue_needed') and cur.get('card_damage_list_blocked')
                        and cur.get('card_damage_recheck_key') != signature)):
                continue  # both chart and survival retries share the recheck budget
            damage_gate = _card_damage_gate(mem, cur, tactic['card'])
            done.append(tid)
            chained_unconfirmed = (tactic.get('after_card')
                                   and tactic['after_card'] not in cur['cards_used']
                                   and tactic['after_card'] in (cur.get('cards_unclassified') or []))
            note = tactic['note']
            if chained_unconfirmed:
                note = f"{note}（前札の実使用告知は未校正のため選択記録で連続使用）"
                if not cur.get('deviation_reason'):
                    cur['strategy_variant'] = 'after_card_unconfirmed'
                    cur['deviation_reason'] = '前の切り札の実使用告知が未校正のため、選択記録を根拠に連続使用を継続'
            cur['card_flow'] = {'card': tactic['card'], 'stage': 'menu', 'note': note,
                                'enemy_hp_at_open': b.enemy_hp, 'tactic_id': tid}
            # 卵落が最大HP合計の余りを超える札は、命中すれば敵の卵を落とす。
            # どの札が落ちさせても同じなので、チャート札も同じ根拠で監視する。
            drop_evidence = _egg_drop_evidence(cur, tactic['card'])
            if drop_evidence and not drop_evidence['drops']:
                drop_evidence = None
            _record(mem, 'battle_card', **_battle_labels(cur), card=tactic['card'], enemy=b.enemy,
                    enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, reason=note,
                    expected_metric='選択後の実使用告知と敵HP減少',
                    observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp,
                                     'card_damage_gate': damage_gate},
                    **({'strong_enemy': strong_evidence} if strong_evidence is not None else {}),
                    **({'egg_drop': drop_evidence,
                        'egg_plan': {key: egg_plan.get(key) for key in
                                     ('threat', 'deny_opening_egg', 'id_sum', 'droppers',
                                      'dropper', 'max_hp_sum',
                                      'remainder', 'egg_state')}}
                       if drop_evidence else {}),
                    resulting_event='card_planned')
            return [pad('b')]
    preempt = _defender_egg_open(mem, cur)
    if preempt is None:
        preempt = _defender_summon_open(mem, cur)
    if preempt is not None:
        return preempt
    if (_defender_last_resort(mem, cur)
            and not cur.get('okunote_last_resort_reopened')
            and not cur.get('okunote_last_resort_recorded')
            and (cur.get('survival') or {}).get('exhausted')):
        # An earlier healthy menu may have rejected the lottery. Recheck the
        # live menu once when later complete HP panels prove losing melee.
        cur['okunote_last_resort_reopened'] = True
        rescue = _survival_state(mem, cur)
        rescue['exhausted'] = False
        rescue['pending_opens'] = 0
        rescue['menu_ticks'] = 0
        _record(mem, 'battle_okunote_recheck', **_battle_labels(cur),
                observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp']},
                reason='先の使用不能メニューを確認後、押し負けが実測されたため一度だけ再確認')
        return [pad('b')]
    if _survival_needed(cur):
        rescue = _survival_state(mem, cur)
        if (_critical_survival_needed(cur) and not rescue.get('critical_open_recheck')
                and not rescue.get('egg_attempted')
                and (rescue.get('exhausted') or rescue['opens'] >= 12
                     or rescue.get('pending_opens', 0) >= 3)):
            # g749 hero/Athena: three unacknowledged B requests exhausted
            # pending_opens at opens=3. That is also a failed opening budget,
            # so it must allow the existing single critical live inspection.
            rescue.update(critical_open_recheck=True, exhausted=False,
                          pending_opens=3, menu_ticks=0, opens=rescue['opens'] + 1)
            _record(mem, 'battle_survival_critical_recheck', **_battle_labels(cur),
                    observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp'],
                                     'open_attempt': rescue['opens']},
                    reason='救済メニューの未確認表示予算を使い切った後、危険HPで一度だけ再確認')
            return _human_menu_open(cur, 'survival', mem)  # one final live inspection
        conserving = (rescue.get('conserving_melee') is True
                      and _hold_melee_egg(mem, cur, set(rescue.get('conserved_labels') or ())))
        if not conserving and _conserved_army_recheck(mem, cur):
            _record(mem, 'battle_conserved_army_recheck', **_battle_labels(cur),
                    observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp'],
                                     'ally_soldiers': cur.get('ally_soldiers'),
                                     'enemy_soldiers': cur.get('enemy_soldiers'),
                                     'observations': cur['conserved_army_recheck']['observations'],
                                     'limit': 2, 'army_advantage': 'unconfirmed'},
                    reason='実メニューで温存した健全な将軍の兵数が一時未読のため、旧兵数を使わず有限で再観測')
            return [{'type': 'wait', 'ms': 50}]
        if (not conserving and not rescue.get('exhausted') and rescue['opens'] < 12
                and rescue.get('pending_opens', 0) < 3):
            rescue['opens'] += 1
            rescue['pending_opens'] = rescue.get('pending_opens', 0) + 1
            rescue['menu_ticks'] = 0
            return _human_menu_open(cur, 'survival', mem)
    return _melee_step(mem, cur)


def _one_hit_finish_step(mem, cur):
    """Push through a one-hit-lethal enemy; open no menu and hold no input."""
    flow = cur.get('card_flow')
    if flow:
        if flow.get('stage') == 'announce':
            _card_use_unclassified(mem, cur, '敵が一撃で倒れるため、未確認の切り札使用告知は未分類のまま白兵へ戻る')
        else:
            cur['card_flow'] = None
    cur['melee_holds'] = 0
    if not cur.get('one_hit_finish'):
        cur['one_hit_finish'] = True
        _record(mem, 'battle_one_hit_finish', **_battle_labels(cur),
                enemy=cur.get('enemy'), enemy_hp=cur.get('enemy_hp'), ally_hp=cur.get('ally_hp'),
                observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp'),
                                 'wall_damage': EGG_WALL_DAMAGE},
                reason='敵HPが一撃で倒せる値のため、奥の手・卵・切り札を使わず白兵で押し切る')
    return _power_mash(mem, cur)


def _melee_step(mem, cur):
    side = cur.get('side')
    defense = True if side == 'defense' else False if side == 'attack' else None
    triggers = enemy_egg_triggers(cur.get('enemy'), player_castle_defense=defense)
    charted = _charted_melee(mem, cur)
    probing = cur.get('card_damage_rescue_needed') is True
    probe_ticks = max(int(cur.get('melee_holds') or 0),
                      int(cur.get('card_damage_probe_ticks') or 0) if probing else 0)
    holds = probe_ticks + (int(cur.get('card_damage_rechecks') or 0) if probing else 0)
    # The count bound cannot see the bleed: hold reads that stay under
    # MELEE_HOLD_LIMIT can still spend most of the fight's opening HP.
    start_hp, hp = cur.get('start_ally_hp'), cur.get('ally_hp')
    if type(start_hp) is int and type(hp) is int and hp > 0:
        hp_budget = max(MELEE_HP_HOLD_FLOOR, start_hp // MELEE_HP_HOLD_DIVISOR)
        hp_bled = start_hp - hp
    else:
        hp_budget = hp_bled = None      # fail closed to the count bound alone
    # Only a hold that is actually in charge can be cut by the budget; an
    # eggless, clash-free or charted melee already mashes regardless.
    hold_cut = (hp_bled is not None and hp_bled >= hp_budget
                and triggers.has_egg is not False and triggers.clash_position is not False
                and not charted)
    forced = (bool(cur.get('melee_forced')) or holds >= MELEE_HOLD_LIMIT
              or hold_cut)
    probe = probing and not forced
    # An unbounded hold is a passive death: after the bound the melee proceeds
    # for the rest of the fight even at the clash egg risk (the rescue already
    # had its chances).
    # There is no calibrated egg-loss receipt yet. A selected dropper plus
    # lower HP is only a candidate, including True flags saved by older bots.
    # The existing HP/count budgets still end a passive hold boundedly.
    duel = cur.get('event_duel') is True
    safe = (triggers.has_egg is False or triggers.clash_position is False
            or charted or forced or probe or duel)
    cur['melee_forced'] = forced
    # A probe is in `safe`, so melee_holds stays zero.  Only probe_ticks is
    # incremented below; inventory rechecks are not copied into either count.
    cur['melee_holds'] = 0 if safe else holds + 1
    if probe:
        cur['card_damage_probe_ticks'] = probe_ticks + 1
    if safe and not probe and _hero_losing_melee(cur):
        # Mashing a hero trade we are measurably losing ends the run: after
        # the hold budget the melee would push to HP 0 (g749 hero/Athena).
        # The command menu is the only way out of a defense, so reopen it
        # while its result is still unobserved, bounded per fight.
        rescue = _survival_state(mem, cur)
        reopens = int(rescue.get('hero_reopens') or 0)
        if (rescue.get('menu_ticks', 0) == 0 and not rescue.get('egg_attempted')
                and reopens < HERO_SURVIVAL_REOPEN_LIMIT):
            rescue.update(hero_reopens=reopens + 1, exhausted=False,
                          pending_opens=0, menu_ticks=0)
            _record(mem, 'battle_hero_survival_recheck', **_battle_labels(cur),
                    enemy=cur.get('enemy'), enemy_hp=cur.get('enemy_hp'),
                    ally_hp=cur.get('ally_hp'),
                    observed_metric={'melee_balance': _melee_balance(cur),
                                     'episode': reopens + 1, 'limit': HERO_SURVIVAL_REOPEN_LIMIT,
                                     'ally_hp_bled': ((start_hp - hp)
                                                      if type(start_hp) is int and type(hp) is int
                                                      else None),
                                     'enemy_hp': cur.get('enemy_hp'),
                                     'enemy_opening_hp': cur.get('start_enemy_hp')},
                    reason='主人公だけが押し負けている白兵のため、未確認の戦闘メニューを再び開いて卵・切り札を探す')
            return _human_menu_open(cur, 'survival', mem)
    mode = 'card_damage_probe' if probe else 'power_mash' if safe else 'egg_safe_hold'
    egg_window = _defender_egg_window(mem, cur)
    taps = 1 if probe or egg_window and egg_window['short_burst'] else POWER_TAPS
    actions = _power_mash(mem, cur, taps=taps) if safe else []
    # Per returned action batch, not just the first use in a fight. These are
    # requested A frames; the executor's fence/delivery result remains separate.
    _record(mem, 'battle_melee', **_battle_labels(cur),
            enemy=cur.get('enemy'), enemy_hp=cur.get('enemy_hp'), ally_hp=cur.get('ally_hp'),
            observed_metric={'melee_balance': _melee_balance(cur)},
            egg_risk_flags=asdict(triggers), melee_control_mode=mode,
            enemy_egg_dropped=None,
            enemy_egg_drop_expected=cur.get('enemy_egg_drop_expected') is True,
            a_frames_sent=taps * 3 if safe else 0,
            clash_gauge=cur.get('clash_gauge'),
            **({'defender_egg_window': egg_window} if egg_window else {}),
            hold_hp_budget=hp_budget, hold_hp_bled=hp_bled,
            reason=('札での撃破を未保証のため、保持予算内の短い入力で実HP・兵数を再観測' if probe
                    else '月一イベントの一騎打ちは保留せず、ぶつかり合いの青ゲージでA連打' if duel
                    else 'チャートのぶつかり合いに向けて押し込む' if charted
                    else '保持中のHP劣化が予算に達したため押し込む' if hold_cut
                    else '卵の激突リスクの保留上限に達したため押し込む' if holds >= MELEE_HOLD_LIMIT
                    else '保持の打ち切り後はこの戦闘を通しで押し込む' if forced
                    else '卵の激突判定なし' if safe
                    else '卵の激突リスクあり・戦線位置未校正のため入力保留'))
    return actions


# Short, released A bursts cover the POWER window without relying on an old
# screenshot to time its leading edge. Reobserve after 400 ms of planned input;
# card/egg menus still use their own policies. Six taps without release waits
# lost the isolated RetroArch tutorial, despite winning a frame-stepped replay.
POWER_TAPS = 4


def _power_mash(mem, cur, *, taps=POWER_TAPS):
    if not cur.get('power_mash'):
        cur['power_mash'] = True
        _record(mem, 'battle_power', **_battle_labels(cur),
                expected_metric={'taps': taps, 'hold_ms': 50, 'release_ms': 50},
                observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp')},
                reason='白兵のぶつかり合いで押し負けないようA連打で踏ん張る')
    # xdotool's keyup/next keydown can fall between emulator polls. An
    # explicit release is necessary for four distinct presses, not a hold.
    return [action for _ in range(taps)
            for action in (pad('a', 3), {'type': 'wait', 'ms': 50})]


def note_duel_event(screen, mem):
    flat = ''.join((screen.text or '').split())
    if flat and any(tok in flat for tok in DUEL_EVENT_TOKENS):
        mem['duel_event'] = {'tick': int(mem.get('tick') or 0), 'text': flat[-40:]}


def _hero_retreat_open(mem, cur):
    if not (egg_bait.retreat_due(mem, cur) or _hero_retreat_needed(cur)) or cur.get('side') == 'defense':
        return None
    if _critical_safe_card(mem, cur, cur.get('survival') or {}, for_retreat=True):
        return None
    if _egg_before_retreat(mem, cur):
        return None
    flow = cur.get('hero_retreat') or {}
    if flow.get('unavailable') or flow.get('exhausted') or flow.get('opens', 0) >= 3:
        return None
    card_flow = cur.get('card_flow') or {}
    if card_flow.get('stage') == 'announce':
        # Preserve a selected card's receipt while HP is above the critical
        # threshold. At critical HP, waiting for an uncalibrated receipt can
        # cost the general before the retreat menu opens (g460: ココット
        # 24 -> 12 -> 0 while stuck in card menus). Unknown card use stays
        # unknown; survival takes precedence over continuing that selection.
        hp = cur.get('ally_hp')
        if type(hp) is not int or hp > GENERAL_CRITICAL_RETREAT_HP:
            return None
        pending_card = card_flow.get('card')
        _card_use_unclassified(mem, cur, '危険HPに達したため未確認の切り札を保留し、退却を優先')
        _record(mem, 'battle_retreat_preempted_card', **_battle_labels(cur),
                general=cur.get('ally'), enemy=cur.get('enemy'), card=pending_card,
                observed_metric={'ally_hp': hp, 'enemy_hp': cur.get('enemy_hp'),
                                 'card_confirmation': 'unclassified'},
                resulting_event='retreat_preempted_card_use',
                reason='切り札の使用確認を待つ間に将軍を失わず、主人公の敗北も避けるため退却を優先')
    flow = cur.setdefault('hero_retreat', {})
    flow['opens'] = flow.get('opens', 0) + 1
    cur['card_flow'] = None  # supersede an unselected chart card intention
    _record(mem, 'battle_hero_retreat_open', **_battle_labels(cur),
            general=cur.get('ally'), enemy=cur.get('enemy'),
            castle=cur.get('castle'), side=cur.get('side'),
            observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp'],
                             'start_ally_hp': cur.get('start_ally_hp'),
                             'melee_balance': _melee_balance(cur),
                             'retreat_assessment': dict(cur.get('retreat_assessment') or {})},
            reason='主人公の敗北によるゲームオーバーまたは一般将軍の喪失を避けるため退却の可否を確認')
    return _human_menu_open(cur, 'retreat', mem)


def _defender_summon_open(mem, cur):
    window = _defender_summon_preempt(mem, cur)
    if not window:
        return None
    guard = cur.setdefault('okunote_summon_preempt', {'opens': 0})
    if guard['opens'] >= DEFENDER_SUMMON_OPEN_LIMIT:
        guard['exhausted'] = True
        _record(mem, 'battle_okunote_summon_open_failed', **_battle_labels(cur),
                observed_metric=window,
                reason='敵召喚への奥の手メニューを有限回試したが確認できず、入力を止めず既存戦闘へ戻る')
        return None
    guard.update(opens=guard['opens'] + 1, reason=window['reason'], stage='opening')
    _record(mem, 'battle_okunote_summon_open', **_battle_labels(cur),
            observed_metric={**window, 'open_attempt': guard['opens']},
            expected_metric='実メニューの奥の手と現在カーソルを確認して選択',
            reason='敵の召喚獣が出ていて味方の卵・札が使えないため、連打を止めて奥の手を確認する')
    return _human_menu_open(cur, 'defender_summon', mem)


def _defender_egg_open(mem, cur):
    window = _defender_egg_window(mem, cur)
    if not window or not window['due']:
        return None
    guard = cur.setdefault('okunote_egg_preempt', {'opens': 0})
    if guard['opens'] >= DEFENDER_EGG_OPEN_LIMIT:
        guard['exhausted'] = True
        _record(mem, 'battle_okunote_egg_open_failed', **_battle_labels(cur),
                observed_metric=window,
                reason='召喚前の奥の手メニューを有限回試したが確認できず、入力を止めず既存戦闘へ戻る')
        return None
    guard.update(opens=guard['opens'] + 1, reason=window['reason'], stage='opening')
    _record(mem, 'battle_okunote_egg_open', **_battle_labels(cur),
            observed_metric={**window, 'open_attempt': guard['opens']},
            expected_metric='実メニューの奥の手と現在カーソルを確認して選択',
            reason='味方の卵・札が使えず敵の召喚危険が目前のため、連打を止めて奥の手を確認する')
    return _human_menu_open(cur, 'defender_egg', mem)


def battle_end(mem, next_kind, *, defense_continues=False, actor_continues=False,
               attack_continues=False, panel_changed=False):
    _hold_general_loss_metric(mem)
    _migrate_card_evidence(mem)
    cur = mem.get('battle')
    if not cur:
        return
    # A panel can blink during the melee; end only on the second
    # consecutive observation without it.
    cur['away'] = cur.get('away', 0) + 1
    if cur['away'] < 2 and not (defense_continues or actor_continues or attack_continues or panel_changed):
        return
    from ..os_layer.roster import dirty_status
    dirty_status(mem, [cur.get('ally')])
    if (cur.get('card_flow') or {}).get('stage') == 'announce':
        _card_use_unclassified(mem, cur, '実使用告知がないまま戦闘が終了')
    mem['battle_seen'] = None
    mem.pop('battle', None)
    _plan_region(mem)['attack'] = None
    enemy_hp, ally_hp = cur.get('enemy_hp'), cur.get('ally_hp')
    if ally_hp == 0:
        from ..os_layer.roster import defeated
        defeated(mem, cur.get('ally'))  # uncertain for this general; not proof of death
    if enemy_hp == 0 and ally_hp not in (None, 0):
        outcome = 'win'
    elif ally_hp == 0 and enemy_hp not in (None, 0):
        outcome = 'loss'
    else:
        outcome = 'unclassified'
    castle = cur.get('castle')
    from ...hanjuku_map_sources import SEASON_KINGS
    seasonal_boss = (mem.get('chapter') == 4 and
                     (cur.get('enemy') in SEASON_KINGS or chart.is_boss_castle(castle)))
    season_win = (seasonal_boss and outcome == 'win' and cur.get('side') == 'attack'
                  and cur.get('enemy') in SEASON_KINGS
                  and castle in (chart.boss_castle(4), *chart.boss_sites(4))
                  and cur.get('entry_evidence') == 'measured_boss_entry')
    receipt = cur.get('defeat_owner') or {}
    observed_owner = (receipt.get('owner') if isinstance(receipt, dict)
                      and receipt.get('castle') == castle
                      and receipt.get('chapter') == (mem.get('chapter') or 0)
                      and castle in chart.castles(mem.get('chapter') or 0)
                      and receipt.get('owner') in ('own', 'enemy')
                      and type(ally_hp) is int and type(enemy_hp) is int
                      and cur.get('side') == 'defense' and outcome == 'loss' else None)
    defense_retained = observed_owner == 'own'
    if (outcome == 'win' and castle and cur.get('side') == 'attack' and not seasonal_boss
            and not attack_continues):
        (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
        captured = mem.setdefault('captured', [])
        if castle not in captured:
            captured.append(castle)
        mem['lost'] = [c for c in mem.get('lost') or [] if c != castle]
        # The winner occupies the castle it took.
        _world(mem).setdefault('garrison', {})[castle] = []
        _garrison_move(mem, cur.get('ally'), target=castle)
        # Our own general stands there now: the remembered defender is stale.
        _drop_defender_record(mem, [castle])
        # Read the taken castle's own list: residents that retreated come
        # back, and the capture is the receipt for who is really there.
        mem.setdefault('roster_read_due', {})[castle] = (
            int(mem.get('tick') or 0) + CAPTURE_READ_DELAY)
    if (outcome == 'win' and castle and cur.get('side') == 'defense' and not seasonal_boss
            # g1075 2026-10-29 23:58: a recruit had cleared アルマムーン's list
            # and the joiner's win made it [ビシソワーズ]; the hero's castle
            # then looked one-general and retakes waited 37 minutes. A
            # defender is one resident, not the list: unread stays unread.
            and (_world(mem).get('garrison') or {}).get(castle) is not None):
        _garrison_move(mem, cur.get('ally'), target=castle)
    if (outcome == 'loss' and castle and cur.get('side') == 'defense' and not seasonal_boss
            and not (defense_continues or defense_retained)):
        (mem.get('castle_income') or {}).pop(STATUS_NAMES.get(castle, castle), None)
        mem['captured'] = [c for c in mem.get('captured', []) if c != castle]
        # Snapshot the garrison before it is dropped: a defense loss is the
        # only loss path that never recorded castle_lost_observed (g1163
        # chapter 7 2026-11-09: マンシー and リデンブロック fell with no loss
        # record at all), and the garrison is what a later fix needs.
        _garrison_row = (_world(mem).get('garrison') or {}).get(castle)
        _garrison_before = list(_garrison_row or ())
        garrison_lost(mem, castle)
        lost = mem.setdefault('lost', [])
        if castle not in lost:
            lost.append(castle)
            _record(mem, 'castle_lost_observed', castle=castle,
                    observed_metric={'cause': 'defense_loss',
                                     'garrison': _garrison_before[:8],
                                     # g1163 2026-11-09 19:49 フィロメラ: a
                                     # defender fought while the bookkeeping
                                     # held no row at all, so the empty list
                                     # alone reads as "undefended" when it was
                                     # only unread.
                                     'garrison_known': isinstance(_garrison_row, list),
                                     'ally': cur.get('ally'), 'enemy': cur.get('enemy')},
                    resulting_event=f'lost:{castle}',
                    reason='守備戦で敗れて城を失ったため、守備人数と相手を残して奪還対象にする')
        # g1075 15:42:43: パタゴーヌ fell, the survey Y closed before its
        # enemy flag was read, and the roof streak built while it was ours
        # (164 readings) took it back 3 s later. The loss is the newest flag.
        mem.setdefault('world_flags', {})[castle] = {
            'kind': 'enemy', 'tick': int(mem.get('tick') or 0), 'chapter': mem.get('chapter') or 0}
        (mem.get('owner_streak') or {}).pop(castle, None)
        # The attacking enemy general moved into the castle he took, so a
        # retake sortie can plan against the panel we just saw (#1553).
        if (isinstance(cur.get('enemy'), str) and cur['enemy'] not in reference.BOSS_GENERALS
                and cur['enemy'] not in chart.BOSSES.values()
                and general_max_hp(cur['enemy']) is not None
                and castle in chart.castles(mem.get('chapter') or 0)):
            mem.setdefault('castle_defenders', {})[castle] = {
                'general': cur['enemy'], 'chapter': mem.get('chapter') or 0,
                'evidence': 'defense_loss_capture', 'step': cur.get('step')}
    if outcome == 'loss' and (cur.get('side') == 'attack' or defense_continues or defense_retained or actor_continues) and cur.get('ally'):
        general = cur['ally']
        for source in list(_world(mem).get('garrison') or {}):
            _garrison_move(mem, general, source=source)
        unknown = _world(mem).setdefault('general_location_unknown', [])
        if general not in unknown:
            unknown.append(general)
        _record(mem, 'general_location_unclassified', general=general, castle=castle,
                reason='敗北した将軍の所在は未確認のため古い駐留情報を破棄し、一覧での再確認を待つ')
    stats = mem.setdefault('stats', {'wins': 0, 'losses': 0, 'unclassified': 0, 'cards_used': 0,
                                     'generals_lost': None, 'cards_confirmed': 0, 'card_evidence_version': 1})
    stats[{'win': 'wins', 'loss': 'losses'}.get(outcome, 'unclassified')] += 1
    confirmed = len(cur.get('cards_used', []))
    stats['cards_confirmed'] = stats.get('cards_confirmed', 0) + confirmed
    if stats.get('cards_used') is not None and cur.get('card_consumption_complete') is True:
        stats['cards_used'] += confirmed
    else:
        stats['cards_used'] = None
    indep = cur.get('independent')
    if isinstance(indep, dict) and outcome in ('win', 'loss'):
        experience.record(mem, indep.get('key'), indep.get('action'), outcome)
        _record(mem, 'experience_result', experience_key=indep.get('key'),
                experience_action=indep.get('action'), outcome=outcome,
                reason='独自判断の結果を経験記憶へ反映')
    step = cur.get('step')
    boss_order = _order_for_step(mem, step)
    chapter = mem.get('chapter') or 0
    boss_name = chart.BOSSES.get(chapter)
    boss_cell = chart.boss_castle(chapter)
    boss_attempt = (_is_boss_order(boss_order, mem)
                    and ((cur.get('enemy') == boss_name and cur.get('castle') == boss_cell)
                         # a 四季王 site lost is sent again like any boss
                         or (chapter == 4 and cur.get('enemy') in SEASON_KINGS
                             and cur.get('castle') in chart.boss_sites(4)))
                    and cur.get('side') == 'attack'
                    and cur.get('entry_evidence') == 'measured_boss_entry')
    # An unclassified end also leaves no measured defeat (g1075 2026-10-30:
    # 4-B3 キッシュ closed his ウイナッツォ fight 26/537 with neither HP at 0;
    # 4-B1・4-B4's site fights never left a result at all). Retrying it like a
    # measured loss is the only way a launched boss step comes back: the same
    # bounded budget applies.
    if outcome in ('loss', 'unclassified') and boss_attempt:
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        for key in ('rare_card_kit', 'strong_card_kit', 'dropper_card_kit', 'sortie_actor_miss'):
            mem.setdefault(key, {}).pop(step, None)
        for key in ('general_override', 'card_override'):
            (_plan_region(mem).get(key) or {}).pop(step, None)
        _plan_region(mem).setdefault('order_context', {}).pop(step, None)
        _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
        if retries[step] <= 3:
            _plan_region(mem).setdefault('orders', {})[step] = 'pending'
            context = {'strategy_variant': 'retry_chart_boss_kit',
                       'deviation_reason': ('ボス戦のHP敗北を確認。出撃将軍と既定切り札を再確認して再試行'
                                            if outcome == 'loss' else
                                            'ボス戦が決着HPなしに終わり撃破の実測がないため、出撃将軍と既定切り札を再確認して再試行'),
                       'expected_metric': {'general': boss_order['general'],
                                           'cards': list(boss_order['cards']),
                                           'goal': f'{boss_name or "ボス"}戦勝利'}}
            mem.setdefault('retry_context', {})[step] = context
            _record(mem, 'order_retry', chart_step=step, **context,
                    observed_metric={'outcome': outcome, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    resulting_event=f'retry:{retries[step]}',
                    reason=('既定のチャート装備を確認してボスへ再挑戦' if outcome == 'loss' else
                            '決着が実測できないままボス戦が終わったため、チャート装備を確認してボスへ再挑戦'))
        else:
            _plan_region(mem).setdefault('orders', {})[step] = 'failed'
            drop_retired_step_context(mem)
            _record(mem, 'situation_held', chart_step=step, strategy_variant='boss_retry_exhausted',
                    observed_metric={'retries': retries[step]}, reason='ボス再試行の上限に到達したため保留')
    elif outcome == 'loss' and (_is_boss_order(boss_order, mem) or cur.get('enemy') == boss_name):
        _record(mem, 'situation_held', chart_step=step, strategy_variant='boss_entry_unclassified',
                observed_metric={'castle': castle, 'side': cur.get('side'),
                                 'entry_evidence': cur.get('entry_evidence')},
                reason='実測ボス突入の証拠がないため再出撃を保留')
    elif (outcome == 'loss' and cur.get('side') == 'attack' and step
            and castle not in mem.get('captured', [])):
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        if retries[step] <= 2:
            _plan_region(mem).setdefault('orders', {})[step] = 'pending'
            # Better than repeating a lost melee: open with up to two イッテツーン
            # (10 general damage each per the chart's card table). Only copies
            # the measured stock holds: a card at 0 cannot be carried, and
            # planning it anyway kept the retreat gate open as if a resource
            # remained (g1153 2026-11-08 13:21 ココット: 在庫0のイッテツーン2枚を
            # 計画し、使用も携行もできず白兵で敗北した).
            stock = mem.get('card_stock') or {}
            copies = min(2, max(0, int(stock.get('イッテツーン', 0) or 0)))
            opening = ['イッテツーン'] * copies
            if opening:
                _plan_region(mem).setdefault('card_override', {})[step] = opening
                reason = f'チャートの白兵で敗北したため、開幕イッテツーン{len(opening)}枚で再攻撃'
                expected = {'enemy_hp_after_open': max(0, (enemy_hp or 0) - 10 * len(opening))}
            else:
                # A stale override would keep planning cards the stock cannot
                # carry; the next sortie starts from the chart kit again.
                _plan_region(mem).get('card_override', {}).pop(step, None)
                reason = 'チャートの白兵で敗北したため、実在庫の切り札なしでチャート装備のまま再攻撃'
                expected = {'enemy_hp_after_open': None}
            mem.setdefault('retry_context', {})[step] = {
                'deviation_reason': reason, 'expected_metric': expected}
            _record(mem, 'order_retry', chart_step=step, strategy_variant='retry_with_opening_cards',
                    deviation_reason=reason, expected_metric=expected,
                    observed_metric={'outcome': outcome, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    resulting_event=f'retry:{retries[step]}', reason='城を取らないと後続手順とボスへ進めない')
        else:
            # Leaving the exhausted step 'launched' made it look like a march
            # still heading for the target: steps waiting on that capture and
            # the off-chart planner kept waiting on a sortie that had ended.
            _plan_region(mem).setdefault('orders', {})[step] = 'failed'
            drop_retired_step_context(mem)
            _record(mem, 'situation_held', chart_step=step, strategy_variant='attack_retry_exhausted',
                    observed_metric={'retries': retries[step], 'outcome': outcome,
                                     'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    reason='攻撃の再試行上限に到達したため、この手順を打ち切って計画外の判断へ渡す')
    boss = chart.BOSSES.get(mem.get('chapter') or 0)
    resulting = None
    if season_win:
        victories = mem.setdefault('boss_victories', {})
        previous = victories.get(cur['enemy'])
        # A repeated/generic ボス entry proves the king's defeat again,
        # but cannot erase the corner already measured for that king.
        site = castle if castle in chart.boss_sites(4) else None
        if not (site is None and isinstance(previous, dict)
                and previous.get('site') in chart.boss_sites(4)):
            victories[cur['enemy']] = {
                'enemy': cur['enemy'], 'chapter': 4, 'site': site,
                'evidence': 'measured_boss_entry_hp_win', 'tick': mem.get('tick')}
        resulting = f"season_king_defeated:{cur['enemy']}"
        if SEASON_KINGS <= set(victories):
            mem['boss_defeated'] = 4
            resulting = 'chapter_4_boss_defeated'
    elif outcome == 'win' and boss and cur.get('enemy') == boss and mem.get('chapter') != 4:
        resulting = f"chapter_{mem['chapter']}_boss_defeated"
        # A boss HP reading proves this battle result, not the next chapter.
        # Only a visible chapter header or a next-chapter fact (castle name,
        # enemy general debut) may advance and reset route state; the defeat
        # is the context a further-jump general needs (observe_chapter_general).
        mem['boss_defeated'] = mem.get('chapter')
    elif outcome == 'win' and castle and cur.get('side') == 'attack' and not seasonal_boss:
        resulting = f'captured:{castle}'
    elif (outcome == 'loss' and castle and cur.get('side') == 'defense' and not seasonal_boss
          and not (defense_continues or defense_retained)):
        resulting = f'lost:{castle}'
    if defense_continues:
        resulting = f'defense_continues:{castle}'
    elif attack_continues:
        resulting = f'attack_continues:{castle}'
    elif defense_retained:
        resulting = f'defense_retained:{castle}'
    _record(mem, 'battle_result', **_battle_labels(cur), enemy=cur.get('enemy'),
            expected_metric=cur.get('strategy_expected'),
            ally=cur.get('ally'), castle=castle, side=cur.get('side'), outcome=outcome,
            observed_metric={'enemy_hp': enemy_hp, 'ally_hp': ally_hp,
                             'cards_used': cur.get('cards_used', []),
                             'cards_selected': cur.get('cards_selected', []),
                             'cards_missing': cur.get('cards_missing', []),
                             'cards_unclassified': cur.get('cards_unclassified', []),
                             'card_consumption_complete': cur.get('card_consumption_complete', False),
                             'hero_retreat_selected': bool((cur.get('hero_retreat') or {}).get('selected')),
                             'general_loss': 'unclassified',
                             **({'castle_owner_after_defeat': observed_owner} if observed_owner else {})},
            resulting_event=resulting or outcome, resulting_stage=None, next_screen=next_kind,
            reason='戦闘終了時のHP表示から判定' if outcome != 'unclassified'
            else '最終HPが0/非0で確定しないため未分類')
    # Owner 2026-11-05: an attack that ended at its destination closes the
    # sortie record. It used to stay 'en_route' after a defeat, so the left
    # status chart kept showing a march that was already over and the ledger
    # never learned win/loss/retreat by diff. A battle on the road (castle
    # different from the sortie target) leaves the march running.
    sortie = (_sorties(mem).get(step)) if step else None
    if isinstance(sortie, dict) and cur.get('side') == 'attack':
        target = sortie.get('target') or sortie.get('planned_target')
        retreat = bool((cur.get('hero_retreat') or {}).get('selected'))
        lost_battle = outcome == 'loss' or retreat
        won_battle = outcome == 'win' and (enemy_hp == 0)
        if castle and target and castle == target and (won_battle or lost_battle):
            result = 'won' if won_battle else ('retreated' if retreat else 'lost')
            _sorties(mem).pop(step, None)
            history = mem.setdefault('sortie_history', [])
            history.append({'step': step, 'general': sortie.get('general'),
                            'source': sortie.get('source'), 'target': target,
                            'outcome': result, 'tick': int(mem.get('tick') or 0),
                            'castle': castle, 'ally_hp': ally_hp, 'enemy_hp': enemy_hp})
            del history[:-40]
            _record(mem, 'sortie_outcome', chart_step=step, general=sortie.get('general'),
                    castle=castle,
                    observed_metric={'outcome': result, 'ally_hp': ally_hp,
                                     'enemy_hp': enemy_hp},
                    reason='戦闘結果で出撃の終了を差分記録し、行軍中の表示から外す')
    # g1163 2026-11-10 23:24: キール fell to 0 HP in a field encounter on the
    # way to おかざき (side None, castle None: a road battle bound to his
    # step); the order stayed 'launched' until the march deadline at 23:47
    # and nobody was sent there for 23 minutes. A general at 0 HP leaves the
    # roster (g1097 2026-11-02) and a retreating one turns back, so the march
    # is over: mark it and let the overdue settlement run now. It keeps the
    # location recheck, the relaunch budget and the blocked-path rule instead
    # of a second settlement. Defense battles and the target's own battle
    # (closed above) are not road battles.
    sortie = (_sorties(mem).get(step)) if step else None
    if isinstance(sortie, dict) and cur.get('side') in (None, 'attack'):
        target = sortie.get('target') or sortie.get('planned_target')
        retreat = bool((cur.get('hero_retreat') or {}).get('selected'))
        if (target and castle != target and cur.get('ally') == sortie.get('general')
                and (retreat or (outcome == 'loss' and ally_hp == 0))
                and sortie.get('status') in ('en_route', 'launched_unconfirmed')
                and type(sortie.get('road_defeat')) is not int):
            sortie['road_defeat'] = int(mem.get('tick') or 0)
            # The outcome ledger is what the march and the attack rules read
            # (``sortie_history`` consumers). A road defeat
            # that only lived on the sortie record was invisible to them:
            # g1163 2026-11-12 15:1x-15:5x lost :11-:15 on the road to オオキク
            # and ``sortie_history`` still ended at :11, so nothing could see
            # that the exchange kept losing.
            history = mem.setdefault('sortie_history', [])
            history.append({'step': step, 'general': sortie.get('general'),
                            'source': sortie.get('source'), 'target': target,
                            'outcome': 'lost', 'tick': int(mem.get('tick') or 0),
                            'castle': castle, 'ally_hp': ally_hp, 'enemy_hp': enemy_hp,
                            'road': True})
            del history[:-40]
            _record(mem, 'sortie_road_defeat', chart_step=step, general=sortie.get('general'),
                    castle=castle,
                    observed_metric={'target': target, 'retreat': retreat,
                                     'ally_hp': ally_hp, 'enemy_hp': enemy_hp},
                    reason='目的地に着く前の戦闘で出撃した将軍が敗北・退却したため、行軍の期限を待たずに出撃を決着させる')
    # One judged battle per started battle, whatever the verdict. The gap
    # against battles_started is the set the status panel must disclose.
    _tally(mem, 'battles_judged')
    from . import camp_recheck as hanjuku_camp_recheck
    hanjuku_camp_recheck.capture(mem, cur)
    _maybe_recall_weak_hero(mem, cur, outcome, ally_hp)
    # A battle result may return straight to the map while the post-month
    # status owner is still expecting its pre-battle roster or SELECT menu.
    # Restore that owner before the next observation can select a sortie.
    from . import post_month_status
    post_month_status.resume_after_battle(mem, cur)


def _maybe_recall_weak_hero(mem, cur, outcome, ally_hp):
    """Pull the hero back to a castle after a win that left him weak.

    g436 23:15-23:18: the hero, marching on ゴーメン (1-A2), won a road battle
    90 -> 14 HP with no soldiers left, marched on, and fell there within two
    seconds of the next battle opening (14 -> 9 -> 0): too fast for any
    in-battle retreat. The march itself has to be called off. The unit menu
    SELECT + A opens is the camp menu (g436 22:04), so the camp recall's
    きかん path is reused.
    """
    if cur.get('ally') != NAME or outcome != 'win' or type(ally_hp) is not int or _flow(mem).get('recall'):
        return
    full = int(mem.get('hero_max_hp') or 0)
    limit = max(HERO_RECALL_MIN_HP, full * HERO_RECALL_RATIO_TENTHS // 10)
    marching = _hero_marching(mem)
    if ally_hp > limit or not marching:
        return
    _flow(mem)['recall'] = {'stage': 'hero_focus', 'hero': True, 'steps': 0, 'sorties': marching}
    (mem.get('y_jumps') or {}).pop('RECALL:target', None)
    (mem.get('y_jumped') or {}).pop('RECALL', None)
    _record(mem, 'hero_recall_start', observed_metric={'ally_hp': ally_hp, 'full_hp': full,
                                                        'sorties': marching},
            reason=f'主人公がHP{ally_hp}まで減って進軍中のため、次の戦闘で倒れる前に自軍城へ帰還させる')


ATTACK = re.compile(r'(\S+?)しょうぐんが(\S+?)じょうにのりこんだ')
DEFENSE = re.compile(r'(\S+?)じょうがてきにせめこまれ')
BOSS_ENTRY_HOLD_LIMIT = 8   # unmatched boss entry messages before the screen is closed anyway
OPENING_DIALOGUE_WAIT_LIMIT = 12   # consecutive taunt-visible panels before the existing judgment resumes (chosen bound, not measured)


def _boss_entry_cell(mem, general):
    """The boss castle an entry text names: chapter 4's 「ボスじょう」 is one of
    four 四季王 sites, the one this general is marching on (no site, no guess)."""
    chapter = mem.get('chapter') or 0
    current = _plan_region(mem).get('attack') or {}
    if (chapter == 4 and current.get('general') == general and current.get('castle') in chart.boss_sites(4)
            and current.get('entry_evidence') == 'measured_boss_entry'):
        # The same entry text on the next frame: its march is arrived by now
        # (g1075 08:41:12-22: ヴィーナス's 4-B4 entry, bound once, was held
        # 7 times and then rewritten to ボス without a step).
        return current['castle']
    if chapter == 4:
        # An unconfirmed launch keeps its site as planned_target only (g1075
        # 08:34:57: カペリーニ's 4-B2 entry matched nothing and was held 7 times).
        now = int(mem.get('tick') or 0)
        sorties = [s for s in _sorties(mem).values()
                   if isinstance(s, dict) and s.get('general') == general
                   and (s.get('target') or s.get('planned_target')) in chart.boss_sites(4)]
        # A re-sent step replaces its sortie; the retired attempt still names
        # the site this general is marching to.
        retired = _world(mem).get('retired_sorties')
        for rows in (retired.values() if isinstance(retired, dict) else ()):
            sorties.extend(s for s in rows or ()
                           if isinstance(s, dict) and s.get('general') == general
                           and (s.get('target') or s.get('planned_target')) in chart.boss_sites(4))
        active = [s for s in sorties if s.get('status') in ('en_route', 'launched_unconfirmed')]
        sites = {s.get('target') or s.get('planned_target') for s in active}
        if len(sites) == 1:
            return sites.pop()
        if not active:
            overdue = [s for s in sorties if s.get('status') == 'overdue'
                       and type(s.get('tick')) is int
                       and 0 <= now - s['tick'] < SITE_LATE_ENTRY_TICKS]
            if overdue:
                newest = max(s['tick'] for s in overdue)
                sites = {s.get('target') or s.get('planned_target') for s in overdue if s['tick'] == newest}
                if len(sites) == 1:
                    return sites.pop()
    return chart.boss_castle(chapter)


def _friendly_general(mem, general):
    """This run's own receipts mark the general as ours.

    Only measured evidence counts: a sortie record (even one whose march
    bound expired), a garrison/general-list row, this month's roster
    receipt, or a launched record. Enemy generals never enter these stores.
    """
    if not isinstance(general, str) or not general or general in (NAME, 'しゅじんこう'):
        return general in (NAME, 'しゅじんこう')
    for pool in (_world(mem).get('sorties'), _world(mem).get('retired_sorties')):
        if not isinstance(pool, dict):
            continue
        for row in pool.values():
            items = row if isinstance(row, list) else (row,)
            if any(isinstance(s, dict) and s.get('general') == general for s in items):
                return True
    for present in (_world(mem).get('garrison') or {}).values():
        if isinstance(present, (list, tuple)) and general in present:
            return True
    for info in (_world(mem).get('launched') or {}).values():
        if isinstance(info, dict) and info.get('general') == general:
            return True
    try:
        from ..os_layer import roster
        receipt = roster.fresh(mem)
    except Exception:
        receipt = None
    return isinstance(receipt, dict) and general in (receipt.get('names') or ())


def message_step(screen: Screen, mem):
    text = screen.text
    encounter = re.fullmatch(r'([^\ufffd\s]+)しょうぐんがてきとそうぐうしました!!', text)
    if encounter:
        general = encounter[1]
        if general != NAME and general_max_hp(general) is None:
            return []
        _record(mem, 'field_encounter_observed', general=general, castle=None,
                observed_metric={'message': text, 'location': 'field'},
                reason='実測の敵との遭遇告知を確認したため会話を進める')
        return [pad('a')]
    boss_entry = re.fullmatch(r'([^\ufffd\s]+)しょうぐんがボスじょうにせめこんだ!!', text)
    if boss_entry:
        general = boss_entry.group(1)
        boss_cell = _boss_entry_cell(mem, general)
        current = _plan_region(mem).get('attack') or {}
        step, match = _match_sortie(mem, boss_cell, general)
        if (match != 'matched' and current.get('castle') == boss_cell
                and current.get('general') == general
                and current.get('entry_evidence') == 'measured_boss_entry'):
            return [pad('a')]              # same entry text still on screen
        # Base boss order or an adjusted/interim boss order (generation-scoped id):
        # the launched order itself must target this chapter's boss castle.
        if not _is_boss_order(_order_for_step(mem, step), mem):
            if match == 'ambiguous':
                # Two sorties of this general are still heading for the boss
                # castle (g460 17:10: the base order and its adjusted wave), so
                # the order id is unknown. Never bind either of them - but the
                # message is measured and the screen must keep moving.
                _record(mem, 'attack_observed', chart_step=None, general=general, castle=boss_cell,
                        expected_metric=None,
                        observed_metric={'general': general, 'message': text, 'sortie_match': match},
                        deviation_reason='ambiguous_sortie',
                        reason='ボス城への出撃が複数在途のため注文は特定せず、実測文のみで進行')
                _plan_region(mem)['attack'] = {'general': general, 'castle': boss_cell, 'side': 'attack',
                                 'step': None, 'entry_evidence': 'measured_boss_entry'}
                return [pad('a')]
            held = mem.get('boss_entry_hold') or {}
            misses = held.get('n') + 1 if held.get('text') == text else 1
            if misses < BOSS_ENTRY_HOLD_LIMIT:
                mem['boss_entry_hold'] = {'text': text, 'n': misses}
                _record(mem, 'situation_held', screen='boss_attack_started',
                        observed_metric={'message': text, 'sortie_match': match,
                                         'hold_count': misses},
                        reason='実測ボス突入文を読んだが出撃注文と一致しないため保留')
                return []
            # Unmatched for the whole limit: the game already committed this
            # attack, so holding the message freezes the screen forever
            # (g460 16:43-). Close it and let the battle be measured.
            mem.pop('boss_entry_hold', None)
            _record(mem, 'attack_observed', chart_step=step, general=general, castle=boss_cell,
                    expected_metric=None,
                    observed_metric={'general': general, 'message': text, 'sortie_match': match},
                    deviation_reason='boss_entry_hold_released',
                    reason=f'出撃注文と一致しない突入文を{BOSS_ENTRY_HOLD_LIMIT}回保留したため表示を閉じる')
            _plan_region(mem)['attack'] = {'general': general, 'castle': boss_cell, 'side': 'attack',
                             'step': step, 'entry_evidence': 'measured_boss_entry'}
            return [pad('a')]
        _bind_sortie(mem, step, boss_cell, general)
        _plan_region(mem)['attack'] = {'general': general, 'castle': boss_cell, 'side': 'attack', 'step': step,
                         'entry_evidence': 'measured_boss_entry'}
        _record(mem, 'attack_observed', chart_step=step, general=general, castle=boss_cell,
                expected_metric={'general': general}, observed_metric={'general': general, 'message': text},
                reason='実測済みのボス城突入文と出撃将軍が一致')
        return [pad('a')]
    m = ATTACK.search(text)
    if m:
        general, castle = m.groups()
        observe_chapter_castle(mem, castle)
        castle = _castle_label(mem, castle)
        launched = (_world(mem).get('launched') or {}).get(castle) or {}
        current = _plan_region(mem).get('attack') or {}
        step, match = _match_sortie(mem, castle, general)
        if match != 'matched' and current.get('castle') == castle and current.get('general') == general:
            return [pad('a')]              # same entry text still on screen
        # 'legacy' is our own launched record for this general and castle; it
        # may carry no step (g839 step 4210: アマンディーヌ read as side='enemy',
        # which switched off the value-aware retreat policy). A sortie record,
        # garrison row or roster receipt proves ours even when the march bound
        # expired (g1133 2026-11-05: ピスタチオ's 1651-tick march to ユージア
        # matched nothing, ran as side 'enemy', and he retreated at 9 vs 7).
        ours = (step is not None or match in ('ambiguous', 'legacy')
                or general in (NAME, 'ヴィーナス', 'ココット', 'ゼウス')
                or _friendly_general(mem, general))
        side = 'attack' if ours else 'enemy'
        _bind_sortie(mem, step, castle, general)
        _record(mem, 'attack_observed', chart_step=step, general=general, castle=castle,
                expected_metric=launched.get('general'), observed_metric=general,
                deviation_reason=(None if step or not ours
                                  else 'ambiguous_sortie' if match == 'ambiguous' else 'unplanned_attack'),
                reason='のりこんだ表示')
        _plan_region(mem)['attack'] = {'general': general, 'castle': castle, 'side': side, 'step': step}
        return [pad('a')]
    m = DEFENSE.search(text)
    if m:
        castle = m.group(1)
        observe_chapter_castle(mem, castle)
        castle = _castle_label(mem, castle)
        if (_plan_region(mem).get('attack') or {}).get('castle') != castle or (_plan_region(mem).get('attack') or {}).get('side') != 'defense':
            _record(mem, 'defense_observed', castle=castle, reason='せめこまれました表示')
            # Keep the threat on the books: the corridor keeps two residents
            # home at a front that has just been attacked (g1163 2026-11-13:
            # オオキク was retaken at 14:05, counter-attacked from 14:15, and
            # lost again while the attack on ナッテ held only one defender).
            mem.setdefault('defense_seen', {})[castle] = int(mem.get('tick') or 0)
            mem['world_map_due'] = True      # the castle may have fallen without a battle
        _plan_region(mem)['attack'] = {'general': None, 'castle': castle, 'side': 'defense', 'step': None}
        return [pad('a')]
    return None
