"""An interruption must not move the planned sortie cell.

The destination picker comes back with its marker wherever the game left it
after a battle, while the bot's old ``cursor``/``y_jumped`` receipt still
names the pre-battle cell. Trusting that receipt confirms the restored marker
as "arrived" and sends the general to a cell offset from the plan.
"""
from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku_screen import Screen


STEP = '1-A2'


def sortie_memory():
    return {
        'chapter': 1, 'tick': 100, '_records': [],
        'active': STEP, 'orders': {STEP: 'pending'},
        'general_override': {STEP: 'ゼウス'},
        'source_override': {STEP: 'アルマムーン'},
        'garrison': {'アルマムーン': ['どうし', 'ゼウス']},
        'order_context': {STEP: {'actual_general': 'ゼウス'}},
        'sortie_attempt': {'step': STEP, 'target_seen': True},
        'cursor': [400, 400],
        'near_goal': {'step': STEP, 'goal': 'ジョンリギ', 'mode': 'target'},
        'y_jumped': {STEP: 'ジョンリギ'},
        'y_jumped_res': {STEP: [0.0, 0.0]},
        'y_jump_target_confirmed': {'step': STEP, 'tick': 98},
        'y_jump_target_closed': {'step': STEP, 'tick': 97},
    }


def screen(kind):
    return Screen([], None, '', kind=kind)


def test_a_battle_drops_the_pre_battle_target_placement():
    mem = sortie_memory()
    mem['battle'] = {'enemy': 'リディア', 'ally': 'アンディーブ',
                     'enemy_hp': 72, 'ally_hp': 36, 'side': 'defense',
                     'castle': None, 'step': STEP,
                     'cards_used': [], 'planned_cards': []}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')          # the second away reading closes it
    assert 'battle' not in mem
    assert 'cursor' not in mem and 'near_goal' not in mem
    assert not mem.get('y_jumped') and not mem.get('y_jumped_res')
    assert 'y_jump_target_confirmed' not in mem
    assert 'y_jump_target_closed' not in mem
    assert mem['uncertain'] is True


def test_a_picker_closed_into_a_battle_drops_the_target_placement():
    mem = sortie_memory()
    policy.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert [r for r in mem['_records']
            if r.get('decision') == 'launch_interrupted_by_battle']
    assert 'cursor' not in mem and 'near_goal' not in mem
    assert not mem.get('y_jumped') and not mem.get('y_jumped_res')
    assert 'y_jump_target_confirmed' not in mem
    assert mem['uncertain'] is True


def test_the_placement_helper_leaves_other_orders_receipts_alone():
    mem = {'tick': 10, 'y_jumped': {STEP: 'ジョンリギ', 'RECALL': 'アルマムーン'},
           'y_jumped_res': {STEP: [0, 0], 'RECALL': [1, 1]},
           'near_goal': {'step': STEP, 'goal': 'ジョンリギ', 'mode': 'target'},
           'cursor': [400, 400]}
    nav.forget_target_placement(mem, STEP)
    assert mem['y_jumped'] == {'RECALL': 'アルマムーン'}
    assert mem['y_jumped_res'] == {'RECALL': [1, 1]}
    assert 'near_goal' not in mem and 'cursor' not in mem
    assert mem['uncertain'] is True


def test_a_fresh_y_jump_keeps_the_route_aim_cell(monkeypatch):
    """A battle forces a fresh jump; it must still land on the measured cell.

    chapter 9's サヌト→オシロガ aims one tile north of the bare castle cell.
    The walking path uses that cell, so the Y path must too, not fall back to
    the chart's castle cell.
    """
    from docich import hanjuku_chart as chart

    step = 'I:c9:サヌト>オシロガ:1'
    order = {'step': step, 'source': 'サヌト', 'target': 'オシロガ'}
    goal = chart.aim_cell(9, 'サヌト', 'オシロガ')
    assert goal != chart.castles(9)['オシロガ']
    mem = {'chapter': 9, 'tick': 10, '_records': []}
    nav._start_y_jump(mem, order, 'オシロガ', 'target', goal_cell=goal)
    assert mem['y_jump']['goal_cell'] == list(goal)
    ox, oy = nav.Y_JUMP_OFFSET[9]
    monkeypatch.setattr(nav, 'world_cursor', lambda frame, chapter: (goal[0]/8 + ox, goal[1]/8 + oy))
    monkeypatch.setattr(nav, 'world_flags', lambda *args, **kwargs: {})
    assert nav._y_jump_step(mem, object()) == [nav.pad('a')]
    assert mem['cursor'] == list(goal)
