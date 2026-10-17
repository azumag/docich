"""Receipts for one irreversible okunote choice, followed by a combat return."""
from ..os_layer.roster import run_identity
from ...hanjuku_egg_reference import general_max_hp
from ...hanjuku_reference import MONSTER_SKILLS


def _scope(mem, cur):
    return [run_identity(mem.get('_run_identity')), mem.get('chapter'),
            *[cur.get(key) for key in ('ally', 'enemy', 'start_ally_hp', 'start_enemy_hp',
                                      'side', 'castle', 'step')]]


def menu_pending(screen, mem, cur):
    attempt = cur.get('okunote_attempt')
    if attempt is None:
        return None
    from ..os_layer.commands import pad
    if (not isinstance(attempt, dict) or attempt.get('scope') != _scope(mem, cur)
            or attempt.get('phase') not in ('parent_selected', 'candidate_selected')):
        return [pad('b')] if screen.kind == 'battle_menu' else [{'type': 'wait', 'ms': 50}]
    if screen.kind == 'battle_menu':
        return [pad('b')]  # no observed completion, including a repeated parent
    if attempt['phase'] == 'candidate_selected':
        return [{'type': 'wait', 'ms': 50}]  # selection is not an effect/return receipt
    return None


def selected(mem, cur, *, candidate):
    cur['okunote_attempt'] = {'scope': _scope(mem, cur),
                              'phase': 'candidate_selected' if candidate else 'parent_selected'}


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
    full = general_max_hp('しゅじんこう' if cur.get('ally') == 'どうし' else cur.get('ally'))
    if (len(ally) != 1 or len(enemy) != 1 or ally[0].name != cur.get('ally')
            or type(full) is not int or proof['max_hp'] != full
            or type(ally[0].hp) is not int or not 0 < ally[0].hp <= full
            or enemy[0].name not in MONSTER_SKILLS or type(enemy[0].hp) is not int
            or not 0 < enemy[0].hp <= 999):
        return None
    if screen.kind == 'battle_menu' and not any(
            r.spans() == [(176, 'おくのて')] for r in screen.menu_rows):
        return None  # another command owner / partial box is not our menu
    return {'ally_hp': ally[0].hp, 'enemy_monster': enemy[0].name,
            'enemy_monster_hp': enemy[0].hp}


def combat_return(screen, mem, cur, *, summon=None):
    """A complete live panel ends the chosen attempt, without claiming an effect."""
    attempt = cur.get('okunote_attempt')
    if (not isinstance(attempt, dict) or attempt.get('scope') != _scope(mem, cur)
            or attempt.get('phase') != 'candidate_selected'):
        return
    b = screen.battle
    ordinary = (screen.kind == 'battle' and b is not None
                and (b.ally, b.enemy) == (cur.get('ally'), cur.get('enemy'))
                and all(type(v) is int and v > 0 for v in (b.ally_hp, b.enemy_hp)))
    if not ordinary and not (screen.kind == 'egg_battle_menu' and summon):
        return
    for key in ('okunote_attempt', 'okunote_flow', 'okunote_egg_preempt',
                'okunote_last_resort_selected', 'okunote_last_resort_recorded',
                'okunote_last_resort_reopened'):
        cur.pop(key, None)
    from ..decision_log import _record
    _record(mem, 'battle_okunote_combat_return', general=cur.get('ally'),
            resulting_event='combat_return_observed_effect_unclassified',
            reason='候補選択後に同じ戦闘の実パネルへ復帰。効果や成功は断定せず次回は現在HPとメニューで再判定')
