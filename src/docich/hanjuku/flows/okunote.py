"""Receipts for one irreversible okunote choice, followed by a combat return."""
from ..os_layer.roster import run_identity
from ...hanjuku_egg_reference import general_max_hp
from ...hanjuku_reference import MONSTER_SKILLS


def _scope(mem, cur):
    return [run_identity(mem.get('_run_identity')), mem.get('chapter'),
            *[cur.get(key) for key in ('ally', 'enemy', 'start_ally_hp', 'start_enemy_hp',
                                      'side', 'castle', 'step')]]


# A chosen candidate whose list stays on screen (its A did not land): waits
# before the first close, B presses to close it, observations between
# them, and the total budget for a stale attempt from another battle.
CANDIDATE_MENU_WAITS = 6
CANDIDATE_CLOSES = 2
CANDIDATE_CLOSE_GAP = 2
STALE_ATTEMPT_WAITS = 8


def menu_pending(screen, mem, cur):
    attempt = cur.get('okunote_attempt')
    if attempt is None:
        return None
    from ..os_layer.commands import pad
    if (not isinstance(attempt, dict) or attempt.get('scope') != _scope(mem, cur)
            or attempt.get('phase') not in ('parent_selected', 'candidate_selected')):
        if screen.kind == 'battle_menu':
            return [pad('b')]
        waits = int(cur.get('okunote_stale_waits') or 0) + 1
        cur['okunote_stale_waits'] = waits
        if waits <= STALE_ATTEMPT_WAITS:
            return [{'type': 'wait', 'ms': 50}]
        cur.pop('okunote_attempt', None)
        cur.pop('okunote_stale_waits', None)
        return None                      # bounded: the ordinary menu logic decides
    if screen.kind == 'battle_menu':
        # g1097 23:06: 親メニュー (きりふだ/たいきゃく/おくのて) が残ったまま —
        # 前の A は届かず B はこの箱では無効 (~200回の無記録ループ)。おくのて
        # 行が見えるなら「再抽選」ではなく未受付の再送。カーソルを確認して
        # 有界に送り直し、尽きたら通常判定へ戻す。
        _rows = screen.menu_rows or screen.lines
        if attempt.get('phase') == 'parent_selected' and any(
                w == 'おくのて' for r in _rows for _, w in r.spans()):
            resends = int(attempt.get('resends') or 0)
            if resends < 4:
                attempt['resends'] = resends + 1
                from ..os_layer.commands import _battle_menu_to
                move = _battle_menu_to(screen, 'おくのて')
                if move == 'here':
                    return [pad('a')]
                if move is not None:
                    return [move]
                return [pad('down')]
            return None
        return [pad('b')]  # no observed completion, including a repeated parent
    if attempt['phase'] == 'candidate_selected':
        if screen.kind == 'okunote_menu':
            return _candidate_list_still_open(mem, cur, attempt)
        return [{'type': 'wait', 'ms': 50}]  # selection is not an effect/return receipt
    return None


def _candidate_list_still_open(mem, cur, attempt):
    """The candidate list is still open after its A: close it, never stall.

    g1163 2026-11-11 19:10:00: A on ハダカでぶつかる left the same three-row list
    on screen (the A was not taken); every later observation waited for the
    effect, 200 waits in five minutes, until the run ended ``screen_stalled``.
    A landed choice replaces the list with its effect text within about two
    seconds (19:09:17 -> 19:09:19), so a list still unchanged after
    ``CANDIDATE_MENU_WAITS`` observations means the A did not land.

    The candidate A stays irreversible and is not re-sent while B can still
    close the list. Only when the bounded closes leave it open is one last A
    sent (the earlier one evidently never landed); after that B is pressed
    every few observations, so input never stops.
    """
    from ..decision_log import _record
    from ..os_layer.commands import pad
    waits = int(attempt.get('menu_waits') or 0) + 1
    attempt['menu_waits'] = waits
    closes = int(attempt.get('closes') or 0)
    if waits <= CANDIDATE_MENU_WAITS + closes * CANDIDATE_CLOSE_GAP:
        return [{'type': 'wait', 'ms': 50}]
    if closes < CANDIDATE_CLOSES:
        attempt['closes'] = closes + 1
        _record(mem, 'battle_okunote_candidate_close', general=cur.get('ally'),
                observed_metric={'close': closes + 1, 'limit': CANDIDATE_CLOSES, 'waits': waits},
                reason='奥の手の候補を選んだ後も候補一覧が変わらず残るため、Aが届かなかったとしてBで一覧を閉じる')
        return [pad('b')]
    if not attempt.get('last_confirm'):
        attempt['last_confirm'] = True
        _record(mem, 'battle_okunote_candidate_last_confirm', general=cur.get('ally'),
                observed_metric={'closes': closes, 'waits': waits},
                reason='Bでも奥の手の候補一覧が閉じないため、届かなかった選択のAを1回だけ送り直す')
        return [pad('a')]
    if waits % (CANDIDATE_CLOSE_GAP + 1) == 0:
        return [pad('b')]
    return [{'type': 'wait', 'ms': 50}]


def selected(mem, cur, *, candidate):
    cur['okunote_attempt'] = {'scope': _scope(mem, cur),
                              'phase': 'candidate_selected' if candidate else 'parent_selected'}


def candidate_chosen(mem, cur):
    """This battle's okunote candidate was chosen; following text is its effect."""
    attempt = cur.get('okunote_attempt')
    return (isinstance(attempt, dict) and attempt.get('scope') == _scope(mem, cur)
            and attempt.get('phase') == 'candidate_selected')


def summon_reading(screen, mem, cur):
    """Bind our current human HP and a real enemy monster, with distinct pools."""
    identity = run_identity(mem.get('_run_identity'))
    proof = cur.get('summon_general_reading')
    if (identity is None or not isinstance(proof, dict)
            or run_identity(proof.get('identity')) != identity
            or proof.get('ally') != cur.get('ally') or proof.get('enemy') != cur.get('enemy')
            or type(proof.get('hp')) is not int or type(proof.get('max_hp')) is not int
            or not 0 < proof['hp'] <= proof['max_hp'] or len(screen.egg_rows) != 2):
        return None
    ally = [r for r in screen.egg_rows if r.side == 'ally']
    enemy = [r for r in screen.egg_rows if r.side == 'enemy']
    # Match _remember_general_for_summon / _egg_general_reading: a resumed
    # hero uses the observed maximum, which can initially be below 90.
    full = mem.get('hero_max_hp') if cur.get('ally') == 'どうし' else general_max_hp(cur.get('ally'))
    if (len(ally) != 1 or len(enemy) != 1 or ally[0].name != cur.get('ally')
            or type(full) is not int or proof['max_hp'] != full
            or type(ally[0].hp) is not int or not 0 < ally[0].hp <= full
            or enemy[0].name not in MONSTER_SKILLS or type(enemy[0].hp) is not int
            # 召喚獣のHPは4桁表示で、ちきゅうちゃんは9999 (g1139 17:38)。999で切ると
            # 照合が毎回失敗し、おくのて行だけが有効な箱でBを打ち続けて止まった。
            or not 0 < enemy[0].hp <= 9999):
        return None
    if screen.kind == 'battle_menu' and not any(
            r.spans() == [(176, 'おくのて')] for r in screen.menu_rows):
        return None  # another command owner / partial box is not our menu
    return {'ally_hp': ally[0].hp, 'enemy_monster': enemy[0].name,
            'enemy_monster_hp': enemy[0].hp}


def _known_summon_monster_name(name):
    return (isinstance(name, str)
            and (name in MONSTER_SKILLS or general_max_hp(name) is not None))


def summon_monster_panel(screen, cur):
    """Read ally/enemy monster HP when both panel rows are known summons.

    Some summon fights show the ally summon (a general's egg can be a general)
    instead of the commanding general, so summon_reading cannot bind ally_hp.
    Persist only the summons' names/HP and leave the generals' ally_hp/enemy_hp
    unread (0 is not used for "unknown").
    """
    if not isinstance(cur, dict) or len(getattr(screen, 'egg_rows', None) or ()) != 2:
        return None
    ally = [r for r in screen.egg_rows if r.side == 'ally']
    enemy = [r for r in screen.egg_rows if r.side == 'enemy']
    if len(ally) != 1 or len(enemy) != 1:
        return None
    ally_row, enemy_row = ally[0], enemy[0]
    if (not _known_summon_monster_name(ally_row.name)
            or not _known_summon_monster_name(enemy_row.name)
            or ally_row.name == cur.get('ally') or enemy_row.name == cur.get('enemy')
            or type(ally_row.hp) is not int or type(enemy_row.hp) is not int
            or not 0 < ally_row.hp <= 9999 or not 0 < enemy_row.hp <= 9999):
        return None
    cur['summon_ally'] = ally_row.name
    cur['summon_ally_hp'] = ally_row.hp
    cur['summon_enemy'] = enemy_row.name
    cur['summon_enemy_hp'] = enemy_row.hp
    return {'ally_hp': ally_row.hp, 'enemy_monster': enemy_row.name,
            'enemy_monster_hp': enemy_row.hp}


def combat_return(screen, mem, cur, *, summon=None):
    """A complete live panel ends the chosen attempt, without claiming an effect."""
    if not candidate_chosen(mem, cur):
        return
    b = screen.battle
    ordinary = (screen.kind == 'battle' and b is not None
                and (b.ally, b.enemy) == (cur.get('ally'), cur.get('enemy'))
                and all(type(v) is int and v > 0 for v in (b.ally_hp, b.enemy_hp)))
    if not ordinary and not (screen.kind == 'egg_battle_menu' and summon):
        return
    if (ordinary or (screen.kind == 'egg_battle_menu' and summon)) and (
            cur.get('okunote_egg_preempt') or cur.get('okunote_summon_preempt')):
        # The enemy lived through this draw: a preempt may draw again.
        # The egg-route redraw stays bounded by OKUNOTE_REDRAW_LIMIT; the
        # enemy-summon preempt keeps drawing until the summon is gone
        # (owner 2026-11-03).
        cur['okunote_draws'] = int(cur.get('okunote_draws') or 0) + 1
    for key in ('okunote_attempt', 'okunote_flow', 'okunote_egg_preempt',
                'okunote_summon_preempt',
                'okunote_last_resort_selected', 'okunote_last_resort_recorded',
                'okunote_last_resort_reopened'):
        cur.pop(key, None)
    from ..decision_log import _record
    _record(mem, 'battle_okunote_combat_return', general=cur.get('ally'),
            resulting_event='combat_return_observed_effect_unclassified',
            reason='候補選択後に同じ戦闘の実パネルへ復帰。効果や成功は断定せず次回は現在HPとメニューで再判定')
