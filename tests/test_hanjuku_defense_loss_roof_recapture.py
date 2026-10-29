"""A lost defense is not taken back by the roof streak built while it was ours.

g1075-1d5f65e9 2026-10-29 JST, chapter 3. パタゴーヌ was ours from 15:28:24;
セルリアク lost its defense to タルタル at 15:42:43. The survey Y's first frame
was the fade (g1075-world-map-survey-fade.png, 15:42:44, パタゴーヌ already
under the enemy flag) and the generic A closed the view unread. The field at
15:42:45 (g1075-ch3-patagone-field-after-loss.png) still shows パタゴーヌ's
red roof, and with the roof streak at 163 own, castle_owned_observed took the
castle back at 15:42:46; only the 15:46:44 Y view listed it as lost again.
"""
from pathlib import Path

from docich import hanjuku_policy as policy
from docich.hanjuku.orchestrator import WORLD_SURVEY_UNKNOWN_HOLD_TICKS, decide, pad
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import castle_roofs

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
FADE = FIXTURES / 'g1075-world-map-survey-fade.png'
FIELD = FIXTURES / 'g1075-ch3-patagone-field-after-loss.png'
CASTLE = 'パタゴーヌ'


def memory():
    return {'chapter': 3, 'tick': 1000, '_records': [],
            'captured': ['グリン', CASTLE], 'garrison': {CASTLE: ['セルリアク']},
            'owner_streak': {CASTLE: {'kind': 'own', 'count': 163}},
            'world_flags': {CASTLE: {'kind': 'own', 'tick': 990, 'chapter': 3}},
            'battle': {'ally': 'セルリアク', 'enemy': 'タルタル', 'ally_hp': 0, 'enemy_hp': 40,
                       'start_ally_hp': 32, 'start_enemy_hp': 40, 'side': 'defense',
                       'castle': CASTLE, 'cards_used': []}}


def finish(mem):
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    return next(r for r in reversed(mem['_records']) if r['decision'] == 'battle_result')


def field_roofs():
    roofs = castle_roofs(read_png(FIELD))
    x, y = policy.chart.castles(3)[CASTLE]
    tx, ty = roofs[0]['target']
    return roofs, (x - tx, y - ty)


def decisions(mem, name):
    return [r for r in mem['_records'] if r['decision'] == name]


def test_the_lost_castle_keeps_its_loss_against_its_own_coloured_roof():
    mem = memory()
    assert finish(mem)['resulting_event'] == f'lost:{CASTLE}'
    assert mem['world_flags'][CASTLE] == {'kind': 'enemy', 'tick': 1000, 'chapter': 3}
    assert CASTLE not in mem['owner_streak']
    roofs, cam = field_roofs()
    assert [r['kind'] for r in roofs] == ['own', 'own']
    for _ in range(4):
        policy.observe_owners(mem, roofs, cam)
    assert CASTLE not in mem['captured'] and mem['lost'] == [CASTLE]
    assert not decisions(mem, 'castle_owned_observed')
    assert [r['castle'] for r in decisions(mem, 'castle_owned_vetoed')] == [CASTLE]


def test_a_defense_kept_by_its_own_flag_leaves_the_flag_and_streak():
    mem = memory()
    policy._apply_world_flags(mem, {CASTLE: 'own'})
    assert finish(mem)['resulting_event'] == f'defense_retained:{CASTLE}'
    assert mem['world_flags'][CASTLE]['kind'] == 'own'
    assert mem['owner_streak'][CASTLE]['count'] == 163


def survey_state(tick=100):
    # world_map_open pressed Y on the map at tick 100; the next frame is the fade.
    return {'screen_kind': 'map', 'policy': {'chapter': 3, 'tick': tick, 'world_map_tick': 100}}


def test_the_fade_after_the_survey_y_holds_the_default_a_boundedly():
    frame = read_png(FADE)
    state = survey_state()
    for after in range(1, WORLD_SURVEY_UNKNOWN_HOLD_TICKS + 1):
        actions, state = decide(frame, state)
        assert pad('a') not in actions
        holds = [r for r in state.get('_records', []) if r.get('decision') == 'world_map_unknown_hold']
        assert [h['observed_metric']['after_y'] for h in holds] == [after]
    actions, state = decide(frame, state)
    assert actions == [pad('a')]
    assert not [r for r in state.get('_records', []) if r.get('decision') == 'world_map_unknown_hold']


def test_a_fade_long_after_the_survey_keeps_its_default():
    actions, state = decide(read_png(FADE), survey_state(tick=100 + WORLD_SURVEY_UNKNOWN_HOLD_TICKS))
    assert actions == [pad('a')]
