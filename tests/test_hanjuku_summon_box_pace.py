"""A summon battle's command boxes are confirmed only when the bot has read them.

g1075-1d5f65e9 2026-10-29 JST:
- 19:16:33 and 19:16:47: デュオニソス (54, egg in hand) defended フヌイヌム against
  キャンディー's マシンナイト (479). Both times his own こうげき/もうこうげき/
  たまごをつかう box was still panning in (こ/も/た at x248, then こうげき/
  もうこうげ/たまごをつ at x216). The frames read 'text', the generic A chose
  こうげき, and the egg battle never saw its menu. He fell and the castle went.
- 19:24:40-19:26:11: four A's decided on the first look at a dialogue page
  reached a summon's skill box (あけまつ twice, はんぎょじん twice); あけまつ then
  greeted again although a second exchange undoes the first. 19:29:11 the
  same kind of A gave おめでとり a third ミカンをおす before あたためる.
"""
from pathlib import Path
from types import SimpleNamespace

from docich import hanjuku_bot
from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku.judgment.akematsu import akematsu_assessment
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
PAN_EDGE = FIXTURES / 'g1075-dionysus-command-pan-edge.png'   # 19:16:32, こ/も/た at x248
PAN = FIXTURES / 'g1075-dionysus-command-pan.png'             # 19:16:47, こうげき/もうこうげ/たまごをつ
ATTACK = FIXTURES / 'g1075-dionysus-attack-dialogue.png'      # 19:16:33, デュオニソス のこうげ…
KYUU = FIXTURES / 'g1075-omedetori-kyuu-dialogue.png'
SLIDING = FIXTURES / 'g1075-omedetori-menu-sliding-up.png'
ENTRANCE = FIXTURES / 'g1075-excalibur-entrance-dialogue.png'   # 19:00:45, エッグモンスター エクスカリバー たんじょう!
AKEMATSU = FIXTURES / 'g1075-akematsu-first-box.png'            # 19:53:46, the box a stray A reached


def kind(path):
    frame = read_png(path).resized()
    return parse(frame, phase=classify(frame)).kind


def state(ally='デュオニソス', enemy='キャンディー'):
    cur = {'ally': ally, 'enemy': enemy, 'ally_hp': 54, 'enemy_hp': 26,
           'start_ally_hp': 54, 'start_enemy_hp': 26, 'side': 'defense',
           'cards_used': [], 'cards_selected': [], 'cards_missing': [],
           'card_evidence_version': 1, 'card_consumption_complete': True}
    return {'chapter': 4, 'policy': {'chapter': 4, 'battle': cur}, 'screen_kind': 'text'}


def run(st, *paths):
    decisions, buttons = [], []
    for path in paths:
        actions, st = hanjuku_bot.decide(read_png(path).resized(), st)
        decisions += [r['decision'] for r in st.get('_records', [])]
        buttons.append([b for a in actions or [] for b in (a.get('buttons') or [])])
    return st, decisions, buttons


def test_the_fixtures_read_as_measured():
    assert kind(PAN_EDGE) == 'monster_menu_pending'
    assert kind(PAN) == 'monster_menu_pending'
    assert kind(ATTACK) == 'text'


def test_our_generals_box_panning_in_is_not_confirmed():
    st, decisions, buttons = run(state(), PAN_EDGE, PAN)
    assert buttons == [[], []]
    assert 'monster_menu_drawing' in decisions
    assert st['policy']['battle']['summon_scene'] is True


def test_a_summon_dialogue_page_is_acknowledged_only_once_it_stays():
    # 19:57:14 and 20:14:43: an A on the second look at the last page still
    # met the skill box. Pages advance by themselves; only a lasting one gets A.
    st, decisions, buttons = run(state(), PAN, KYUU, KYUU, ATTACK, ATTACK, *[KYUU] * 5)
    assert buttons[1:] == [[], [], [], [], [], [], [], ['a'], []]
    assert decisions.count('summon_dialogue_paced') == 1


def test_dialogue_outside_a_summon_battle_keeps_its_immediate_a():
    _, _, buttons = run(state(), KYUU, ATTACK)
    assert buttons == [['a'], ['a']]


def test_the_measured_pages_before_a_box_get_no_first_look_a():
    # おめでとり's box slides in, then two new pages each seen once: no A can
    # be in flight when the next box arrives.
    _, _, buttons = run(state('リーキ', 'ライム'), SLIDING, KYUU, ATTACK)
    assert buttons == [[], [], []]


def test_a_stray_greeting_counts_as_akematsus_one_greeting():
    mem = {'battle': {'ally': 'リーキ', 'enemy': 'キャンディー'},
           'monster_unchosen': {'ally': 'あけまつ', 'enemy': 'キャンディー', 'enemy_hp': 26}}
    menus.count_unchosen_monster_turn(mem)
    ally, enemy = SimpleNamespace(name='あけまつ'), SimpleNamespace(name='キャンディー')
    assert akematsu_assessment(mem, ally, enemy)['action'] == 'skill2'      # おもてなし next


def test_an_egg_monster_entrance_starts_the_paced_scene():
    # 19:53:26 たまごをつかう, 19:53:46 あけまつ's first box took the A decided
    # on the page before it: no box had been seen in this battle yet.
    assert kind(ENTRANCE) == 'text' and kind(AKEMATSU) == 'monster_menu'
    st, _, buttons = run(state('リーキ', 'ヴィーナス'), ENTRANCE, KYUU, *[ENTRANCE] * 4)
    assert buttons == [[], [], [], [], [], ['a']]
    assert st['policy']['battle']['summon_scene'] is True
