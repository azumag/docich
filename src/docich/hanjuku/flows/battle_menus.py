"""手順層: battle command menus (cards, eggs, okunote, monsters).

Navigates the command/egg/card/monster menus opened by ``flows.battle`` and
selects the option the judgment layer chose.
"""
from __future__ import annotations

from types import SimpleNamespace

from ..os_layer.screen import OKUNOTE_CHOICES, Screen
from ... import hanjuku_chart as chart, hanjuku_experience as experience, hanjuku_reference as reference
from ...hanjuku_egg_reference import general_max_hp
from ..decision_log import _record
from ..state import egg_memory
from ..state.regions import plan as _plan_region
from . import egg_bait
from .battle import _card_battle_reading, _card_gate_close, _hero_retreat_open, _remember_general_for_summon
from ..judgment.battle import (BEHIND_EGG_RATIO_TENTHS, CARD_ANNOUNCE_LIMIT, CARD_LIST_CURSOR_LIMIT,
                               CARD_SPEND_ATTEMPT_LIMIT, _card_spend_attempts, _note_card_spend_attempt,
                               OKUNOTE_MAX_SELF_DAMAGE, SURVIVAL_CARDS, _available_rare_tactic, _battle_labels,
                               _behind, _boss_tactics_allowed, _card_damage_gate,
                               _card_gate_alternatives, _card_gate_choice,
                               _card_gate_refused, _card_use_unclassified, _command_open_wait, _confirmed_carried_kit, _critical_safe_card,
                               DEFENDER_EGG_OPEN_LIMIT, DEFENDER_SUMMON_SCROLL_LIMIT, DEFENSE_MENU_READ_LIMIT,
                               EGG_WALL_DAMAGE, SUMMON_SCROLL_BATCH,
                               _critical_survival_needed, _defender_egg_window, _defender_last_resort, _defender_summon_preempt,
                               _defender_summon_last_resort, _defense_kit_read_unread, _egg_drop_trace,
                               _egg_before_retreat, _egg_rescue_pending, _egg_drop_evidence, _hero_retreat_needed, _hold_melee_egg, _melee_balance,
                               _retreat_cards_to_spend, _retreat_immediate_loss, _retreat_remaining_cards,
                               _healthy_melee_margin_evidence, _melee_continuation_evidence,
                               _stale_army_melee_hold,
                               _dying_with_egg, _migrate_card_evidence, _one_hit_lethal, _hp1_resource_hold, _own_egg_empty,
                               _own_egg_needed, _own_egg_positive,
                               _survival_needed, _survival_state, _watch_card_melee, _watch_egg_drop,
                               _wounded_summoner, _summoner_return_risk, _rescue_rearm)
from ..judgment.events import (BALLOON_FINCH_MAX_HP, _MONSTER_SKILLS, _monster_effectful, _monster_heal,
                               _monster_owner, _monster_damage_second, _monster_damage_first,
                               _monster_chikyu_choice, _monster_lampkin_choice, _lampkin_menu, _lampkin_forced_kind,
                               _monster_attack_policy_default, _monster_menu_attackless)
from ..judgment.balloon import balloon_assessment, balloon_command_selected
from ..judgment.akematsu import akematsu_assessment, akematsu_command_selected
from ..judgment.omedetori import omedetori_assessment, omedetori_command_selected
from ..os_layer.commands import _battle_menu_to, menu_to, pad
from ..os_layer.readers import ALL_CARD_NAMES, BATTLE_MENU, _fold_skill, _options
from ..os_layer.world import NAME
from ..strategy.economy import _egg_recheck


LAST_RESORT_STALL_TICKS = 60   # a full turn apart with the summon HP unmoved
EMPTY_CARD_LIST_TEXTS = ('きりふだはありません', 'きりふだなし', 'なにももってない')
CARD_LIST_SAME_FRAME_LIMIT = 12
# Owner 2026-11-24: 相手（敵が召喚したモンスター）がゾンビなら卵を使わず
# 白兵で対処する。ゾンビパンチは0ダメージ、ケアルは自己回復で、将軍を倒す
# 即死・自爆の技能を持たない (hanjuku_reference.MONSTER_SKILLS)。原典チャート
# (games/hanjuku-sfc-speedrun/charts/7.md) も対ブラウニーで「ゾンビは召喚
# させ、もうこうげきでターン経過」と白兵を指示している。実画面の敵召喚獣が
# 1体だけ読め、名前が完全一致したときだけ適用する (未知・複数・未読へ広げない)。
#
# Owner 2026-11-24 (追加): ウゴカザルも同じ。2つの技「なぐれっ！」「かきむしれ！」
# はどちらも0ダメージで、卵を使わず「こうげき」だけで倒せる。ゾンビと同じく
# 将軍を脅かさないので、卵・おくのてを消費せず白兵で処理する。
MELEE_ONLY_SUMMONS = frozenset({'ゾンビ', 'ウゴカザル'})
# Fields read from a real general status page (g1097 marching/camp/fort and
# g1190 chapter 4 castle frames).  HP + ちんぎん alone also appear on a 砦
# card and a sortie-confirm panel, so the page needs its battle/egg/hobby
# fields as well; 「しろのなかにいます」 stays an alternate key because that
# sentence alone identified the g1190 A-forever page.
STATUS_PANEL_FIELDS = ('HP', 'ちんぎん', 'たまご', 'せんとう', 'ないせい', 'しゅみ')


def _is_general_status_panel_text(text: str) -> bool:
    """True only for the general status page, not any HP + ちんぎん panel.

    The measured status layout carries all of ``STATUS_PANEL_FIELDS`` plus
    ``Bボタンでもどる``; the 砦 card lacks the return sentence and the sortie
    confirm has neither the egg nor the hobby row.  Camping/marching *status*
    pages are still this layout (the location sentence changes, the fields do
    not), which is intended: they are the same page whose leftover A-advance
    was the g1190 2026-11-22 09:00 (n=30341) failure.
    """
    if 'Bボタンでもどる' not in text:
        return False
    if 'しろのなかにいます' in text:
        return True
    return all(field in text for field in STATUS_PANEL_FIELDS)


# Owner 2026-11-24: the five-item human command list (こうげき / もうこうげき /
# たまごをつかう (grey) / きりふだ / たいきゃく) is a three-row window.  When it
# was left scrolled to the bottom page, the bot saw only きりふだ/たいきゃく and
# sent B 297 times; B does not close this box (measured 2026-11-24, 10/40-frame
# presses changed nothing).  Walk the cursor up one row per observation until
# the cursor row is exactly こうげき, then confirm.  The bound is well above the
# measured four ups; the overrun holds instead of falling back to B.
HUMAN_COMMAND_SCROLL_UP_LIMIT = 6
HUMAN_COMMAND_SCROLL_KEY = 'human_command_scroll'
# The human five-item list is drawn in the right-hand column (word x=176).
# Enemy-general command menus use the left column (x=32/40) and ignore the
# player's up/down.  Treating those as the human list sent six no-op ups and
# then held with no input (g1190 ch12 2026-11-24 20:31:27, reproduced on the
# real g747 enemy-command frame).
HUMAN_COMMAND_SCROLL_X_MIN = 168
# An exhausted episode holds only this many observations before releasing the
# frame to the previous owner.  The old code returned [] forever.
HUMAN_COMMAND_SCROLL_EXHAUSTED_HOLD = 2
# Two consecutive ups whose frame/cursor did not change prove that up is a
# no-op on this box; stop rather than spend the whole up budget.
HUMAN_COMMAND_SCROLL_NO_PROGRESS_LIMIT = 2
# A backstop for an episode that stays on a menu forever without confirming.
HUMAN_COMMAND_SCROLL_OBSERVATION_LIMIT = 20


def _human_menu_column(screen):
    """True when a command row is drawn in the right-hand human column."""
    for line in getattr(screen, 'menu_rows', ()) or ():
        spans = line.spans()
        if spans and spans[0][0] >= HUMAN_COMMAND_SCROLL_X_MIN:
            return True
    return False


def _cursor_row_text(screen):
    """The whole text of the cursor row in the human column, or None.

    Joining every span at the cursor's y handles a row the OCR split into
    pieces; the caller compares the joined string as a whole.  Substring tests
    stay forbidden because もうこうげき contains こうげき.
    """
    y = getattr(screen, 'menu_cursor', None)
    if type(y) is not int:
        return None
    for line in getattr(screen, 'menu_rows', ()) or ():
        if line.y != y:
            continue
        words = [word for x, word in line.spans() if x >= HUMAN_COMMAND_SCROLL_X_MIN]
        return ''.join(words) if words else None
    return None


def _cursor_exact_label(screen):
    """Exactly the row text under the measured cursor, or None when unread.

    The same list contains こうげき and もうこうげき, and the latter contains
    the former as a substring.  Every caller must compare the whole row, never
    use a substring test, or it would confirm もうこうげき (measured: Ku-mon
    swallows Athena on that row).
    """
    return _cursor_row_text(screen)


def _human_five_item_box(screen, labels=None):
    """The scrolled human command list: きりふだ visible, no おくのて row.

    A grey たまご row is not read as text, so the bottom/middle pages show
    きりふだ without たまご.  A real egg-battle box carries たまご (white or
    grey) and its 4th おくのて row; neither is this list.
    """
    if labels is None:
        labels = {word for _, _, word in _options(screen)}
    if getattr(screen, 'kind', None) not in ('battle_menu', 'egg_battle_menu'):
        return False
    if not _human_menu_column(screen):
        return False
    if getattr(screen, 'menu_cursor', None) not in (176, 192, 196, 208, 212):
        return False
    if (getattr(screen, 'hidden_battle_commands', False)
            or getattr(screen, 'disabled_item_box', False)
            or getattr(screen, 'okunote_only_box', False)):
        return False
    # The human command list is drawn over the live battle panel.  A bare
    # menu with no HP rows is a synthetic/legacy card-flow box, not this list.
    if not getattr(screen, 'egg_rows', None):
        return False
    return ('きりふだ' in labels and 'おくのて' not in labels
            and 'たまごをつかう' not in labels)


def _human_scroll_state(cur):
    return cur.get(HUMAN_COMMAND_SCROLL_KEY) if isinstance(cur, dict) else None


def _human_scroll_active(cur):
    state = _human_scroll_state(cur)
    return isinstance(state, dict) and state.get('active') is True


def _human_scroll_owns(screen, labels, cur, *, chapter=None):
    """True while the measured five-item scroll owns this command box.

    The evidence is specific to the ch12 boss command list observed in g1190.
    Other chapters have real grey-egg summon menus with the same OCR shape;
    their existing card/egg owner must retain control.  The right-hand column
    is part of the evidence: enemy-general command menus use the left column
    and ignore the player's keys.
    """
    if chapter != 12:
        return False
    if not _human_menu_column(screen):
        return False
    if _human_five_item_box(screen, labels):
        return True
    if not _human_scroll_active(cur):
        return False
    if getattr(screen, 'menu_cursor', None) is None:
        return False
    if not labels or labels.intersection({'おくのて', 'たまごをつかう'}):
        return False
    # The upper page drops きりふだ from OCR; only the attack rows remain.
    return bool(labels.intersection({'こうげき', 'もうこうげき', 'きりふだ', 'たいきゃく'}))


def _stale_battle_actor(screen, cur):
    """The screen's own general is not the one policy.battle remembers.

    g1190 ch12 2026-11-24: policy.battle still held エストラゴン/カシス while
    the live frame was アテナ vs クーモン.  The stale enemy general is normal in
    a summon fight (the panel names the monster), so only the ally row is
    evidence; the general cannot be replaced between observations.
    """
    if not isinstance(cur, dict):
        return False
    ally = cur.get('ally')
    names = [row.name for row in (getattr(screen, 'egg_rows', ()) or ())
             if row.side == 'ally' and isinstance(row.name, str) and row.name]
    return bool(isinstance(ally, str) and ally and names and ally not in names)


def _screen_ally_name(screen):
    """The ally general's name read from the live battle panel, or None."""
    for row in (getattr(screen, 'egg_rows', ()) or ()):
        if row.side == 'ally' and isinstance(row.name, str) and row.name:
            return row.name
    return None


def _begin_human_scroll(cur, screen=None):
    """Create the persisted scroll episode once with its live actor/context."""
    state = _human_scroll_state(cur)
    if isinstance(state, dict):
        return state
    state = {
        'active': True,
        'ups': 0,
        'observations': 0,
        'actor': _screen_ally_name(screen) if screen is not None else None,
        'last_frame': getattr(screen, 'frame_sha256', None) if screen is not None else None,
        'last_cursor': getattr(screen, 'menu_cursor', None) if screen is not None else None,
        'no_progress': 0,
    }
    cur[HUMAN_COMMAND_SCROLL_KEY] = state
    return state


def _release_human_scroll(mem, cur, reason, *, observed=None):
    """Pop the episode and record why ownership was returned."""
    state = cur.pop(HUMAN_COMMAND_SCROLL_KEY, None)
    if not isinstance(state, dict):
        return
    metric = {'ups': state.get('ups'), 'exhausted': bool(state.get('exhausted')),
              'observations': state.get('observations')}
    if observed:
        metric.update(observed)
    _record(mem, 'battle_menu_scroll_released', **_battle_labels(cur),
            observed_metric=metric, reason=reason)


def _human_scroll_expired(screen, cur):
    """True when the persisted episode no longer describes the live frame.

    The episode is scoped to one battle-menu owner: leaving the command menu,
    losing the right-hand column, or reading a different ally general ends it.
    The observation limit is a backstop for a menu that never confirms.
    """
    state = _human_scroll_state(cur)
    if not isinstance(state, dict) or state.get('active') is not True:
        return True
    if getattr(screen, 'kind', None) not in ('battle_menu', 'egg_battle_menu'):
        return True
    if not _human_menu_column(screen) and getattr(screen, 'menu_rows', None):
        # A draw with readable rows on the left is the enemy's menu.  An
        # unreadable transient keeps the episode for the observation limit.
        return True
    actor = state.get('actor')
    live = _screen_ally_name(screen)
    if (isinstance(actor, str) and actor
            and isinstance(live, str) and live and actor != live):
        return True
    if int(state.get('observations') or 0) > HUMAN_COMMAND_SCROLL_OBSERVATION_LIMIT:
        return True
    return False


def expire_human_scroll(screen, mem):
    """Release an episode that no longer matches the live frame.

    Called for every observation from the dispatcher.  A marker stored inside
    a stale ``policy.battle`` survives that battle in production (the live
    g1190 record did), so it cannot rely on ``battle_end`` alone.
    """
    cur = mem.get('battle')
    if not isinstance(cur, dict):
        return
    state = _human_scroll_state(cur)
    if not isinstance(state, dict):
        return
    if not _human_scroll_expired(screen, cur):
        return
    _release_human_scroll(
        mem, cur,
        '戦闘メニューから離れた、右列コマンドでなくなった、味方が変わった、'
        'または観測上限に達したためスクロール所有を解除する',
        observed={'screen_kind': getattr(screen, 'kind', None),
                  'cursor_y': getattr(screen, 'menu_cursor', None),
                  'actor_on_screen': _screen_ally_name(screen),
                  'episode_actor': state.get('actor')})


def _human_scroll_step(screen, mem, cur, labels):
    """One closed-loop up per observation until the cursor row is こうげき.

    Returns a pad action list, or None when no scroll episode owns the frame.
    The episode is released on the exact こうげき row, when the screen/actor
    no longer matches, or after a bounded hold once it is exhausted; it never
    returns [] forever.  ``up_changed`` in the records separates "up moved the
    cursor" from "this box ignored up".
    """
    state = _human_scroll_state(cur)
    if not isinstance(state, dict) or state.get('active') is not True:
        return None
    state['observations'] = int(state.get('observations') or 0) + 1
    if _human_scroll_expired(screen, cur):
        _release_human_scroll(
            mem, cur,
            '戦闘メニューから離れた、右列コマンドでなくなった、味方が変わった、'
            'または観測上限に達したためスクロール所有を解除する',
            observed={'screen_kind': getattr(screen, 'kind', None),
                      'cursor_y': getattr(screen, 'menu_cursor', None),
                      'labels': sorted(labels)})
        return None
    exact = _cursor_exact_label(screen)
    if exact == 'こうげき':
        cur.pop(HUMAN_COMMAND_SCROLL_KEY, None)
        _record(mem, 'battle_menu_scroll_attack', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'cursor_y': screen.menu_cursor,
                                 'cursor_label': exact, 'ups': state.get('ups'),
                                 'actor': state.get('actor')},
                reason='五項目コマンドリストを上へ戻し、カーソル行が完全一致でこうげきになったためAで確定する')
        return [pad('a')]
    if state.get('exhausted'):
        held = int(state.get('exhausted_held') or 0)
        if held < HUMAN_COMMAND_SCROLL_EXHAUSTED_HOLD:
            state['exhausted_held'] = held + 1
            return []
        _release_human_scroll(
            mem, cur,
            '有界の保留後もこうげき行を観測できないため、フレームを以前のオーナーへ戻す',
            observed={'labels': sorted(labels), 'cursor_y': screen.menu_cursor,
                      'cursor_label': exact})
        return None
    ups = int(state.get('ups') or 0)
    # The first up has no previous up to compare with; None is not "unchanged".
    changed = None if ups == 0 else (
        screen.frame_sha256 != state.get('last_frame')
        or screen.menu_cursor != state.get('last_cursor'))
    no_progress = (int(state.get('no_progress') or 0) + 1
                   if (ups and changed is False) else 0)
    state['no_progress'] = no_progress
    if no_progress >= HUMAN_COMMAND_SCROLL_NO_PROGRESS_LIMIT:
        state.update(exhausted=True, exhausted_held=0)
        _record(mem, 'battle_menu_scroll_exhausted', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'cursor_y': screen.menu_cursor,
                                 'cursor_label': exact, 'ups': ups,
                                 'limit': HUMAN_COMMAND_SCROLL_UP_LIMIT,
                                 'up_changed': False, 'no_progress': no_progress},
                reason='upを送っても画面/カーソルが変わらないため、これ以上のupを止めて有界に保留する')
        return []
    if ups >= HUMAN_COMMAND_SCROLL_UP_LIMIT:
        state.update(exhausted=True, exhausted_held=0)
        _record(mem, 'battle_menu_scroll_exhausted', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'cursor_y': screen.menu_cursor,
                                 'cursor_label': exact, 'ups': ups,
                                 'limit': HUMAN_COMMAND_SCROLL_UP_LIMIT,
                                 'up_changed': changed, 'no_progress': no_progress},
                reason='有界な上スクロール上限でもこうげき行を観測できないため、Bを送らず入力を保留する')
        return []
    state['ups'] = ups + 1
    state['last_frame'] = screen.frame_sha256
    state['last_cursor'] = screen.menu_cursor
    _record(mem, 'battle_menu_scroll_up', **_battle_labels(cur),
            observed_metric={'labels': sorted(labels), 'cursor_y': screen.menu_cursor,
                             'cursor_label': exact, 'up_attempt': ups + 1,
                             'limit': HUMAN_COMMAND_SCROLL_UP_LIMIT,
                             'up_changed': changed, 'no_progress': no_progress},
            reason='攻撃コマンドが上にスクロールアウトした五項目リストのため、Bではなく上へ戻す')
    return [pad('up')]


def _is_empty_card_list_text(screen: Screen) -> bool:
    """True when the battle card box explicitly states no cards are carried."""
    compact = ''.join((screen.text or '').split())
    return any(token in compact for token in EMPTY_CARD_LIST_TEXTS)


def _okunote_last_resort_gate(cur, last_resort, now=None):
    """A last resort that did not dent the summon for a whole turn is not repeated.

    g1163 2026-11-13 19:28-19:41: five ``battle_okunote_last_resort`` records
    in one defence with the enemy summon stuck at 27 HP and the ally 32 -> 0.
    Each draw only cost our own general. A same-tick reuse (the menu just
    reopened) is still allowed; the owner's reuse rule stays.
    """
    if not last_resort:
        return False
    previous, enemy = cur.get('okunote_last_resort_enemy_hp'), cur.get('enemy_hp')
    since = cur.get('okunote_last_resort_tick')
    if (type(previous) is int and type(enemy) is int and enemy >= previous
            and type(now) is int and type(since) is int
            and now - since >= LAST_RESORT_STALL_TICKS):
        cur['okunote_last_resort_failed'] = True
        return False
    cur['okunote_last_resort_enemy_hp'] = enemy
    cur['okunote_last_resort_tick'] = now
    return True


def orphan_battle_menu_step(screen, mem, *, owner_active=None):
    """Close an unbound parent menu finitely, then require new native context.

    The gray commands do not identify an actor, HP or a usable Okunote. A
    background animation/fade must neither refill the cancellation budget nor
    release an exhausted episode to the generic scene-confirm fallback.

    ``owner_active`` is the orchestrator's pre-flow snapshot
    (``status_panel.status_panel_owner_active``): when a live flow is reading
    this general status page, the page belongs to it and this step returns
    None.  Returning ``[]`` would stop the dispatcher before that owner ran.
    """
    flow = mem.get('orphan_battle_menu')
    restored = isinstance(mem.get('resume_battle_menu'), dict)
    if mem.get('battle'):
        mem.pop('orphan_battle_menu', None)
        mem.pop('resume_battle_menu', None)
        return None
    # A general's status page (opened by the monthly/roster walk) carries the
    # measured STATUS_PANEL_FIELDS and 「Bボタンでもどる」.  The plain-command
    # fade path was advancing it with A forever (g1190 2026-11-22 09:00,
    # n=30341).  Earlier this branch accepted any HP + ちんぎん panel with the
    # return sentence, including non-status cards that carry those two rows.
    # Require the measured status fields instead (#5408); camp/march *status*
    # pages have the same layout and stay covered.
    # The owners that read this page dispatch after this step, so only close it
    # when no owner was active before the flows ran (post_month_status failed
    # every month with status_unreadable because this B beat its reader).  When
    # an owner is active, return None before the plain-command A path below:
    # advancing the owner's page with A is the g1190 loop this branch fixed.
    text = screen.text or ''
    if _is_general_status_panel_text(text):
        if owner_active:
            # Drop the stale claim, as the close path did: the owner reads this
            # page and returns to its roster, and a leftover plain-command flow
            # would press A on that roster frame ahead of the same owner.
            mem.pop('orphan_battle_menu', None)
            mem.pop('resume_battle_menu', None)
            return None
        mem.pop('orphan_battle_menu', None)
        mem.pop('resume_battle_menu', None)
        _record(mem, 'battle_status_screen_return',
                observed_metric={'screen': screen.kind, 'text': text[:60]},
                reason='将軍ステータス画面はAで進まずBで戻る')
        return [pad('b')]
    parent = screen.kind == 'battle_menu'
    if not parent and flow is None and not restored:
        return None
    if not parent and screen.kind not in {
            'unknown', 'text', 'battle_menu_pending', 'okunote_menu',
            'monster_menu', 'monster_menu_pending', 'egg_choice_menu', 'egg_battle_menu'}:
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
        if flow.get('plain_command'):
            # A normal attack is under way. Its fade/result frames are not a
            # new menu, so advance them with a bounded A instead of holding to
            # input_stalled (g1190 ch9 2026-11-15: one plain_command then hold).
            stage = flow.get('advance') or {}
            stage['n'] = int(stage.get('n') or 0) + 1
            flow['advance'] = stage
            if stage['n'] > 60:
                mem.pop('orphan_battle_menu', None)
                _record(mem, 'battle_menu_plain_advance_limit',
                        observed_metric={'screen': screen.kind, 'n': stage['n']},
                        reason='通常コマンド後のA進行が上限に達したためBで戻る')
                return [pad('b')]
            if stage['n'] % 2 == 1:
                _record(mem, 'battle_menu_plain_advance',
                        observed_metric={'screen': screen.kind, 'n': stage['n']},
                        reason='通常コマンド選択後の戦闘アニメーション/結果画面を有界のAで進める')
                return [pad('a')]
        return []  # a fade or an irreversible list is not another parent B
    words = {word for _, _, word in _options(screen)}
    if ('こうげき' in words and 'たいきゃく' in words
            and not words.intersection({'おくのて', 'たまごをつかう', 'きりふだ'})):
        # A plain こうげき/たいきゃく list is a normal combat command menu,
        # not an unreadable gray/okunote parent. Choosing こうげき proceeds the
        # battle; holding it stalled the run (g1190 ch9 2026-11-15, ねばねば!!,
        # input_stalled). Egg/okunote rows still take the bounded close path.
        flow['plain_command'] = True
        _record(mem, 'battle_menu_plain_command',
                observed_metric={'cursor_y': screen.menu_cursor, 'words': sorted(words)},
                reason='将軍とHPは読めないが通常コマンドだけの戦闘メニューのため、保留せずこうげきで戦闘を進める')
        return [pad('a')]
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
    if (parent and not flow.get('command_pressed')
            and words.intersection({'きりふだ', 'こうげき'})):
        # B does not close this box: after the bounded closes, one confirm on
        # the observed top command (きりふだ/こうげき) is the only way forward.
        # g1190 ch9 2026-11-16 held this same menu 101 observations at a time.
        flow['command_pressed'] = True
        _record(mem, 'battle_menu_orphan_command',
                observed_metric={'words': sorted(words), 'cursor_y': screen.menu_cursor},
                reason='閉じない実戦闘メニューの先頭コマンドを1回だけ確定して戦闘を進める')
        return [pad('a')]
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


MENU_MOVE_BATCH_MAX = 6        # the most option rows batched into one no-hand menu move
MENU_MOVE_RELEASE_MS = 60      # released interval so each batched press reads as a distinct tap


def _menu_move_batch(screen, label, move):
    """Expand a vertical no-hand menu move into a bounded press run.

    Each command-box row is one cursor step, so when the target row sits
    several readable rows away the whole run can be sent in one observation
    instead of re-reading between single presses (a row takes ~500 ms each
    under ``menu_navigation_feedback``). Overshooting is harmless: callers
    only confirm on ``'here'``, so a too-far cursor simply steps back on the
    next read. Only the contiguous no-hand column menus are batched — a
    hand-cursor grid can move horizontally and is left to single presses.
    """
    buttons = move.get('buttons') if isinstance(move, dict) else None
    direction = buttons[0] if isinstance(buttons, list) and buttons else None
    if direction not in ('up', 'down') or getattr(screen, 'hand', None):
        return move
    cur = getattr(screen, 'menu_cursor', None)
    rows = sorted({line.y for line in getattr(screen, 'menu_rows', ()) or ()})
    if type(cur) is not int or cur not in rows:
        return move
    target_ys = [y for x, y, w in _options(screen) if w == label]
    if not target_ys:
        return move
    target_y = min(target_ys, key=lambda y: abs(y - cur))
    if target_y not in rows:
        return move
    dist = rows.index(target_y) - rows.index(cur)
    count = dist if direction == 'down' else -dist
    if count <= 1:
        return move
    count = min(count, MENU_MOVE_BATCH_MAX)
    actions = [move]
    for _ in range(count - 1):
        actions.extend([{'type': 'wait', 'ms': MENU_MOVE_RELEASE_MS}, pad(direction)])
    return actions


def _emit_moves(move):
    """Wrap a single move dict in a list; a built action list passes through."""
    if isinstance(move, list):
        return move
    return [move] if isinstance(move, dict) else []


def _command_rows_to(screen, label):
    """One step towards a command row read in ``menu_rows``.

    ``_battle_menu_to`` reads ``screen.lines``, but a defence summon box parses
    its command rows into ``menu_rows`` (disabled grey rows are excluded from
    the readable text). Move from the measured knight cursor so a grey row is
    never confirmed and no starting row is assumed. ``'here'`` means confirm,
    a pad dict means one step, ``None`` means an unreadable cursor/target.
    """
    targets = [line.y for line in (screen.menu_rows or [])
               if any(word == label for _, word in line.spans())]
    if screen.menu_cursor is None or not targets:
        return None
    y = min(targets, key=lambda row: abs(row - screen.menu_cursor))
    if y == screen.menu_cursor:
        return 'here'
    return pad('down' if y > screen.menu_cursor else 'up')


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
        return _menu_move_batch(screen, label, move)
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
            return _menu_move_batch(screen, label, move)
        mem.pop(key, None)
        _record(mem, 'battle_menu_nav_unaccepted', screen=screen.kind, choice=label,
                observed_metric={'direction': direction, 'retries': state['retries'],
                                 'frame_sha256': sha},
                reason='同一画面のまま方向入力が受け付けられないため、確定せずメニューを閉じる')
        return pad('b')
    state.update(sha=sha, waits=0)
    return _menu_move_batch(screen, label, move)


def _retreat_forbidden(mem, cur):
    """Owner 2026-11-09: in chapter 9 only the hero (どうし) may retreat.

    Every other general fights on with eggs, cards and おくのて; the retreat
    row is left alone so a chapter-9 sortie never ends by sending a general
    home mid-chapter.
    """
    if mem.get('chapter') != 9:
        return False
    ally = cur.get('ally')
    return isinstance(ally, str) and ally not in ('', NAME, 'しゅじんこう', 'しじんこう')


def _hero_retreat_menu(screen, mem, cur):
    # Owner 2026-11-23: retreat is banned uniformly.  It was chosen in a pinch
    # (often after cards/eggs ran out) and the menu then stalled the battle.
    # Fight to the end with こうげき instead.  Record once per battle.
    if not cur.get('retreat_banned_noted'):
        cur['retreat_banned_noted'] = True
        _record(mem, 'battle_retreat_banned', **_battle_labels(cur),
                general=cur.get('ally'), enemy=cur.get('enemy'),
                castle=cur.get('castle'), side=cur.get('side'),
                reason='退却は停滞の原因になるため一律禁止し、切り札・卵を使い切った後もそのまま戦い切る')
    return None


RESCUE_RECLOSE_WAIT = 3     # observations a spent rescue menu may stay open
RESCUE_RECLOSE_LIMIT = 6    # extra closing Bs per battle


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
    stale = _stale_army_melee_hold(mem, cur) if 'たまごをつかう' in labels else None
    if (stale is not None and type(rescue.get('stale_conserved', 0)) is int
            and rescue.get('stale_conserved', 0) < 2):
        # The live panel is covered but the last panel was healthy and clearly
        # ahead. Return to melee and re-read the panel instead of spending an
        # egg on an unread frame; bounded to two holds per battle.
        rescue['stale_conserved'] = rescue.get('stale_conserved', 0) + 1
        rescue.update(conserving_melee=True, conserved_labels=sorted(labels), exhausted=False)
        _record(mem, 'battle_egg_conserve', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'melee_balance': _melee_balance(cur),
                                 'conservation': stale},
                reason='実パネルが未読でも最後の観測でHP・戦闘力・兵数が白兵優位のため卵を温存して戻る')
        return [pad('b')]
    rescue.pop('conserving_melee', None)
    if (critical and (egg_ready or (safe_card and 'きりふだ' in labels))
            and not rescue.get('critical_menu_recheck')
            and (rescue['menu_ticks'] > 12 or rescue.get('exhausted'))):
        rescue.update(critical_menu_recheck=True, exhausted=False, menu_ticks=1)
    if rescue['menu_ticks'] > 12 or rescue.get('exhausted'):
        rescue['exhausted'] = True
        # One B hands the spent rescue back; repeating it every observation
        # left the battle box open forever (g1163 2026-11-10 19:3x-20:0x: 414
        # identical [b] plans with no recorded reason).
        if not rescue.get('exhausted_closed'):
            rescue['exhausted_closed'] = True
            _record(mem, 'battle_menu_rescue_exhausted', **_battle_labels(cur),
                    observed_metric={'menu_ticks': rescue.get('menu_ticks'),
                                     'labels': sorted(labels)},
                    reason='救済メニューの観測予算を使い切ったため一度だけ戻り、以後は通常のコマンド判定に委ねる')
            return [pad('b')]
        # The single B can be swallowed while the window draws, or the menu
        # is reopened by another owner; a spent menu left open pauses the
        # battle until the 101-observation fallback (g1190 2026-11-16 ch9).
        # Close again after a few observations, bounded per battle.
        waits = rescue['exhausted_waits'] = int(rescue.get('exhausted_waits') or 0) + 1
        closes = int(rescue.get('exhausted_recloses') or 0)
        if waits >= RESCUE_RECLOSE_WAIT and closes < RESCUE_RECLOSE_LIMIT:
            rescue['exhausted_waits'] = 0
            rescue['exhausted_recloses'] = closes + 1
            _record(mem, 'battle_menu_rescue_reclose', **_battle_labels(cur),
                    observed_metric={'recloses': closes + 1, 'labels': sorted(labels)},
                    reason='使い切った救済メニューが開いたまま戦闘が止まっているため、Bで閉じて白兵へ戻す')
            return [pad('b')]
        return None
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
    # 携行が出撃確定で読めているなら、実メニューを開いて同じ一覧を再確認する
    # 必要はない (#4431)。確定携行−確定使用の差分が空、または残りの全てが
    # 選択ゲートを通らない (一覧実読みが必要な制御札を除く) なら、開いても
    # 使える札は無いとメモリで確定できる。確定できない出撃や未読の実一覧は
    # 従来通りメニューを開いて確認する。
    if ('きりふだ' in labels and not rescue.get('cards_exhausted')
            and not rescue.get('listed_cards')):
        confirmed_kit = _confirmed_carried_kit(mem, cur)
        if confirmed_kit is not None and all(
                not _card_damage_gate(mem, cur, c, listed_cards=confirmed_kit)['allowed']
                and not _card_damage_gate(mem, cur, c, listed_cards=confirmed_kit)['needs_live_list']
                for c in confirmed_kit):
            rescue['cards_exhausted'] = True
            _record(mem, 'battle_card_menu_skipped_confirmed_kit', **_battle_labels(cur),
                    observed_metric={'confirmed_kit': list(confirmed_kit),
                                     'melee_balance': _melee_balance(cur)},
                    reason='出撃確定の携行札と確定使用の差分から使える切り札が無いと分かるため、携行確認のためだけの実メニュー開放を省く')
    if (critical or _dying_with_egg(mem, cur) or rescue.get('egg_priority')) and egg_ready and not safe_card:
        label = 'たまごをつかう'
    elif ('きりふだ' in labels and (safe_card or not rescue.get('cards_exhausted'))
            and rescue['cards_checked'] < 3) or final_read:
        label = 'きりふだ'
    elif ('たまごをつかう' in labels and not rescue['egg_attempted']
          and not _own_egg_empty(mem, cur)):
        label = 'たまごをつかう'
    elif ('たまごをつかう' in labels and critical
          and cur.get('side') == 'defense'
          and cur.get('castle') in chart.castles(mem.get('chapter') or 0)
          and not rescue.get('egg_break_bait') and _own_egg_empty(mem, cur)):
        # A spent-but-intact egg on a losing defender: pull the shell out so
        # the next hit breaks it, then おくのて becomes the only usable row
        # (チコリ died at 0 uses before the shell ever cracked). The row may
        # be greyed and inert — the bait flag bounds this to one try, after
        # which melee resumes and a landed hit still cracks the held shell.
        # Castle defense only: an attacker's egg bait has no defensive need.
        label = 'たまごをつかう'
        rescue['egg_break_bait'] = True
        _record(mem, 'battle_egg_break_bait', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'melee_balance': _melee_balance(cur)},
                resulting_event='selection_planned_not_yet_confirmed',
                reason='卵残0で使えず敗勢のため、卵を出して敵の一撃で殻を割らせ、おくのてを使える状態にする')
    else:
        rescue['exhausted'] = True
        _record(mem, 'battle_survival_unavailable', **_battle_labels(cur),
                observed_metric={'labels': sorted(labels), 'melee_balance': _melee_balance(cur)},
                reason='読み取れる未試行の救済手段がないため白兵へ戻る')
        return [pad('b')]
    move = _battle_menu_move(screen, mem, label)
    if move != 'here':
        return _emit_moves(move)  # no blind A on a missing cursor
    if label == 'きりふだ':
        if rescue['cards_checked'] >= 3:
            rescue['final_card_read'] = True
        rescue['cards_checked'] += 1
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    else:
        _egg_recheck(mem)
        rescue['egg_attempted'] = True
        # One selection is not a used summon: the box can fade before the row
        # is taken (g1163 2026-11-12 18:0x: the egg menu opened, the skill box
        # never became readable, and the general fell with the egg unused).
        # A bounded second selection gives that attempt a retry.
        rescue['egg_attempts'] = int(rescue.get('egg_attempts') or 0) + 1
        # Remember the HP at this attempt: a later HP loss is what licenses the
        # bounded extra retry (#5143/#5144 owner decision 2026-11-23).
        rescue['egg_attempt_hp'] = cur.get('ally_hp')
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
    # docich#4999 (owner 2026-11-22): while a retreat is pending, a listed
    # card whose earlier use was never confirmed is most likely still
    # carried — an unchanged count proves nothing either way. It gets
    # exactly one more selection (cards_spend_retry) before the retreat
    # proceeds instead of staying on the audit's remaining list. The same
    # two-reading settle applies: a flickering list is not a re-pick.
    retry = []
    if uncertain and (egg_bait.retreat_due(mem, cur) or _hero_retreat_needed(cur)):
        attempts = _card_spend_attempts(cur)
        remaining = set(_retreat_remaining_cards(cur))
        retry = [c for c in uncertain if c in remaining
                 and attempts.get(c, 0) < CARD_SPEND_ATTEMPT_LIMIT]
        if retry:
            uncertain = [c for c in uncertain if c not in retry]
    rescue['cards_uncertain'] = bool(uncertain)
    if (not candidates and (uncertain or retry)
            and flow['count_readings'] < 2 and flow['list_ticks'] <= 10):
        return []
    if not candidates and retry:
        for card in retry:
            _note_card_spend_attempt(cur, card)
        candidates = retry
    if not candidates:
        if not names and flow['list_ticks'] <= 3:
            return []
        rescue['cards_exhausted'] = True
        cur.pop('card_damage_list_blocked', None)  # absence/uncertain copies, not damage refusal
        cur['card_flow'] = None
        if flow['list_ticks'] > 10 and counts:
            rescue['cards_uncertain'] = True
        if uncertain:
            # docich#5198: a listed card that was attempted but whose count
            # never dropped is the same fail-closed "selected, consumption
            # unconfirmed" as _card_use_unclassified. Book it on the same
            # ledger so battle_result's metric — and the audit's
            # unconfirmed-only downgrade — can see it. kit_spent stays
            # untouched: the unchanged live count says the card is most
            # likely still carried, so it remains plannable for this sortie.
            unclassified = cur.setdefault('cards_unclassified', [])
            unclassified += [card for card in uncertain if card not in unclassified]
            cur['card_consumption_complete'] = False
            _record(mem, 'battle_card_remaining_unconfirmed', **_battle_labels(cur),
                    observed_metric={'listed_counts': counts,
                                     'counts_at_selection': dict(previous),
                                     'cards_unclassified': list(unclassified)},
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
    move = _menu_move_batch(screen, card, _battle_menu_to(screen, card))
    if move != 'here':
        if flow['list_ticks'] > CARD_LIST_CURSOR_LIMIT:
            # A final current cursor may select an eligible card, as in the
            # chart list. Missing/off-row cursors get no extra navigation.
            rescue['cards_exhausted'] = True
            rescue['cards_uncertain'] = bool(counts)
            cur.pop('card_damage_list_blocked', None)
            cur['card_flow'] = None
            return [pad('b')]
        return _emit_moves(move)
    gate = _card_damage_gate(mem, cur, card, listed_cards=names)
    if not gate['allowed']:
        _card_gate_refused(mem, cur, card, gate, listed_cards=names)
        return _card_gate_close(mem, cur, names)
    flow.update(card=card, stage='announce', selection_planned=True)
    # docich#4977: the row-disappearance evidence needs the list seen at
    # selection. The HP/soldier baselines are deliberately NOT snapshotted
    # here: a selection-time reading would attribute melee-caused drops
    # during the announce wait to the card (a fake receipt — the control
    # melee tests forbid it). Those evidences stay limited to flows whose
    # baseline was recorded when the battle menu opened.
    flow['names_at_select'] = list(names)
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

# The skill box settles within about a second of sliding or panning in. A box
# still half drawn after this many observations goes back to the ordinary
# default rather than holding the battle.
MONSTER_MENU_PENDING_MAX = 8


def _unchosen_turn_candidate(screen, mem):
    """Our summon's box, seen moving while we have not chosen this turn."""
    if mem.get('monster_own_choice'):
        return              # the box leaving after our own choice
    panel = {row.side: row for row in screen.egg_rows if row.side}
    ally, enemy = panel.get('ally'), panel.get('enemy')
    skills = [skill.replace(' ', '') for skill in reference.MONSTER_SKILLS.get(ally.name, ())] if ally else []
    words = [word for line in screen.lines if line.y >= 176
             for x, word in line.spans() if x >= 176 and len(word) >= 2]
    if not any(skill.startswith(word) for skill in skills for word in words):
        return              # not our monster's box (or its owner unread)
    mem['monster_unchosen'] = {'ally': ally.name, 'enemy': enemy.name if enemy else None,
                               'enemy_hp': enemy.hp if enemy else None}


def count_unchosen_monster_turn(mem, *, sent_frame=None):
    """Count a turn our summon spent on its top skill without our choice.

    The skill box can take a press that left before it appeared: an A decided
    on a dialogue frame arrived as the box slid in (g1075 18:27:47, 18:43:30)
    and confirmed the top row. The cursor starts every turn on the top row
    (ラビットサタン and ガートルード needed a down for skill 2 on each turn),
    so that turn spent skill 1. Count it so the stall switch still comes after
    MONSTER_STALL_TURNS uses of a skill that never moves the enemy HP.
    """
    seen = mem.pop('monster_unchosen', None)
    if not isinstance(seen, dict) or not seen.get('ally'):
        return
    stall = mem.get('monster_stall')
    enemy, enemy_hp = seen.get('enemy'), seen.get('enemy_hp')
    if isinstance(stall, dict) and isinstance(stall.get('scope'), list) and stall['scope'][:1] == [seen['ally']]:
        enemy = enemy or stall['scope'][1]
        enemy_hp = enemy_hp if type(enemy_hp) is int else stall.get('enemy_hp')
    if not enemy or type(enemy_hp) is not int:
        return
    scope = [seen['ally'], enemy]
    if not isinstance(stall, dict) or stall.get('scope') != scope or stall.get('enemy_hp') != enemy_hp:
        stall = mem['monster_stall'] = {'scope': scope, 'enemy_hp': enemy_hp, 'turns': {}}
    turns = stall.setdefault('turns', {})
    turns['skill1'] = turns.get('skill1', 0) + 1
    if seen['ally'] == 'あけまつ':
        # g1075 19:24:41: a stray A sent ごあいさつ and the next own turn sent
        # it again; a second exchange undoes the first.
        akematsu_command_selected(mem, SimpleNamespace(name=seen['ally']), SimpleNamespace(name=enemy),
                                  'skill1', cursor_confirmed=False)
    observed = {'ally': seen['ally'], 'enemy': enemy, 'enemy_hp': enemy_hp, 'turns': dict(turns)}
    if sent_frame:
        observed['sent_frame_sha256'] = sent_frame
    _record(mem, 'monster_turn_unchosen', observed_metric=observed,
            reason=('会話画面で決めたAが送信時には技メニューに届いていたため、最上段の1技目を使ったターンとして数える'
                    if sent_frame else
                    '技メニューが出たのにこちらが選ばないまま次の画面へ進んだため、最上段の1技目を使ったターンとして数える'))


def count_overtaken_monster_turn(screen, mem, sent_frame):
    """Count the turn an A decided on a dialogue page spent on our summon's box.

    g1075 18:58:48.87: the A decided on 「ウッドボールは すばやく よけた!」 was
    sent after the corner had captured ガートルード's box sliding in and chose
    であい; the agent's next frame was already the enemy's menu, so the box was
    never in a decision. The sender journal names the frame each input reached
    (bot.py hands over its snapshot), and that frame shows whose box took it.
    """
    mem.pop('monster_own_choice', None)     # this A was decided on a dialogue page
    _unchosen_turn_candidate(screen, mem)
    if mem.get('monster_unchosen'):
        count_unchosen_monster_turn(mem, sent_frame=sent_frame)
        mem['monster_own_choice'] = True    # spent: the same box leaving is not another turn


def monster_menu_pending_step(screen, mem):
    """Hold the confirm until a summon battle's box settles; its own step decides."""
    _summon_scene(mem)
    _unchosen_turn_candidate(screen, mem)
    ticks = mem['monster_menu_pending_ticks'] = mem.get('monster_menu_pending_ticks', 0) + 1
    if ticks == 1:
        _record(mem, 'monster_menu_drawing',
                reason='召喚戦の技・コマンド欄が表示途中のため、既定のAで最上段を確定せず表示を待つ')
    if ticks <= MONSTER_MENU_PENDING_MAX:
        return []
    mem.pop('monster_menu_pending_ticks', None)
    _record(mem, 'monster_menu_incomplete', observed_metric={'held': ticks - 1},
            reason='召喚獣の技メニューが観測上限まで表示途中のままのため、既定の入力に戻す')
    return None


def _summon_scene(mem):
    """Mark this battle as one with a summon on the field."""
    if isinstance(mem.get('battle'), dict):
        mem['battle']['summon_scene'] = True


# Summon dialogue advances by itself (19:57:04: the last page went to the box
# with no A). A page still there after this many looks gets its A.
SUMMON_DIALOGUE_LOOKS = 4


def summon_dialogue_pace(screen, mem, phase, kind, actions, digest):
    """Send a summon battle's dialogue A only when a page has stayed for a while.

    g1075 2026-10-29: every stray A that confirmed a box's top row in a summon
    battle was decided on the first look at the page before that box (18:43:30,
    18:58:48, 19:07:16, 19:22:38, 19:24:40, 19:25:59, 19:26:11). Those last pages
    stayed 0.3-0.7 s, and the A sent about 0.3 s after the look reached the
    skill box instead: the top skill ran past the two-turn switch, and
    あけまつ greeted twice. A page that is still there on the next look gets
    its A; one that leaves by itself never sends a press into the next box.
    Two looks were not enough (v314): at 19:57:14.20 and 20:14:43.52 the A sent
    on the second look at the last page was followed 0.2 s later by the skill
    box, and the turn went on without our choice. The box takes a press that
    meets the page's own advance, so the page is left to advance by itself.
    An egg monster's entrance page starts the scene: at 19:53:46 the bot's own
    たまごをつかう brought あけまつ in, and its first box took the A decided on
    the page before it because no box had been seen yet.
    """
    battle = mem.get('battle')
    if (isinstance(battle, dict) and kind == 'text' and screen.text
            and 'エッグモンスター' in screen.text.replace(' ', '')):
        _summon_scene(mem)
    if (actions != [pad('a')] or phase != 'dialogue' or kind not in ('text', 'unknown')
            or not isinstance(battle, dict) or not battle.get('summon_scene')):
        mem.pop('summon_dialogue', None)
        return actions
    key = f'text:{screen.text}' if kind == 'text' and screen.text else digest
    seen = mem.get('summon_dialogue')
    looks = (seen['looks'] + 1 if isinstance(seen, dict) and seen.get('key') == key
             and type(seen.get('looks')) is int else 1)
    if looks >= SUMMON_DIALOGUE_LOOKS:
        mem.pop('summon_dialogue', None)
        return actions
    mem['summon_dialogue'] = {'key': key, 'looks': looks}
    if not battle.get('summon_dialogue_paced'):
        battle['summon_dialogue_paced'] = True
        _record(mem, 'summon_dialogue_paced',
                reason='召喚戦の会話は自動で進むため、同じページが続くときだけAを送り、最後のページへのAが次の技・コマンド欄を確定しないようにする')
    return []


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


CARD_ROW_WALK_TOPDOWN = 2    # ups that reach the command box's top row
CARD_ROW_WALK_AFTER = 2      # held observations before the deterministic walk
CARD_MENU_TICK_LIMIT = 8     # same bound as before: the walk finishes by tick 5

def _card_usable_in_battle(mem, cur, card):
    """Match the battle-use gate so forbidden boss cards are not audit misses."""
    return card not in reference.BOSS_ONLY_CARDS or _boss_tactics_allowed(mem, cur)


def _carried_card_left(mem, cur):
    """A card the general actually carries and has not spent yet.

    Castle stock alone is not "holding" a card for the miss audit; only the
    planned sortie kit or a confirmed carried kit counts.
    """
    attempted = {*(cur.get('cards_selected') or []), *(cur.get('cards_used') or []),
                 *(cur.get('cards_unclassified') or []), *(cur.get('cards_missing') or [])}
    planned = [c for c in (cur.get('planned_cards') or [])
               if isinstance(c, str) and c not in attempted
               and _card_usable_in_battle(mem, cur, c)]
    if planned:
        return planned[0]
    kit = _confirmed_carried_kit(mem, cur) or []
    return next((c for c in kit if isinstance(c, str) and c not in attempted
                 and _card_usable_in_battle(mem, cur, c)), None)


def _card_row_walk(flow, screen, ticks):
    '''docich#4673: reach きりふだ without a readable command-box cursor.

    The measured box has three rows (たまごをつかう / きりふだ / たいきゃく,
    16 px apart). When the cursor is unreadable the flow used to hold until
    the tick limit and close with B, so a usable card was never tried. Up
    twice reaches the top row from anywhere in this box; one down is
    きりふだ, and A opens the list. Bounded; None when the box or tick is
    not right for it.
    '''
    text = screen.text or ''
    measured = ('たまごをつかう' in text and 'きりふだ' in text
                and 'たいきゃく' in text)
    if (flow.get('stage') != 'menu' or not measured
            or ticks < CARD_ROW_WALK_AFTER):
        return None
    walk = int(flow.get('card_row_walk') or 0)
    flow['card_row_walk'] = walk + 1
    if walk < CARD_ROW_WALK_TOPDOWN:
        return [pad('up')]
    if walk == CARD_ROW_WALK_TOPDOWN:
        return [pad('down')]
    flow['stage'] = 'list'
    return [pad('a')]


def battle_menu_step(screen: Screen, mem):
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    if screen.kind != 'battle_menu':
        return []
    _labels = {word for _, _, word in _options(screen)}
    # A measured five-item scroll owns this box even before the B fallback:
    # its きりふだ/たいきゃく-only bottom page is not a modal overlay.  An
    # active card flow still owns the menu: it deliberately walks to きりふだ.
    _scroll_owned = not flow and _human_scroll_owns(
        screen, _labels, cur, chapter=mem.get('chapter'))
    if (_screen_egg_empty(screen, mem, cur) and not _own_egg_empty(mem, cur)
            and not _scroll_owned):
        _record(mem, 'battle_egg_actor_unconfirmed',
                reason='実画面の本人は残数0のため、旧戦闘文脈で卵を選ばず戻る')
        return [pad('b')]
    cur['resource_menu_observed'] = True  # a B attempt alone never proves this
    cur.pop('command_open_pending', None)  # foreground receipt, not a planned B
    _card_battle_reading(screen, cur, mem)
    if _white_egg_available(screen):
        # Owner 2026-11-22: the white row is the live evidence; it overrides
        # a stale zero left by a paid recovery and any exhausted survival wait.
        ally = cur.get('ally') if isinstance(cur, dict) else None
        if isinstance(ally, str) and ally in (mem.get('egg_zero_owners') or []):
            mem['egg_zero_owners'] = [n for n in mem['egg_zero_owners'] if n != ally]
            _record(mem, 'battle_egg_white_available', **_battle_labels(cur),
                    observed_metric={'ally': ally, 'screen': screen.kind},
                    reason='白文字のたまごをつかうを実測したため、保存された卵0より画面を優先する')
        # Only override an already-bound attack decision; do not create a new
        # independent-menu decision here (that suppresses the normal record).
        if mem.get('indep_menu') and mem.get('indep_menu_action') == 'attack':
            mem['indep_menu_action'] = 'use_egg'
            mem['egg_action'] = 'use_egg'
            if isinstance(cur, dict) and isinstance(cur.get('independent'), dict):
                cur['independent']['action'] = 'use_egg'
    _plain_attack_box = ('こうげき' in _labels and 'たいきゃく' in _labels
                         and not _labels.intersection({'おくのて', 'たまごをつかう', 'きりふだ'}))
    if _scroll_owned:
        _begin_human_scroll(cur, screen)
        scroll = _human_scroll_step(screen, mem, cur, _labels)
        if scroll is not None:
            return scroll
    _sha = getattr(screen, 'frame_sha256', None)
    _recent = [d for d in (cur.get('menu_recent') or []) if isinstance(d, str)][:2]
    cur['menu_same'] = int(cur.get('menu_same') or 0) + 1 if _sha in _recent else 0
    if _sha is not None:
        cur['menu_recent'] = ([_sha] + [d for d in _recent if d != _sha])[:2]
    _modal_box = bool(getattr(screen, 'hidden_battle_commands', False)
                      or getattr(screen, 'disabled_item_box', False)
                      or getattr(screen, 'okunote_only_box', False))
    if (int(cur.get('menu_same') or 0) >= 10
            and _labels and 'たいきゃく' in _labels
            and not _labels.intersection({'こうげき', 'もうこうげき', 'たまごをつかう'})
            and (_modal_box or (screen.menu_cursor is None and not screen.menu_rows))):
        # A command box with no usable command and no readable rows/cursor is
        # a modal overlay: A on the dead きりふだ row does nothing. B closes
        # it and the melee resumes (owner 2026-11-16). A readable 5-item list
        # is never closed with B; it is scrolled up above (owner 2026-11-24).
        _record(mem, 'battle_menu_close', **_battle_labels(cur),
                observed_metric={'labels': sorted(_labels), 'modal_box': _modal_box,
                                 'cursor_y': screen.menu_cursor},
                reason='攻撃コマンドの無い戦闘メニューはBで閉じて白兵へ戻る')
        return [pad('b')]
    cur['own_egg_zero_observed'] = _own_egg_empty(mem, cur)
    cur['own_egg_positive_observed'] = _own_egg_positive(mem, cur)
    if (_one_hit_lethal(cur) or _hp1_resource_hold(cur)) and not _plain_attack_box:
        # A menu that is already open (or opened before the HP fell): close it
        # unused. battle_step opens none while the enemy is one push from death
        # or a non-boss enemy sits at HP1.
        if (cur.get('card_flow') or {}).get('stage') != 'announce':
            cur['card_flow'] = None
        if not cur.get('one_hit_menu_closed'):
            cur['one_hit_menu_closed'] = True
            hold = _hp1_resource_hold(cur) and not _one_hit_lethal(cur)
            _record(mem, 'battle_one_hit_menu_close', **_battle_labels(cur),
                    observed_metric={'enemy_hp': cur.get('enemy_hp'), 'ally_hp': cur.get('ally_hp'),
                                     'enemy_soldiers': cur.get('enemy_soldiers'),
                                     'non_boss_hp1_hold': hold},
                    reason=('ボス戦以外で相手HPが1のため、奥の手・卵・切り札を選ばずメニューを閉じて白兵へ戻る'
                            if hold else
                            '敵HPが一撃で倒せる値のため、奥の手・卵・切り札を選ばずメニューを閉じて白兵へ戻る'))
        return [pad('b')]
    if flow and flow.get('read_kit'):
        # #4456: reuse the command-menu navigation to reach きりふだ, then let
        # card_list_step read the names. No selection and no consumption.
        flow['command_menu_ticks'] = int(flow.get('command_menu_ticks') or 0) + 1
        walk = _card_row_walk(flow, screen, flow['command_menu_ticks'])
        if walk is not None:
            return walk
        if flow['command_menu_ticks'] > CARD_MENU_TICK_LIMIT:
            _defense_kit_read_unread(mem, cur, '切り札行へ到達できず上限')
            return [pad('b')]
        move = _battle_menu_move(screen, mem, 'きりふだ')
        if move != 'here':
            if isinstance(move, dict) and move.get('buttons') == ['b']:
                _defense_kit_read_unread(mem, cur, '切り札行へ動かないため一覧を開かない')
            return [move] if move else []
        flow['stage'] = 'list'
        return [pad('a')]
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
    if (screen.hidden_battle_commands or screen.has('おくのて')) and not (
            flow and 'きりふだ' in _labels):
        cur.pop('card_damage_list_blocked', None)
        res = okunote_step(screen, mem)
        if res is not None:
            return res
        # 照合不能な召喚戦メニューへのBが尽きたため、通常のコマンド判定へ進む
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
        walk = _card_row_walk(flow, screen, flow['command_menu_ticks'])
        if walk is not None:
            return walk
        if flow['command_menu_ticks'] > CARD_MENU_TICK_LIMIT:
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
            return _emit_moves(move)
        flow['stage'] = 'list'
        return [pad('a')]
    if not flow and cur.get('egg_battle') and 'きりふだ' in screen.text:
        rare = _available_rare_tactic(mem, cur)
        # docich#5600 (owner 2026-11-25: 資源は使い切る): a carried, usable
        # card is worth opening even without a rare tactic. Leaving it unopened
        # while attacking melee is the card_miss signal the audit files.
        carried = _carried_card_left(mem, cur) if rare is None else None
        move = _battle_menu_move(screen, mem, 'きりふだ') if (rare or carried) else None
        if move == []:
            return []  # an identical frame is still not the card row
        if isinstance(move, dict) and move.get('buttons') == ['b']:
            # The box did not accept the direction: close it without opening the
            # card flow. `_battle_menu_to` never emits B, so this is the
            # bounded unaccepted close, not a step toward the row.
            return _emit_moves(move)
        if move is not None:
            if rare is not None:
                card, tid = 'キャトルミュー', rare['tactic_id']
                cur.setdefault('tactics_done', []).append(tid)
            else:
                card, tid = carried, None
            cur['card_flow'] = {'card': card, 'stage': 'list' if move == 'here' else 'down',
                                'tactic_id': tid, 'enemy_hp_at_open': cur.get('enemy_hp'),
                                'enemy_soldiers_at_open': cur.get('enemy_soldiers')}
            _record(mem, 'battle_card', **_battle_labels(cur), card=card,
                    observed_metric={'enemy_hp': cur.get('enemy_hp'), 'egg_battle': True},
                    reason=('実携行のキャトルミューをボスへ使用し90ダメージを狙う'
                            if card == 'キャトルミュー' and cur.get('enemy') in chart.BOSSES.values() else
                            '実携行のキャトルミューを敵EMへ使用し224ダメージと石化を狙う'
                            if card == 'キャトルミュー' else
                            '召喚獣戦で実際に携行している切り札を開いて使う'))
            return [pad('a')] if move == 'here' else _emit_moves(move)
    if cur.get('survival') or _survival_needed(cur):
        res = _survival_menu(screen, mem, cur)
        if res is not None or not _plain_attack_box:
            return res
    if _plain_attack_box:
        # 召喚獣戦の状態異常（「ないている!!」「ネバネバ!!」等）で表示される
        # こうげき / たいきゃく のみの箱は B で閉じられない (g1190 ch6 60-4
        # ペコリーノ vs キャンドロー)。退却・救済の判定後に残った場合はこうげきで進める。
        move = _battle_menu_move(screen, mem, 'こうげき')
        if move == 'here':
            _record(mem, 'battle_menu_plain_command', **_battle_labels(cur),
                    observed_metric={'cursor_y': screen.menu_cursor, 'words': sorted(_labels)},
                    reason='Bで閉じられないこうげき・たいきゃくのみの戦闘メニューのため、こうげきで戦闘を進める')
            return [pad('a')]
        if move is not None:
            return _emit_moves(move)
        _record(mem, 'battle_menu_plain_command', **_battle_labels(cur),
                observed_metric={'cursor_y': screen.menu_cursor, 'words': sorted(_labels)},
                reason='カーソル未読でもこうげき・たいきゃくのみの戦闘メニューのため、既定のこうげきをAで確定する')
        return [pad('a')]
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
            return _emit_moves(move)
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
    from . import okunote
    if okunote.candidate_chosen(mem, cur):
        # g1075 14:35:52: リューキシもどき was chosen and its effect dialogue
        # filled these frames (ビシソワーズ 13 -> 0, a win). The inputs stay;
        # only the record no longer reports an unread menu.
        guard.update(exhausted=True, stage='unavailable', close_pending=True)
        _record(mem, 'battle_okunote_effect_text_limit', **_battle_labels(cur),
                reason='奥の手の候補を選んだ後の演出テキストが観測上限まで続いたためBで送る。メニュー未読ではない')
        return [pad('b')]
    # #2953 実例A: an unread menu is not a refusal of the rescue. Close the
    # unread box and reopen it through the same bounded opening budget.
    if guard.get('opens', 0) < DEFENDER_EGG_OPEN_LIMIT:
        guard.update(stage=None, unread_ticks=0, close_pending=True)
        _record(mem, 'battle_okunote_egg_menu_retry', **_battle_labels(cur),
                observed_metric={'open_attempt': guard.get('opens', 0), 'limit': DEFENDER_EGG_OPEN_LIMIT},
                reason='召喚前の奥の手メニューを読み取れなかったため、Bで閉じて同じ開く回数の範囲内で再度開く')
        return [pad('b')]
    guard.update(exhausted=True, stage='unavailable', close_pending=True)
    _record(mem, 'battle_okunote_egg_menu_unread', **_battle_labels(cur),
            observed_metric={'open_attempt': guard.get('opens', 0), 'limit': DEFENDER_EGG_OPEN_LIMIT},
            reason='召喚前の奥の手メニューを読み取れない観測が上限に達し、再度開く回数も使い切ったため、未知の行を確定せず戻る')
    return [pad('b')]


# Bounded B attempts to close an unbindable summon-fight command menu. In that
# menu B does nothing (g1097 22:09, same frame 40+ observations); the fallback
# hands the screen back to the ordinary menu logic instead of looping.
OKUNOTE_SUMMON_UNCONFIRMED_LIMIT = 3
# A committed rescue may continue scrolling past the 16-tick parent budget,
# but never forever: past this total the おくのて row is treated as unread.
OKUNOTE_COMMITTED_TICKS_LIMIT = 48


OKUNOTE_ONLY_BOX_DOWNS = 4      # 有効行へカーソルを下げる上限 (実測は2回)
OKUNOTE_ONLY_BOX_CONFIRMS = 2   # 同一画面が残るときの再確定上限


def _okunote_only_box_step(screen, mem, cur):
    """きりふだ・たいきゃくが灰色で おくのて だけが生きた箱を有界で確定する。

    g1163 2026-11-13 09:1x の実測: カーソルは灰色の きりふだ 行 (y176) にあり、
    下方向は受け付けるが B はこの箱を閉じない。この箱の形が
    ``hidden_battle_commands`` (3行すべて灰色) にも ``disabled_item_box``
    (たまご+きりふだ の2行) にも当たらないため、通常の戦闘メニューとして B を
    送り続け、同じ画面のまま5時間 戦闘が進まなかった。

    実画面で証明できるのは「きりふだ/たいきゃく は使用不能」「おくのて だけが
    生きている」の2点だけなので、選ぶのは常に おくのて 行で、確定は1回ずつ
    数える。下げられない・確定が効かない場合は推測で別の行を押さず None を返し、
    呼び出し元の通常判定へ戻す。
    """
    sha = getattr(screen, 'frame_sha256', None)
    flow = cur.get('okunote_only_box_step')
    if (not isinstance(flow, dict) or type(flow.get('downs')) is not int
            or type(flow.get('confirms')) is not int):
        flow = cur['okunote_only_box_step'] = {'downs': 0, 'confirms': 0, 'sha': None,
                                               'waits': 0}
    elif sha is not None and flow.get('sha') == sha and flow.get('confirms'):
        # 同じ画面が戻ってきた: 確定が届いていない可能性だけを数え、無記録の
        # 連打はしない (Bの無限ループと同じ失敗を繰り返さない)。
        waits = flow.get('waits') if type(flow.get('waits')) is int else 0
        if waits < 2:
            flow['waits'] = waits + 1
            return []
        flow['waits'] = 0
        if flow['confirms'] < OKUNOTE_ONLY_BOX_CONFIRMS:
            flow['confirms'] += 1
            _record(mem, 'battle_okunote_only_box_reconfirm', **_battle_labels(cur),
                    observed_metric={'confirms': flow['confirms'], 'cursor_y': screen.menu_cursor},
                    reason='奥の手行の確定が画面に反映されないため、カーソル確認済みのAを有界で再送する')
            return [pad('a')]
        return None
    if screen.menu_cursor == 208:
        flow.update(confirms=flow['confirms'] + 1, sha=sha, waits=0)
        _record(mem, 'battle_okunote_only_box_confirm', **_battle_labels(cur),
                observed_metric={'cursor_y': screen.menu_cursor, 'downs': flow['downs'],
                                 'self_damage_upper': OKUNOTE_MAX_SELF_DAMAGE},
                resulting_event='irreversible_risk_accepted',
                reason='カードも退却も使用不能で奥の手だけが有効な戦闘メニューを、カーソル確認済みで1回確定し戦闘を進める')
        return [pad('a')]
    if flow['downs'] >= OKUNOTE_ONLY_BOX_DOWNS:
        _record(mem, 'battle_okunote_only_box_unread', **_battle_labels(cur),
                observed_metric={'downs': flow['downs'], 'cursor_y': screen.menu_cursor},
                reason='有効な奥の手行へカーソルを下げられないため確定せず通常のコマンド判定へ戻す')
        flow['downs'] = 0
        return None
    flow['downs'] += 1
    flow['sha'] = sha
    if flow['downs'] == 1:
        _record(mem, 'battle_okunote_only_box_down', **_battle_labels(cur),
                observed_metric={'cursor_y': screen.menu_cursor,
                                 'limit': OKUNOTE_ONLY_BOX_DOWNS},
                reason='Bでは閉じない箱のため、実画面で唯一有効な奥の手行へカーソルを下げる')
    return [pad('down')]


def _safer_rescue_available(mem, cur):
    """A measured egg or carried card the battle could still spend.

    The command menu always draws たまごをつかう/きりふだ/たいきゃく, so their
    labels cannot prove a usable resource: 2026-11-24 13:10 JST the decline
    claimed 使用可能な卵/切り札 while パプリカ had no egg (has_egg false) and
    an empty carried list, and the general fell in melee. Only the battle's
    own measured plan (``cur['egg_plan']``) counts here.
    """
    plan = cur.get('egg_plan') if isinstance(cur.get('egg_plan'), dict) else {}
    profile = plan.get('ally_profile') if isinstance(plan.get('ally_profile'), dict) else {}
    if profile.get('has_egg') is True and not _own_egg_empty(mem, cur):
        return True
    return any(isinstance(row, dict) and row.get('usable') is True
               for row in (plan.get('cards') or ()))


def _okunote_risk_protected(mem, cur, hp, ally_full, enemy_full):
    """A low HP battle still has a safer rescue than the irreversible lottery.

    The original gate treated the visible きりふだ/たまご rows as proof of a
    usable card or egg, which they never were (the rows are always drawn).
    It also required a confirmed ``side == 'defense'``; battles that interrupt
    a sortie have ``side`` and ``castle`` unknown and could never rescue a
    losing general at all (2026-11-24 13:1x-13:2x: パプリカ/デュオニソス/
    キャンディー declined at HP 34/21/8/22 and were lost). A confirmed attack
    keeps the old policy; an unclassified battle may now open the lottery when
    no measured egg/card is left.
    """
    hero = cur.get('ally') in (NAME, 'しじんこう', 'しゅじんこう')
    return bool(
        cur.get('ally') in (None, 'だいじん')
        or (hero and type(hp) is int and hp <= OKUNOTE_MAX_SELF_DAMAGE)
        or cur.get('side') == 'attack'
        or (cur.get('side') == 'defense'
            and cur.get('castle') not in chart.castles(mem.get('chapter') or 0))
        or cur.get('enemy') in chart.BOSSES.values()
        or cur.get('planned_cards') or cur.get('card_flow')
        or cur.get('egg_battle') or mem.get('egg_battle')
        or cur.get('card_summon_observed')
        or cur.get('okunote_last_resort_recorded') or cur.get('okunote_last_resort_selected')
        or _safer_rescue_available(mem, cur)
        or type(hp) is not int or hp <= 0
        or type(cur.get('enemy_hp')) is not int or cur.get('enemy_hp') <= 0
        or ally_full is None or enemy_full is None
        or type(cur.get('start_ally_hp')) is not int or cur['start_ally_hp'] <= 0
        or type(cur.get('start_enemy_hp')) is not int or cur['start_enemy_hp'] <= 0
        or cur['start_ally_hp'] > ally_full or cur['start_enemy_hp'] > enemy_full
        or cur.get('enemy_hp') > cur['start_enemy_hp'] or hp > cur['start_ally_hp'])


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
            summon_panel = okunote.summon_monster_panel(screen, cur) if summon is None else None
            if cur.get('okunote_summon_exhausted'):
                # The bounded attempts for this battle are spent. B does not
                # close this menu (g1097), so hand the screen back once with a
                # single confirm on the highlighted row instead of pressing B
                # forever (g1163 2026-11-10 19:1x: no decision records for
                # minutes while the おくのて menu stayed open).
                if getattr(screen, 'okunote_only_box', False):
                    # g1163 2026-11-13 09:1x: きりふだ/たいきゃく が灰色で
                    # おくのて だけが生きた箱。先頭行は たまごをつかう ではなく
                    # 灰色の きりふだ なので、up/up/A は死んだ行を押すだけで
                    # 何も起きない。唯一有効な行へカーソルを下げて1回だけ確定する。
                    return _okunote_only_box_step(screen, mem, cur)
                if not cur.get('okunote_summon_exhausted_confirm'):
                    cur['okunote_summon_exhausted_confirm'] = True
                    _record(mem, 'battle_okunote_exhausted_confirm',
                            **_battle_labels(cur),
                            observed_metric={'cursor_y': screen.menu_cursor},
                            reason='照合不能のまま残った奥の手メニューを、先頭行（たまご）へ戻して確定し戦闘を進める')
                    # Two ups put the cursor on the first row (たまごをつかう);
                    # a blind A on the last row would pick たいきゃく (retreat).
                    return [pad('up'), pad('up'), pad('a')]
                return None
            if summon is None and summon_panel is None:
                if getattr(screen, 'okunote_only_box', False):
                    # 照合できないままBを有界回数送るより、実測した唯一の
                    # 有効行へカーソルを下げて確定する方が戦闘を進められる。
                    return _okunote_only_box_step(screen, mem, cur)
                # g1097 22:09: この将軍コマンドは「たいきゃく」の下に隠れた
                # おくのて行を持つ (hidden_battle_commands)。B は効かず、同じ
                # 画面が40観測以上続いた。照合不能でもおくのて行へ向かうため、
                # 既存のスクロールと同じく有界な down を送り、尽きたら通常の
                # メニュー処理へ委ねる (オーナー指摘 2026-11-02)。
                unconfirmed = int(cur.get('okunote_summon_unconfirmed') or 0)
                cur['okunote_summon_unconfirmed'] = unconfirmed + 1
                _record(mem, 'battle_okunote_summon_unconfirmed', **_battle_labels(cur),
                        observed_metric={'attempts': unconfirmed + 1,
                                         'limit': OKUNOTE_SUMMON_UNCONFIRMED_LIMIT,
                                         'cursor_y': screen.menu_cursor},
                        reason='現在の本人HP・敵召喚獣・自軍コマンドを同じ戦闘に照合できず奥の手を開かない')
                if unconfirmed + 1 <= OKUNOTE_SUMMON_UNCONFIRMED_LIMIT:
                    if screen.hidden_battle_commands:
                        return [pad('down')]
                    return [pad('b')]
                cur['okunote_summon_exhausted'] = True
                _record(mem, 'battle_okunote_summon_unconfirmed_exhausted',
                        **_battle_labels(cur),
                        observed_metric={'attempts': unconfirmed + 1},
                        reason='照合不能な召喚戦メニューへの入力が有界回数を超えてもおくのて行を確認できないため、以後は通常のコマンド判定へ戻す')
                return None
            summon = summon or summon_panel
            cur.pop('okunote_summon_unconfirmed', None)
            if isinstance(summon, dict) and type(summon.get('enemy_monster_hp')) is int:
                cur['summon_enemy_hp'] = summon['enemy_monster_hp']
            _egg_general_reading(screen, mem, cur)
            hp = summon['ally_hp'] if isinstance(summon, dict) else hp
            # Owner 2026-11-04: 相手のHPが一撃で倒せる残りなら奥の手は不要
            # （召喚獣戦でも同じ）。通常のこうげきへ戻す。
            if 0 < summon['enemy_monster_hp'] <= EGG_WALL_DAMAGE:
                _record(mem, 'battle_okunote_skip_one_hit', **_battle_labels(cur),
                        observed_metric={'enemy_monster_hp': summon['enemy_monster_hp'],
                                         'wall_damage': EGG_WALL_DAMAGE},
                        reason='敵の召喚獣のHPが一撃で倒せる残りのため、おくのてを使わず通常攻撃へ戻す')
                return None
        elif _one_hit_lethal(cur) or _hp1_resource_hold(cur):
            # Owner 2026-11-04: 通常戦でも相手のHPが一撃で倒せる残りなら
            # 奥の手を開かない（既存の one_hit_lethal と同じ境界）。
            # Owner 2026-11-25: ボス戦以外で相手HP1なら資源を使わない.
            hold = _hp1_resource_hold(cur) and not _one_hit_lethal(cur)
            _record(mem, 'battle_okunote_skip_one_hit', **_battle_labels(cur),
                    observed_metric={'enemy_hp': cur.get('enemy_hp'),
                                     'enemy_soldiers': cur.get('enemy_soldiers'),
                                     'wall_damage': EGG_WALL_DAMAGE,
                                     'non_boss_hp1_hold': hold},
                    reason=('ボス戦以外で相手HPが1のため、おくのてを使わず通常攻撃へ戻す'
                            if hold else
                            '相手のHPが一撃で倒せる残りのため、おくのてを使わず通常攻撃へ戻す'))
            return None
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
        guard = cur.get('okunote_egg_preempt') or cur.get('okunote_summon_preempt') or {}
        if guard.get('selected'):
            # g1097 23:06: 親メニュー (きりふだ/たいきゃく/おくのて) が実は
            # 残ったまま — 前の A は届かず B は無効。この無記録 B が ~200 回
            # 続いた。おくのて行が見えるなら「もう一度A」は再抽選ではなく
            # 未受付の再送なので、有界でカーソルを合わせて送る。
            if 'おくのて' in labels:
                retries = int(guard.get('parent_retries') or 0)
                if retries < 4:
                    move = _menu_move_batch(screen, 'おくのて', _battle_menu_to(screen, 'おくのて'))
                    if move == 'here':
                        guard['parent_retries'] = retries + 1
                        _record(mem, 'battle_okunote_parent_resend', **_battle_labels(cur),
                                observed_metric={'retry': retries + 1, 'cursor_y': screen.menu_cursor},
                                reason='おくのて選択のAが届かず親メニューが残っているため、カーソル確認済みのAを有界で再送する')
                        return [pad('a')]
                    guard['parent_retries'] = retries + 1
                    if move is not None:
                        return _emit_moves(move)
                    return [pad('down')]
                _record(mem, 'battle_okunote_committed_unread', **_battle_labels(cur),
                        observed_metric={'parent_retries': retries},
                        reason='確定済み救済のおくのて行を有界回数再送してもメニューが残るため、以後は戻る')
                return None
            return [pad('b')]  # a repeated parent frame is not another lottery
        if guard and cur['okunote_only_observed']:
            # A real menu can arrive after the bounded B opening expired.
            # It authorizes a current cursor check, never another blind B loop.
            guard.update(exhausted=False, stage='menu', unread_ticks=0)
        egg_window = _defender_egg_window(mem, cur, menu_open=cur['okunote_only_observed'])
        egg_preempt = bool(egg_window and egg_window['due'])
        summon_window = _defender_summon_preempt(mem, cur)
        summon_preempt = bool(summon_window and summon_window['due'])
        if egg_window and not egg_preempt:
            rescue = _survival_state(mem, cur)
            rescue['exhausted'] = True
            # #2953 実例B: this latch waits for the enemy's summon. The summon
            # onset re-arms it once so the egg battle can judge the rescue again.
            rescue['egg_window_latch'] = True
            if not flow.get('egg_window_waited'):
                flow['egg_window_waited'] = True
                _record(mem, 'battle_okunote_egg_wait', **_battle_labels(cur),
                        observed_metric=egg_window,
                        reason='奥の手だけが有効だが召喚前の期限には未到達のため、白兵のHPと位置リスクを再観測する')
            return [pad('b')]
        if summon_preempt and not egg_preempt:
            guard = cur.setdefault('okunote_summon_preempt', {'opens': 0})
            guard.update(reason=summon_window['reason'], stage='menu')
            if not guard.get('recorded'):
                guard['recorded'] = True
                _record(mem, 'battle_okunote_summon_preempt', **_battle_labels(cur),
                        observed_metric={**summon_window, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                        resulting_event='irreversible_risk_accepted',
                        reason='敵の召喚獣が出ていて味方の卵・札が使えないため、開いている救済メニューで奥の手を選ぶ。自傷リスクは残る')
        if egg_preempt:
            guard = cur.setdefault('okunote_egg_preempt', {'opens': 0})
            guard.update(reason=egg_window['reason'], stage='menu')
            if not guard.get('recorded'):
                guard['recorded'] = True
                _record(mem, 'battle_okunote_egg_preempt', **_battle_labels(cur),
                        observed_metric={**egg_window, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                        resulting_event='irreversible_risk_accepted',
                        reason=('敵卵あり・味方卵と札は実メニューで使用不能・接触召喚の位置は未校正のため、開いている救済メニューで奥の手を選ぶ。自傷リスクは残る'
                                if egg_window['reason'] == 'menu_open_contact_unmeasured' else
                                '前の奥の手で敵将軍が残ったため、召喚より先に奥の手を引き直す。自傷リスクは残る'
                                if egg_window['reason'] == 'okunote_redraw' else
                                '敵卵あり・味方卵と札は実メニューで使用不能・召喚危険が目前の城防衛で奥の手を選ぶ。主人公も対象だが自傷リスクは残る'))
        last_resort = _okunote_last_resort_gate(
            cur, _defender_summon_last_resort(mem, cur, summon) if summon
            else _defender_last_resort(mem, cur), int(mem.get('tick') or 0))
        if cur.get('okunote_last_resort_failed') and not cur.get('okunote_last_resort_failed_recorded'):
            cur['okunote_last_resort_failed_recorded'] = True
            _record(mem, 'battle_okunote_last_resort_failed', **_battle_labels(cur),
                    observed_metric={'ally_hp': hp, 'enemy_hp': cur.get('enemy_hp'),
                                     'previous_enemy_hp': cur.get('okunote_last_resort_enemy_hp')},
                    reason='奥の手で敵召喚獣のHPが減らなかったため、繰り返さず救済メニューへ切り替える')
        if last_resort and not cur.get('okunote_last_resort_recorded'):
            cur['okunote_last_resort_recorded'] = True
            _record(mem, 'battle_okunote_last_resort', **_battle_labels(cur),
                    observed_metric={'ally_hp': hp, 'start_ally_hp': cur['start_ally_hp'],
                                     'enemy_hp': cur['enemy_hp'],
                                     'start_enemy_hp': cur['start_enemy_hp'],
                                     'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                    resulting_event='irreversible_risk_accepted',
                    reason='他の手段が使用不能で押し負ける一般守備将軍の最後の救済。自傷リスクは残る')
        if (type(hp) is not int or hp <= OKUNOTE_MAX_SELF_DAMAGE) and not (last_resort or egg_preempt or summon_preempt):
            rescue = _survival_state(mem, cur)
            # Re-arm exhausted on tide change.
            _rescue_rearm(mem, cur, {'owner': 'ally'})
            rescue = _survival_state(mem, cur)
            # Owner 2026-11-04: the parent rescue menu cannot show the
            # okunote candidates before おくのて is opened. Declining from
            # "no safe candidate" here was logically impossible; only the
            # candidate list (okunote_menu) is evidence. The rescue's egg/card
            # exhaustion is not a veto either. Open the lottery in a losing
            # pinch (or conserve a healthy/covered melee) and choose among the
            # visible choices after opening.
            visible = {w for _, _, w in _options(screen)}
            ally_full = general_max_hp(cur.get('ally')) if cur.get('ally') else None
            enemy_full = general_max_hp(cur.get('enemy')) if cur.get('enemy') else None
            conserve = (_hold_melee_egg(mem, cur, visible)
                        or cur.get('card_soldiers_current') is not True
                        or cur.get('card_hp_unread') or cur.get('card_context_unclassified'))
            protected = _okunote_risk_protected(mem, cur, hp, ally_full, enemy_full)
            if conserve:
                if not flow.get('risk_conserved'):
                    flow['risk_conserved'] = True
                    _record(mem, 'battle_okunote_risk_conserved', **_battle_labels(cur),
                            observed_metric={'ally_hp': hp, 'melee_balance': _melee_balance(cur)},
                            reason='低HPだが白兵優位または未読パネルのため奥の手を温存し白兵へ戻る')
                flow['risk_declined'] = True
                rescue['exhausted'] = True
                return [pad('b')]
            if protected:
                if not flow.get('risk_declined'):
                    flow['risk_declined'] = True
                    _record(mem, 'battle_okunote_risk_declined', **_battle_labels(cur),
                            observed_metric={'ally_hp': hp, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE,
                                             'side': cur.get('side'), 'castle': cur.get('castle')},
                            reason='低HPだが主人公・計画札・召喚戦・ボス・使用可能な卵/切り札などの既存保護条件があるため、奥の手を開かず白兵へ戻る')
                rescue['exhausted'] = True
                return [pad('b')]
            # Losing pinch: fall through to open おくのて. The candidates are
            # checked only after the list is visible.
            _record(mem, 'battle_okunote_risk_accepted', **_battle_labels(cur),
                    observed_metric={'ally_hp': hp, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                    reason='低HPで白兵優位がないため奥の手を開く（候補は開いた後に確認する）')

    # The irreversible candidate list cannot be cancelled. A parent-menu
    # navigation budget must never block a now-readable best candidate.
    if flow['ticks'] > 16 and screen.kind == 'battle_menu':
        # g1097 22:28: the earlier unconfirmed phase burned ticks past 16
        # before the rescue menu became readable, so the budgeted B ran
        # forever. B is a no-op on the sole-command rescue menu: once a
        # preempt/last-resort was recorded the only way forward is the
        # おくのて row below, never another cancel attempt.
        committed = (cur.get('okunote_last_resort_recorded')
                     or (cur.get('okunote_summon_preempt') or {}).get('recorded')
                     or (cur.get('okunote_egg_preempt') or {}).get('recorded'))
        committed_active = committed and cur.get('okunote_only_observed')
        # g1097 23:40: with ``ticks <= LIMIT`` inside the first condition the
        # unread limit below was unreachable — past it the budgeted B always
        # returned first, looping a button this box ignores. A おくのて row
        # that is readable *and* has a readable cursor must still be selected
        # below at any tick count; the limit still bounds navigation toward a
        # row whose label or cursor cannot be confirmed.
        if not committed_active:
            if cur.get('okunote_egg_preempt'):
                cur['okunote_egg_preempt'].update(exhausted=True, stage='unavailable', close_pending=True)
            return [pad('b')]
        okunote_row_ready = ('おくのて' in {w for _, _, w in _options(screen)}
                             and screen.menu_cursor is not None)
        if flow['ticks'] > OKUNOTE_COMMITTED_TICKS_LIMIT and not okunote_row_ready:
            for key in ('okunote_egg_preempt', 'okunote_summon_preempt'):
                if cur.get(key):
                    cur[key].update(exhausted=True, stage='unavailable', close_pending=True)
            _record(mem, 'battle_okunote_committed_unread', **_battle_labels(cur),
                    observed_metric={'ticks': flow['ticks']},
                    reason='確定済み救済メニューのおくのて行を有界観測しても読めないため、以後は未知の行を確定せず戻る')
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
        from ..os_layer.screen import OKUNOTE_SELF_DAMAGE_CHOICES as _sd
        shown = [w for w in OKUNOTE_CHOICES if w in names]
        candidates = [w for w in shown if w not in _sd]
        if not candidates:
            if not shown:
                return []
            label = shown[0]
            _record(mem, 'battle_okunote_forced_self_damage', **_battle_labels(cur),
                    observed_metric={'choices': shown},
                    reason='可視候補がすべて自傷のため、選ばざるを得ない候補で奥の手を進める')
        else:
            label = candidates[-1]
    move = _menu_move_batch(screen, label, _battle_menu_to(screen, label))
    if move != 'here':
        if (move is None and screen.kind == 'battle_menu'
                and (cur.get('okunote_egg_preempt') or {}).get('recorded')):
            return [pad('down')]  # bounded by the 16-observation menu budget
        return _emit_moves(move)
    if label == 'おくのて' and cur.get('okunote_last_resort_recorded'):
        cur['okunote_last_resort_selected'] = True
    if label == 'おくのて':
        for key in ('okunote_egg_preempt', 'okunote_summon_preempt'):
            guard = cur.get(key) or {}
            if guard.get('recorded'):
                cur[key].update(selected=True, stage='selected')
                _survival_state(mem, cur)['exhausted'] = True
    _record(mem, 'battle_okunote_select', **_battle_labels(cur), choice=label,
            observed_metric={'options': sorted(names)},
            reason='奥の手のカーソルを確認して選択。候補は効果が高いものを優先')
    okunote.selected(mem, cur, candidate=label != 'おくのて')
    return [pad('a')]


def _defense_card_kit_read(screen, mem, cur, flow, names):
    """#4456: read the real きりふだ names only; never select or consume a card.

    A readable, stable list becomes ``cur['defense_card_menu']`` and feeds the
    judgment layer with a scope-bound kit. An empty/unreadable list is recorded
    with no names, so the kit stays unknown (fail-closed). Bounded by
    DEFENSE_MENU_READ_LIMIT, matching the existing unreadable-list pattern.
    """
    cur.setdefault('defense_egg_kit_read', {'opens': 1})
    flow['list_ticks'] = int(flow.get('list_ticks') or 0) + 1
    scope = {k: cur.get(k) for k in ('enemy', 'ally', 'start_enemy_hp', 'start_ally_hp')}
    # Only a pair of matching readable observations within the bounded window
    # can establish the kit. A late first sighting must remain fail-closed.
    if flow['list_ticks'] > DEFENSE_MENU_READ_LIMIT:
        cur['defense_card_menu'] = {**scope, 'cards': [], 'readings': flow['list_ticks']}
        _defense_kit_read_unread(mem, cur, '読取上限までに一致する実一覧を確認できない')
        return [pad('b'), pad('b')]
    if not names:
        if _is_empty_card_list_text(screen):
            cur['defense_card_menu'] = {**scope, 'cards': [], 'readings': flow['list_ticks']}
            cur['defense_egg_kit_read']['read_done'] = True
            cur['card_flow'] = None
            _record(mem, 'battle_defense_kit_read', **_battle_labels(cur),
                    observed_metric={**scope, 'cards': [], 'observations': flow['list_ticks']},
                    resulting_event='kit_read_no_selection',
                    reason='防衛戦の実切り札一覧が空表示のため、携行札なしとして戻る')
            return [pad('b'), pad('b')]
        flow.pop('kit_names', None)
        return []
    cards = list(dict.fromkeys(names))
    previous = flow.get('kit_names')
    flow['kit_names'] = cards
    if previous != cards:
        return []          # require two consecutive agreeing readable lists
    cur['defense_card_menu'] = {**scope, 'cards': cards, 'readings': flow['list_ticks']}
    cur['defense_egg_kit_read']['read_done'] = True
    cur['card_flow'] = None
    _record(mem, 'battle_defense_kit_read', **_battle_labels(cur),
            observed_metric={**scope, 'cards': cards, 'observations': flow['list_ticks']},
            resulting_event='kit_read_no_selection',
            reason='防衛戦の実切り札一覧から札名だけを読み、選択・消費せず携行として扱う')
    return [pad('b'), pad('b')]


def card_list_step(screen: Screen, mem):
    """Keep card intention/selection separate from observed consumption."""
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    _card_battle_reading(screen, cur, mem)
    flow = cur.get('card_flow')
    if ((_one_hit_lethal(cur) or _hp1_resource_hold(cur))
            and not (flow and flow.get('stage') == 'announce')):
        cur['card_flow'] = None
        return [pad('b')]  # an unselected list closes unused: the next push wins
    names = [w for _, _, w in _options(screen) if w in ALL_CARD_NAMES]
    rescue = {**(cur.get('survival') or {}), 'listed_cards': names}
    if (cur.get('side') != 'defense' and _hero_retreat_needed(cur)
            and flow and flow.get('stage') == 'list'
            and not flow.get('announce_retried')
            # docich#4999: retreat now defers to any listed card the gate
            # permits (for_retreat) and to a survival list still spending
            # the remaining selections; the immediate-loss floor keeps the
            # g460 emergency close inside both rules.
            and not _critical_safe_card(mem, cur, rescue, for_retreat=True)
            and not (flow.get('survival') and not _retreat_immediate_loss(cur)
                     and _retreat_cards_to_spend(cur))
            and not (cur.get('hero_retreat') or {}).get('unavailable')
            and not (cur.get('hero_retreat') or {}).get('exhausted')):
        cur['card_flow'] = None
        cur['card_retreat_cancels'] = int(cur.get('card_retreat_cancels') or 0) + 1
        _record(mem, 'battle_hero_retreat_cancel_card', **_battle_labels(cur),
                reason='未選択の切り札一覧を閉じ、どうしの緊急退却を優先')
        return [pad('b')]
    # Recognition follows the real catalogue; tactic/rescue selection retains
    # its own planning and damage gates. A held event card must be visible.
    if not flow:
        return [pad('b')]
    if flow.get('read_kit'):
        return _defense_card_kit_read(screen, mem, cur, flow, names)
    if flow.get('stage') in ('menu', 'down') and names:
        # 実の札行が読めるのに帳簿が移動途中のまま残る開きずれがある。可視の
        # 一覧を現段として扱い、保持している選択手順へ合流させる。
        flow['stage'] = 'list'
    if flow['stage'] == 'list' and flow.get('survival'):
        return _survival_card_list(screen, mem, cur, flow, names)
    if flow['stage'] == 'list':
        if flow['card'] not in names:
            # A 500-ms observation can catch the opening text before the
            # carried names draw (g490: two confirmed イッテツーン, then an
            # empty OCR list). An unreadable frame is not a missing-card
            # receipt. Explicit absence stays immediate; other readable
            # lists must agree twice, and unreadable lists back out boundedly.
            absent = _is_empty_card_list_text(screen)
            # docich#4977: an unchanged frame is the same observation, not a
            # new one — identical sha256 repeats must not spend the bounded
            # wait that exists to give the list time to become readable.
            # Bound identical static repeats separately so an unrecognised
            # text screen cannot freeze forever.
            list_sha = getattr(screen, 'frame_sha256', None)
            if list_sha is None or list_sha != flow.get('list_obs_sha'):
                flow['list_observations'] = int(flow.get('list_observations', 0)) + 1
                flow['list_same_observations'] = 1
            else:
                flow['list_same_observations'] = int(flow.get('list_same_observations', 0)) + 1
            if list_sha is not None:
                flow['list_obs_sha'] = list_sha
            previous = flow.get('missing_list_names')
            flow['missing_list_names'] = names
            if not absent and (not names or previous != names):
                if (flow['list_observations'] < 4
                        and int(flow.get('list_same_observations', 0)) < CARD_LIST_SAME_FRAME_LIMIT):
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
        move = _menu_move_batch(screen, flow['card'], _battle_menu_to(screen, flow['card']))
        flow['cursor_ticks'] = int(flow.get('cursor_ticks', 0)) + 1
        if move != 'here':
            if flow['cursor_ticks'] > CARD_LIST_CURSOR_LIMIT:
                _card_use_unclassified(mem, cur, '切り札のカーソルを確認できるまでの上限に達したため一覧を閉じる')
                return [pad('b')]
            return _emit_moves(move)
        flow['stage'] = 'announce'
        flow['selection_planned'] = True
        # docich#4977: snapshot the visible list the card will be judged
        # against (row-disappearance evidence). The HP/soldier baselines
        # are deliberately NOT snapshotted at selection time: a drop
        # during the announce wait can come from melee, and attributing
        # it to the card would fabricate a use receipt. Those evidences
        # use only the baseline recorded when the battle menu opened.
        flow['names_at_select'] = list(names)
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
        if flow.get('egg_drop_only'):
            cur['egg_drop_selected_tick'] = mem.get('tick')
            _egg_drop_trace(mem, cur, 'selected', card=flow['card'],
                            tactic_id=flow.get('tactic_id'), listed_cards=names)
        return [pad('a')]
    if flow['stage'] == 'announce' and flow.get('selection_planned'):
        flow['announce_ticks'] = flow.get('announce_ticks', 0) + 1
        # docich#4977: the announce banner is uncalibrated, but the list that
        # stays open behind it is itself evidence — a fired card leaves its
        # row. Accumulate the distinct frames on which a readable list (or
        # the explicit empty-list text) no longer shows the selected card.
        # The visible rows must stay inside the list seen at selection: a
        # scrolled window or a misread into another real card name is not a
        # consumption receipt.
        absent_text = _is_empty_card_list_text(screen)
        sha = getattr(screen, 'frame_sha256', None)
        at_select = flow.get('names_at_select')
        card = flow.get('card')
        row_gone = (card and isinstance(at_select, list) and card in at_select
                    and ((names and card not in names
                          and set(names) <= set(at_select) - {card})
                         or (absent_text and not names)))
        if row_gone and type(sha) is str:
            absent_frames = flow.setdefault('announce_absent_frames', [])
            if sha not in absent_frames:
                absent_frames.append(sha)
        if flow['announce_ticks'] > CARD_ANNOUNCE_LIMIT:
            # docich#4703: a measured enemy HP drop after the selection is
            # evidence the card fired even when the announce banner was not
            # read. ch4 16-x spent 6 announce_retry and 13 open_unclassified in
            # fifteen minutes while the cards themselves were landing. Record
            # the use from that measurement instead of retrying (or giving the
            # card up) on sight alone.
            opening = flow.get('enemy_hp_at_open')
            now_hp = cur.get('enemy_hp')
            soldiers_open = flow.get('enemy_soldiers_at_open')
            now_soldiers = cur.get('enemy_soldiers')
            observed = expected = reason = None
            if type(opening) is int and type(now_hp) is int and now_hp < opening:
                observed = {'evidence': 'enemy_hp_drop',
                            'enemy_hp_at_open': opening, 'enemy_hp': now_hp}
                expected = '敵HPの低下'
                reason = '選択後に敵HPが低下したため、使用告知の代わりに実測で切り札の使用を確定する'
            elif len(flow.get('announce_absent_frames') or ()) >= 2:
                # Two distinct frames must agree: a single flickered OCR row
                # is not a consumption receipt.
                observed = {'evidence': 'card_row_absent', 'listed_cards': names,
                            'distinct_absent_frames': len(flow['announce_absent_frames'])}
                expected = '選択した札の行が一覧から消えること'
                reason = '異なる2フレームで選択した札の行が一覧から消えたため、使用告知の代わりに実測で切り札の使用を確定する'
            elif (type(soldiers_open) is int and type(now_soldiers) is int
                    and now_soldiers < soldiers_open):
                observed = {'evidence': 'enemy_soldiers_drop',
                            'enemy_soldiers_at_open': soldiers_open,
                            'enemy_soldiers': now_soldiers}
                expected = '敵の兵士数の減少'
                reason = '選択後に敵の兵士数が減ったため、使用告知の代わりに実測で切り札の使用を確定する'
            if observed is not None:
                flow['stage'] = 'done'
                cur['card_consumption_complete'] = True
                used = cur.setdefault('cards_used', [])
                if flow.get('card') and flow['card'] not in used:
                    used.append(flow['card'])
                _record(mem, 'battle_card_effect_confirmed', **_battle_labels(cur),
                        card=flow.get('card'), observed_metric=observed,
                        expected_metric=expected, reason=reason)
                cur['card_flow'] = None
                return []
            # Owner 2026-11-02 (VSヴィーナス at オトラント): a timed-out
            # announce means the list is still open — the carry may never
            # have fired, so retreating here leaves the card unused.
            # Re-enter the same card once: a consumed row falls into the
            # missing-card path; a still-carried row gets one more A.
            if not flow.get('announce_retried'):
                flow['announce_retried'] = True
                flow.update(stage='list', selection_planned=False,
                            announce_ticks=0, cursor_ticks=0, list_observations=0,
                            list_same_observations=0, list_obs_sha=None, announce_absent_frames=[])
                _record(mem, 'battle_card_announce_retry', **_battle_labels(cur),
                        card=flow['card'],
                        expected_metric='実使用告知または一覧からの消失',
                        resulting_event='card_retry_not_yet_confirmed',
                        reason='切り札の使用告知が確認できないままなので、消費を断定せず同じ札をもう一度選択する')
                return []
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


def _white_egg_available(screen):
    """A white 「たまごをつかう」 row is positive availability evidence.

    The battle state can retain a stale zero (e.g. after a paid recovery);
    the measured white command row outranks that bookkeeping.  Grey/disabled
    rows are excluded by the parser's disabled_item_box flag.
    """
    return (getattr(screen, 'kind', None) == 'battle_menu'
            and not getattr(screen, 'disabled_item_box', False)
            and 'たまごをつかう' in (getattr(screen, 'text', '') or ''))


def _screen_egg_empty(screen, mem, battle):
    """The actual named foreground actor outranks missing battle bookkeeping."""
    if _white_egg_available(screen):
        return False
    names = []
    if screen.battle and general_max_hp(screen.battle.ally) is not None:
        names = [screen.battle.ally]
    else:
        names = [row.name for row in screen.egg_rows if row.side == 'ally'
                 and general_max_hp('しゅじんこう' if row.name == NAME else row.name) is not None]
    return (_own_egg_empty(mem, {'ally': names[0]}) if len(names) == 1
            else _own_egg_empty(mem, battle))


def egg_choice_step(screen: Screen, mem):
    """Stop a proven slot reel, or navigate a stationary Elabel picker."""
    if _screen_egg_empty(screen, mem, mem.get('battle')):
        return [pad('b')]
    from ..os_layer.screen import egg_choice_names
    names = egg_choice_names(screen)
    if not names:
        return []
    key = tuple(names)
    flow = mem.get('egg_choice')
    actor = (mem.get('battle') or {}).get('ally')
    slot = ('ハズレ' in names
            or egg_memory.egg_type(mem, actor) == 'スロットエッグ'
            or (flow or {}).get('mode') == 'slot')
    if not slot and screen.menu_cursor not in (176, 192, 208):
        return []
    _summon_scene(mem)
    mode = 'slot' if slot else 'elabel'
    # Reel labels and the knight change automatically. Neither is a new
    # episode or a target to navigate toward. Also discard an old Elabel
    # retry latch when a hotloaded observation first proves this is a slot.
    if (not flow or flow.get('mode', 'elabel') != mode
            or (not slot and tuple(flow.get('names') or ()) != key)):
        flow = mem['egg_choice'] = {'names': names, 'mode': mode, 'selected': 0, 'wait': 0}
    flow['names'] = names
    if flow['selected']:
        flow['wait'] += 1
        if flow['wait'] <= 3:
            return []
        if flow['selected'] >= 2:
            if not flow.get('held'):
                flow['held'] = True
                _record(mem, 'egg_choice_unconfirmed', observed_metric={'candidates': names},
                        reason='有限回の卵入力後も選択画面が続くため、召喚成功とせず保留')
            return []
    if slot:
        flow['selected'] += 1
        flow['wait'] = 0
        _record(mem, 'egg_choice_select',
                observed_metric={'candidates': names, 'mode': 'slot',
                                 'cursor_y': screen.menu_cursor},
                resulting_event='slot_stop_requested_not_yet_confirmed',
                reason='スロット卵種またはハズレ表示で回転中と確認し、上下で候補を追わずAで停止を要求。停止結果は次画面で確認')
        return [pad('a')]
    # owner 2026-11-26: 三択は「強いモンスター」を選ぶ。強さは素の5能力
    # (HP/攻撃/防御/命中/速さ) の合計で決める。既知の名前が無ければ従来どおり
    # 先頭候補（未検証の並べ替えはしない）。
    from ...hanjuku_reference import strongest_egg_candidate
    choice = strongest_egg_candidate(names) or names[0]
    move = _menu_move_batch(screen, choice, _battle_menu_to(screen, choice))
    if move != 'here':
        return _emit_moves(move)
    flow['selected'] += 1
    flow['wait'] = 0
    _record(mem, 'egg_choice_select', observed_metric={'candidates': names, 'choice': choice},
            resulting_event='summon_selected_not_yet_confirmed',
            reason='実表示の三候補と騎士カーソルを確認し、素の5能力の合計が最大の召喚獣を選択')
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


def note_boss_monster_hp(mem, battle, enemy_rows):
    """Remember the boss monster's displayed HP for progress / kill detection.

    g1133 2026-11-06: the boss fight happens but the ally panel is unreadable,
    so no battle record survives (see #3501). The enemy monster's HP is still
    readable and fell across entries (ハードロボ 1041→1005→860→754). Keeping
    the last value lets the status and diagnostics show boss progress, and a
    later policy can react to a low boss without inventing a battle result.
    """
    attack = _plan_region(mem).get('attack') or {}
    if not chart.is_boss_castle(attack.get('castle')):
        return
    if not enemy_rows or len(enemy_rows) != 1:
        return
    row = enemy_rows[0]
    name, hp = getattr(row, 'name', None), getattr(row, 'hp', None)
    if not isinstance(name, str) or type(hp) is not int or not 0 < hp <= 9999:
        return
    seen = {'castle': attack.get('castle'), 'monster': name, 'hp': hp,
            'general': attack.get('general') or battle.get('ally'),
            'tick': int(mem.get('tick') or 0)}
    mem['boss_hp_seen'] = seen
    # Keep a bounded history of boss-panel readings: the boss's HP falling
    # across entries is the only proof a wave actually hurt it, and without the
    # per-attacker rows a wave that entered but dealt nothing looks identical
    # (g1163 chapter 7 2026-11-09 20:2x: 426 -> 298 across two arrivals).
    history = mem.get('boss_hp_history')
    history = history if isinstance(history, list) else []
    if not history or history[-1].get('hp') != hp or history[-1].get('general') != seen['general']:
        # A new reading that repeats the previous HP after a different general
        # entered means that general's wave dealt nothing to the boss, so the
        # next wave should look elsewhere (g1163 chapter 7 2026-11-09: the boss
        # sat at 426 through one arrival and only fell on the next).
        prior = history[-1] if history else None
        if (isinstance(prior, dict) and prior.get('hp') == hp
                and prior.get('general') and prior['general'] != seen['general']):
            hurtless = mem.get('boss_no_damage')
            hurtless = hurtless if isinstance(hurtless, list) else []
            if prior['general'] not in hurtless:
                mem['boss_no_damage'] = (hurtless + [prior['general']])[-8:]
        history.append(seen)
        mem['boss_hp_history'] = history[-8:]
    # A readable boss battle panel is itself proof of the entry. The 「せめこんだ」
    # text can be missed on the frames between the map and the panel (g1163
    # 2026-11-09 13:36: ヘーゼル fought ハードロボ but no attack_observed was
    # recorded, so the wave stayed en route and was re-sent as overdue at 14:00
    # while the boss HP never moved). Bind the in-flight sortie here.
    if attack.get('step') is None:
        from ..strategy.orders import _bind_sortie, _match_sortie
        general = attack.get('general') or battle.get('ally')
        step, match = _match_sortie(mem, attack.get('castle'), general)
        if step is not None and match == 'matched':
            _bind_sortie(mem, step, attack.get('castle'), general)
            attack['step'] = step
            _plan_region(mem)['attack'] = attack
            from ..decision_log import _record
            _record(mem, 'boss_entry_bound_by_panel', chart_step=step, general=general,
                    castle=attack.get('castle'),
                    observed_metric={'monster': name, 'hp': hp, 'sortie_match': match},
                    reason='突入文を読めなかったがボスの戦闘パネルを実測したため、在途の出撃と結び付ける')


def _egg_retreat_return(screen, mem, battle):
    """Reuse the finite return-to-human-command route; B is not a receipt."""
    attack = _plan_region(mem).get('attack') or {}
    if chart.is_boss_castle(attack.get('castle')):
        # g1133 2026-11-06: three measured boss entries (ヘラ 04:39,
        # アマンディーヌ 04:43, パプリカ 04:48) reached the boss battle menu,
        # pressed the B return below three times, and ended at the main menu
        # with battle_entry_expired: the boss fight never started. B here
        # cancels the boss attack, so a boss entry never takes this route.
        if not battle.get('boss_return_held_recorded'):
            battle['boss_return_held_recorded'] = True
            _record(mem, 'boss_battle_return_held', **_battle_labels(battle),
                    observed_metric={'castle': attack.get('castle'),
                                     'general': attack.get('general'),
                                     'enemy': battle.get('enemy')},
                    reason='ボス城突入中のB復帰は攻撃を取り消すため、復帰を使わず戦闘を続ける')
        return None
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


# A failed おくのて label read used to latch the rescue route for the whole
# fight. The row is only *unreadable*, not proven absent, and the box re-renders
# every time it is re-opened: g1288 ch10 2026-11-22 08:2x kept
# okunote_summon_preempt.exhausted with a 187 HP summon alive and the ally at
# 22 HP, so the battle went on without 奥の手. Re-arm boundedly instead.
SUMMON_SCROLL_REARM_TICKS = 40      # observations (~60 s) before one more look
SUMMON_SCROLL_REARM_LIMIT = 2       # extra looks per battle

# Owner 2026-11-24: 城守り戦で敵の召喚獣が生きている間は、おくのて行が一度
# 読めなかっただけで通常攻撃へ戻さない。live g1342 2026-11-24 02:59 フーリック
# (ゼウス vs ビシソワーズ→ドラゴンフライ, 召喚獣HP263) は 17 回おくのてを
# 引いた後、描画途中の箱で最後の 2 押しが反映されず「おくのて行なし」と判定して
# こうげきへ戻り、城を失った。召喚獣が生きている間は短い間隔で読み直しを続ける
# (押下・確定は従来どおり有界。確定は可視行のみ)。
SUMMON_SCROLL_LIVE_REARM_TICKS = 8
SUMMON_SCROLL_LIVE_REARM_LIMIT = 12
# An unread cursor / a one-row redraw is not proof that おくのて is absent.
SUMMON_SCROLL_UNREAD_WAITS = 3


def _live_defense_summon(battle):
    """A castle defense whose enemy summon still justifies drawing おくのて."""
    if battle.get('side') != 'defense':
        return False
    if not (battle.get('enemy_summon_observed') or battle.get('card_summon_observed')):
        return False
    hp = battle.get('summon_enemy_hp')
    if type(hp) is int:
        # A summon one edge hit finishes needs no lottery (owner 2026-11-04).
        return hp > EGG_WALL_DAMAGE
    return True     # an unread summon HP is not evidence of the one-hit case


def _summon_scroll_rearm(mem, battle, guard):
    """Re-open the bounded scroll after a failed label read.

    A live castle-defense summon keeps the route open with a short gap, so one
    unread frame cannot end 奥の手 for the rest of the fight. Other fights keep
    the finite two looks.
    """
    if guard.get('exhausted_reason') != 'scroll_unread':
        return False
    hp = battle.get('summon_enemy_hp')
    if type(hp) is int and 0 < hp <= EGG_WALL_DAMAGE:
        return False    # 一撃で倒せる残りHPでは奥の手を使わない (owner 2026-11-04)
    if battle.get('melee_only_summon'):
        return False    # 0ダメージ召喚獣は白兵で対処し、抽選を開かない (owner 2026-11-24)
    live = _live_defense_summon(battle)
    limit = SUMMON_SCROLL_LIVE_REARM_LIMIT if live else SUMMON_SCROLL_REARM_LIMIT
    gap = SUMMON_SCROLL_LIVE_REARM_TICKS if live else SUMMON_SCROLL_REARM_TICKS
    rearms = int(guard.get('rearms') or 0)
    if rearms >= limit:
        if not guard.get('rearm_limit_recorded'):
            guard['rearm_limit_recorded'] = True
            _record(mem, 'battle_okunote_summon_rearm_limit', **_battle_labels(battle),
                    observed_metric={'rearms': rearms, 'limit': limit,
                                     'summon_live': live,
                                     'summon_enemy_hp': battle.get('summon_enemy_hp')},
                    reason='おくのて行の読み直し上限に達したため、以後は実コマンドで応戦する')
        return False
    since = guard.get('exhausted_tick')
    now = int(mem.get('tick') or 0)
    if type(since) is not int or now - since < gap:
        return False
    guard.update(exhausted=False, downs=0, stage='scroll_rearm', unread_waits=0,
                 rearms=rearms + 1, exhausted_tick=0, exhausted_reason=None)
    _record(mem, 'battle_okunote_summon_rearm', **_battle_labels(battle),
            observed_metric={'rearms': rearms + 1, 'limit': limit,
                             'waited_ticks': now - since, 'summon_live': live},
            reason='おくのて行が読めないまま同一戦闘で打ち切っていたが、召喚獣が残っているため有界で読み直す')
    return True


def _defender_summon_egg_menu(screen, mem, battle):
    """おくのて in the egg-battle command box: a 4th row reached by scrolling.

    v359 sent B to open the rescue menu, but B on this box does nothing
    (v361 live: same frame 3 times, battle_okunote_summon_open_failed, fell
    back to こうげき). Owner-confirmed layout: こうげき / もうこうげき /
    たまごをつかう (greyed, unread by OCR) / おくのて — the 3-row box scrolls
    to show the last row. Move the cursor down until おくのて is readable,
    never B, never A on an unread row.
    """
    guard = battle.get('okunote_summon_preempt')
    if isinstance(guard, dict) and guard.get('selected'):
        # The parent A was sent; a repeated egg menu is still the wait for
        # the candidate list, never another confirmation on this box.
        attempt = battle.get('okunote_attempt')
        if isinstance(attempt, dict) and attempt.get('phase') == 'parent_selected':
            waits = int(guard.get('selected_waits') or 0)
            if waits < 8:
                guard['selected_waits'] = waits + 1
                return [{'type': 'wait', 'ms': 50}]
        guard['exhausted'] = True
        return None
    if (isinstance(guard, dict) and guard.get('exhausted')
            and _summon_scroll_rearm(mem, battle, guard)):
        guard = battle['okunote_summon_preempt']
    window = _defender_summon_preempt(mem, battle)
    if not window:
        return None
    guard = battle.setdefault('okunote_summon_preempt', {'opens': 0})
    labels = [w for _, _, w in _options(screen)]
    _stale_human = (_stale_battle_actor(screen, battle)
                    and _human_menu_column(screen)
                    and not getattr(screen, 'hidden_battle_commands', False)
                    and not getattr(screen, 'disabled_item_box', False)
                    and not getattr(screen, 'okunote_only_box', False))
    if (mem.get('chapter') == 12 and 'おくのて' not in labels
            and 'たまごをつかう' not in labels
            and (_human_five_item_box(screen, set(labels))
                 or _human_scroll_active(battle)
                 or _stale_human)):
        # The human command list has no おくのて row. Its upper page reads as
        # this egg box, and a stale policy.battle used to send the cursor back
        # down to the bottom page after every up. Never scroll it.
        if not guard.get('human_command_list'):
            guard['human_command_list'] = True
            _record(mem, 'battle_okunote_summon_human_menu', **_battle_labels(battle),
                    observed_metric={'visible': labels, 'cursor_y': screen.menu_cursor,
                                     'stale_actor': _stale_battle_actor(screen, battle)},
                    reason='人間用五項目コマンドリストにおくのて行は無いため、下方スクロールせず実コマンドへ戻す')
        return None
    if (labels and not _human_menu_column(screen)
            and not getattr(screen, 'hidden_battle_commands', False)
            and not getattr(screen, 'disabled_item_box', False)):
        # Enemy-general command menus are drawn in the left column (x=32/40).
        # They are not a player-scrollable おくのて box; down is ignored and
        # only stalls the turn.
        return None
    from . import okunote
    if 'おくのて' in labels:
        move = _menu_move_batch(screen, 'おくのて', _battle_menu_to(screen, 'おくのて'))
        if move == 'here':
            if not guard.get('recorded'):
                guard['recorded'] = True
                _record(mem, 'battle_okunote_summon_preempt', **_battle_labels(battle),
                        observed_metric={**window, 'max_self_damage': OKUNOTE_MAX_SELF_DAMAGE},
                        resulting_event='irreversible_risk_accepted',
                        reason='敵の召喚獣が出ていて味方の卵・札が使えないため、卵戦コマンドのおくのてを選ぶ。自傷リスクは残る')
            guard.update(selected=True, stage='selected')
            _survival_state(mem, battle)['exhausted'] = True
            _record(mem, 'battle_okunote_select', **_battle_labels(battle), choice='おくのて',
                    observed_metric={'options': labels},
                    reason='奥の手のカーソルを確認して選択。候補は効果が高いものを優先')
            okunote.selected(mem, battle, candidate=False)
            return [pad('a')]
        if move is not None:
            return _emit_moves(move)
        # Cursor unread: keep scrolling like the no-label case, never confirm.
    downs = int(guard.get('downs') or 0)
    room = DEFENDER_SUMMON_SCROLL_LIMIT - downs
    if room > 0:
        # The grey たまご row sits above おくのて and the box needs three to four
        # downs (owner-confirmed layout, v362). One down per observation spent
        # four looks (x 1.5 s) on every draw of the live g1288 summon fight
        # (2026-11-22 02:09-02:11: four battle_okunote_summon_scroll rows
        # between each おくのて selection; 68 such rows per day). Batch a
        # bounded pair: the same presses reach the same clamped row, and the
        # next observation still re-reads the labels, so a visible おくのて is
        # moved to instead of overshot. Never more than the existing limit.
        batch = min(SUMMON_SCROLL_BATCH, room)
        guard['downs'] = downs + batch
        guard['stage'] = 'scrolling'
        _record(mem, 'battle_okunote_summon_scroll', **_battle_labels(battle),
                observed_metric={'cursor_y': screen.menu_cursor, 'visible': labels,
                                 'down_attempt': downs + batch, 'batch': batch},
                reason='たまごをつかう（灰色）の下のおくのて行へカーソルを下げる')
        return [pad('down') for _ in range(batch)]
    # The scroll budget is spent. A frame whose cursor or rows are still
    # unread (the box is redrawing) is not evidence that おくのて is absent:
    # live g1342 02:59:05 sent the last pair into a redrawing defense box and
    # the very next frame had an unread cursor, so the old code latched the
    # route off and attacked with the summon still alive. Re-read first, then
    # give the turn back only from a readable box.
    waits = int(guard.get('unread_waits') or 0)
    if ((screen.menu_cursor is None or len(labels) < 2)
            and waits < SUMMON_SCROLL_UNREAD_WAITS):
        guard['unread_waits'] = waits + 1
        _record(mem, 'battle_okunote_summon_unread_box', **_battle_labels(battle),
                observed_metric={'waits': waits + 1, 'limit': SUMMON_SCROLL_UNREAD_WAITS,
                                 'cursor_y': screen.menu_cursor, 'visible': labels,
                                 'down_attempts': downs},
                reason='おくのて行の探索中に箱のカーソル/行が未読のため、行なしと断定せず再観測する')
        return [{'type': 'wait', 'ms': 50}]
    guard.pop('unread_waits', None)
    guard.update(exhausted=True, exhausted_reason='scroll_unread',
                 exhausted_tick=int(mem.get('tick') or 0))
    _record(mem, 'battle_okunote_summon_open_failed', **_battle_labels(battle),
            observed_metric={**window, 'down_attempts': downs},
            reason='有界な下方スクロールでもおくのて行が表示されないため、未知の行を確定せず既存の応戦へ戻る')
    return None


_ENEMY_COMMAND_SETTLE_CANCEL_PERIOD = 12

# g1107: an unread (greyed-row) cursor kept blind A rejected for hours. The
# 4-row command box needs at most 3 ups to re-park on its top row; beyond
# this bound the bot holds instead of confirming an unobserved row.
EGG_ATTACK_UNREAD_UP_LIMIT = 6


def _enemy_command_wait(mem, key, scope, decision, metric, wait_reason,
                        unconfirmed_reason):
    """Bounded enemy-command wait: a frozen box earns a cancel tap, never a confirm.

    The observation cap used to leave the end to the screen-stall watchdog,
    but the command box keeps animating, so a permanently parked enemy menu
    can hold the corner for hours (g1107 レモン egg battle, 2026-11-04:
    ~2.5h of identical egg_battle_menu waits). After the cap, a periodic B
    either closes a misrecognized own menu or is ignored on a real enemy
    turn; it can never select a row.
    """
    previous = mem.get(key)
    same_scope = isinstance(previous, dict) and previous.get('scope') == scope
    count = previous.get('observations') if same_scope else 0
    count = count if type(count) is int and 0 <= count <= 30 else 30
    count = min(count + 1, 31)
    settle = (previous.get('settle_ticks') or 0) + 1 if same_scope and count == 31 else 0
    mem[key] = {'scope': scope, 'observations': count, 'settle_ticks': settle}
    if (count in (1, 31)
            and not (same_scope and previous.get('observations') == count)):
        _record(mem, decision,
                deviation_reason='enemy_command_unconfirmed' if count == 31 else None,
                observed_metric={**metric, 'observations': count},
                reason=unconfirmed_reason if count == 31 else wait_reason)
    if count == 31 and settle >= _ENEMY_COMMAND_SETTLE_CANCEL_PERIOD:
        mem[key]['settle_ticks'] = 0
        _record(mem, 'enemy_command_wait_cancel', observed_metric=metric,
                reason='敵コマンド表示が再観測上限後も変化しないため、確定を伴わないキャンセルで実際の入力待ちかを確認する')
        return [pad('b')]
    return []


def _summon_card_row_hidden(screen, battle, *, cards_left, empty_egg):
    """A carried card is still unrevealed in the summon command box.

    Owner 2026-11-22: in an enemy-summon fight the きりふだ row sits below
    たまごをつかう and is absent from the command text until the box scrolls,
    so a carried card must be revealed and opened before こうげき.
    Owner 2026-11-25: ボス戦や敵が召喚獣をだしたとき、卵を持っておらず
    きりふだを持っている場合は切り札を使う.

    A greyed or spent egg row drops out of the white OCR (measured in
    ``os_layer/screen.py``: たまごをつかう drops out of OCR), so "no egg row
    in a measured summon command box" is the same no-egg evidence as an
    observed zero count.  The old gate required the row or a measured zero and
    therefore never scrolled, and the turn attacked with the card in hand
    (card_miss series #5259/#5322/#5331/#5352/#5362/#5373/#5381/#5398).

    This only decides navigation.  Opening still requires the readable
    きりふだ row, so an unread or greyed frame can never spend a card.
    ``screen.kind`` is required for the missing-row branch: a message panel
    keeps the egg-battle latch and can contain こうげき wording, and spending
    the bounded scroll budget there would leave the real box unscrolled.
    """
    if not cards_left or battle.get('card_flow') or 'きりふだ' in (screen.text or ''):
        return False
    text = screen.text or ''
    if 'たまごをつかう' in text or empty_egg:
        return True
    if battle.get('summon_egg_unavailable'):
        return True   # the defence summon box already proved the egg row dead
    return screen.kind == 'egg_battle_menu' and ('こうげき' in text or 'もうこうげき' in text)


def _summon_card_rearm(mem, battle):
    """Re-open the bounded card-reveal scroll after it ran out.

    The reveal budget is only just deep enough to reach the きりふだ row, so
    a single press eaten by a redraw -- or a frame that read the top page
    while the box was mid-scroll -- forfeit the carried card for the rest of
    the battle (card_miss #5600: scrolls=3 with the row never readable).  As
    long as the carry ledger still lists a card, the hidden gate keeps
    proving the same evidence; give the box another bounded look after a
    gap, mirroring the おくのて scroll re-arm.  A truly dead row just
    re-exhausts and the turn goes back to attacking.
    """
    rearms = int(battle.get('summon_card_rearms') or 0)
    if rearms >= SUMMON_CARD_REARM_LIMIT:
        return False
    now = int(mem.get('tick') or 0)
    since = battle.get('summon_card_exhausted_tick')
    if type(since) is not int:
        battle['summon_card_exhausted_tick'] = now
        return False
    if now - since < SUMMON_CARD_REARM_TICKS:
        return False
    battle['summon_card_rearms'] = rearms + 1
    battle['summon_card_scroll'] = 0
    battle['summon_card_exhausted_tick'] = now
    battle.pop('summon_card_scroll_out', None)
    _record(mem, 'battle_summon_card_rearm', **_battle_labels(battle),
            observed_metric={'rearms': rearms + 1, 'limit': SUMMON_CARD_REARM_LIMIT,
                             'waited_ticks': now - since},
            reason='有界スクロールが尽きても携行札が残るため、描画途中・入力抜けの可能性を残して有界で揭示をやり直す')
    return True


def egg_battle_step(screen: Screen, mem):
    """Summoned-monster battle: a turn menu that waits for a command.

    The chart avoids provoking enemy eggs but gives no command for this
    battle. Independent judgment defaults to たまごをつかう so an enemy
    summon is answered with an egg rather than a stall or blind attack.
    This is original pattern ⑥ (own egg when melee is not enough); patterns
    ②④⑤ stay chart-directed. Messages inside it advance with A.
    A measured ゾンビ summon is the exception: it is met with melee and the
    egg is kept (owner 2026-11-24, ``MELEE_ONLY_SUMMONS``).
    """
    if screen.kind != 'egg_battle_menu':
        mem.pop('egg_menu_stage', None)
        mem.pop('enemy_general_menu', None)
        mem.pop('enemy_general_menu_pending', None)
        return [pad('a')]
    _labels = {word for _, _, word in _options(screen)}
    _battle_ctx = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    # The same five-item list is classified egg_battle_menu once きりふだ drops
    # off its upper page.  The right-hand column is part of the evidence: the
    # enemy-general command menu is drawn on the left and ignores the player's
    # keys, so it must keep its own wait owner.  A stale policy.battle alone is
    # not enough to create a persisted episode; it may only correct one
    # readable attack row (A on こうげき, up from もうこうげき).
    _scroll_owner = _human_scroll_owns(
        screen, _labels, _battle_ctx, chapter=mem.get('chapter'))
    _five = _human_five_item_box(screen, _labels)
    _stale_row = (
        _stale_battle_actor(screen, _battle_ctx)
        and _human_menu_column(screen)
        and bool(_labels.intersection({'こうげき', 'もうこうげき'}))
        and not _labels.intersection({'たまごをつかう', 'おくのて'})
        and not getattr(screen, 'hidden_battle_commands', False)
        and not getattr(screen, 'disabled_item_box', False)
        and not getattr(screen, 'okunote_only_box', False)
    )
    _stale_exact = _cursor_exact_label(screen) if _stale_row else None
    if (mem.get('chapter') == 12 and _battle_ctx and _stale_row
            and not _scroll_owner and _stale_exact == 'こうげき'):
        _record(mem, 'battle_menu_scroll_attack', **_battle_labels(_battle_ctx),
                observed_metric={'labels': sorted(_labels), 'cursor_y': screen.menu_cursor,
                                 'cursor_label': _stale_exact, 'ups': 0,
                                 'stale_actor': True},
                reason='staleなpolicy.battleでも右列コマンドのカーソル行が完全一致でこうげきのため、Aで一度だけ確定する')
        return [pad('a')]
    _human_menu = bool(
        _scroll_owner or _five
        or (_stale_row and _stale_exact in ('こうげき', 'もうこうげき')))
    if mem.get('chapter') == 12 and _battle_ctx and _human_menu:
        _begin_human_scroll(_battle_ctx, screen)
        _scroll = _human_scroll_step(screen, mem, _battle_ctx, _labels)
        if _scroll is not None:
            return _scroll
    partial_enemy = _partial_enemy_general_command(screen)
    if partial_enemy:
        for key in ('egg_battle', 'egg_action', 'egg_key', 'egg_needed',
                    'egg_menu_stage', 'egg_battle_row_dead', 'enemy_general_menu'):
            mem.pop(key, None)
        return _enemy_command_wait(
            mem, 'enemy_general_menu_pending',
            [partial_enemy.name, partial_enemy.hp],
            'enemy_general_menu_pending',
            {'enemy': partial_enemy.name, 'enemy_hp': partial_enemy.hp,
             'ally_panel_unread': len(screen.egg_rows) == 1},
            '敵将軍の実HP欄と左コマンドの途中表示。味方HPを補完せず入力を保留する',
            '敵コマンド途中表示の再観測上限。味方HPを補完せず、無害な間欠キャンセルで入力待ちを確認する')
    mem.pop('enemy_general_menu_pending', None)
    enemy_command = _enemy_general_command(screen)
    if enemy_command:
        ally, enemy = enemy_command
        # Invalidate our old navigation before returning; no stale down/down/A
        # sequence, empty-egg recheck or learned own action belongs to this turn.
        for key in ('egg_battle', 'egg_action', 'egg_key', 'egg_needed',
                    'egg_menu_stage', 'egg_battle_row_dead'):
            mem.pop(key, None)
        return _enemy_command_wait(
            mem, 'enemy_general_menu',
            [ally.name, ally.hp, enemy.name, enemy.hp],
            'enemy_general_menu_wait',
            {'owner': 'enemy', 'ally_monster': ally.name, 'ally_hp': ally.hp,
             'enemy': enemy.name, 'enemy_hp': enemy.hp},
            '敵将軍の実コマンド表示のため、敵の操作終了を待つ',
            '敵コマンド表示の再観測上限。味方の卵や攻撃を推測入力せず、無害な間欠キャンセルで入力待ちを確認する')
    mem.pop('enemy_general_menu', None)
    _summon_scene(mem)
    # 将軍HPをこの召喚戦のメニュー行から最新化してから、勝てそうかを判定する。
    battle = mem.get('battle') or {}
    from . import okunote
    summon = okunote.summon_reading(screen, mem, battle)
    if summon is None:
        summon = okunote.summon_monster_panel(screen, battle)
    okunote.combat_return(screen, mem, battle, summon=summon)
    if battle:
        if isinstance(summon, dict) and type(summon.get('enemy_monster_hp')) is int:
            battle['summon_enemy_hp'] = summon['enemy_monster_hp']
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
    # Owner 2026-11-24: 相手がゾンビなら卵を使わず白兵で対処する。HP比や
    # 経験則より優先するが、実画面で敵召喚獣が1体だけ読めて名前が一致した
    # ときだけ適用する (未知・複数・未読の敵へは広げない)。
    melee_only = (len(enemy_rows) == len(enemy_monsters) == 1
                  and enemy_monsters[0].name in MELEE_ONLY_SUMMONS)
    if isinstance(mem.get('battle'), dict):
        # Keep the okunote preempt from spending our general's HP on a lottery
        # against a zero-damage zombie; cleared as soon as the panel changes.
        mem['battle']['melee_only_summon'] = melee_only
    summon_threat = bool(general_reading and len(enemy_rows) == len(enemy_monsters) == 1
                        and not melee_only
                        and (enemy_monsters[0].name == 'ダークエルフ'
                             or general_reading[0] * 10
                             <= enemy_monsters[0].hp * BEHIND_EGG_RATIO_TENTHS))
    if summon_threat:
        wants_egg = True
        egg_evidence = {**egg_evidence, 'rule': 'enemy_monster_threat',
                        'enemy_monster': enemy_monsters[0].name,
                        'enemy_monster_hp': enemy_monsters[0].hp}
    elif melee_only:
        wants_egg = False
        egg_evidence = {**egg_evidence, 'rule': 'melee_only_summon',
                        'enemy_monster': enemy_monsters[0].name,
                        'enemy_monster_hp': enemy_monsters[0].hp}
    empty_egg = _screen_egg_empty(screen, mem, battle)
    hero_summon = isinstance(battle, dict) and battle.get('ally') == NAME
    if hero_summon and not wants_egg and not melee_only:
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
        rescue = battle.get('survival') or {}
        if rescue.pop('egg_window_latch', False):
            rescue['exhausted'] = False
            _record(mem, 'battle_survival_rearm', **_battle_labels(battle),
                    observed_metric={'ally_hp': battle.get('ally_hp'), 'enemy_hp': battle.get('enemy_hp')},
                    reason='召喚前の期限待ちで止めた救済を、召喚の発生を契機に再評価する（#2953）')
        exp = mem.get('_experience')
        key = experience.situation_key('egg_summon', mem)
        # ⑥の写像: HP不利ならたまご、勝てそうなら温存 (attack) から始める。
        action = experience.preferred(exp, key, default='use_egg' if wants_egg and not empty_egg else 'attack',
                                      kind='egg_summon')
        if (hero_summon or summon_threat or combat_danger) and not empty_egg and not melee_only:
            action = 'use_egg'  # learned attacks cannot overrule a measured summon threat
        if melee_only:
            action = 'attack'   # owner 2026-11-24: zombie is met by melee, never by our egg
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
                reason=(f'オーナー指示 (2026-11-24): 相手が{enemy_monsters[0].name}'
                        '（0ダメージ召喚獣）のため、たまごを使わず白兵で対処する'
                        if melee_only else
                        'チャートに召喚戦の指示がないための独自判断（原典戦術⑥、'
                        '自軍が健在でHP・兵士数・戦闘力から白兵を続けられそうならたまごを温存）'
                        if not wants_egg else
                        'チャートに召喚戦の指示がないための独自判断（原典戦術⑥、既定はたまご）'))
    attack = _plan_region(mem).get('attack') or {}
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
    # 相手がゾンビのときはこの昇格も行わない (owner 2026-11-24)。
    if (not melee_only
            and (mem.get('egg_needed') is False or summon_threat or combat_danger)
            and wants_egg and not empty_egg
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
    if melee_only:
        # A hot-loaded or learned 卵 decision made before the panel was read
        # must not spend the egg on a zombie (owner 2026-11-24). Record the
        # reversal once per battle; later observations just keep attacking.
        if mem.get('egg_action') != 'attack' and not battle.get('melee_only_override'):
            battle['melee_only_override'] = True
            _record(mem, 'egg_battle', strategy_variant='egg_battle_melee_only',
                    deviation_reason='チャート外: 敵の卵召喚戦（0ダメージ召喚獣は白兵）',
                    expected_metric='0ダメージ召喚獣への白兵と戦闘結果',
                    observed_metric={'egg_need': egg_evidence,
                                     'enemy_monster': enemy_monsters[0].name,
                                     'enemy_monster_hp': enemy_monsters[0].hp},
                    source_pattern='⑥',
                    reason=(f'オーナー指示 (2026-11-24): 相手が{enemy_monsters[0].name}'
                            '（0ダメージ召喚獣）のため、たまごの温存判断を白兵へ戻す'))
        mem['egg_action'] = 'attack'
        if isinstance(battle.get('independent'), dict):
            battle['independent']['action'] = 'attack'
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
        # A greyed egg row in a boss fight is worth logging, but it is NOT
        # proof that the general cannot hurt the boss: g1163 2026-11-09
        # 14:55:50 ペコリーノ entered ハードロボ with the row grey and the boss
        # went 152 -> 70 in the same fight. v562 wrote egg_uses=0 from this row
        # and would have benched the wave that was actually working, so the
        # evidence stays a log only.
        if not empty_egg and chart.is_boss_castle((_plan_region(mem).get('attack') or {}).get('castle')):
            general = ((_plan_region(mem).get('attack') or {}).get('general')
                       or (battle or {}).get('ally'))
            if isinstance(general, str) and general and not mem.get('egg_row_unavailable_logged'):
                mem['egg_row_unavailable_logged'] = general
                _record(mem, 'egg_row_unavailable', general=general,
                        observed_metric={'castle': (_plan_region(mem).get('attack') or {}).get('castle'),
                                         'egg_row': 'unavailable'},
                        reason='ボス戦で卵の行が灰色だったことを記録する（召喚不能とは断定しない）')
        action = 'attack'
        if empty_egg:
            mem['egg_action'] = 'attack'
            if isinstance(battle.get('independent'), dict):
                battle['independent']['action'] = 'attack'
    if action == 'attack':
        battle = (mem.get('battle') if isinstance(mem.get('battle'), dict)
                  else mem.setdefault('egg_retreat_flow', {}))
        interrupted_flow = battle.get('card_flow')
        if isinstance(interrupted_flow, dict) and interrupted_flow.get('read_kit'):
            # The read-only defense kit probe exists to prepare a pre-summon
            # egg drop.  Once the enemy summon menu owns the turn that window
            # has passed, and this flow cannot advance here; close it instead
            # of letting its truthy value suppress the carried-card reveal.
            _defense_kit_read_unread(
                mem, battle, '敵召喚メニューへ置き換わり実切り札一覧の読み取りを継続できない')
        elif (isinstance(interrupted_flow, dict)
                and interrupted_flow.get('stage') in ('menu', 'down', 'list')
                and not interrupted_flow.get('selection_planned')
                and not interrupted_flow.get('read_kit')):
            # The enemy summon dialogue can replace the normal battle menu
            # between planning a card and opening its list.  The old cursor
            # navigation belongs to the previous menu; keep the exact card
            # intent, then reopen it from the summon menu's real list.
            interrupted_card = interrupted_flow.get('card')
            if isinstance(interrupted_card, str):
                battle['summon_card_resume'] = {
                    'card': interrupted_card,
                    'tactic_id': interrupted_flow.get('tactic_id'),
                }
            battle['card_flow'] = None
        elif (isinstance(interrupted_flow, dict)
                and (interrupted_flow.get('stage') == 'announce'
                     or interrupted_flow.get('selection_planned'))):
            # docich#5538/#5612: the announce wait only advances on the
            # card_list screen.  Once the enemy summon menu replaces it the
            # flow is frozen, but its truthy value also suppresses the reveal
            # scroll and reopen gate.  Hold the attack while we bound out a
            # possible misread; never spend a melee turn with that blocker
            # still unresolved, then release it as unclassified.
            scope = [interrupted_flow.get('card'), interrupted_flow.get('stage')]
            stall = battle.get('summon_card_flow_stall')
            observations = (int(stall.get('observations') or 0) + 1
                            if isinstance(stall, dict)
                            and stall.get('scope') == scope else 1)
            battle['summon_card_flow_stall'] = {'scope': scope,
                                                'observations': observations}
            if observations >= SUMMON_CARD_FLOW_STALL_LIMIT:
                battle.pop('summon_card_flow_stall', None)
                _card_use_unclassified(
                    mem, battle,
                    '選択済み・消費未確定の切り札告知待ちが召喚画面で進行不能のまま滞留したため、未分類として保持して解放する')
            else:
                return []
        else:
            battle.pop('summon_card_flow_stall', None)
        if int(battle.get('card_retreat_cancels') or 0) >= CARD_RETREAT_CANCEL_LIMIT:
            abandoned = [c for c in (battle.get('planned_cards') or [])
                         if c not in (battle.get('cards_unclassified') or [])]
            if abandoned:
                battle.setdefault('cards_unclassified', []).extend(abandoned)
                _record(mem, 'battle_summon_card_abandoned', **_battle_labels(battle),
                        observed_metric={'cancels': battle.get('card_retreat_cancels'),
                                         'abandoned': abandoned},
                        reason='退却禁止のため切り札一覧の再オープンが空転する。札は使えないものとして攻撃へ切り替える')
        attempted = {*(battle.get('cards_selected') or []), *(battle.get('cards_used') or []),
                     *(battle.get('cards_unclassified') or []), *(battle.get('cards_missing') or [])}
        cards_left = [c for c in (battle.get('planned_cards') or [])
                      if c not in attempted and _card_usable_in_battle(mem, battle, c)]
        if not cards_left:
            # The summon battle's battle context often has no planned_cards,
            # even though the sortie confirmation carried a card.  Use the
            # measured carried kit (departure binding minus spent) so a held
            # card is not silently treated as absent.
            carried = _confirmed_carried_kit(mem, battle) or []
            cards_left = [c for c in carried
                          if c not in attempted and _card_usable_in_battle(mem, battle, c)]
        # Owner 2026-11-22: enemy summon + our general was going straight to
        # こうげき while a carried card was still in planned_cards.  In a
        # summon fight the きりふだ row sits BELOW たまごをつかう and is not in
        # the visible command text until the box scrolls down; open it first.
        if _summon_card_row_hidden(screen, battle, cards_left=cards_left,
                                   empty_egg=empty_egg):
            scrolls = int(battle.get('summon_card_scroll') or 0)
            if (scrolls >= SUMMON_CARD_SCROLL_LIMIT
                    and _summon_card_rearm(mem, battle)):
                scrolls = 0
            if scrolls < SUMMON_CARD_SCROLL_LIMIT:
                battle['summon_card_scroll'] = scrolls + 1
                _record(mem, 'battle_summon_card_scroll', **_battle_labels(battle),
                        observed_metric={'scrolls': scrolls + 1, 'cards_left': list(cards_left),
                                         'text': (screen.text or '')[:60],
                                         'cursor_y': screen.menu_cursor,
                                         'visible': sorted({w for _, _, w in _options(screen)}),
                                         'screen_kind': screen.kind,
                                         'empty_egg': bool(empty_egg)},
                        reason='召喚獣戦の切り札行はたまごをつかうの下にあるため、下へスクロールして表示する')
                return [pad('down')]
            if not battle.get('summon_card_scroll_out'):
                # The reveal scroll spent its whole budget and re-arms; the
                # audit needs this marker to tell a tried-and-failed open
                # from a gate that never scrolled (#5600 was scrolls=3).
                battle['summon_card_scroll_out'] = True
                _record(mem, 'battle_summon_card_exhausted', **_battle_labels(battle),
                        observed_metric={'scrolls': scrolls,
                                         'rearms': int(battle.get('summon_card_rearms') or 0),
                                         'cards_left': list(cards_left),
                                         'cursor_y': screen.menu_cursor,
                                         'visible': sorted({w for _, _, w in _options(screen)}),
                                         'screen_kind': screen.kind,
                                         'text': (screen.text or '')[:60]},
                        reason='有界な下方スクロールを尽きても切り札行が読めないため、実コマンドでの応戦へ戻る')
        if int(battle.get('summon_card_opens') or 0) >= SUMMON_CARD_OPEN_LIMIT:
            # Reopening the same carried card after the retreat-cancel loop only
            # churns the menu (retreat is banned); attack instead.
            cards_left = []
        resume = battle.get('summon_card_resume')
        if not isinstance(resume, dict) or resume.get('card') not in cards_left:
            resume = None
            battle.pop('summon_card_resume', None)
        if (cards_left and 'きりふだ' in (screen.text or '')
                and not battle.get('card_flow')):
            target = _battle_menu_to(screen, 'きりふだ')
            move = _menu_move_batch(screen, 'きりふだ', target) if target is not None else None
            if move == 'here':
                card = resume['card'] if resume else cards_left[0]
                battle['card_flow'] = {'card': card, 'stage': 'list',
                                       'note': ('召喚会話で中断された携行切り札を再開'
                                                if resume else '敵召喚獣戦の携行切り札を開幕使用'),
                                       'enemy_hp_at_open': battle.get('enemy_hp')}
                # A later card in the same fight needs its own bounded look at
                # the box, which returns to the top page after the list closes
                # (owner 2026-11-24: 2枚目以降の切り札も開く).
                battle['summon_card_scroll'] = 0
                battle['summon_card_rearms'] = 0
                battle.pop('summon_card_exhausted_tick', None)
                battle.pop('summon_card_scroll_out', None)
                battle['summon_card_opens'] = int(battle.get('summon_card_opens') or 0) + 1
                battle.pop('summon_card_resume', None)
                _record(mem, 'battle_summon_card_open', **_battle_labels(battle),
                        card=card,
                        observed_metric={'enemy': battle.get('enemy'),
                                         'cards_left': list(cards_left),
                                         'resumed_pending_flow': bool(resume),
                                         'resumed_tactic_id': resume.get('tactic_id') if resume else None},
                        reason=('召喚会話で中断された未選択の切り札を実一覧から再開する'
                                if resume else '敵が召喚獣でこちらが将軍のため、通常攻撃より先に切り札を開く'))
                return [pad('a')]
            if move is not None:
                return _emit_moves(move)
        # g498: B did not leave the turn menu, yet the next observation
        # immediately chose A and Venus lost. Require a bounded return flow,
        # and actually request retreat if the normal command menu is reached.
        # g460 安全弁は「卵か札か退却のどれもない」局面だけ。勝てそうなので
        # たまごを温存している間は、まず白兵で戦う (退却しない)。
        # Owner 2026-11-23: retreat is banned; with no card the summon turn
        # attacks instead of probing the retreat menu.
        # g508: after Excalibur fell, stale general HP82 concealed the
        # wounded Venus while blind A kept choosing the weakest attack.
        # Fierce attack adds lost HP to damage, but can miss/lose initiative;
        # use it only as a bounded-resource last resort with actual panels.
        enemy_rows = [r for r in screen.egg_rows if r.side == 'enemy'
                      and r.name in _MONSTER_SKILLS and type(r.hp) is int and r.hp > 0]
        # 敵が将軍でも召喚獣でも、敵HPが一桁なら通常攻撃で倒せるため、
        # 命中・先手を損なうもうこうげきは選ばない。
        enemy_low_hp = any(r.side == 'enemy' and type(r.hp) is int and 0 < r.hp < 10
                           for r in screen.egg_rows)
        visible_opts = {w for _, _, w in _options(screen)}
        fierce = (general_reading is not None and 0 < general_reading[0] * 2 <= general_reading[1]
                  and len(enemy_rows) == 1 and not enemy_low_hp
                  and not cards_left
                  and ('もうこうげき' in visible_opts or 'もうこうげき' in (screen.text or ''))
                  and (empty_egg or 'たまごをつかう' not in screen.text))
        # Owner 2026-11-02 (城防衛で対召喚獣戦、卵もない時はおくのて): a castle
        # defender facing an enemy summon with the egg row dead and no card left
        # takes the lottery instead of attacking into it (g1097 18:06 ペコリーノ
        # died to ローラーキラー by もうこうげき with おくのて never tried).
        # The latch lets the summon preempt see the egg is gone once the
        # command menu replaces this panel.
        if (isinstance(mem.get('battle'), dict) and battle.get('side') == 'defense'
                and len(enemy_rows) == 1 and not cards_left
                and (empty_egg or 'たまごをつかう' not in screen.text)):
            battle['summon_egg_unavailable'] = True
            opening = _defender_summon_egg_menu(screen, mem, battle)
            if opening is not None:
                return opening
        label = 'もうこうげき' if fierce else 'こうげき'
        move = _menu_move_batch(screen, label, _battle_menu_to(screen, label))
        if move is None:
            visible = {w for _, _, w in _options(screen)}
            if screen.menu_cursor is None:
                if empty_egg:
                    # Never confirm the empty egg at an unread cursor, but B
                    # does not close this battle box either (g1097): the corner
                    # looped B forever on one summon menu (g1163 2026-11-10
                    # 19:3x-19:4x, 414 identical [b] plans). After two Bs, step
                    # once to the next row (きりふだ) and confirm it instead.
                    closes = int(battle.get('empty_egg_b_presses') or 0)
                    if closes < 2:
                        battle['empty_egg_b_presses'] = closes + 1
                        return [pad('b')]
                    if not battle.get('empty_egg_card_fallback'):
                        battle['empty_egg_card_fallback'] = True
                        _record(mem, 'battle_empty_egg_card_fallback',
                                **_battle_labels(battle),
                                observed_metric={'visible': sorted(w for _, _, w in _options(screen)),
                                                 'b_presses': closes},
                                reason='卵が使えずBでも閉じない戦闘メニューのため、次の行（きりふだ）へ進めて戦闘を動かす')
                        return [pad('down'), pad('a')]
                    return [pad('b')]
                # g1107 2026-11-03: おくのて探索が尽きた後にカーソルが灰色の
                # たまごをつかう行 (未読) に残り、未確認カーソルへの blind A が
                # 2,000 回以上拒否され続けた。カーソルが読めないときは確定せず、
                # 最上行へ戻す有界な up で再観測し、尽きれば保留する。fierce は
                # 既存通り未読カーソルを動かさない。
                if fierce or battle.get('egg_attack_label') == 'もうこうげき':
                    return []
                nudges = battle.get('egg_attack_unread_ups') or 0
                if type(nudges) is int and nudges < EGG_ATTACK_UNREAD_UP_LIMIT:
                    battle['egg_attack_unread_ups'] = nudges + 1
                    _record(mem, 'battle_egg_attack_unread_cursor',
                            **_battle_labels(battle),
                            observed_metric={'command': label, 'visible': sorted(visible),
                                             'up_attempt': nudges + 1},
                            reason='コマンド行とカーソルが未読のままなので、未知の行を確定せず上へ戻して再観測する')
                    return [pad('up')]
                if nudges == EGG_ATTACK_UNREAD_UP_LIMIT:
                    _record(mem, 'battle_egg_attack_unread_cursor_held',
                            **_battle_labels(battle),
                            observed_metric={'command': label, 'visible': sorted(visible),
                                             'up_attempts': nudges},
                            reason='有界な上戻しでもコマンド行・カーソルを再観測できないため、未知の行を確定せず入力を保留する')
                return []
            battle.pop('egg_attack_unread_ups', None)
            if (label not in visible
                    and visible.intersection({'もうこうげき', 'おくのて'})):
                # おくのて探索で箱がスクロールしたままなら、上へ戻して
                # こうげき行を再観測する (見えない行をAで確定しない)。
                return [pad('up')]
            if empty_egg:
                return [pad('b')]  # never confirm the empty egg at an unread cursor
            # Never confirm a previously selected fierce row blindly.
            return [] if fierce or battle.get('egg_attack_label') == 'もうこうげき' else [pad('a')]
        if battle.get('egg_attack_label') != label:
            battle['egg_attack_label'] = label
            note_boss_monster_hp(mem, battle, enemy_rows)
            _record(mem, 'battle_egg_general_attack', **_battle_labels(battle),
                    observed_metric={'command': label, 'general_hp': battle.get('ally_hp'),
                                     'max_hp': general_reading[1] if general_reading else None,
                                     'enemy_monster': enemy_rows[0].name if len(enemy_rows) == 1 else None,
                                     'enemy_hp': enemy_rows[0].hp if len(enemy_rows) == 1 else None},
                    reason=('卵・予定札がなく退却もできない負傷将軍は、実HPに基づきもうこうげきで応戦する'
                            if fierce else '実コマンド位置を確認して通常攻撃へ戻す'))
        if move == 'here' and label == 'こうげき':
            # File only after the attack row is actually selected. An unread
            # cursor or an in-progress walk is not evidence that the card was
            # skipped in favour of an attack.
            missed = _carried_card_left(mem, battle)
            # docich#5217: once this battle has spent a card, the next one is
            # not evidence that the bot skipped cards in favour of an attack.
            # The audit's card_miss signal is "attacked without opening any of
            # the carried cards"; filing after a real use only produces
            # duplicates (live: ミックミー used, ノリウツール left, 3 issues).
            if (missed and cards_left and not battle.get('cards_used')
                    and not battle.get('boss_card_missed_noted')):
                battle['boss_card_missed_noted'] = True
                parked_flow = battle.get('card_flow')
                _record(mem, 'battle_boss_card_missed', **_battle_labels(battle), card=missed,
                        # The month audit projects the battle identity from
                        # these top-level fields (#5398 was filed as
                        # 戦闘: 不明 / 城: 不明 / 画面: None, which left the
                        # summon-vs-boss and attack-vs-defence question open).
                        ally=battle.get('ally'), enemy=battle.get('enemy'),
                        castle=battle.get('castle'), side=battle.get('side'),
                        screen_kind=screen.kind,
                        observed_metric={'cards_left': list(cards_left),
                                         'screen': screen.kind,
                                         'text': (screen.text or '')[:80],
                                         'scrolls': battle.get('summon_card_scroll'),
                                         'card_flow': (
                                             {'card': parked_flow.get('card'),
                                              'stage': parked_flow.get('stage'),
                                              'selection_planned': bool(
                                                  parked_flow.get('selection_planned'))}
                                             if isinstance(parked_flow, dict) else None),
                                         'summon_egg_unavailable': bool(
                                             battle.get('summon_egg_unavailable')),
                                         'empty_egg': bool(empty_egg),
                                         'enemy_summon_observed': bool(
                                             battle.get('enemy_summon_observed')
                                             or battle.get('card_summon_observed')),
                                         'cursor': screen.menu_cursor},
                        reason='敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ')
        return [pad('a')] if move == 'here' else _emit_moves(move)
    _egg_recheck(mem)
    if screen.hand:
        move = menu_to(screen, 'たまごをつかう')
        if move == 'here':
            return [pad('a')]
        if move:
            return _emit_moves(move)
        # Hand present but the label is unreadable: keep moving like no-hand.
    # g1190 2026-11-18: the defence summon box (mixed_item_box) parses its
    # command rows into menu_rows and leaves the knight on the grey きりふだ
    # row. Walk from that measured cursor to the egg row and confirm only when
    # actually there; an unreadable cursor/target is held, never blind-A'd.
    # A normal egg battle box keeps its reviewed down/down walk.
    if screen.mixed_item_box:
        move = _command_rows_to(screen, 'たまごをつかう')
        if move == 'here':
            return [pad('a')]
        return _emit_moves(move)
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
# docich#5592: total skill choices per battle before closing the menu.
MONSTER_MENU_TOTAL_CHOICES = 40
# Own turns one skill may spend while the enemy panel HP never moves. g1075
# 2026-10-29 13:37-13:57: a defending おめでとり used ミカンをおす 30 times,
# its HP 1000 -> 603, while ラズベリー stayed at 45 HP; あたためる was never tried.
# With 4 turns (v289) the switch came at 14:13, ~10 minutes and 190 HP later,
# and one あたためる took ラズベリー from 45 to 0.
MONSTER_STALL_TURNS = 2
# A summon whose two verified skills cannot damage this opponent gets one own
# turn per skill instead of the comparison budget. The attack table already
# rules out a damage difference, so a second turn of either skill only spends
# HP: g1153 2026-11-08 17:45-17:47 とうめいにんげん (all zero rows) kept ゼウス
# at 85 HP across seven own turns while falling 44 -> 1.
MONSTER_ATTACKLESS_TURNS = 1
# A menu redraw re-decides the same turn on the next observation (14:13:28
# and :29 both chose あたためる); decisions this close count as one turn.
MONSTER_STALL_SAME_TURN_TICKS = 3
# Total own turns a non-attackless stall may run before the summon returns.
# The stall count needs 2*MONSTER_STALL_TURNS turns to prove neither skill
# moves the enemy panel; two further turns are grace for a low hit rate.
# g1190-ch1v4 2026-11-20 (docich#4738): サマカンテ vs 将軍リーキ kept ホット
# ないちげき for 7+ own turns at enemy HP 30 while falling 614 -> 534 -> dead;
# keeping the choice forever only delays the general's exposure that a lost
# summon forces anyway, so the stall returns once ``return_risk`` allows it.
MONSTER_STALL_RETURN_TURNS = 6
# A summon fight's command box hides きりふだ below たまごをつかう; one or two
# downs reveal it.  Bound the scroll so an unreadable box cannot loop.
SUMMON_CARD_SCROLL_LIMIT = 3
# The reveal scroll is exactly deep enough to reach the row, so one press
# lost to a half-drawn frame forfeits a carried card for the whole battle
# (card_miss #5600: scrolls=3, the row never read).  The same failure class
# already re-arms the おくのて scroll; give the box a bounded second look
# after a gap.  A genuinely dead (grey) row simply re-exhausts and the turn
# goes back to attacking -- the looks are bounded either way.
SUMMON_CARD_REARM_TICKS = 8       # observations (~12 s) before one more look
SUMMON_CARD_REARM_LIMIT = 2       # extra bounded looks per battle
CARD_RETREAT_CANCEL_LIMIT = 2
SUMMON_CARD_OPEN_LIMIT = 2      # reopen attempts per battle before giving up   # cancels before the carried card is abandoned
# egg_battle_menu observations a selected-but-unconfirmed card flow may sit
# frozen before it is booked unclassified and released.  The flow can only
# advance on a card_list screen, so attacking while it is parked would skip
# the remaining carried cards (card_miss #5612).  The bound only rides out a
# misread frame; the attack is held rather than confirmed during the look.
SUMMON_CARD_FLOW_STALL_LIMIT = 3
# Owner 2026-11-22: do not return a summon that is finishing a nearly-dead
# enemy (or is far ahead in HP).  The bounded stall was returning at 6 turns
# even at enemy HP 27 with the summon at 515, which looked like throwing a
# winning fight away.  Keep pressing while either condition holds.
MONSTER_FINISH_ENEMY_HP = 30
MONSTER_FINISH_HP_RATIO = 3


def _monster_stall_switch(mem, owner, ally, enemy, action, skill_count, *, attackless=False,
                          return_risk=None):
    """Try the other skill once the chosen one has not moved the enemy's HP.

    Counts our own decided turns per skill while the enemy panel HP stays the
    same for this ally/enemy pair; any HP change starts over. When both skills
    have spent their turns the choice is kept while ``return_risk`` shields a
    measured wounded/weak general; otherwise the stall is bounded by
    MONSTER_STALL_RETURN_TURNS total own turns and the summon then returns,
    because a summon that cannot move the enemy panel is only spending its own
    HP and the general's exposure is forced once it falls. ``attackless``
    marks a menu whose verified table rows are both zero for this opponent:
    there the comparison budget shrinks to one turn per skill and the menu
    then returns the summon instead of repeating commands that cannot damage
    the enemy.
    """
    if owner != 'ally' or ally is None or enemy is None or skill_count < 2:
        return action
    if action not in ('skill1', 'skill2') or type(enemy.hp) is not int:
        return action
    scope = [ally.name, enemy.name]
    stall = mem.get('monster_stall')
    if not isinstance(stall, dict) or stall.get('scope') != scope or stall.get('enemy_hp') != enemy.hp:
        stall = mem['monster_stall'] = {'scope': scope, 'enemy_hp': enemy.hp, 'turns': {}}
    turns = stall['turns']
    finishing = (type(ally.hp) is int and type(enemy.hp) is int and enemy.hp > 0
                 and (enemy.hp <= MONSTER_FINISH_ENEMY_HP
                      or ally.hp >= enemy.hp * MONSTER_FINISH_HP_RATIO))
    tick = mem.get('tick')
    if (type(tick) is int and type(stall.get('tick')) is int and stall.get('last') in ('skill1', 'skill2')
            and 0 <= tick - stall['tick'] <= MONSTER_STALL_SAME_TURN_TICKS):
        return stall['last']                  # the same turn, redrawn: keep its choice
    needed = MONSTER_ATTACKLESS_TURNS if attackless else MONSTER_STALL_TURNS
    other = 'skill2' if action == 'skill1' else 'skill1'
    if turns.get(action, 0) >= needed:
        if turns.get(other, 0) < needed:
            if not turns.get(other):
                _record(mem, 'monster_skill_stall', strategy_variant=f'monster_stall_{other}',
                        observed_metric={'ally': ally.name, 'enemy': enemy.name, 'enemy_hp': enemy.hp,
                                         'turns': dict(turns), 'from': action, 'to': other,
                                         'attackless': attackless},
                        reason=f'{needed}ターン使っても敵のHPが変わらないため、もう一方の技を試す')
            action = other
        elif attackless:
            if not stall.get('returned'):
                stall['returned'] = True
                _record(mem, 'monster_skill_stall', strategy_variant='monster_stall_return',
                        observed_metric={'ally': ally.name, 'enemy': enemy.name, 'enemy_hp': enemy.hp,
                                         'turns': dict(turns), 'attackless': True},
                        reason='両技とも攻撃性能がなく敵のHPが動かないため、無効な攻撃を繰り返さず召喚獣を戻す')
            # Latch 'retreat' so a same-turn redraw cannot resurrect the skill.
            stall['last'], stall['tick'] = 'retreat', tick
            return 'retreat'
        elif finishing:
            if not stall.get('finishing_keep'):
                stall['finishing_keep'] = True
                _record(mem, 'monster_skill_stall',
                        strategy_variant='monster_stall_finishing_keep',
                        observed_metric={'ally': ally.name, 'enemy': enemy.name,
                                         'ally_hp': ally.hp, 'enemy_hp': enemy.hp,
                                         'turns': dict(turns)},
                        reason='敵HPが残りわずか／味方が優勢のため、召喚獣を戻さず押し切る')
        elif (not return_risk
                and turns.get('skill1', 0) + turns.get('skill2', 0) >= MONSTER_STALL_RETURN_TURNS):
            if not stall.get('returned'):
                stall['returned'] = 'bounded'
                _record(mem, 'monster_skill_stall', strategy_variant='monster_stall_bounded_return',
                        observed_metric={'ally': ally.name, 'enemy': enemy.name, 'enemy_hp': enemy.hp,
                                         'turns': dict(turns)},
                        reason=f'両技とも{MONSTER_STALL_RETURN_TURNS}ターン敵のHPを動かせないため、'
                               'これ以上削られる前に召喚獣を戻す')
            stall['last'], stall['tick'] = 'retreat', tick
            return 'retreat'
        elif not stall.get('recorded'):
            stall['recorded'] = True
            _record(mem, 'monster_skill_stall', strategy_variant='monster_stall_both',
                    observed_metric={'ally': ally.name, 'enemy': enemy.name, 'enemy_hp': enemy.hp,
                                     'turns': dict(turns)},
                    reason='両方の技で敵のHPが変わらないが、召喚獣を戻すと将軍が出るため選択を維持する')
    stall['last'], stall['tick'] = action, tick
    turns[action] = turns.get(action, 0) + 1
    return action


LAMPKIN_PERIOD = 4        # かぼちゃ once, then へんなおどり x3, repeated (owner 2026-11-02)
LAMPKIN_SEND_WINDOW = 4   # observations in which one confirm is still the same turn


def lampkin_cycle_choice(mem, enemy):
    """The cycle's skill for this turn: skill2 (かぼちゃ) on 0, 4, 8..., else skill1.

    The count advances only when a command is sent (``lampkin_command_selected``),
    so a menu that flickers or is redrawn between the decision and the press
    cannot skip ahead. For a few observations after a send the sent skill stays
    latched, so a menu still on screen does not change the pending turn.
    """
    name = enemy.name if enemy is not None else None
    cyc = mem.get('lampkin_cycle')
    if not isinstance(cyc, dict) or cyc.get('enemy') != name:
        cyc = mem['lampkin_cycle'] = {'enemy': name, 'n': 0}   # a new opponent restarts it
    tick, sent = int(mem.get('tick') or 0), cyc.get('sent_tick')
    if type(sent) is int and 0 <= tick - sent < LAMPKIN_SEND_WINDOW and cyc.get('latched'):
        return cyc['latched']
    return 'skill2' if cyc.get('n', 0) % LAMPKIN_PERIOD == 0 else 'skill1'


def lampkin_command_selected(mem, enemy, action):
    cyc = mem.get('lampkin_cycle')
    if not isinstance(cyc, dict) or cyc.get('enemy') != (enemy.name if enemy is not None else None):
        return
    tick, sent = int(mem.get('tick') or 0), cyc.get('sent_tick')
    if (type(sent) is int and 0 <= tick - sent < LAMPKIN_SEND_WINDOW
            and cyc.get('latched') == action):
        return                                    # the same confirm, seen again
    cyc.update(n=int(cyc.get('n') or 0) + 1, sent_tick=tick, latched=action)


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
    # docich#5592 (owner 2026-11-25): a whole month was spent re-choosing in
    # this menu (903 choices / 180 s, zero sorties). Keep the per-skill gates,
    # but also cap the total choices per battle: past the cap, close the menu
    # (もどれ) so the battle and the month can progress. Bounded safety valve.
    cur_battle = mem.get('battle')
    if not isinstance(cur_battle, dict):
        cur_battle = None
    choices = 0
    if cur_battle is not None:
        try:
            choices = int(cur_battle.get('monster_menu_choices') or 0)
        except (TypeError, ValueError):
            choices = 0
    if choices > MONSTER_MENU_TOTAL_CHOICES:
        if not cur_battle.get('monster_menu_total_cap_logged'):
            cur_battle['monster_menu_total_cap_logged'] = True
            _record(mem, 'monster_menu_total_cap',
                    observed_metric={'choices': choices,
                                     'limit': MONSTER_MENU_TOTAL_CHOICES},
                    reason='召喚獣メニューの選択回数が上限を超えたため、メニューを閉じて戦闘を進める')
        return [pad('b')]
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
        if enemy.name in reference.MONSTER_SKILLS:
            # A normal battle reading resets card_summon_observed every frame;
            # latch the enemy-side summon so the okunote preempt survives the
            # melee that follows (owner 2026-11-02).
            mem['battle']['enemy_summon_observed'] = True
    cur = mem.get('battle')
    if isinstance(cur, dict) and enemy is not None:
        opening_scope = cur.get('melee_opening_scope')
        opening_enemy = opening_scope[2] if isinstance(opening_scope, (list, tuple)) and len(opening_scope) >= 5 else None
        opening_enemy_hp = opening_scope[4] if isinstance(opening_scope, (list, tuple)) and len(opening_scope) >= 5 else None
        transformed = False
        if isinstance(enemy.name, str) and isinstance(opening_enemy, str) and enemy.name != opening_enemy:
            transformed = True
        if (type(enemy.hp) is int and type(opening_enemy_hp) is int
                and enemy.hp > opening_enemy_hp):
            transformed = True
        if any('たまごにもどれ' in str(t) for t in row_texts):
            transformed = True
        if transformed and not cur.get('enemy_transform_recorded'):
            cur['enemy_transform_recorded'] = True
            cur['enemy_transform_observed'] = True
            try:
                _record(mem, 'battle_enemy_transform_observed', **_battle_labels(cur),
                        enemy=enemy.name, enemy_hp=enemy.hp,
                        observed_metric={'opening_enemy': opening_enemy,
                                        'opening_enemy_hp': opening_enemy_hp,
                                        'enemy': enemy.name, 'enemy_hp': enemy.hp,
                                        'transform_skill': any('たまごにもどれ' in str(t) for t in row_texts)},
                        reason='敵が卵を使用して変身したため開幕の卵温存判断を無効化する')
                if cur.get('egg_drop_decision_recorded') and not cur.get('egg_drop_enemy_use_recorded'):
                    cur['egg_drop_enemy_use_recorded'] = True
                    _egg_drop_trace(mem, cur, 'enemy_egg_used',
                                    transform_skill=any('たまごにもどれ' in str(t) for t in row_texts))
            except Exception:
                pass
            rescue = cur.get('survival')
            if isinstance(rescue, dict):
                rescue.pop('conserving_melee', None)
                rescue.pop('conserved_labels', None)
                rescue.pop('conserved_army_recheck', None)
    owner = _monster_owner(skill_lines, ally, enemy)
    _summon_scene(mem)
    if owner == 'ally':
        mem.pop('monster_unchosen', None)       # our own turn: this step decides it
    else:
        count_unchosen_monster_turn(mem)
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
    # docich#4716: a skill whose predicted damage is high but whose real effect
    # is zero (an immune enemy) was chosen turn after turn: the re-decide rule
    # only fired when the panel HP changed, and it never did. Track the turns a
    # chosen skill left the enemy HP untouched and drop it after two of them,
    # so the other slot gets the turn while the reference table still gates it.
    no_damage = mem.setdefault('monster_no_damage', {})
    prev_choice = mem.get('monster_menu_choice')
    if (owner == 'ally' and prev_choice in ('skill1', 'skill2')
            and mem.get('monster_menu_choice_hp') == hp_state):
        no_damage[prev_choice] = int(no_damage.get(prev_choice) or 0) + 1
        _record(mem, 'monster_skill_no_damage',
                observed_metric={'skill': prev_choice, 'enemy': enemy.name if enemy else None,
                                 'enemy_hp': enemy.hp if enemy else None,
                                 'count': no_damage[prev_choice]},
                reason='同じ技で敵HPが動かないため、この技の実効なしを数える')
    elif isinstance(prev_choice, str):
        no_damage.pop(prev_choice, None)
    if len(skill_lines) >= 2:
        if int(no_damage.get('skill1') or 0) >= 2:
            damage_first = False
        if int(no_damage.get('skill2') or 0) >= 2:
            damage_second = False
    # #3071: ちきゅうちゃん self-destructs only into a lone general.
    chikyu = _monster_chikyu_choice(
        owner, ally, enemy, [''.join(line.known.split()) for line in skill_lines])
    lampkin_skills = [''.join(line.known.split()) for line in skill_lines]
    lampkin_forced = _monster_lampkin_choice(owner, ally, enemy, lampkin_skills)
    # Owner 2026-11-04: 相手もランプキンなら「へんなおどり」だけを選ぶ。
    # 同名同士では owner が曖昧（両側のスキルが一致）でも、味方パネルが
    # ランプキンならこの戦闘のメニューはこちらなので同じ規則を使う。
    lampkin_mirror = bool(_lampkin_menu('ally', ally, lampkin_skills)
                          and enemy is not None and enemy.name == 'ランプキン')
    if lampkin_mirror:
        lampkin_forced = 'skill1'
    lampkin_cycle = (_lampkin_menu(owner, ally, lampkin_skills) and lampkin_forced is None)
    lampkin = lampkin_forced or (lampkin_cycle_choice(mem, enemy) if lampkin_cycle else None)
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
    omedetori_cycle = (owner == 'ally' and ally is not None
                       and ally.name == 'おめでとり' and len(skill_lines) == 2
                       and (_fold_skill(skill_lines[0].known) == _fold_skill('ミカンをおす')
                            or _fold_skill(skill_lines[1].known) == _fold_skill('あたためる')))
    akematsu = akematsu_assessment(mem, ally, enemy) if akematsu_cycle else None
    omedetori = omedetori_assessment(mem, ally, enemy) if omedetori_cycle else None
    special_cycle = balloon_cycle or akematsu_cycle or omedetori_cycle
    # A live deployment can change a rule while the menu HP stays unchanged.
    # Re-evaluate old cached choices on the first new-policy observation.
    special_epoch = ('20261029-omedetori-v1' if omedetori_cycle
                     else '20261124-balloon-final-jelly-v1' if balloon_cycle
                     else '20261016-balloon-akematsu-v2')
    if (not mem.get('monster_menu_choice') or mem.get('monster_menu_choice_hp') != hp_state
            or (special_cycle and mem.get('monster_menu_choice_policy_epoch') != special_epoch)
            or (akematsu_cycle and mem.get('monster_menu_choice') != akematsu['action'])
            or (omedetori_cycle and mem.get('monster_menu_choice') != omedetori['action'])
            or mem.get('monster_menu_choice') == 'retreat'
            or (damage_second and mem.get('monster_menu_choice') not in ('skill2', 'retreat'))
            or (damage_first and mem.get('monster_menu_choice') not in ('skill1', 'retreat'))
            or (chikyu and mem.get('monster_menu_choice') not in (chikyu, 'retreat'))
            or (lampkin and mem.get('monster_menu_choice') not in (lampkin, 'retreat'))
            or int(no_damage.get(mem.get('monster_menu_choice')) or 0) >= 2):
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
        # Data-backed twin of ``powerless``: the transcribed attack rows for
        # this owned menu are both zero against the current opponent. The menu
        # still tries each skill once, then returns instead of repeating a
        # command that cannot move the enemy panel (g1153 とうめいにんげん).
        attackless = _monster_menu_attackless(owner, ally, enemy, lampkin_skills)
        heal_first = bool(second) and _monster_heal(first) and not _monster_heal(second)
        default = 'skill1'
        if damage_first:
            default = 'skill1'
        elif damage_second:
            default = 'skill2'
        elif chikyu:
            default = chikyu
        elif lampkin:
            default = lampkin
        elif balloon_cycle:
            # Measured hits use a three-turn reserve; absent measurements,
            # a conservative explicitly-unproven reserve prevents 9999 loops.
            balloon = balloon_assessment(mem, ally, enemy, BALLOON_FINCH_MAX_HP)
            default = balloon['action']
        elif akematsu_cycle:
            # Switching abilities a second time undoes the first exchange.
            # After issuing one observed ごあいさつ, summon with おもてなし.
            default = akematsu['action']
        elif omedetori_cycle:
            # One ミカンをおす, then the self-destruct あたためる at the next decision.
            default = omedetori['action']
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
        # Issue #2835: opponent-aware attack command selection.
        # Gate rationale:
        #   - special_cycle/damage_first/damage_second/powerless/heal_first are
        #     already-resolved tactics that must not be overridden.
        #   - `default == 'skill1'`: only intervene when the generic default
        #     is the first skill; if experience/owner already chose skill2
        #     (effect-only second skill), keep that conservative choice.
        #   - `not behind`: when the summoner is behind, keep the existing
        #     behind-policy (skill2 effect) rather than the opponent-aware
        #     attack pick, matching policy 1-3 which assume a non-behind
        #     position for the summoned monster's own attack.
        #   - `not heal_first` keeps heal-first monsters on their recovery path.
        policy_evidence = None
        if (not special_cycle and not damage_first and not damage_second and not chikyu
                and not lampkin and not powerless and len(skill_lines) >= 2
                and default == 'skill1' and not heal_first and not behind):
            policy_evidence = {}
            policy_default = _monster_attack_policy_default(
                owner, ally, enemy, skill_lines, first, second, ally_hp, enemy_hp, mem,
                evidence=policy_evidence)
            if policy_default is not None:
                default = policy_default
            if not policy_evidence:
                policy_evidence = None
        # Owner 2026-11-03: ランプキンは自分のHPが一桁になったら、倒される
        # 前に「たまごにもどる」で帰還する。攻撃継続より卵へ戻す判断を優先し、
        # ボス/HP1の強制スキル選択より先に適用する。
        # Owner 2026-11-04: the enemy can be the same monster (ランプキン),
        # which makes _monster_owner ambiguous (both skill sets match) and
        # left the retreat unselected at HP 1/3/6. The ally panel is our
        # monster, so take the return from the ally Lampkin menu regardless.
        lampkin_return = bool(_lampkin_menu('ally', ally, lampkin_skills)
                              and type(ally_hp) is int and 0 < ally_hp <= 9)
        exp = mem.get('_experience')
        key = experience.situation_key('monster_menu', mem)
        # Special monster owner tactics must not be overridden by generic
        # retreat/experience: Balloon Finch survival and Akematsu one-shot swap.
        action = ('retreat' if lampkin_return else
                  default if special_cycle else
                  'retreat' if powerless else 'skill1' if damage_first else 'skill2' if damage_second
                  else chikyu or lampkin or experience.preferred(exp, key, default=default, kind='monster_menu'))
        # HP pools alone do not establish that this monster cannot win: a
        # living summon can attack, heal or disable the opponent. Returning
        # also exposes its general. Discard saved/learned HP-only retreats;
        # retain the measured two-useless-skills and Lampkin low-HP returns.
        if action == 'retreat' and not (powerless or lampkin_return):
            action = default
        if not special_cycle and not damage_first and not damage_second and not chikyu and not lampkin:
            action = _monster_stall_switch(mem, owner, ally, enemy, action, len(skill_lines),
                                           attackless=attackless, return_risk=return_risk)
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
            try:
                battle['monster_menu_choices'] = int(battle.get('monster_menu_choices') or 0) + 1
            except (TypeError, ValueError):
                battle['monster_menu_choices'] = 1
            battle['independent'] = {'kind': 'monster_menu', 'key': key, 'action': action}
        if action == 'retreat' and lampkin_return:
            label = 'たまごに もどれ'
            why = (f'HP{ally_hp}が一桁のため、倒される前に「たまごにもどる」で'
                   '帰還する（オーナー 2026-11-03）')
        elif action == 'retreat':
            label = 'たまごに もどれ'
            returned = (mem.get('monster_stall') or {}).get('returned')
            why = ('両技とも攻撃性能がなく敵のHPも動かないため、無効な攻撃を繰り返さず戻す'
                   if returned is True else
                   f'両技で{MONSTER_STALL_RETURN_TURNS}ターン敵のHPを動かせないため、'
                   'これ以上削られる前に戻す'
                   if returned == 'bounded' else
                   '両技に攻撃性能がない召喚獣のため、無効な攻撃を繰り返さず戻す')
        elif balloon_cycle:
            label = second if action == 'skill2' else first
            if balloon['rule'] == 'final_jelly_shout':
                why = ('ファイナルゼリー戦でHP1000以上に達したため、以降も'
                       '「シャウト」を続けて相手を削り切る（オーナー 2026-11-24）')
            elif balloon['rule'] == 'final_jelly_inflate':
                why = (f'ファイナルゼリー戦でシャウトへ移るため、HP{ally_hp}を'
                       f'目標{balloon["target_hp"]}以上へ「ふくらむ」で引き上げる'
                       '（オーナー 2026-11-24）')
            elif balloon['rule'] == 'survival_buffer':
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
        elif omedetori_cycle:
            label = second if action == 'skill2' else first
            if action == 'skill1':
                why = '「ミカンをおす」はこの戦闘で1回だけ送信し、その後は「あたためる」へ進む'
            elif omedetori['rule'] == 'unscoped_mikan_hold':
                why = '戦闘の同一性が読めないため自爆の「あたためる」を選ばず「ミカンをおす」で待つ'
            elif omedetori['rule'] == 'warm_after_unverified_attempt':
                why = ('カーソル未読で「ミカンをおす」を1回試みたため、送信成功は未確認だが'
                       '同じ技の無限試行を防ぎ「あたためる」に切り替える')
            else:
                why = '「ミカンをおす」を1回送信済みのため、次の選択で自爆の「あたためる」を選ぶ'
        elif lampkin:
            label = second if action == 'skill2' else first
            kind = _lampkin_forced_kind(enemy)
            if lampkin_mirror:
                label = first
                why = '同名のランプキン戦は「へんなおどり」だけを選ぶ（オーナー 2026-11-04）'
            elif kind == 'boss':
                why = 'ボスモンスターに「かぼちゃ」はきかないため、つねに「へんなおどり」を選ぶ（オーナー 2026-11-02）'
            elif kind == 'hp1':
                why = '相手のHPが1でそのまま押し切れるため、「かぼちゃ」を使わず「へんなおどり」を選ぶ（オーナー 2026-11-02）'
            else:
                n = int((mem.get('lampkin_cycle') or {}).get('n') or 0)
                why = (f'「かぼちゃ」1回→「へんなおどり」{LAMPKIN_PERIOD - 1}回の繰り返し'
                       f'（{n % LAMPKIN_PERIOD + 1}/{LAMPKIN_PERIOD}ターン目、オーナー 2026-11-02）')
        elif chikyu:
            label = second if action == 'skill2' else first
            why = ('自爆は将軍と兵士を確定で全滅させるため、敵将軍だけの今ハルマゲドンで決着させる'
                   if action == 'skill2' else
                   '自爆は召喚獣に自分の残りHP分・ボスに最大HPの1/8しか効かないため、だいじしんで敵を半減し続ける')
        elif (policy_evidence and policy_evidence.get('choice') == action
              and action in ('skill1', 'skill2')):
            label = second if action == 'skill2' else first
            kind = ('召喚獣' if policy_evidence.get('opponent_kind') == 'monster' else '将軍')
            why = (f"攻撃データ上、{kind}「{policy_evidence.get('opponent')}」への実効ダメージが"
                   f"{policy_evidence.get('score1')}対{policy_evidence.get('score2')}のため"
                   f"「{label}」を選ぶ")
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
        stalled = (mem.get('monster_stall') or {}).get('turns') or {}
        if (not special_cycle and action != default and action != 'retreat'
                and stalled.get(default, 0) >= MONSTER_STALL_TURNS):
            label = first if action == 'skill1' else second
            why = f'{MONSTER_STALL_TURNS}ターン使っても敵のHPが変わらない技の代わりに試す（既定 {default}）'
        elif not special_cycle and action != default and action != 'retreat':
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
                                 'omedetori_cycle': omedetori if omedetori_cycle else None,
                                 'damage_first': damage_first,
                                 'chikyu_choice': chikyu,
                                 'lampkin_choice': lampkin,
                                 'lampkin_return': lampkin_return,
                                 'wounded_general_return_blocked': bool(protected_return
                                     and return_risk['rule'] == 'wounded_general'),
                                 'general_return_blocked': protected_return,
                                 'general_return_risk': return_risk,
                                 'monster_attack_policy': policy_evidence,
                                 'experience_key': key},
                reason=f'召喚獣の技メニューで「{label}」を選択: {why}')
    action = mem.get('monster_menu_choice')
    if owner == 'ally':
        mem['monster_own_choice'] = True
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
    if target > cur + 4 or target < cur - 4:
        row_ys = [line.y for line in rows]
        # A readable contiguous column can be pressed through to the skill row
        # in one run (2 rows to ファバードもどき = ↓×2+A in one observation).
        # An unreadable cursor is only a predicted position, so it keeps one
        # step per observation and a drifted guess still self-corrects on the
        # next real read.
        if type(screen.menu_cursor) is int and screen.menu_cursor in row_ys:
            count = target_index - row_ys.index(screen.menu_cursor)
            if abs(count) > 1:
                direction = 'down' if count > 0 else 'up'
                count = min(abs(count), MENU_MOVE_BATCH_MAX)
                mem['monster_menu_cursor'] = target
                acts = [pad(direction)]
                for _ in range(count - 1):
                    acts.extend([{'type': 'wait', 'ms': MENU_MOVE_RELEASE_MS}, pad(direction)])
                return acts
        mem['monster_menu_cursor'] = cur + 16 if target > cur else cur - 16
        return [pad('down' if target > cur else 'up')]
    if screen.menu_cursor == target and balloon_cycle:
        balloon_command_selected(mem, ally, enemy, action, BALLOON_FINCH_MAX_HP)
    if akematsu_cycle and action == 'skill1':
        # The cursor may remain unreadable for the entire menu. This is only
        # an attempted input, never evidence of a successful stat exchange.
        # Nevertheless, sending ごあいさつ on every frame is worse.
        akematsu_command_selected(
            mem, ally, enemy, action,
            cursor_confirmed=(screen.menu_cursor == target))
    if omedetori_cycle and action == 'skill1':
        # Same contract as ごあいさつ: a predicted cursor is only an attempt.
        omedetori_command_selected(
            mem, ally, enemy, action,
            cursor_confirmed=(screen.menu_cursor == target))
    if lampkin_cycle and action in ('skill1', 'skill2'):
        lampkin_command_selected(mem, enemy, action)
    return [pad('a')]
