"""Continue favourable melee, then leave only after a bound enemy summon.

Static egg capability permits a plan, never an available-count/depletion fact.
The live general/enemy-monster panel proves a summon; quantities stay unknown.
"""
from ... import hanjuku_reference as reference
from ...hanjuku_egg_reference import enemy_egg_triggers, general_max_hp
from ..decision_log import _record
from ..os_layer.roster import run_identity
from ..os_layer.world import NAME

RETURN_PROOF_TICKS = 24  # finite B return / command-opening / menu episodes


def _scope(mem, cur):
    identity = run_identity(mem.get('_run_identity'))
    chapter, tick = mem.get('chapter'), mem.get('tick')
    if (identity is None or type(chapter) is not int or chapter <= 0
            or type(tick) is not int or tick < 0
            or cur.get('ally') in (NAME, 'しゅじんこう')
            or any(not isinstance(cur.get(key), str) or general_max_hp(cur[key]) is None
                   for key in ('ally', 'enemy'))):
        return None
    return {'identity': identity, 'chapter': chapter, 'ally': cur.get('ally'),
            'enemy': cur.get('enemy'), 'start_ally_hp': cur.get('start_ally_hp'),
            'start_enemy_hp': cur.get('start_enemy_hp'), 'step': cur.get('step'),
            'castle': cur.get('castle'), 'side': cur.get('side')}


def _active(mem, cur):
    state = cur.get('enemy_egg_bait')
    scope = _scope(mem, cur)
    saved = state.get('scope') if isinstance(state, dict) else None
    if (not isinstance(state, dict) or scope is None or state.get('scope') != scope
            or not isinstance(saved, dict) or run_identity(saved.get('identity')) != scope['identity']
            or any(type(saved.get(key)) is not type(value) for key, value in scope.items())
            or type(state.get('armed_tick')) is not int
            or not 0 <= state['armed_tick'] <= mem['tick']
            or state.get('phase') not in ('waiting', 'summoned', 'cancelled')):
        cur.pop('enemy_egg_bait', None)
        return None
    return state


def cancel(mem, cur, reason):
    state = _active(mem, cur)
    if state and state['phase'] != 'cancelled':
        state['phase'] = 'cancelled'
        _record(mem, 'enemy_egg_bait_cancelled', general=cur.get('ally'),
                resulting_event='ordinary_protection_resumed', reason=reason)


def observe_melee(screen, mem, cur):
    from ..judgment.battle import _own_egg_empty, _pre_summon_hp_advantage
    previous = cur.get('enemy_egg_bait')
    state = _active(mem, cur)
    if previous is not None and state is None:
        return  # discard an old scope before considering a new intention
    if state and state['phase'] == 'cancelled':
        return
    if not _own_egg_empty(mem, cur):
        cancel(mem, cur, '本人の現在卵なしを確認できず召喚待ちを終了する')
        return
    if state and state['phase'] == 'summoned':
        return  # a real return to ordinary commands must still request retreat
    scope = _scope(mem, cur)
    b = screen.battle
    if (scope is None or b is None or (b.ally, b.enemy) != (cur.get('ally'), cur.get('enemy'))
            or not _pre_summon_hp_advantage(cur)
            or enemy_egg_triggers(cur.get('enemy'), player_castle_defense=False).has_egg is not True):
        cancel(mem, cur, '現在の本人HP優勢・敵の卵使用能力を確認できず既存の保護へ戻す')
        return
    if state is None:
        cur['enemy_egg_bait'] = {'scope': scope, 'armed_tick': mem['tick'], 'phase': 'waiting'}
        _record(mem, 'enemy_egg_bait_started', general=cur.get('ally'),
                observed_metric={'enemy': cur.get('enemy'), 'enemy_egg_basis': 'static_capability',
                                 'enemy_remaining_uses': None},
                resulting_event='summon_unconfirmed',
                reason='本人の卵なしとHP優勢を確認し通常白兵を続ける。敵の現在卵残数と召喚は未確認')


def observe_summon(screen, mem, cur):
    from ..judgment.battle import _own_egg_empty
    state = _active(mem, cur)
    if not state or state['phase'] == 'cancelled':
        return False
    if not _own_egg_empty(mem, cur):
        cancel(mem, cur, '後続の本人卵観測を優先し古い卵なしの召喚待ちを終了する')
        return False
    if state['phase'] == 'summoned':
        return retreat_due(mem, cur)  # repeated pixels never replenish the B budget/proof age
    allies = [r for r in screen.egg_rows if r.side == 'ally']
    enemies = [r for r in screen.egg_rows if r.side == 'enemy']
    reading = cur.get('summon_general_reading')
    if (screen.kind != 'egg_battle_menu' or len(screen.egg_rows) != 2
            or len(allies) != 1 or len(enemies) != 1
            or not isinstance(reading, dict) or type(reading.get('max_hp')) is not int
            or allies[0].name != cur.get('ally')
            or type(allies[0].hp) is not int or not 0 < allies[0].hp <= reading.get('max_hp', 0)
            or run_identity(reading.get('identity')) != state['scope']['identity']
            or (reading.get('ally'), reading.get('enemy'), reading.get('hp')) != (
                cur.get('ally'), cur.get('enemy'), allies[0].hp)
            or not isinstance(enemies[0].name, str) or enemies[0].name not in reference.MONSTER_SKILLS
            or type(enemies[0].hp) is not int or not 0 < enemies[0].hp <= 999):
        return False
    state.update(phase='summoned', observed_tick=mem['tick'], general_hp=allies[0].hp,
                 enemy_monster=enemies[0].name, monster_hp=enemies[0].hp)
    _record(mem, 'enemy_egg_bait_summon_observed', general=cur.get('ally'),
            observed_metric={'enemy_general': cur.get('enemy'), 'enemy_monster': enemies[0].name,
                             'general_hp': allies[0].hp, 'monster_hp': enemies[0].hp,
                             'enemy_remaining_uses': None, 'quantity_change': 'unclassified'},
            resulting_event='enemy_summon_observed_retreat_pending',
            reason='同戦闘の本人将軍と敵側の実召喚獣を確認し退却へ移る。残数0や全卵枯渇は断定しない')
    return True


def retreat_due(mem, cur):
    from ..judgment.battle import _own_egg_empty
    state = _active(mem, cur)
    if (not state or state['phase'] != 'summoned' or cur.get('side') != 'attack'
            or not _own_egg_empty(mem, cur) or cur.get('card_hp_unread')
            or cur.get('card_context_unclassified')
            or type(state.get('observed_tick')) is not int
            or not state['armed_tick'] <= state['observed_tick'] <= mem['tick']
            or mem['tick'] - state['observed_tick'] > RETURN_PROOF_TICKS):
        return False
    return (isinstance(state.get('enemy_monster'), str) and state['enemy_monster'] in reference.MONSTER_SKILLS
            and type(state.get('monster_hp')) is int and 0 < state['monster_hp'] <= 999
            and type(state.get('general_hp')) is int and 0 < state['general_hp'] <= general_max_hp(cur['ally'])
            and type(cur.get('ally_hp')) is int and 0 < cur['ally_hp'] <= general_max_hp(cur['ally']))
