"""A summon turn taken by a press we did not choose still counts toward the stall switch.

g1075-1d5f65e9 2026-10-29 JST: an A decided on a dialogue frame can reach the
game just as the skill box slides in. At 18:43:30.8 one confirmed
ピスクピグプレム's top skill; the next capture (18:43:30.9) already showed the
box leaving for バーミセリ's general menu. The cursor starts every turn on the
top row (ラビットサタン and ガートルード needed a down for skill 2 each turn), so
such a turn spent skill 1 but never reached the two-turn count.
"""
from pathlib import Path

from docich import hanjuku_bot
from docich.hanjuku.flows.battle_menus import MONSTER_STALL_SAME_TURN_TICKS
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
PAN_LEAVING = FIXTURES / 'g1075-pisukupigupuremu-box-panning.png'   # 18:43:31, いば/わか at x240
ENEMY_MENU = FIXTURES / 'g1075-barmiseri-general-menu.png'          # 18:43:13, こうげき/もうこうげき
ENEMY_ATTACK = FIXTURES / 'g1075-barmiseri-attack-dialogue.png'     # 18:43:33, バーミセリ のこうげき!
SLIDING = FIXTURES / 'g1075-omedetori-menu-sliding-up.png'
PANNING = FIXTURES / 'g1075-omedetori-menu-panning-in.png'
SETTLED = FIXTURES / 'g1075-omedetori-menu-riesling.png'
KYUU = FIXTURES / 'g1075-omedetori-kyuu-dialogue.png'


def state(ally='ココット', enemy='リースリング'):
    cur = {'ally': ally, 'enemy': enemy, 'ally_hp': 24, 'enemy_hp': 4,
           'start_ally_hp': 24, 'start_enemy_hp': 37, 'side': 'attack',
           'cards_used': [], 'cards_selected': [], 'cards_missing': [],
           'card_evidence_version': 1, 'card_consumption_complete': True}
    return {'chapter': 4, 'policy': {'chapter': 4, 'battle': cur}, 'screen_kind': 'text'}


def run(st, *paths):
    decisions, buttons = [], []
    for path in paths:
        actions, st = hanjuku_bot.decide(read_png(path), st)
        decisions += [r['decision'] for r in st.get('_records', [])]
        buttons.append([b for a in actions or [] for b in (a.get('buttons') or [])])
    return st, decisions, buttons


def dialogue(n=MONSTER_STALL_SAME_TURN_TICKS + 2):
    return [KYUU] * n


def turns(st):
    return (st['policy'].get('monster_stall') or {}).get('turns')


def test_the_fixtures_read_as_measured():
    def read(path):
        frame = read_png(path)
        return parse(frame, phase=classify(frame)).kind
    assert read(PAN_LEAVING) == 'monster_menu_pending'
    assert read(ENEMY_MENU) == 'egg_battle_menu'
    assert read(ENEMY_ATTACK) == 'text'


def test_a_box_that_left_without_our_choice_counts_skill_one():
    st, decisions, _ = run(state('デュオニソス', 'バーミセリ'), ENEMY_ATTACK, PAN_LEAVING, ENEMY_MENU)
    assert 'monster_turn_unchosen' in decisions
    stall = st['policy']['monster_stall']
    assert stall['scope'] == ['ピスクピグプレム', 'バーミセリ'] and stall['enemy_hp'] == 89
    assert stall['turns'] == {'skill1': 1}


def test_a_box_that_settles_is_our_own_turn_and_is_mikan_once():
    st, decisions, buttons = run(state(), *dialogue(), SLIDING, SETTLED, *dialogue())
    assert buttons[len(dialogue()) + 1] == ['a']               # ミカンをおす on the settled box
    assert 'monster_turn_unchosen' not in decisions
    assert st['policy']['battle']['omedetori_cycle']['mikan_submitted'] is True
    assert turns(st) is None                                    # omedetori is not a stall count


def test_the_box_leaving_after_our_choice_is_not_a_second_turn():
    st, decisions, _ = run(state(), *dialogue(), SETTLED, PANNING, *dialogue())
    assert 'monster_turn_unchosen' not in decisions
    assert st['policy']['battle']['omedetori_cycle']['mikan_submitted'] is True
    assert turns(st) is None


def test_mikan_then_warm_on_the_next_settled_box_with_no_stall_count():
    # A stray box slides in between; the next settled box must already try あたためる.
    st, decisions, buttons = run(state(), *dialogue(), SETTLED, *dialogue(),
                                 SLIDING, SETTLED)
    assert buttons[len(dialogue())] == ['a']                    # ミカンをおす
    assert buttons[-1] == ['down']                              # toward あたためる
    assert 'monster_skill_stall' not in decisions
    assert st['policy']['battle']['omedetori_cycle']['mikan_submitted'] is True
