"""手順層: battle command menus (cards, eggs, okunote, monsters).

Navigates the command/egg/card/monster menus opened by ``flows.battle`` and
selects the option the judgment layer chose.
"""
from __future__ import annotations

from ..os_layer.screen import OKUNOTE_CHOICES, Screen
from ... import hanjuku_chart as chart, hanjuku_experience as experience, hanjuku_reference as reference
from ...hanjuku_egg_reference import general_max_hp
from ..decision_log import _record
from . import egg_bait
from .battle import _card_battle_reading, _card_gate_close, _hero_retreat_open, _remember_general_for_summon
from ..judgment.battle import (BEHIND_EGG_RATIO_TENTHS, CARD_ANNOUNCE_LIMIT, CARD_LIST_CURSOR_LIMIT,
                               OKUNOTE_MAX_SELF_DAMAGE, SURVIVAL_CARDS, _available_rare_tactic, _battle_labels,
                               _behind, _card_damage_gate, _card_gate_alternatives, _card_gate_choice,
                               _card_gate_refused, _card_use_unclassified, _command_open_wait, _critical_safe_card,
                               _critical_survival_needed, _defender_egg_window, _defender_last_resort,
                               _defender_summon_last_resort,
                               _egg_before_retreat, _egg_rescue_pending, _egg_drop_evidence, _hero_retreat_needed, _hold_melee_egg, _melee_balance,
                               _healthy_melee_margin_evidence, _melee_continuation_evidence,
                               _migrate_card_evidence, _own_egg_empty, _own_egg_needed, _own_egg_positive,
                               _survival_needed, _survival_state, _watch_card_melee, _watch_egg_drop,
                               _wounded_summoner, _summoner_return_risk, _rescue_rearm)
from ..judgment.events import (BALLOON_FINCH_MAX_HP, _MONSTER_SKILLS, _monster_effectful, _monster_heal,
                               _monster_owner, _monster_damage_second, _monster_damage_first)
from ..judgment.balloon import balloon_assessment, balloon_command_selected
from ..judgment.akematsu import akematsu_assessment, akematsu_command_selected
from ..os_layer.commands import _battle_menu_to, menu_to, pad
from ..os_layer.readers import ALL_CARD_NAMES, BATTLE_MENU, _fold_skill, _options
from ..os_layer.world import NAME
from ..strategy.economy import _egg_recheck


def orphan_battle_menu_step(screen, mem):
    """Close an unbound parent menu finitely, then require new native context.

    The gray commands do not identify an actor, HP or a usable Okunote. A
    background animation/fade must neither refill the cancellation budget nor
    release an exhausted episode to the generic scene-confirm fallback.
    """
    flow = mem.get('orphan_battle_menu')
    restored = isinstance(mem.get('resume_battle_menu'), dict)
    if mem.get('battle'):
        mem.pop('orphan_battle_menu', None)
        mem.pop('resume_battle_menu', None)
        return None
    parent = screen.kind == 'battle_menu'
    if not parent and flow is None and not restored:
        return None
    if not parent and screen.kind not in {
            'unknown', 'text', 'battle_menu_pending', 'okunote_menu',
            'monster_menu', 'egg_choice_menu', 'egg_battle_menu'}:
        mem.pop('orphan_battle_menu', None)
        mem.pop('resume_battle_menu', None)
        _record(mem, 'battle_menu_reobserved',
                observed_metric={'screen': screen.kind},
                reason='文脈未確認の戦闘メニューから別の実前面へ戻ったため、その読取手順へ渡す')
        return None
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    if flow is None:
        flow = mem['orphan_battle_menu'] = {'identity': identity, 'attempts': 0}
    elif (not isinstance(flow, dict) or flow.get('identity') != identity
          or type(flow.get('attempts')) is not int or not 0 <= flow['attempts'] <= 3):
        # A persisted foreign/corrupt claim cannot acquire a new allowance.
        flow = mem['orphan_battle_menu'] = {'identity': identity, 'attempts': 3}
    if not parent:
        return []  # a fade or an irreversible list is not another parent B
    if flow['attempts'] < 3:
        flow['attempts'] += 1
        _record(mem, 'battle_menu_context_recheck',
                observed_metric={'close_attempt': flow['attempts'], 'limit': 3,
                                 'cursor_y': screen.menu_cursor},
                reason='将軍とHPの文脈がない実戦闘メニューを閉じ、実戦闘パネルで再確認する')
        return [pad('b')]
    if not flow.get('unconfirmed'):
        flow['unconfirmed'] = True
        _record(mem, 'battle_menu_recheck_unconfirmed',
                observed_metric={'close_attempts': 3},
                reason='有限の閉じる入力後も実戦闘メニューが残るため、卵や奥の手を推測して選ばない')
    return []


def command_open_pending_step(screen, mem):
    cur = mem.get('battle') or {}
    if not cur.get('command_open_pending'):
        return None
    if screen.kind in ('unknown', 'text'):
        return _command_open_wait(mem, cur)
    if screen.kind not in ('battle', 'battle_menu_pending'):
        cur.pop('command_open_pending', None)
    # HP readings belong to battle_step. A measured clipped command belongs
    # to its existing bounded pending-menu route, not a guessed option.
    return None


BATTLE_MENU_STALL_WAIT = 4      # unchanged observations held before re-pressing
BATTLE_MENU_STALL_RETRIES = 2   # further identical presses before closing the menu


def _battle_menu_move(screen, mem, label, *, key='battle_menu_move'):
    """One command-box step that never repeats a press on an identical frame.

    g713 UID16230..16232: the same command box came back byte-identical after
    each down and the cursor stayed on y176 while the panel was not yet taking
    input. Repeating the direction there either does nothing or is buffered
    into several row moves, so the next A could land on a row the bot never
    chose. An unchanged frame is the only evidence available that the previous
    press was not accepted: hold, retry the same single press a bounded number
    of times, then close the menu instead of confirming anything.

    Return contract (identical to :func:`_battle_menu_to`): a single pad dict
    (``'here'`` when the cursor is already on the label), ``None`` when no
    cursor/target is readable, or ``[]`` to plan no input while the window is
    held. The unaccepted close is a single pad dict too, never a one-element
    list: callers wrap the result with ``[move] if move else []``, so a list
    here would nest into ``[[pad('b')]]`` and every consumer that parses the
    actions (``hanjuku.actions.parse_actions``) rejects a list item.
    """
    move = _battle_menu_to(screen, label)
    if not isinstance(move, dict):
        mem.pop(key, None)
        return move
    sha = getattr(screen, 'frame_sha256', None)
    buttons = move.get('buttons')
    direction = buttons[0] if isinstance(buttons, list) and buttons else None
    if not isinstance(sha, str) or direction is None:
        mem.pop(key, None)
        return move
    cur = mem.get('battle') or {}
    context = [mem.get('chapter'), cur.get('ally'), cur.get('enemy'),
               screen.kind, screen.menu_cursor, label, direction]
    state = mem.get(key)
    if not (isinstance(state, dict) and state.get('context') == context
            and type(state.get('waits')) is int and type(state.get('retries')) is int):
        state = mem[key] = {'context': context, 'sha': None, 'waits': 0, 'retries': 0}
    if state.get('sha') == sha:
        if state['waits'] < BATTLE_MENU_STALL_WAIT:
            state['waits'] += 1
            return []
        if state['retries'] < BATTLE_MENU_STALL_RETRIES:
            state.update(waits=0, retries=state['retries'] + 1)
            _record(mem, 'battle_menu_nav_retry', screen=screen.kind, choice=label,
                    observed_metric={'direction': direction, 'retry': state['retries'],
                                     'frame_sha256': sha},
                    reason='同一画面が続くため同じ方向入力を待ってから再試行する')
            return move
        mem.pop(key, None)
        _record(mem, 'battle_menu_nav_unaccepted', screen=screen.kind, choice=label,
                observed_metric={'direction': direction, 'retries': state['retries'],
                                 'frame_sha256': sha},
                reason='同一画面のまま方向入力が受け付けられないため、確定せずメニューを閉じる')
        return pad('b')
    state.update(sha=sha, waits=0)
    return move


def _hero_retreat_menu(screen, mem, cur):
    # A castle defense cannot retreat (owner 2026-09-29; the menu has no
    # たいきゃく row there), so never try to select it.
    if not (egg_bait.retreat_due(mem, cur) or _hero_retreat_needed(cur)) or cur.get('side') == 'defense':
        return None
    if _egg_rescue_pending(cur):
        return []
    if ('きりふだ' in {w for _, _, w in _options(screen)}
            and _critical_safe_card(mem, cur, cur.get('survival') or {}, for_retreat=True)):
        return _survival_menu(screen, mem, cur)
    if _egg_before_retreat(mem, cur, menu=True) and 'たまごをつかう' in {w for _, _, w in _options(screen)}:
        if (cur.get('card_flow') or {}).get('stage') == 'announce':
            _card_use_unclassified(mem, cur, '本人の未試行の卵を優先し、切り札の使用告知は未確認として保持')
        else:
            cur['card_flow'] = None
        _survival_state(mem, cur)['egg_priority'] = True
        return _survival_menu(screen, mem, cur)
    flow = cur.setdefault('hero_retreat', {})
    if flow.get('unavailable') or flow.get('exhausted'):
        return None
    if 'たいきゃく' not in {w for _, _, w in _options(screen)}:
        flow['unavailable'] = True
        _record(mem, 'battle_hero_retreat_unavailable', **_battle_labels(cur),
                general=cur.get('ally'), enemy=cur.get('enemy'),
                castle=cur.get('castle'), side=cur.get('side'),
                reason='退却が有効な項目として読めないため卵・切り札・奥の手で対処')
        return None
    if flow.get('selected'):
        flow['wait'] = flow.get('wait', 0) + 1
        if flow['wait'] <= 3:
            return []
        if flow['selected'] >= 2:
            flow['exhausted'] = True
            return [pad('b')]
    flow['menu_ticks'] = flow.get('menu_ticks', 0) + 1
    if flow['menu_ticks'] > 8:
        flow['exhausted'] = True
        return [pad('b')]
    move = _battle_menu_move(screen, mem, 'たいきゃく')
    if move != 'here':
        return [move] if move else []
    if (cur.get('card_flow') or {}).get('selection_planned'):
        _card_use_unclassified(mem, cur, '退却を優先し切り札の実使用は未確認')
    else:
        cur['card_flow'] = None
    flow['selected'] = flow.get('selected', 0) + 1
    flow['wait'] = 0
    _record(mem, 'battle_hero_retreat_select', **_battle_labels(cur),
            general=cur.get('ally'), enemy=cur.get('enemy'),
            castle=cur.get('castle'), side=cur.get('side'), choice='たいきゃく',
            observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp'],
                             'retreat_assessment': dict(cur.get('retreat_assessment') or {})},
            resulting_event='retreat_selected_not_yet_confirmed',
            reason='主人公または将軍の危険な敗北を避けるため有効な退却行とカーソルを確認して決定')
    return [pad('a')]


def _survival_menu(screen, mem, cur):
    rescue = _survival_state(mem, cur)
    rescue['pending_opens'] = 0
    rescue['menu_ticks'] += 1
    if rescue.get('egg_pending'):
        rescue['egg_wait'] = rescue.get('egg_wait', 0) + 1
        if rescue['egg_wait'] <= 3:
            return []  # allow the accepted summon to leave its menu
        rescue['egg_pending'] = False
    labels = {word for _, _, word in _options(screen)}
    critical = _critical_survival_needed(cur)
    safe_card = critical and _critical_safe_card(mem, cur, rescue)
    egg_ready = ('たまごをつかう' in labels and not rescue['egg_attempted']
                 and not _own_egg_empty(mem, cur))
    conserving = ('たまごをつかう' in labels and _hold_melee_egg(mem, cur, labels)
                  and ('きりふだ' not in labels or rescue.get('cards_exhausted')
                       or rescue['cards_checked'] >= 3))
    if conserving:
        # Keeping melee is not evidence that rescue resources are exhausted.
        # Recheck this permission on every subsequent ordinary panel so HP
        # loss/summons cannot retain it or trigger twelve repeated menu tours.
        rescue.update(conserving_melee=True, conserved_labels=sorted(labels), exhausted=False)
        _record(mem, 'battle_egg_conserve', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'melee_balance': _melee_balance(cur),
                                 'conservation': (_healthy_melee_margin_evidence(mem, cur)
                                                  or _melee_continuation_evidence(mem, cur)
                                                  or {'rule': 'healthy_melee_advantage'})},
                reason='実HP・戦闘力と観測兵数または未読兵数の不利側境界で白兵優位のため卵を温存して戻る')
        return [pad('b')]
    rescue.pop('conserving_melee', None)
    if (critical and (egg_ready or (safe_card and 'きりふだ' in labels))
            and not rescue.get('critical_menu_recheck')
            and (rescue['menu_ticks'] > 12 or rescue.get('exhausted'))):
        rescue.update(critical_menu_recheck=True, exhausted=False, menu_ticks=1)
    if rescue['menu_ticks'] > 12 or rescue.get('exhausted'):
        rescue['exhausted'] = True
        return [pad('b')]
    # A visible list is authoritative. Do not infer carried cards from the
    # chart, a different general's sortie, or a planned inventory.
    # Cards first, then the egg (owner: 切り札が先 - the chart's order).
    # At critical HP do not spend more live melee frames scouting an unknown
    # card inventory when a usable egg is already on the current menu.
    # Three selections whose announcements all stayed unclassified are not a
    # reading of an empty list (g839 4214-4272: 59/59 vs 40 retreated with
    # きりふだ still on the menu). Read the real list once more; its own
    # rules use a card, keep cards_uncertain, or find it empty.
    attempted = rescue.get('cards_attempted') or []
    final_read = ('きりふだ' in labels and rescue['cards_checked'] >= 3
                  and not rescue.get('cards_exhausted') and not rescue.get('final_card_read')
                  and attempted and attempted[-1] in (cur.get('cards_unclassified') or []))
    if (critical or rescue.get('egg_priority')) and egg_ready and not safe_card:
        label = 'たまごをつかう'
    elif ('きりふだ' in labels and (safe_card or not rescue.get('cards_exhausted'))
            and rescue['cards_checked'] < 3) or final_read:
        label = 'きりふだ'
    elif ('たまごをつかう' in labels and not rescue['egg_attempted']
          and not _own_egg_empty(mem, cur)):
        label = 'たまごをつかう'
    else:
        rescue['exhausted'] = True
        _record(mem, 'battle_survival_unavailable', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'melee_balance': _melee_balance(cur)},
                reason='読み取れる未試行の救済手段がないため白兵へ戻る')
        return [pad('b')]
    move = _battle_menu_move(screen, mem, label)
    if move != 'here':
        return [move] if move else []  # no blind A on a missing cursor
    if label == 'きりふだ':
        if rescue['cards_checked'] >= 3:
            rescue['final_card_read'] = True
        rescue['cards_checked'] += 1
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    else:
        _egg_recheck(mem)
        rescue['egg_attempted'] = True
        rescue['egg_pending'] = True
    _record(mem, 'battle_survival_select', **_battle_labels(cur),
            observed_metric={'label': label, 'melee_balance': _melee_balance(cur)},
            resulting_event='selection_planned_not_yet_confirmed',
            reason='カーソルと有効な項目を確認して救済行動を選択')
    return [pad('a')]


def _survival_card_list(screen, mem, cur, flow, names):
    rescue = cur['survival']
    rescue['listed_cards'] = list(names)
    flow['list_ticks'] += 1
    # Names can disappear during a fade. Bound the wait and return to the
    # parent menu once, so an empty/unsupported list can still lead to an egg.
    # A name is not an inventory count (g514: two イッテツーン, one
    # unclassified selection, then healthy バジル40 vs27 retreated). A
    # second copy is eligible only after two agreeing live lists show fewer
    # copies than at the previous selection. No consumption receipt is
    # inferred, and an unchanged/unreadable list cannot cause repeated A.
    counts = {c: names.count(c) for c in SURVIVAL_CARDS if c in names}
    same = counts == flow.get('counts')
    flow['count_readings'] = flow.get('count_readings', 0) + 1 if same else 1
    flow['counts'] = counts
    previous = rescue.setdefault('card_counts_at_selection', {})
    attempted = rescue['cards_attempted']
    candidates = [c for c in SURVIVAL_CARDS if c in counts and (
        c not in attempted or (type(previous.get(c)) is int
                              and counts[c] < previous[c]
                              and flow['count_readings'] >= 2))]
    uncertain = [c for c in counts if c in attempted and c not in candidates]
    rescue['cards_uncertain'] = bool(uncertain)
    if (not candidates and uncertain and flow['count_readings'] < 2
            and flow['list_ticks'] <= 10):
        return []
    if not candidates:
        if not names and flow['list_ticks'] <= 3:
            return []
        rescue['cards_exhausted'] = True
        cur.pop('card_damage_list_blocked', None)  # absence/uncertain copies, not damage refusal
        cur['card_flow'] = None
        if flow['list_ticks'] > 10 and counts:
            rescue['cards_uncertain'] = True
        if uncertain:
            _record(mem, 'battle_card_remaining_unconfirmed', **_battle_labels(cur),
                    observed_metric={'listed_counts': counts,
                                     'counts_at_selection': dict(previous)},
                    reason='同名札が実一覧に残るが使用・枚数減少を確認できないため再決定を保留。救済手段なしとは断定しない')
        return [pad('b')]
    eligible = []
    for candidate in candidates:
        gate = _card_damage_gate(mem, cur, candidate, listed_cards=names)
        if gate['allowed']:
            eligible.append(candidate)
        else:
            _card_gate_refused(mem, cur, candidate, gate, listed_cards=names)
    if not eligible:
        return _card_gate_close(mem, cur, names)
    card = _card_gate_choice(mem, cur, eligible, names)
    move = _battle_menu_to(screen, card)
    if move != 'here':
        if flow['list_ticks'] > CARD_LIST_CURSOR_LIMIT:
            # A final current cursor may select an eligible card, as in the
            # chart list. Missing/off-row cursors get no extra navigation.
            rescue['cards_exhausted'] = True
            rescue['cards_uncertain'] = bool(counts)
            cur.pop('card_damage_list_blocked', None)
            cur['card_flow'] = None
            return [pad('b')]
        return [move] if move else []
    gate = _card_damage_gate(mem, cur, card, listed_cards=names)
    if not gate['allowed']:
        _card_gate_refused(mem, cur, card, gate, listed_cards=names)
        return _card_gate_close(mem, cur, names)
    flow.update(card=card, stage='announce', selection_planned=True)
    cur['card_damage_rescue_needed'] = False
    rescue['cards_attempted'].append(card)
    previous[card] = counts[card]
    rescue['cards_uncertain'] = False
    cur['card_consumption_complete'] = False
    cur.setdefault('cards_selected', []).append(card)
    _watch_card_melee(mem, cur, gate)
    egg_drop = _egg_drop_evidence(cur, card)
    _watch_egg_drop(cur, card, cur.get('enemy_hp'), egg_drop)
    _record(mem, 'battle_card_selected', **_battle_labels(cur), card=card,
            expected_metric='実使用告知と卵落' if egg_drop and egg_drop['drops'] else '実使用告知',
            observed_metric={'listed_cards': names, 'listed_count': counts[card],
                             'survival': True, 'egg_drop': egg_drop,
                             'card_damage_gate': gate},
            resulting_event='selection_planned_not_yet_confirmed',
            reason=('HP低下で卵を落とせる切り札を選択。消費は未確定' if egg_drop and egg_drop['drops']
                    else 'HP低下に対処する切り札を選択。消費は未確定'))
    return [pad('a')]

def battle_menu_pending_step(mem):
    if not mem.get('battle'):
        return []
    ticks = mem['battle_menu_pending_ticks'] = mem.get('battle_menu_pending_ticks', 0) + 1
    if ticks <= 8:
        return []
    mem.pop('battle_menu_pending_ticks', None)
    _record(mem, 'battle_command_incomplete',
            reason='表示途中の戦闘メニューが観測上限に達したため決定せず戻る')
    return [pad('b')]


def _disabled_defense_probe(screen, mem, cur):
    """Inspect below an observed disabled two-row box, without accepting a lottery.

    g713 Chevre/Tartar30774 has no retreat row. The older three-row gray
    detector never scrolled this box, so an unavailable-resource B ended
    the rescue episode. Two downs are inspection only: they do not prove
    an egg broke or that an okunote exists. Its actual row and the existing
    last-resort guards still own any irreversible A.
    """
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    if (not screen.disabled_item_box or screen.hidden_battle_commands
            or screen.menu_cursor not in (192, 208, 196, 212)
            or any(w in BATTLE_MENU or w == 'おくのて' for _, _, w in _options(screen))
            or identity is None or cur.get('away')
            or cur.get('ally') in (None, NAME, 'しゅじんこう', 'だいじん')
            or cur.get('side') != 'defense'
            or cur.get('castle') not in chart.castles(mem.get('chapter') or 0)
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('planned_cards') or cur.get('card_flow')
            or cur.get('okunote_last_resort_selected')
            or (cur.get('okunote_egg_preempt') or {}).get('selected')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_summon_observed') or screen.egg_rows
            or cur.get('egg_battle') or mem.get('egg_battle')):
        return None
    hp, enemy, start, enemy_start = (cur.get(k) for k in
        ('ally_hp', 'enemy_hp', 'start_ally_hp', 'start_enemy_hp'))
    ally_max, enemy_max = general_max_hp(cur.get('ally')), general_max_hp(cur.get('enemy'))
    if (any(type(v) is not int or v <= 0 for v in
            (hp, enemy, start, enemy_start, ally_max, enemy_max))
            or not hp <= start <= ally_max or not enemy <= enemy_start <= enemy_max):
        return None
    balance = _melee_balance(cur)
    ac, ec = balance.get('ally_combat'), balance.get('enemy_combat')
    ours, theirs = balance.get('ally_melee_score'), balance.get('enemy_melee_score')
    if (type(ac) is not int or type(ec) is not int or ac <= 0 or ec <= 0
            or hp * 2 > enemy or ac > ec
            or type(ours) is not int or type(theirs) is not int or ours * 2 > theirs):
        return None  # uncertain armies cannot license a lottery; this only inspects a losing HP/combat match
    scope = {'identity': identity, 'chapter': mem.get('chapter'),
             'ally': cur['ally'], 'enemy': cur['enemy'],
             'start_ally_hp': start, 'start_enemy_hp': enemy_start}
    probe = cur.get('disabled_defense_probe')
    if probe is None:
        probe = cur['disabled_defense_probe'] = {**scope, 'downs': 0}
    if (not isinstance(probe, dict) or any(probe.get(k) != v for k, v in scope.items())
            or run_identity(probe.get('identity')) != identity
            or any(type(probe.get(k)) is not int for k in
                   ('chapter', 'start_ally_hp', 'start_enemy_hp'))
            or probe.get('closed')
            or type(probe.get('downs')) is not int or not 0 <= probe['downs'] < 2):
        # Exhaustion/malformed scope tightens into close-only ownership. A
        # failed B must not release the next unread foreground to generic A.
        cur['disabled_defense_probe'] = {**scope, 'downs': 2, 'closed': True,
                                        'pending': True, 'unread': 3}
        return [pad('b')]  # no reset of a malformed, stale or exhausted persisted inspection
    probe['downs'] += 1
    probe.update(pending=True, unread=0)
    _record(mem, 'battle_disabled_defense_probe', **_battle_labels(cur),
            observed_metric={'ally_hp': hp, 'enemy_hp': enemy, 'melee_balance': balance,
                             'cursor_y': screen.menu_cursor, 'down_attempt': probe['downs']},
            resulting_event='row_inspection_only',
            reason='卵と札が実2行で使用不能の一般守備将軍が劣勢のため、下の奥の手行を有限で確認。存在・破損・選択は未確認')
    return [pad('down')]


def _disabled_defense_pending(screen, mem, cur):
    """A fading inspection cannot fall through to generic unknown/text A."""
    from ..os_layer.roster import run_identity
    probe = cur.get('disabled_defense_probe')
    if not isinstance(probe, dict) or probe.get('pending') is not True:
        return None
    if any(type(cur.get(k)) is int and cur[k] <= 0 for k in ('ally_hp', 'enemy_hp')):
        probe.update(pending=False, closed=True)
        return None  # a measured battle end belongs to the result/dialogue handler
    identity = run_identity(mem.get('_run_identity'))
    valid = (identity is not None and run_identity(probe.get('identity')) == identity
             and type(probe.get('chapter')) is int and probe['chapter'] == mem.get('chapter')
             and type(probe.get('downs')) is int and 1 <= probe['downs'] <= 2
             and all(probe.get(k) == cur.get(k) for k in
                     ('ally', 'enemy', 'start_ally_hp', 'start_enemy_hp'))
             and cur.get('side') == 'defense'
             and cur.get('ally') not in (None, NAME, 'しゅじんこう', 'だいじん'))
    if screen.kind in ('unknown', 'text', 'battle_menu_pending'):
        if valid and probe.get('closed') is True:
            return [pad('b')]  # a planned close is not evidence that the unread menu closed
        unread = probe.get('unread')
        if valid and type(unread) is int and 0 <= unread < 3:
            probe['unread'] = unread + 1
            return [{'type': 'wait', 'ms': 50}]
        probe.update(pending=bool(valid), closed=True)
        return [pad('b')]
    probe['pending'] = False
    if not valid:
        probe['closed'] = True
        return [pad('b')] if screen.kind == 'battle_menu' else None
    if screen.kind == 'battle_menu':
        return battle_menu_step(screen, mem)
    # Complete combat/another foreground has its own handler. The original
    # two-down allowance is retained; no stale observation resets it.
    return None


def battle_menu_step(screen: Screen, mem):
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    if screen.kind != 'battle_menu':
        return []
    if _screen_egg_empty(screen, mem, cur) and not _own_egg_empty(mem, cur):
        _record(mem, 'battle_egg_actor_unconfirmed',
                reason='実画面の本人は残数0のため、旧戦闘文脈で卵を選ばず戻る')
        return [pad('b')]
    cur['resource_menu_observed'] = True  # a B attempt alone never proves this
    cur.pop('command_open_pending', None)  # foreground receipt, not a planned B
    _card_battle_reading(screen, cur, mem)
    cur['own_egg_zero_observed'] = _own_egg_empty(mem, cur)
    cur['own_egg_positive_observed'] = _own_egg_positive(mem, cur)
    # A newly readable resource invalidates the earlier all-disabled receipt
    # before a pending preemption can select an irreversible command.
    if any(w in BATTLE_MENU for _, _, w in _options(screen)):
        cur['okunote_only_observed'] = False
        guard = cur.get('okunote_egg_preempt')
        if guard and not guard.get('selected'):
            cur.pop('okunote_egg_preempt', None)
            rescue = _survival_state(mem, cur)
            rescue.update(exhausted=False, menu_ticks=0, pending_opens=0)
            return _survival_menu(screen, mem, cur)
    probe = _disabled_defense_probe(screen, mem, cur)
    if probe is not None:
        return probe
    if screen.hidden_battle_commands or screen.has('おくのて'):
        cur.pop('card_damage_list_blocked', None)
        return okunote_step(screen, mem)
    retreat = _hero_retreat_menu(screen, mem, cur)
    if retreat is not None:
        return retreat
    if flow and flow.get('survival'):
        flow['menu_returns'] = flow.get('menu_returns', 0) + 1
        if flow['stage'] == 'list' and flow['menu_returns'] <= 2:
            return [pad('a')] if _battle_menu_to(screen, 'きりふだ') == 'here' else []
        if flow['stage'] == 'announce':
            _card_use_unclassified(mem, cur, '実使用告知を確認できないまま戦闘メニューへ復帰')
        else:
            cur['survival']['cards_exhausted'] = True
            cur['card_flow'] = None
        return _survival_menu(screen, mem, cur)
    if flow:
        flow['command_menu_ticks'] = flow.get('command_menu_ticks', 0) + 1
        if flow['command_menu_ticks'] > 8:
            _card_use_unclassified(mem, cur, '切り札行とカーソルの確認が観測上限に達したため保留')
            return [pad('b')]
        move = _battle_menu_move(screen, mem, 'きりふだ')
        if move != 'here':
            if isinstance(move, dict) and move.get('buttons') == ['b']:
                # The box did not accept the direction. Keep the carried card
                # (no selection, no consumption) and release the flow; a close
                # is not progress toward the row.
                _card_use_unclassified(mem, cur, '切り札行へ動かないため一覧を開かずメニューを閉じる')
            elif move:
                flow['stage'] = 'down'
            return [move] if move else []
        flow['stage'] = 'list'
        return [pad('a')]
    if not flow and cur.get('egg_battle') and 'きりふだ' in screen.text:
        rare = _available_rare_tactic(mem, cur)
        move = _battle_menu_move(screen, mem, 'きりふだ') if rare else None
        if move == []:
            return []  # an identical frame is still not the card row
        if isinstance(move, dict) and move.get('buttons') == ['b']:
            # The box did not accept the direction: close it without opening the
            # card flow. `_battle_menu_to` never emits B, so this is the
            # bounded unaccepted close, not a step toward the row.
            return [move]
        if move is not None:
            tid = rare['tactic_id']
            cur.setdefault('tactics_done', []).append(tid)
            cur['card_flow'] = {'card': 'キャトルミュー', 'stage': 'list' if move == 'here' else 'down',
                                'tactic_id': tid, 'enemy_hp_at_open': cur.get('enemy_hp')}
            _record(mem, 'battle_card', **_battle_labels(cur), card='キャトルミュー',
                    observed_metric={'enemy_hp': cur.get('enemy_hp'), 'egg_battle': True},
                    reason=('実携行のキャトルミューをボスへ使用し90ダメージを狙う'
                            if cur.get('enemy') in chart.BOSSES.values() else
                            '実携行のキャトルミューを敵EMへ使用し224ダメージと石化を狙う'))
            return [pad('a')] if move == 'here' else [move]
    if cur.get('survival') or _survival_needed(cur):
        return _survival_menu(screen, mem, cur)
    # Chart has no card due this frame: independent judgment under the chart,
    # mapped from the original six melee patterns (①③ pass / ⑥ egg; ②④⑤ are
    # chart-directed cards and never chosen blindly here).
    exp = mem.get('_experience')
    key = experience.situation_key('battle_menu', mem)
    balance = _melee_balance(cur)
    combat_unknown = (not (cur.get('egg_battle') or cur.get('card_summon_observed'))
                      and all(isinstance(cur.get(k), str) for k in ('ally', 'enemy'))
                      and all(type(cur.get(k)) is int and cur[k] > 0 for k in ('ally_hp', 'enemy_hp'))
                      and any(balance[k] is None for k in ('ally_combat', 'enemy_combat')))
    if not mem.get('indep_menu'):
        mem['indep_menu'] = True
        default = 'use_egg' if _behind(cur) and not _own_egg_empty(mem, cur) else 'pass'
        action = experience.preferred(exp, key, default=default, kind='battle_menu')
        if combat_unknown and not _own_egg_empty(mem, cur):
            action = 'use_egg'
        if action == 'use_egg' and (_own_egg_empty(mem, cur)
                                  or _hold_melee_egg(mem, cur, {w for _, _, w in _options(screen)})):
            action = 'pass'
        mem['indep_menu_key'] = key
        mem['indep_menu_action'] = action
        # Original patterns: ①/③ continue melee when not behind; ⑥ use egg
        # only when behind (melee+chart cards insufficient / none due).
        pattern = '⑥' if action == 'use_egg' else ('③' if cur.get('clashed') else '①')
        battle = mem.get('battle')
        if isinstance(battle, dict):
            battle['independent'] = {'kind': 'battle_menu', 'key': key, 'action': action,
                                     'pattern': pattern}
        _record(mem, 'independent_menu',
                strategy_variant=f'independent_{action}',
                deviation_reason='チャートに戦術なし: 状況判断',
                expected_metric='戦闘結果による判断の検証',
                observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp'),
                                 'experience_key': key, 'source_pattern': pattern,
                                 'melee_balance': _melee_balance(cur)},
                source_pattern=pattern,
                reason='チャートが当該フレームに切り札を指示していないための独自判断（原典戦術'
                       + pattern + 'に相当）')
    action = mem.get('indep_menu_action', 'pass')
    if action == 'pass' and combat_unknown and not _own_egg_empty(mem, cur):
        action = mem['indep_menu_action'] = 'use_egg'
        mem['indep_menu_key'] = key
        if isinstance(cur.get('independent'), dict):
            cur['independent'].update(action=action, key=key)
        _record(mem, 'battle_combat_recheck', observed_metric={'melee_balance': balance},
                reason='将軍の戦闘値が未知のため、保存済みの見送りを実メニューの卵対処へ戻す')
    if action == 'use_egg' and _hold_melee_egg(mem, cur, {w for _, _, w in _options(screen)}):
        mem['indep_menu_action'] = 'pass'
        return [pad('b')]
    if action == 'use_egg' and (_own_egg_empty(mem, cur) or 'たまごをつかう' not in screen.text):
        # A spent egg greys its row out (unreadable): A there does nothing.
        if not mem.get('egg_row_dead'):
            mem['egg_row_dead'] = True
            _record(mem, 'situation_held', screen=screen.kind, strategy_variant='egg_unavailable',
                    reason=('使用回数0を観測済みのため卵を使わず白兵へ戻る'
                            if _own_egg_empty(mem, cur) else
                            'たまごをつかうが使えない表示のため卵を諦めて白兵へ戻る'))
        return [pad('b')]
    if action == 'use_egg':
        move = _battle_menu_move(screen, mem, 'たまごをつかう')
        if move != 'here':
            return [move] if move else []
        _egg_recheck(mem)
        return [pad('a')]
    return [pad('b')]


def defender_egg_pending_step(screen, mem):
    """Keep the selected rescue route across menus and incomplete frames."""
    cur = mem.get('battle') or {}
    probe = _disabled_defense_pending(screen, mem, cur)
    if probe is not None:
        return probe
    guard = cur.get('okunote_egg_preempt') or {}
    if (cur.get('card_flow') or cur.get('egg_battle') or mem.get('egg_battle')
            or not all(type(cur.get(k)) is int and cur[k] > 0 for k in ('ally_hp', 'enemy_hp'))):
        return None
    if guard.get('close_pending'):
        if screen.kind in ('unknown', 'text', 'battle_menu_pending'):
            # A planned B is not a receipt that the unread menu closed. Keep
            # closing it instead of letting the next observation send blind A.
            return [pad('b')]
        guard.pop('close_pending', None)
    if guard.get('stage') not in ('opening', 'menu', 'selected') or guard.get('exhausted'):
        return None
    if screen.kind == 'battle_menu':
        return battle_menu_step(screen, mem)
    if screen.kind == 'okunote_menu':
        return okunote_step(screen, mem)
    if screen.kind not in ('unknown', 'text', 'battle_menu_pending'):
        return None
    guard['unread_ticks'] = guard.get('unread_ticks', 0) + 1
    if guard['unread_ticks'] <= 8:
        # An explicit short wait also prevents the generic no-input fallback
        # from accepting the still-unread command with A.
        return [{'type': 'wait', 'ms': 50}]
    guard.update(exhausted=True, stage='unavailable', close_pending=True)
    _record(mem, 'battle_okunote_egg_menu_unread', **_battle_labels(cur),
            reason='召喚前の奥の手メニューを読み取れない観測が上限に達したため、未知の行を確定せず戻る')
    return [pad('b')]


def okunote_step(screen, mem):
    cur = mem.get('battle')
    if not cur:
        return []
    from . import okunote
    pending = okunote.menu_pending(screen, mem, cur)
    if pending is not None:
        return pending
    flow = cur.setdefault('okunote_flow', {'ticks': 0})
    flow['ticks'] += 1
    if screen.kind == 'battle_menu':
        hp = cur.get('ally_hp')
        summon = None
        if screen.egg_rows or cur.get('egg_battle') or mem.get('egg_battle'):
            summon = okunote.summon_reading(screen, mem, cur)
            if summon is None:
                _record(mem, 'battle_okunote_summon_unconfirmed', **_battle_labels(cur),
                        reason='現在の本人HP・敵召喚獣・自軍コマンドを同じ戦闘に照合できず奥の手を開かない')
                return [pad('b')]
            _egg_general_reading(screen, mem, cur)
            hp = summon['ally_hp']
        # g482: パプリカ34 entered the irreversible random choices and chose
        # しんだフリ (28..48 self damage), then lost at HP0. Before opening
        # the candidates, require enough *current* HP to survive even the
        # worst Lv1 result, except the measured losing non-hero defense below.
        # An already-open list cannot be cancelled: below
        # we still choose the strongest visible candidate there.
        labels = {w for _, _, w in _options(screen)}
        # The gray parent rows or a sole active おくのて are direct evidence;
        # chart inventory/house state cannot prove the currently usable menu.
        cur['okunote_only_observed'] = ((screen.hidden_battle_commands or 'おくのて' in labels)
            and not labels.intersection({'たまごをつかう', 'きりふだ', 'たいきゃく'}))
        guard = cur.get('okunote_egg_preempt') or {}
        if guard.get('selected'):
            return [pad('b')]  # a repeated parent frame is not another lottery
        if guard and cur['okunote_only_observed']:
            # A real menu can arrive after the bounded B opening expired.
            # It authorizes a current cursor check, never another blind B loop.
            guard.update(exhausted=False, stage='menu', unread_ticks=0)
        egg_window = _defender_egg_window(mem, cur)
        egg_preempt = bool(egg_window and egg_window['due'])
        if egg_window and not egg_preempt:
            rescue = _survival_state(mem, cur)
            rescue['exhausted'] = True
            if not flow.get('egg_window_waited'):
                flow['egg_window_waited'] = True
                _record(mem, 'battle_okunote_egg_wait', **_battle_labels(cur),
                        observed_metric=egg_window,
                        reason='奥の手だけが有効だが召喚前の期限には未到達のため、白兵のHPと位置リスクを再観測する')
            return [pad('b')]
        if egg_preempt:
            guard = cur.setdefault('okunote_egg_preempt', {'opens': 0})
            guard.update(reason=egg_window['reason'], stage='menu')
            if not guard.get('recorded'):
                guard['recorded'] = True
                _record(mem, 'battle_okunote_egg_preempt', **_battle_labels(cur),
                        observed_metric={**egg_window, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                        resulting_event='irreversible_risk_accepted',
                        reason='敵卵あり・味方卵と札は実メニューで使用不能・召喚危険が目前の城防衛で奥の手を選ぶ。主人公も対象だが自傷リスクは残る')
        last_resort = (_defender_summon_last_resort(mem, cur, summon) if summon
                       else _defender_last_resort(mem, cur))
        if last_resort and not cur.get('okunote_last_resort_recorded'):
            cur['okunote_last_resort_recorded'] = True
            _record(mem, 'battle_okunote_last_resort', **_battle_labels(cur),
                    observed_metric={'ally_hp': hp, 'start_ally_hp': cur['start_ally_hp'],
                                     'enemy_hp': cur['enemy_hp'],
                                     'start_enemy_hp': cur['start_enemy_hp'],
                                     'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                    resulting_event='irreversible_risk_accepted',
                    reason='他の手段が使用不能で押し負ける一般守備将軍の最後の救済。自傷リスクは残る')
        if (type(hp) is not int or hp <= OKUNOTE_MAX_SELF_DAMAGE) and not (last_resort or egg_preempt):
            rescue = _survival_state(mem, cur)
            # Re-arm exhausted on tide change.
            _rescue_rearm(mem, cur, {'owner': 'ally'})
            rescue = _survival_state(mem, cur)
            # Candidate-first: check safe okunote choices (not self-damage).
            visible = {w for _, _, w in _options(screen)} if getattr(screen, 'kind', None) == 'battle_menu' else set()
            # Unobserved is not safe (#2102): do not fall back to the static
            # OKUNOTE_CHOICES when the menu is not visible.
            okunote_visible = [w for w in OKUNOTE_CHOICES if w in visible] if visible else []
            from ..os_layer.screen import OKUNOTE_SELF_DAMAGE_CHOICES
            safe = [c for c in okunote_visible if c not in OKUNOTE_SELF_DAMAGE_CHOICES]

            if not safe or rescue.get('exhausted'):
                if not flow.get('risk_declined'):
                    flow['risk_declined'] = True
                    _record(mem, 'battle_okunote_risk_declined', **_battle_labels(cur),
                            observed_metric={'ally_hp': hp, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                            reason='奥の手は候補確認後にキャンセルできず自傷もあるため、低HPでは開かず白兵へ戻る')
                rescue['exhausted'] = True
                return [pad('b')]
            # safe candidate exists: fall through to open okunote
    # The irreversible candidate list cannot be cancelled. A parent-menu
    # navigation budget must never block a now-readable best candidate.
    if flow['ticks'] > 16 and screen.kind == 'battle_menu':
        if cur.get('okunote_egg_preempt'):
            cur['okunote_egg_preempt'].update(exhausted=True, stage='unavailable', close_pending=True)
        return [pad('b')]
    if screen.hidden_battle_commands:
        _record(mem, 'battle_okunote_scroll', **_battle_labels(cur),
                observed_metric={'cursor_y': screen.menu_cursor},
                reason='灰色のたまご・切り札・退却の下にあるおくのてへスクロール')
        return [pad('down')]
    names = {w for _,_,w in _options(screen)}
    if screen.kind == 'battle_menu' and 'おくのて' in names:
        label = 'おくのて'
    else:
        candidates = [w for w in OKUNOTE_CHOICES if w in names]
        if not candidates:
            return []
        label = candidates[-1]
    move = _battle_menu_to(screen, label)
    if move != 'here':
        if (move is None and screen.kind == 'battle_menu'
                and (cur.get('okunote_egg_preempt') or {}).get('recorded')):
            return [pad('down')]  # bounded by the 16-observation menu budget
        return [move] if move else []
    if label == 'おくのて' and cur.get('okunote_last_resort_recorded'):
        cur['okunote_last_resort_selected'] = True
    if label == 'おくのて' and (cur.get('okunote_egg_preempt') or {}).get('recorded'):
        cur['okunote_egg_preempt'].update(selected=True, stage='selected')
        _survival_state(mem, cur)['exhausted'] = True
    _record(mem, 'battle_okunote_select', **_battle_labels(cur), choice=label,
            observed_metric={'options': sorted(names)},
            reason='奥の手のカーソルを確認して選択。候補は効果が高いものを優先')
    okunote.selected(mem, cur, candidate=label != 'おくのて')
    return [pad('a')]


def card_list_step(screen: Screen, mem):
    """Keep card intention/selection separate from observed consumption."""
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    _card_battle_reading(screen, cur, mem)
    flow = cur.get('card_flow')
    names = [w for _, _, w in _options(screen) if w in ALL_CARD_NAMES]
    rescue = {**(cur.get('survival') or {}), 'listed_cards': names}
    if (cur.get('side') != 'defense' and _hero_retreat_needed(cur)
            and flow and flow.get('stage') == 'list'
            and not _critical_safe_card(mem, cur, rescue)
            and not (cur.get('hero_retreat') or {}).get('unavailable')
            and not (cur.get('hero_retreat') or {}).get('exhausted')):
        cur['card_flow'] = None
        _record(mem, 'battle_hero_retreat_cancel_card', **_battle_labels(cur),
                reason='未選択の切り札一覧を閉じ、どうしの緊急退却を優先')
        return [pad('b')]
    # Recognition follows the real catalogue; tactic/rescue selection retains
    # its own planning and damage gates. A held event card must be visible.
    if not flow:
        return [pad('b')]
    if flow['stage'] == 'list' and flow.get('survival'):
        return _survival_card_list(screen, mem, cur, flow, names)
    if flow['stage'] == 'list':
        if flow['card'] not in names:
            # A 500-ms observation can catch the opening text before the
            # carried names draw (g490: two confirmed イッテツーン, then an
            # empty OCR list). An unreadable frame is not a missing-card
            # receipt. Explicit absence stays immediate; other readable
            # lists must agree twice, and unreadable lists back out boundedly.
            absent = any(text in ''.join(screen.text.split()) for text in
                         ('きりふだはありません', 'きりふだなし'))
            flow['list_observations'] = int(flow.get('list_observations', 0)) + 1
            previous = flow.get('missing_list_names')
            flow['missing_list_names'] = names
            if not absent and (not names or previous != names):
                if flow['list_observations'] < 4:
                    return []
                _record(mem, 'battle_card_list_unclassified', **_battle_labels(cur),
                        card=flow['card'], observed_metric={'listed_cards': names,
                                                           'observations': flow['list_observations']},
                        expected_metric='読める切り札一覧または明示的な切り札なし表示',
                        reason='札一覧を確定できないため、携行不足と断定せず入力を戻す')
                cur['card_flow'] = None
                return [pad('b'), pad('b')]
            cur.setdefault('cards_missing', []).append(flow['card'])
            cur.pop('card_damage_list_blocked', None)
            if not cur.get('deviation_reason'):
                cur['strategy_variant'] = 'chart_card_unavailable'
                cur['deviation_reason'] = 'チャートで予定した切り札を携行していない'
            _record(mem, 'battle_card_missing', **_battle_labels(cur), card=flow['card'],
                    expected_metric={'carried_card': flow['card']}, observed_metric={'listed_cards': names},
                    resulting_event='card_not_selected', reason='切り札を携行していないため白兵を継続')
            cur['card_flow'] = None
            return [pad('b'), pad('b')]
        gate = _card_damage_gate(mem, cur, flow['card'], listed_cards=names)
        if not gate['allowed']:
            rejected = flow['card']
            _card_gate_refused(mem, cur, rejected, gate, listed_cards=names)
            alternate = _card_gate_choice(mem, cur,
                                          _card_gate_alternatives(mem, cur, names, omit=rejected), names)
            if alternate is None:
                # Keep the last assessment on the rejected chart card, not
                # on a candidate inspected only to find a safe alternative.
                _card_damage_gate(mem, cur, rejected, listed_cards=names)
                return _card_gate_close(mem, cur, names)
            flow.update(card=alternate,
                        note='単発撃破を保証できない予定札を実一覧の安全な代替へ変更')
            gate = _card_damage_gate(mem, cur, alternate, listed_cards=names)
            _record(mem, 'battle_card_damage_replan', **_battle_labels(cur), card=alternate,
                    observed_metric={'rejected_card': rejected, 'listed_cards': names,
                                     'card_damage_gate': gate},
                    resulting_event='replacement_not_yet_selected',
                    reason='予定札の致死判定を満たさないため実一覧で根拠を満たす札へ移動')
        # The list may retain its cursor after a failed/partial input. Never
        # assume row zero or send a multi-key selection from an old image.
        move = _battle_menu_to(screen, flow['card'])
        flow['cursor_ticks'] = int(flow.get('cursor_ticks', 0)) + 1
        if move != 'here':
            if flow['cursor_ticks'] > CARD_LIST_CURSOR_LIMIT:
                _card_use_unclassified(mem, cur, '切り札のカーソルを確認できるまでの上限に達したため一覧を閉じる')
                return [pad('b')]
            return [move] if move else []
        flow['stage'] = 'announce'
        flow['selection_planned'] = True
        cur['card_damage_rescue_needed'] = False
        cur['card_consumption_complete'] = False
        cur.setdefault('cards_selected', []).append(flow['card'])
        _watch_card_melee(mem, cur, gate)
        _watch_egg_drop(cur, flow['card'], cur.get('enemy_hp'),
                        _egg_drop_evidence(cur, flow['card']))
        _record(mem, 'battle_card_selected', **_battle_labels(cur), card=flow['card'],
                expected_metric='実使用告知', observed_metric={'listed_cards': names,
                                                           'card_damage_gate': gate},
                resulting_event='selection_planned_not_yet_confirmed', reason='切り札選択入力を予定。消費は未確定')
        return [pad('a')]
    if flow['stage'] == 'announce' and flow.get('selection_planned'):
        flow['announce_ticks'] = flow.get('announce_ticks', 0) + 1
        if flow['announce_ticks'] > CARD_ANNOUNCE_LIMIT:
            _card_use_unclassified(mem, cur, '切り札の告知待ち上限に達したため選択画面から戻る')
            return [pad('b')]
        # No live frame/parser signature has calibrated a use receipt yet.
        # Keep capturing this flow, but never unlock after_card from guessed text.
        cur['card_consumption_complete'] = False
        if not flow.get('uncalibrated_candidate_recorded'):
            flow['uncalibrated_candidate_recorded'] = True
            _record(mem, 'battle_card_candidate', **_battle_labels(cur), card=flow['card'],
                    expected_metric='実画面とparserで校正済みの切り札使用証拠',
                    observed_metric={'confirmation': 'unclassified', 'screen_kind': screen.kind},
                    resulting_event='card_use_unclassified', reason='告知署名が未校正のため消費を確定せず画像収集')
    return []


def _screen_egg_empty(screen, mem, battle):
    """The actual named foreground actor outranks missing battle bookkeeping."""
    names = []
    if screen.battle and general_max_hp(screen.battle.ally) is not None:
        names = [screen.battle.ally]
    else:
        names = [row.name for row in screen.egg_rows if row.side == 'ally'
                 and general_max_hp('しゅじんこう' if row.name == NAME else row.name) is not None]
    return (_own_egg_empty(mem, {'ally': names[0]}) if len(names) == 1
            else _own_egg_empty(mem, battle))


def egg_choice_step(screen: Screen, mem):
    """Choose a real offered summon; never type a name or infer an egg result."""
    if _screen_egg_empty(screen, mem, mem.get('battle')):
        return [pad('b')]
    from ..os_layer.screen import egg_choice_names
    names = egg_choice_names(screen)
    if not names or screen.menu_cursor not in (176, 192, 208):
        return []
    key = tuple(names)
    flow = mem.get('egg_choice')
    if not flow or tuple(flow.get('names') or ()) != key:
        flow = mem['egg_choice'] = {'names': names, 'selected': 0, 'wait': 0}
    if flow['selected']:
        flow['wait'] += 1
        if flow['wait'] <= 3:
            return []
        if flow['selected'] >= 2:
            if not flow.get('held'):
                flow['held'] = True
                _record(mem, 'egg_choice_unconfirmed', observed_metric={'candidates': names},
                        reason='有限回の召喚選択後も同じ三択のため、召喚成功とせず保留')
            return []
    # The first actual candidate is a valid summon. No unverified power ranking
    # or arbitrary entry is introduced; later skill/HP policy handles its fight.
    choice = names[0]
    move = _battle_menu_to(screen, choice)
    if move != 'here':
        return [move] if move else []
    flow['selected'] += 1
    flow['wait'] = 0
    _record(mem, 'egg_choice_select', observed_metric={'candidates': names, 'choice': choice},
            resulting_event='summon_selected_not_yet_confirmed',
            reason='実表示の三候補と騎士カーソルを確認し、先頭の召喚獣を選択')
    return [pad('a')]


def _egg_general_reading(screen, mem, battle):
    """Read this battle's general, never substitute a summoned monster's HP."""
    rows = [row for row in screen.egg_rows
            if row.side == 'ally' and row.name == battle.get('ally')
            and type(row.hp) is int and row.hp >= 0]
    if len(rows) != 1:
        return None
    ally = battle.get('ally')
    full = (mem.get('hero_max_hp') if ally == NAME else general_max_hp(ally))
    if type(full) is not int or full <= 0 or rows[0].hp > full:
        return None
    battle['ally_hp'] = rows[0].hp
    # Keep the general's own panel separate from the summoned monster's HP.
    # The battle object is replaced at every opponent/general transition.
    _remember_general_for_summon(mem, battle, ally, rows[0].hp)
    return rows[0].hp, full


def _enemy_general_command(screen):
    """A complete left command box belongs to the enemy facing our monster.

    g747 1310/17492 show our red monster and the blue enemy general in the
    right HP panel. The left box is the general's attack/fierce/egg menu,
    not a second summon command for our already-present monster.
    """
    if screen.kind != 'egg_battle_menu' or len(screen.egg_rows) != 2:
        return None
    allies = [r for r in screen.egg_rows if r.side == 'ally']
    enemies = [r for r in screen.egg_rows if r.side == 'enemy']
    if len(allies) != 1 or len(enemies) != 1:
        return None
    ally, enemy = allies[0], enemies[0]
    full = general_max_hp(enemy.name)
    # Exact native spelling at g747/1310; no fuzzy name matching.
    monster_name = {'てつじん8ごう': 'てつじん８ごう'}.get(ally.name, ally.name)
    if (monster_name not in _MONSTER_SKILLS or type(ally.hp) is not int or ally.hp <= 0
            or type(enemy.hp) is not int or type(full) is not int
            or not 0 < enemy.hp <= full):
        return None
    rows = screen.menu_rows
    labels = [r.known for r in rows]
    if labels not in (['こうげき', 'もうこうげき'],
                      ['こうげき', 'もうこうげき', 'たまごをつかう']):
        return None
    spans = [r.spans() for r in rows]
    if (any(len(s) != 1 or s[0][0] != 40 for s in spans)
            or any(b.y - a.y != 16 for a, b in zip(rows, rows[1:]))):
        return None
    return ally, enemy


def _partial_enemy_general_command(screen):
    """Recognize the left command transition without inventing our missing HP.

    g749/5561 hides our monster panel while the blue enemy general's panel
    and left fierce-attack row are still visible. This is only a reason to
    withhold input, not evidence of our current monster or its health.
    """
    if screen.kind != 'egg_battle_menu' or len(screen.egg_rows) not in (1, 2):
        return None
    enemies = [r for r in screen.egg_rows if r.side == 'enemy']
    if len(enemies) != 1:
        return None
    enemy = enemies[0]
    full = general_max_hp(enemy.name)
    if (enemy.side != 'enemy' or type(enemy.hp) is not int
            or type(full) is not int or not 0 < enemy.hp <= full):
        return None
    rows = screen.menu_rows
    labels = [r.known for r in rows]
    commands = ['こうげき', 'もうこうげき', 'たまごをつかう']
    if not labels or not any(labels == commands[i:j]
                             for i in range(3) for j in range(i + 1, 4)):
        return None
    # g749/6027 has both panels, but the left box is still eight pixels
    # from its settled column. Do not relax the complete-owner guard.
    columns = (32, 40)
    if len(screen.egg_rows) == 2:
        allies = [r for r in screen.egg_rows if r.side == 'ally']
        if (len(allies) != 1 or allies[0].name not in _MONSTER_SKILLS
                or type(allies[0].hp) is not int or allies[0].hp <= 0):
            return None
        columns = (32,)
    if (not any(all(r.spans() == [(x, r.known)] for r in rows) for x in columns)
            or any(not 144 <= r.y <= 216 for r in rows)
            or any(b.y - a.y != 16 for a, b in zip(rows, rows[1:]))):
        return None
    return enemy


def _egg_retreat_return(screen, mem, battle):
    """Reuse the finite return-to-human-command route; B is not a receipt."""
    attempts = int(battle.get('egg_retreat_attempts') or 0)
    battle['egg_retreat_needed'] = True
    battle['egg_retreat_tried'] = True
    if attempts < 3:
        battle['egg_retreat_attempts'] = attempts + 1
        _record(mem, 'battle_egg_retreat_attempt', **_battle_labels(battle),
                observed_metric={'enemy': battle.get('enemy'), 'ally_hp': battle.get('ally_hp'),
                                 'enemy_hp': battle.get('enemy_hp'), 'attempt': attempts + 1},
                reason='卵も札もない召喚戦のため、通常の退却メニューへの復帰を再確認する')
        return [pad('b')]
    if not battle.get('egg_retreat_unavailable_recorded'):
        battle['egg_retreat_unavailable_recorded'] = True
        _record(mem, 'battle_egg_retreat_unavailable', **_battle_labels(battle),
                observed_metric={'attempts': attempts, 'screen': screen.kind},
                reason='有限回の復帰入力でも退却メニューを確認できないため、退却成功とせず応戦する')
    return None


def egg_battle_step(screen: Screen, mem):
    """Summoned-monster battle: a turn menu that waits for a command.

    The chart avoids provoking enemy eggs but gives no command for this
    battle. Independent judgment defaults to たまごをつかう so an enemy
    summon is answered with an egg rather than a stall or blind attack.
    This is original pattern ⑥ (own egg when melee is not enough); patterns
    ②④⑤ stay chart-directed. Messages inside it advance with A.
    """
    if screen.kind != 'egg_battle_menu':
        mem.pop('egg_menu_stage', None)
        mem.pop('enemy_general_menu', None)
        mem.pop('enemy_general_menu_pending', None)
        return [pad('a')]
    partial_enemy = _partial_enemy_general_command(screen)
    if partial_enemy:
        for key in ('egg_battle', 'egg_action', 'egg_key', 'egg_needed',
                    'egg_menu_stage', 'egg_battle_row_dead', 'enemy_general_menu'):
            mem.pop(key, None)
        scope = [partial_enemy.name, partial_enemy.hp]
        previous = mem.get('enemy_general_menu_pending')
        count = (previous.get('observations') if isinstance(previous, dict)
                 and previous.get('scope') == scope else 0)
        count = count if type(count) is int and 0 <= count <= 30 else 30
        count = min(count + 1, 31)
        mem['enemy_general_menu_pending'] = {'scope': scope, 'observations': count}
        if count in (1, 31) and previous != mem['enemy_general_menu_pending']:
            _record(mem, 'enemy_general_menu_pending',
                    deviation_reason='enemy_command_unconfirmed' if count == 31 else None,
                    observed_metric={'enemy': partial_enemy.name,
                                     'enemy_hp': partial_enemy.hp,
                                     'observations': count,
                                     'ally_panel_unread': len(screen.egg_rows) == 1},
                    reason='敵将軍の実HP欄と左コマンドの途中表示。味方HPを補完せず入力を保留する')
        return []
    mem.pop('enemy_general_menu_pending', None)
    enemy_command = _enemy_general_command(screen)
    if enemy_command:
        ally, enemy = enemy_command
        # Invalidate our old navigation before returning; no stale down/down/A
        # sequence, empty-egg recheck or learned own action belongs to this turn.
        for key in ('egg_battle', 'egg_action', 'egg_key', 'egg_needed',
                    'egg_menu_stage', 'egg_battle_row_dead'):
            mem.pop(key, None)
        scope = [ally.name, ally.hp, enemy.name, enemy.hp]
        previous = mem.get('enemy_general_menu')
        count = (previous.get('observations') if isinstance(previous, dict)
                 and previous.get('scope') == scope else 0)
        count = count if type(count) is int and 0 <= count <= 30 else 30
        count = min(count + 1, 31)
        mem['enemy_general_menu'] = {'scope': scope, 'observations': count}
        if count in (1, 31) and previous != mem['enemy_general_menu']:
            _record(mem, 'enemy_general_menu_wait',
                    deviation_reason='enemy_command_unconfirmed' if count == 31 else None,
                    observed_metric={'owner': 'enemy', 'ally_monster': ally.name,
                                     'ally_hp': ally.hp, 'enemy': enemy.name,
                                     'enemy_hp': enemy.hp, 'observations': count},
                    reason=('敵将軍の実コマンド表示のため、敵の操作終了を待つ'
                            if count == 1 else
                            '敵コマンド表示の再観測上限。味方の卵や攻撃を推測入力せず、既存の画面停滞検出へ委ねる'))
        return []
    mem.pop('enemy_general_menu', None)
    # 将軍HPをこの召喚戦のメニュー行から最新化してから、勝てそうかを判定する。
    battle = mem.get('battle') or {}
    from . import okunote
    summon = okunote.summon_reading(screen, mem, battle)
    okunote.combat_return(screen, mem, battle, summon=summon)
    if battle:
        battle['card_summon_observed'] = True
        battle['card_context_unclassified'] = False
    general_reading = _egg_general_reading(screen, mem, battle)
    if egg_bait.observe_summon(screen, mem, battle):
        returning = _egg_retreat_return(screen, mem, battle)
        if returning is not None:
            return returning
    wants_egg, egg_evidence = _own_egg_needed(mem, battle, general_reading)
    # g684: アルテミス 49/49 saved her egg against ヘーゼル's old HP11,
    # while the actual opponent was ダークエルフ HP240. A live monster
    # panel owns this comparison; the general's old HP/army do not. Its
    # instant-death skill is dangerous even at a low remaining monster HP.
    enemy_rows = [r for r in screen.egg_rows if r.side == 'enemy']
    enemy_monsters = [r for r in enemy_rows
                      if r.name in reference.MONSTER_SKILLS
                      and type(r.hp) is int and r.hp > 0]
    summon_threat = bool(general_reading and len(enemy_rows) == len(enemy_monsters) == 1
                        and (enemy_monsters[0].name == 'ダークエルフ'
                             or general_reading[0] * 10
                             <= enemy_monsters[0].hp * BEHIND_EGG_RATIO_TENTHS))
    if summon_threat:
        wants_egg = True
        egg_evidence = {**egg_evidence, 'rule': 'enemy_monster_threat',
                        'enemy_monster': enemy_monsters[0].name,
                        'enemy_monster_hp': enemy_monsters[0].hp}
    empty_egg = _screen_egg_empty(screen, mem, battle)
    hero_summon = isinstance(battle, dict) and battle.get('ally') == NAME
    if hero_summon and not wants_egg:
        # g604 3-11: どうし 66/90 looked healthy against チコリ (HP11), so the
        # egg was "saved"; the summoned ダークエルフ's ひとだまくん is instant
        # death (gcgx evil_egg) and HP comparisons cannot price it. A dead
        # hero ends the run, so he always answers a summon with his egg.
        wants_egg = True
        egg_evidence = {**egg_evidence, 'rule': 'hero_vs_summon'}
    combat_danger = (not all(type(egg_evidence.get(k)) is int for k in ('ally_combat', 'enemy_combat'))
                     or (all(type(egg_evidence.get(k)) is int for k in
                             ('ally_melee_score', 'enemy_melee_score'))
                         and egg_evidence['ally_melee_score'] * 10
                         <= egg_evidence['enemy_melee_score'] * BEHIND_EGG_RATIO_TENTHS))
    if not mem.get('egg_battle'):
        mem['egg_battle'] = True
        exp = mem.get('_experience')
        key = experience.situation_key('egg_summon', mem)
        # ⑥の写像: HP不利ならたまご、勝てそうなら温存 (attack) から始める。
        action = experience.preferred(exp, key, default='use_egg' if wants_egg and not empty_egg else 'attack',
                                      kind='egg_summon')
        if (hero_summon or summon_threat or combat_danger) and not empty_egg:
            action = 'use_egg'  # learned attacks cannot overrule a measured summon threat
        if action == 'use_egg' and empty_egg:
            action = 'attack'
        mem['egg_action'] = action
        mem['egg_key'] = key
        mem['egg_needed'] = wants_egg
        if mem.get('battle'):
            battle['independent'] = {'kind': 'egg_summon', 'key': key, 'action': action,
                                     'pattern': '⑥'}
            battle['egg_battle'] = True     # earlier retreat threshold (see _hero_retreat_needed)
        _record(mem, 'egg_battle', strategy_variant=f'egg_battle_{action}',
                deviation_reason='チャート外: 敵の卵召喚戦',
                expected_metric='召喚獣への対処と戦闘結果',
                observed_metric={'experience_key': key, 'egg_need': egg_evidence},
                source_pattern='⑥',
                reason=('チャートに召喚戦の指示がないための独自判断（原典戦術⑥、'
                        '自軍が健在でHP・兵士数・戦闘力から白兵を続けられそうならたまごを温存）'
                        if not wants_egg else
                        'チャートに召喚戦の指示がないための独自判断（原典戦術⑥、既定はたまご）'))
    attack = mem.get('attack') or {}
    # g514: the hero was 34/90 against the boss's Hydra, with both planned
    # cards unused. Available eggs/cards must not bypass his retreat check.
    # A current named general panel and the same attack receipt are required;
    # monster HP, another general, or a previous battle cannot authorize B.
    if (general_reading and battle.get('ally') == NAME
            and battle.get('side') == attack.get('side') == 'attack'
            and attack.get('general') == NAME and battle.get('step')
            and attack.get('step') == battle.get('step')
            and attack.get('castle') == battle.get('castle')
            and not battle.get('away')
            and type(battle.get('start_ally_hp')) is int
            and 0 < battle['start_ally_hp'] <= general_reading[1]):
        retreat = _hero_retreat_open(mem, battle)
        if retreat is not None:
            return retreat
    if (_available_rare_tactic(mem, battle) and not battle.get('rare_egg_command_return')):
        battle['rare_egg_command_return'] = True
        _record(mem, 'battle_rare_egg_command', **_battle_labels(battle),
                observed_metric={'card': 'キャトルミュー'},
                reason='実携行のレア札で召喚敵へ対処するため、通常コマンドへ一度戻る')
        return [pad('b')]
    # 途中昇格: 温存していた (前回まで勝てていた) のに劣勢になったら、たまごを
    # 使う判断へ切り替える。回復しても温存へは戻さない (一方向だけ)。
    if ((mem.get('egg_needed') is False or summon_threat or combat_danger) and wants_egg and not empty_egg
            and mem.get('egg_action') == 'attack'):
        mem['egg_action'] = 'use_egg'
        if isinstance(battle.get('independent'), dict):
            battle['independent']['action'] = 'use_egg'
        _record(mem, 'egg_battle', strategy_variant='egg_battle_use_egg',
                deviation_reason='チャート外: 敵の卵召喚戦',
                expected_metric='召喚獣への対処と戦闘結果',
                observed_metric={'egg_need': egg_evidence}, source_pattern='⑥',
                reason=('実画面の敵召喚獣が将軍より強いか即死攻撃を持つため、卵の温存を取り消す'
                        if summon_threat else
                        '両軍の戦闘力比較が不利または未確認のため、既存の卵温存判断を取り消す'
                        if combat_danger else '温存していたが、自軍のHPが最大の7割以下になるか、'
                        'HPと兵士の合戦力が敵の7割を下回ったため途中でたまごを'
                        '使う判断へ切り替える'))
    mem['egg_needed'] = wants_egg
    action = mem.get('egg_action', 'use_egg')
    if empty_egg or (action == 'use_egg' and 'たまごをつかう' not in screen.text):
        # A spent egg greys the row out and drops it from OCR (mirrors
        # battle_menu_step's きりふだ/たいきゃく fallback); chasing a label
        # that never appears held the bot here forever (viewer report
        # 2026-09-28: 持ってないタマゴを使おうとして止まっている). The
        # attack fallback still checks the observed command cursor.
        if not mem.get('egg_battle_row_dead'):
            mem['egg_battle_row_dead'] = True
            _record(mem, 'situation_held', screen=screen.kind, strategy_variant='egg_unavailable',
                    reason=('使用回数0を観測済みのため卵を使わず既存の退却・応戦へ進む'
                            if empty_egg else
                            'たまごをつかうが使えない表示のため卵を諦めてこうげきで応戦する'))
        action = 'attack'
        if empty_egg:
            mem['egg_action'] = 'attack'
            if isinstance(battle.get('independent'), dict):
                battle['independent']['action'] = 'attack'
    if action == 'attack':
        battle = (mem.get('battle') if isinstance(mem.get('battle'), dict)
                  else mem.setdefault('egg_retreat_flow', {}))
        attempted = {*(battle.get('cards_selected') or []), *(battle.get('cards_used') or []),
                     *(battle.get('cards_unclassified') or [])}
        cards_left = [c for c in (battle.get('planned_cards') or []) if c not in attempted]
        # g498: B did not leave the turn menu, yet the next observation
        # immediately chose A and Venus lost. Require a bounded return flow,
        # and actually request retreat if the normal command menu is reached.
        # g460 安全弁は「卵か札か退却のどれもない」局面だけ。勝てそうなので
        # たまごを温存している間は、まず白兵で戦う (退却しない)。
        if (not cards_left and battle.get('side') != 'defense'
                and (wants_egg or empty_egg or 'たまごをつかう' not in screen.text)):
            returning = _egg_retreat_return(screen, mem, battle)
            if returning is not None:
                return returning
        # g508: after Excalibur fell, stale general HP82 concealed the
        # wounded Venus while blind A kept choosing the weakest attack.
        # Fierce attack adds lost HP to damage, but can miss/lose initiative;
        # use it only as a bounded-resource last resort with actual panels.
        enemy_rows = [r for r in screen.egg_rows if r.side == 'enemy'
                      and r.name in _MONSTER_SKILLS and type(r.hp) is int and r.hp > 0]
        fierce = (general_reading is not None and 0 < general_reading[0] * 2 <= general_reading[1]
                  and len(enemy_rows) == 1 and not cards_left
                  and (empty_egg or 'たまごをつかう' not in screen.text))
        label = 'もうこうげき' if fierce else 'こうげき'
        move = _battle_menu_to(screen, label)
        if move is None:
            if empty_egg:
                return [pad('b')]  # never confirm the empty egg at an unread cursor
            # Never confirm a previously selected fierce row blindly.
            return [] if fierce or battle.get('egg_attack_label') == 'もうこうげき' else [pad('a')]
        if battle.get('egg_attack_label') != label:
            battle['egg_attack_label'] = label
            _record(mem, 'battle_egg_general_attack', **_battle_labels(battle),
                    observed_metric={'command': label, 'general_hp': battle.get('ally_hp'),
                                     'max_hp': general_reading[1] if general_reading else None,
                                     'enemy_monster': enemy_rows[0].name if len(enemy_rows) == 1 else None},
                    reason=('卵・予定札がなく退却もできない負傷将軍は、実HPに基づきもうこうげきで応戦する'
                            if fierce else '実コマンド位置を確認して通常攻撃へ戻す'))
        return [pad('a')] if move == 'here' else [move]
    _egg_recheck(mem)
    if screen.hand:
        move = menu_to(screen, 'たまごをつかう')
        if move == 'here':
            return [pad('a')]
        if move:
            return [move]
        # Hand present but the label is unreadable: keep moving like no-hand.
    # No hand (or unreadable hand): walk down twice from こうげき to たまご.
    stage = int(mem.get('egg_menu_stage') or 0)
    if stage < 2:
        mem['egg_menu_stage'] = stage + 1
        return [pad('down')]
    return [pad('a')]
# Enemy-owned menu: hold this many observations before acting anyway. The
# agent observes every 1500 ms (config/games/hanjuku-hero.toml interval_ms),
# so 30 observations ≈ 45 s - long enough for the observed enemy selection
# window (g344 kept the same menu for 31 s) and far below the 300 s stasis
# limit, yet bounded so a stuck screen cannot freeze the corner.
MONSTER_MENU_HOLD_LIMIT = 30


def monster_menu_step(screen: Screen, mem):
    """Our summoned monster's own turn: a skill menu with independent judgment.

    The chart has no command for this menu. The owner is decided from the
    skill table: an enemy-owned menu waits for the enemy AI, bounded by
    MONSTER_MENU_HOLD_LIMIT so a stuck screen cannot freeze the bot. An
    allied menu checks the exposed general before retreating, keeps the special second skill
    while behind, skips a heal-first skill while ahead so damage still
    happens, otherwise uses the first skill, refined by measured
    experience. Movement follows the knight cursor when visible and a
    row-step stage when it is not.
    """
    rows = screen.menu_rows
    if not rows:
        return []
    row_texts = [''.join(line.known.split()) for line in rows]
    menu_key = tuple(row_texts)
    had_cursor_context = (mem.get('monster_menu_choice_scope') is not None
                          or mem.get('monster_menu_cursor') is not None
                          or mem.get('monster_menu_choice') is not None
                          or mem.get('monster_menu_cursor_recheck') is True)
    # JSON persistence turns the tuple into a list. The same observed menu
    # must retain its finite enemy hold across process/observation reloads.
    previous_menu = mem.get('monster_menu_key')
    if previous_menu != menu_key and previous_menu != list(menu_key):
        mem['monster_menu_key'] = menu_key
        for key in ('monster_menu_cursor', 'monster_menu_choice', 'monster_menu_choice_key',
                    'monster_menu_choice_hp', 'monster_ally_max_hp'):
            mem.pop(key, None)
        if had_cursor_context:
            # A partial redraw (even only the return row) cannot release the
            # requirement to reobserve a cursor after discarding a prediction.
            mem['monster_menu_cursor_recheck'] = True
        mem['monster_menu_hold'] = 0
    skill_lines = [line for line, text in zip(rows, row_texts) if 'もどれ' not in text]
    if not skill_lines:
        # The menu is still drawing: acting now could land on the retreat row.
        return []
    return_index = next((i for i, text in enumerate(row_texts) if 'もどれ' in text), None)
    panel = {row.side: row for row in screen.egg_rows if row.side}
    ally, enemy = panel.get('ally'), panel.get('enemy')
    mem['monster_panel'] = {'ally': ally.name if ally else None,
                            'ally_hp': ally.hp if ally else None,
                            'enemy': enemy.name if enemy else None,
                            'enemy_hp': enemy.hp if enemy else None}
    if mem.get('battle') and enemy and type(enemy.hp) is int and enemy.hp > 0:
        mem['battle']['card_summon_observed'] = True
        mem['battle']['card_context_unclassified'] = False
    owner = _monster_owner(skill_lines, ally, enemy)
    scope = [owner, ally.name if ally else None, enemy.name if enemy else None]
    if mem.get('monster_menu_choice_scope') != scope:
        mem.pop('monster_menu_choice', None)
        mem.pop('monster_menu_cursor', None)
        mem['monster_menu_choice_scope'] = scope
        if had_cursor_context:
            mem['monster_menu_cursor_recheck'] = True
    if mem.get('monster_menu_cursor_recheck'):
        if (type(screen.menu_cursor) is not int
                or screen.menu_cursor not in {line.y for line in rows}):
            _record(mem, 'monster_menu_wait', deviation_reason='scope_cursor_unread',
                    observed_metric={'owner': owner, 'scope': scope},
                    reason='所有者・召喚獣が変わり旧予測を破棄したため、実カーソルを再観測するまで確定しない')
            return []
        mem.pop('monster_menu_cursor_recheck', None)
    if owner == 'enemy':
        hold = int(mem.get('monster_menu_hold') or 0) + 1
        mem['monster_menu_hold'] = hold
        if hold <= MONSTER_MENU_HOLD_LIMIT:
            if hold == 1:
                _record(mem, 'monster_menu_wait',
                        observed_metric={'owner': owner,
                                         'enemy': enemy.name if enemy else None,
                                         'enemy_hp': enemy.hp if enemy else None,
                                         'menu': list(menu_key)},
                        reason='技選択は敵側の表示（敵AIが選ぶ）ため入力を保留')
            return []
        if hold == MONSTER_MENU_HOLD_LIMIT + 1:
            _record(mem, 'monster_menu_wait', deviation_reason='enemy_menu_stuck',
                    observed_metric={'hold_observations': hold, 'menu': list(menu_key)},
                    reason='敵側表示のまま停留が上限を超えたため、画面停止を避けて選択へ移行')
    else:
        mem['monster_menu_hold'] = 0
    # Re-decide when the panel HP changed: a cached choice repeated the same
    # skill every turn (g407: ふくらむ x71) and never let a heal-first monster
    # alternate ふくらむ→シャウト (owner 2026-09-29).
    hp_state = (ally.hp if ally else None, enemy.hp if enemy else None)
    # GCGX: エクスカリバる is instant death (11/current HP against
    # monsters), マサムネる hits four times. g498 used the former at
    # 282HP against a 240HP Dark Elf, then lost the summon and Venus.
    damage_second = _monster_damage_second(
        owner, ally, enemy, [''.join(line.known.split()) for line in skill_lines])
    damage_first = _monster_damage_first(
        owner, ally, enemy, [''.join(line.known.split()) for line in skill_lines])
    return_risk = _summoner_return_risk(mem, owner, ally, enemy)
    # Each named monster has an ordered two-skill table. When one label is
    # partly unreadable, requiring both exact OCR matches accidentally falls
    # back to the generic first-skill loop. Owner must still be proven by the
    # reference table, and at least one slot must match exactly.
    balloon_cycle = (owner == 'ally' and ally is not None
                     and ally.name == 'バルーンフィンチ' and len(skill_lines) == 2
                     and (_fold_skill(skill_lines[0].known) == _fold_skill('ふくらむ')
                          or _fold_skill(skill_lines[1].known) == _fold_skill('シャウト')))
    akematsu_cycle = (owner == 'ally' and ally is not None
                      and ally.name == 'あけまつ' and len(skill_lines) == 2
                      and (_fold_skill(skill_lines[0].known) == _fold_skill('ごあいさつ')
                           or _fold_skill(skill_lines[1].known) == _fold_skill('おもてなし')))
    akematsu = akematsu_assessment(mem, ally, enemy) if akematsu_cycle else None
    special_cycle = balloon_cycle or akematsu_cycle
    # A live deployment can change a rule while the menu HP stays unchanged.
    # Re-evaluate old cached choices on the first new-policy observation.
    special_epoch = '20261016-balloon-akematsu-v2'
    if (not mem.get('monster_menu_choice') or mem.get('monster_menu_choice_hp') != hp_state
            or (special_cycle and mem.get('monster_menu_choice_policy_epoch') != special_epoch)
            or (akematsu_cycle and mem.get('monster_menu_choice') != akematsu['action'])
            or mem.get('monster_menu_choice') == 'retreat'
            or (damage_second and mem.get('monster_menu_choice') not in ('skill2', 'retreat'))
            or (damage_first and mem.get('monster_menu_choice') not in ('skill1', 'retreat'))):
        ally_hp = ally.hp if ally else None
        enemy_hp = enemy.hp if enemy else None
        behind = _behind({'ally_hp': ally_hp, 'enemy_hp': enemy_hp})
        first = ''.join(skill_lines[0].known.split())
        second = ''.join(skill_lines[1].known.split()) if len(skill_lines) >= 2 else ''
        # g496: ウゴカザル's two commands do nothing; spending turns on
        # them cost 120 -> 46 -> 11 HP without reducing the defender.
        powerless = (owner == 'ally' and ally.name == 'ウゴカザル'
                     and len(skill_lines) == 2
                     and {_fold_skill(first), _fold_skill(second)}
                     == _MONSTER_SKILLS['ウゴカザル'])
        heal_first = bool(second) and _monster_heal(first) and not _monster_heal(second)
        default = 'skill1'
        if damage_first:
            default = 'skill1'
        elif damage_second:
            default = 'skill2'
        elif balloon_cycle:
            # Measured hits use a three-turn reserve; absent measurements,
            # a conservative explicitly-unproven reserve prevents 9999 loops.
            balloon = balloon_assessment(mem, ally, enemy, BALLOON_FINCH_MAX_HP)
            default = balloon['action']
        elif akematsu_cycle:
            # Switching abilities a second time undoes the first exchange.
            # After issuing one observed ごあいさつ, summon with おもてなし.
            default = akematsu['action']
        elif heal_first:
            # Generic heal-first monsters still use the best HP observed in
            # this menu session as their recovery target.
            seen = mem.get('monster_ally_max_hp')
            if type(ally_hp) is not int:
                default = 'skill1'
            elif behind or (type(seen) is int and ally_hp < seen):
                default = 'skill1'
            else:
                default = 'skill2'
            if type(ally_hp) is int:
                mem['monster_ally_max_hp'] = max(int(seen or 0), ally_hp)
        elif behind and len(skill_lines) >= 2 and _monster_effectful(second):
            default = 'skill2'
        exp = mem.get('_experience')
        key = experience.situation_key('monster_menu', mem)
        # Special monster owner tactics must not be overridden by generic
        # retreat/experience: Balloon Finch survival and Akematsu one-shot swap.
        action = (default if special_cycle else
                  'retreat' if powerless else 'skill1' if damage_first else 'skill2' if damage_second
                  else experience.preferred(exp, key, default=default, kind='monster_menu'))
        # HP pools alone do not establish that this monster cannot win: a
        # living summon can attack, heal or disable the opponent. Returning
        # also exposes its general. Discard saved/learned HP-only retreats;
        # retain the explicit, measured two-useless-skills exception.
        if action == 'retreat' and not powerless:
            action = default
        # Keep the existing telemetry shape. The general's exposure risk is
        # still reported, but useful summons no longer request a return that
        # needs to be blocked by that separate guard.
        protected_return = False
        mem['monster_menu_choice'] = action
        mem['monster_menu_choice_key'] = key
        mem['monster_menu_choice_hp'] = hp_state
        mem['monster_menu_choice_policy_epoch'] = special_epoch if special_cycle else None
        battle = mem.get('battle')
        if isinstance(battle, dict):
            battle['independent'] = {'kind': 'monster_menu', 'key': key, 'action': action}
        if action == 'retreat':
            label = 'たまごに もどれ'
            why = '両技に攻撃性能がない召喚獣のため、無効な攻撃を繰り返さず戻す'
        elif balloon_cycle:
            label = second if action == 'skill2' else first
            if balloon['rule'] == 'survival_buffer':
                why = (f"シャウト後HP{balloon['post_shout_hp']}が、被ダメージ推定"
                       f"{balloon['incoming_damage']}に25%の余裕を加えた"
                       f"{balloon['survival_hits']}回分を超えるため全回復を待たず攻撃")
            elif balloon['rule'] == 'observed_zero_reserve':
                why = (f"無被弾の確定観測が{balloon['zero_loss_samples']}回あり、"
                       f"HP{ally_hp}が暫定目標{balloon['target_hp']}に達したためシャウト"
                       "（今後の被ダメージは未確定）")
            elif balloon['rule'] == 'unmeasured_reserve':
                why = (f"被ダメージ未推定だがHP{ally_hp}が保守的な暫定目標"
                       f"{balloon['target_hp']}に達したためシャウト"
                       "（3回生存の保証ではない）")
            elif action == 'skill2':
                why = f'HPが上限{BALLOON_FINCH_MAX_HP}なので「シャウト」で相手を削る'
            elif balloon['incoming_damage'] is not None:
                why = (f"HPが{ally_hp}で耐久目標{balloon['target_hp']}未満なので"
                       '「ふくらむ」で次のシャウトに備える')
            else:
                why = (f"敵ダメージ未推定のため、HP{ally_hp}を暫定目標"
                       f"{balloon['target_hp']}まで「ふくらむ」で確保する"
                       f"（無被弾の確定観測{balloon['zero_loss_samples']}回）")
        elif akematsu_cycle:
            label = second if action == 'skill2' else first
            if action == 'skill1':
                why = '能力交換の「ごあいさつ」はこの戦闘で1回だけ送信し、その後は召喚技へ移る'
            elif akematsu['rule'] == 'unscoped_hospitality':
                why = '戦闘の同一性が読めないため能力交換を繰り返さず「おもてなし」で交代する'
            elif akematsu['rule'] == 'hospitality_after_unverified_attempt':
                why = ('カーソル未読で「ごあいさつ」を1回試みたため、交換成功は未確認だが'
                       '同じ技の無限試行を防ぎ「おもてなし」に切り替える')
            else:
                why = '「ごあいさつ」を1回送信済みのため、以降は「おもてなし」で別の召喚獣を呼ぶ'
        elif action == 'skill2' and len(skill_lines) >= 2:
            label = second
            why = ('敵召喚獣には即死の反復より通常2回攻撃を優先する'
                   if damage_second and ally.name == 'ユニコーン' else
                   '敵召喚獣には低確率の即死より4回攻撃を優先する' if damage_second else
                   '1技目が回復技のため敵を減らす2技目を選ぶ'
                   if heal_first and not behind else '味方が劣勢で効果付きの2技目')
        else:
            label, why = first, ('敵召喚獣には混乱技の反復より実ダメージの1技目を優先する'
                                 if damage_first else '先手を取れる1技目を続ける')
        if not special_cycle and action != default and action != 'retreat':
            why = f'過去の結果に基づく経験の選択（既定 {default}）'
        _record(mem, 'monster_menu_choice',
                strategy_variant=f'monster_menu_{action}',
                deviation_reason='チャートに召喚獣ターンの指示がない',
                expected_metric='召喚獣ターンの選択と戦闘結果',
                observed_metric={'action': action, 'owner': owner, 'menu': list(menu_key),
                                 'ally_hp': ally_hp, 'enemy_hp': enemy_hp,
                                 'target_hp': balloon['target_hp'] if balloon_cycle else None,
                                 'balloon_survival': balloon if balloon_cycle else None,
                                 'akematsu_exchange': akematsu if akematsu_cycle else None,
                                 'damage_first': damage_first,
                                 'wounded_general_return_blocked': bool(protected_return
                                     and return_risk['rule'] == 'wounded_general'),
                                 'general_return_blocked': protected_return,
                                 'general_return_risk': return_risk,
                                 'experience_key': key},
                reason=f'召喚獣の技メニューで「{label}」を選択: {why}')
    action = mem.get('monster_menu_choice')
    if action == 'retreat':
        if return_index is None:
            return []
        target_index = return_index
    elif action == 'skill2' and len(skill_lines) >= 2:
        target_index = rows.index(skill_lines[1])
    else:
        target_index = rows.index(skill_lines[0])
    target = rows[target_index].y
    cur = screen.menu_cursor
    if cur is None:
        cur = mem.get('monster_menu_cursor')
    if cur is None:
        cur = rows[0].y
    if target > cur + 4:
        mem['monster_menu_cursor'] = cur + 16
        return [pad('down')]
    if target < cur - 4:
        mem['monster_menu_cursor'] = cur - 16
        return [pad('up')]
    if screen.menu_cursor == target and balloon_cycle:
        balloon_command_selected(mem, ally, enemy, action, BALLOON_FINCH_MAX_HP)
    if akematsu_cycle and action == 'skill1':
        # The cursor may remain unreadable for the entire menu. This is only
        # an attempted input, never evidence of a successful stat exchange.
        # Nevertheless, sending ごあいさつ on every frame is worse.
        akematsu_command_selected(
            mem, ally, enemy, action,
            cursor_confirmed=(screen.menu_cursor == target))
    return [pad('a')]
