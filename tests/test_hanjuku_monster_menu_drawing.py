"""A summon skill box still drawing is not a message to acknowledge.

g1075-1d5f65e9 2026-10-29 18:05-18:12 JST: ココット's おめでとり against
リースリング (HP 4). The skill box slid up from below the panel or panned in
from the right on each of 9 own turns; those frames read 'text' and the
generic A confirmed the top row, ミカンをおす, on 7 of them. Only the 2 turns
decided on the settled box reached the skill-stall count, so あたためる came
after 9 ミカンをおす, not 2. One あたためる then took リースリング to 0.
"""
from pathlib import Path

from docich import hanjuku_bot
from docich.hanjuku.flows.battle_menus import MONSTER_MENU_PENDING_MAX, MONSTER_STALL_TURNS
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
SLIDING = FIXTURES / 'g1075-omedetori-menu-sliding-up.png'     # 18:05:26, rows at y188/204
PANNING = FIXTURES / 'g1075-omedetori-menu-panning-in.png'     # 18:08:26, ミカ/あた/たま at x240
DIALOGUE = FIXTURES / 'g1075-omedetori-kyuu-dialogue.png'      # 18:05:29, after ミカンをおす
SETTLED = FIXTURES / 'g1075-omedetori-menu-riesling.png'       # 18:10:47, rows at y176
METRONOME = FIXTURES / 'g1075-metronome-menu-sliding-up.png'  # 2026-10-29 20:15:33, もっとはやく!! at y188


def kind(path):
    frame = read_png(path)
    return parse(frame, phase=classify(frame)).kind


def summon_state():
    cur = {'ally': 'ココット', 'enemy': 'リースリング', 'ally_hp': 24, 'enemy_hp': 4,
           'start_ally_hp': 24, 'start_enemy_hp': 37, 'side': 'attack',
           'cards_used': [], 'cards_selected': [], 'cards_missing': [],
           'card_evidence_version': 1, 'card_consumption_complete': True}
    return {'chapter': 4, 'policy': {'chapter': 4, 'battle': cur}, 'screen_kind': 'text'}


def buttons(actions):
    return [b for a in actions or [] for b in (a.get('buttons') or [])]


def test_the_sliding_and_panning_boxes_read_as_a_menu_still_drawing():
    assert kind(SLIDING) == 'monster_menu_pending'
    assert kind(PANNING) == 'monster_menu_pending'
    assert kind(SETTLED) == 'monster_menu'
    assert kind(DIALOGUE) == 'text'


def test_a_drawing_box_gets_no_confirm():
    for path in (SLIDING, PANNING):
        actions, state = hanjuku_bot.decide(read_png(path), summon_state())
        assert 'a' not in buttons(actions)
        assert 'monster_menu_drawing' in [r['decision'] for r in state['_records']]


def test_each_turn_is_decided_on_the_settled_box_so_the_stall_switch_comes_after_two():
    state = summon_state()
    picks = []
    for _ in range(MONSTER_STALL_TURNS + 1):
        for path in (SLIDING, PANNING):
            actions, state = hanjuku_bot.decide(read_png(path), state)
            assert 'a' not in buttons(actions)
        actions, state = hanjuku_bot.decide(read_png(SETTLED), state)
        picks.append(buttons(actions))
        acks = []
        for _ in range(6):
            actions, state = hanjuku_bot.decide(read_png(DIALOGUE), state)
            acks.append(buttons(actions))
        assert acks == [[], [], [], ['a'], [], []]     # only a page that stays gets its A
    assert picks[:MONSTER_STALL_TURNS] == [['a']] * MONSTER_STALL_TURNS
    assert picks[MONSTER_STALL_TURNS] == ['down']       # toward あたためる
    assert state['policy']['monster_stall']['turns'] == {'skill1': MONSTER_STALL_TURNS, 'skill2': 1}


def test_a_box_that_never_settles_falls_back_after_the_bound():
    state = summon_state()
    seen = []
    for _ in range(MONSTER_MENU_PENDING_MAX + 1):
        actions, state = hanjuku_bot.decide(read_png(SLIDING), state)
        seen.append(buttons(actions))
    assert all('a' not in b for b in seen[:MONSTER_MENU_PENDING_MAX])
    assert seen[MONSTER_MENU_PENDING_MAX] == ['a']
    assert 'monster_menu_incomplete' in [r['decision'] for r in state['_records']]


def test_a_sliding_box_whose_skills_read_half_width_still_waits():
    # 20:15:33: もっとはやく!!/もっとおそく!! were drawn without the third row
    # or the cursor; the reference spells them with ！！, the box was read as
    # text, and its default A confirmed the top skill.
    assert kind(METRONOME) == 'monster_menu_pending'
    state = summon_state()
    state['policy']['battle'].update(ally='メトロノーム', enemy='ドラゴンフライ', ally_hp=51,
                                     enemy_hp=202, start_ally_hp=51, start_enemy_hp=234)
    actions, state = hanjuku_bot.decide(read_png(METRONOME), state)
    assert 'a' not in buttons(actions)
    assert 'monster_menu_drawing' in [r['decision'] for r in state['_records']]
