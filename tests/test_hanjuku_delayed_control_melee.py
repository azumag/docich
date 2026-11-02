"""g692: the usable post-selection army panel arrived after 26 observations."""
from pathlib import Path
import json

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_card_damage_gate import memory

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'


def native(label):
    return parse(read_png(FIXTURES / ('g692-venus-artemis-' + label + '.png')))


def delayed_selection():
    mem = memory('クースカン', enemy='アルテミス', enemy_hp=49,
                 ally='ヴィーナス', ally_hp=82, soldiers=(5, 3))
    mem['egg_uses'] = {'ヴィーナス': 0}
    cur = mem['battle']
    cur.update(planned_cards=[], castle='ナキューメラ',
               card_flow={'stage': 'list', 'survival': True, 'list_ticks': 0,
                          'announce_retried': True})  # post-retry timeout path
    opening = native('opening')
    assert opening.battle.enemy_hp == 49 and opening.battle.ally_hp == 82
    assert opening.field_soldiers == (5, 3)
    p._card_battle_reading(opening, cur, mem)
    p._survival_state(mem, cur)
    assert p.card_list_step(native('kooskan-list'), mem) == [p.pad('a')]
    selected_tick = cur['card_melee_followup']['selected_tick']
    # The actual selection and uncalibrated receipt wait do not prove use.
    for _ in range(p.CARD_ANNOUNCE_LIMIT + 1):
        mem['tick'] += 1
        p.card_list_step(native('kooskan-list'), mem)
    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['クースカン']
    p._survival_state(mem, cur).update(exhausted=True, cards_uncertain=False)
    # Fixed g692 observation events between selection 1017 and panel 1037.
    mem['tick'] = selected_tick + 26
    return json.loads(json.dumps(mem))


def test_native_late_fresh_advantage_can_follow_up_without_inventing_card_use():
    mem = delayed_selection()
    screen = native('followup-panel')
    assert screen.battle.enemy_hp == 23 and screen.battle.ally_hp == 82
    # The five yellow/red allies and one green/purple enemy have crossed the
    # screen centre. The old x128 split incorrectly assigned them 3 vs 3.
    assert screen.field_soldiers == (5, 1)
    actions = p.battle_step(screen, mem)
    assert actions == p._power_mash(mem, mem['battle'])
    assert mem['battle']['card_melee_followup']['bursts'] == 1
    assert any(r['decision'] == 'battle_card_melee_followup' for r in mem['_records'])
    assert mem['battle']['cards_used'] == []
    assert mem['battle']['cards_unclassified'] == ['クースカン']
    assert not mem['battle'].get('enemy_egg_dropped')


def test_native_late_hp_drop_with_clipped_enemy_still_cannot_force_followup():
    mem = delayed_selection()
    screen = native('unbalanced-panel')
    assert screen.battle.enemy_hp == 23 and screen.field_soldiers == (5, None)
    control = json.loads(json.dumps(mem))
    control['battle'].pop('card_melee_followup')
    expected = p.battle_step(screen, control)
    assert p.battle_step(screen, mem) == expected
    assert not any(r['decision'] == 'battle_card_melee_followup' for r in mem['_records'])
    assert mem['battle']['card_melee_followup']['bursts'] == 0
