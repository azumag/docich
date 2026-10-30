"""A parked boss march may back the bot out of a menu (#3126 evidence).

g1075 2026-10-31 08:20: 4-B1..4-B4 were overdue with boss_relaunch=2 for
39,658-57,843 observations while the bot stayed in shop/month text. next_order
is only reached from the field map, so the stale re-arm never ran. This test
pins the bounded escape and the parked-step predicate.
"""
from docich import hanjuku_policy as policy
from docich.hanjuku.flows import map as map_flow
from docich.hanjuku.strategy import orders as O
from docich.hanjuku_screen import Screen

SITE = '四季王城(南西)'


def memory(tick=10000, *, age=10000, status='overdue', relaunch=2):
    return {'chapter': 4, 'tick': tick, 'active': None,
            'orders': {'4-B1': 'launched'},
            'sorties': {'4-B1': {'general': 'アルテミス', 'source': 'ミヒラギアン',
                                 'status': status, 'target': SITE,
                                 'tick': tick - age}},
            'boss_relaunch': {'4-B1': relaunch},
            'captured': ['アルカ'], 'lost': []}


def test_a_long_overdue_boss_step_is_parked():
    mem = memory()
    assert O.parked_boss_steps(mem) == ['4-B1']


def test_a_step_inside_its_budget_or_cooldown_is_not_parked():
    assert O.parked_boss_steps(memory(age=O.BOSS_STALE_COOLDOWN - 1)) == []
    assert O.parked_boss_steps(memory(relaunch=O.BOSS_RELAUNCH_LIMIT - 1)) == []
    assert O.parked_boss_steps(memory(status='en_route')) == []
    mem = memory()
    mem['orders']['4-B1'] = 'pending'
    assert O.parked_boss_steps(mem) == []


def test_an_arrived_boss_with_a_live_fight_is_not_parked():
    mem = memory(status='arrived')
    assert O.parked_boss_steps(mem) == ['4-B1']
    mem['battle'] = {'step': '4-B1', 'ally': 'アルテミス'}
    assert O.parked_boss_steps(mem) == []


def test_a_parked_march_backs_out_of_a_shop_screen_once_per_cooldown():
    mem = memory()
    actions = map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop')
    assert actions == [policy.pad('b')]
    assert mem['world_map_due'] is True and mem['uncertain'] is True
    assert mem['boss_stall_escape_tick'] == mem['tick']
    assert any(record.get('decision') == 'boss_stall_escape'
               for record in mem['_records'])
    assert map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop') is None
    mem['tick'] += map_flow.BOSS_STALL_ESCAPE_TICKS
    assert map_flow.boss_stall_escape_step(mem, 'text', 'shop') == [policy.pad('b')]


def test_the_escape_does_not_preempt_a_map_battle_or_another_owner():
    for kind, phase in (('map', 'shop'), ('battle', 'battle'),
                        ('battle_menu', 'battle'), ('text', 'event'),
                        ('world_map', 'event')):
        assert map_flow.boss_stall_escape_step(memory(), kind, phase) is None
    mem = memory()
    mem['active'] = '4-B1'
    assert map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop') is None
    mem = memory()
    mem['recall'] = {'stage': 'dest'}
    assert map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop') is None
    mem = memory()
    mem['battle'] = {'step': '4-B1'}
    assert map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop') is None


def test_the_orchestrator_returns_the_escape_action(monkeypatch):
    from docich.hanjuku import orchestrator
    from docich.hanjuku_pixels import Frame
    from docich.hanjuku_screen import Screen as ScreenType

    screen = ScreenType(lines=[], hand=None, text='', kind='shop_list')
    monkeypatch.setattr(orchestrator, 'classify', lambda frame: 'shop')
    monkeypatch.setattr('docich.hanjuku.os_layer.screen.parse',
                        lambda frame, phase=None: screen)
    mem = memory()
    actions, state = orchestrator.decide(Frame(256, 224, bytes(256 * 224 * 3)),
                                         {'policy': mem})
    assert actions == [policy.pad('b')]
    assert state['policy']['world_map_due'] is True
    assert any(record.get('decision') == 'boss_stall_escape'
               for record in state['_records'])


def test_a_held_boss_general_list_is_cancelled_before_the_stall_terminal():
    mem = memory()
    mem['active'] = '4-B1'
    assert map_flow.boss_sortie_menu_escape_step(mem, 'general_list', []) is None
    mem['tick'] += map_flow.BOSS_SORTIE_MENU_HOLD_TICKS - 1
    assert map_flow.boss_sortie_menu_escape_step(mem, 'general_list', []) is None
    mem['tick'] += 1
    assert map_flow.boss_sortie_menu_escape_step(mem, 'general_list', []) == [policy.pad('b')]
    assert mem['world_map_due'] is True
    assert 'boss_sortie_menu_hold' not in mem
    assert any(record.get('decision') == 'boss_sortie_menu_escape'
               for record in mem['_records'])
    assert map_flow.BOSS_SORTIE_MENU_HOLD_TICKS < 200   # input_stalled is ~200 ticks at 1.5 s


def test_the_sortie_menu_escape_only_owns_a_boss_order_with_no_action():
    for kind, actions in (('map', []), ('general_list', [policy.pad('a')]),
                          ('battle', []), ('general_list', None)):
        mem = memory()
        mem['active'] = '4-B1'
        assert map_flow.boss_sortie_menu_escape_step(mem, kind, actions) is None
    mem = memory()
    mem['active'] = '4-C1'   # a normal castle order is not in this watchdog
    assert map_flow.boss_sortie_menu_escape_step(mem, 'general_list', []) is None
    mem = memory()
    mem['active'] = '4-B1'
    mem['recall'] = {'stage': 'dest'}
    assert map_flow.boss_sortie_menu_escape_step(mem, 'general_list', []) is None


def test_a_changed_step_or_screen_restarts_the_hold_window():
    mem = memory()
    mem['active'] = '4-B1'
    assert map_flow.boss_sortie_menu_escape_step(mem, 'general_list', []) is None
    mem['tick'] += map_flow.BOSS_SORTIE_MENU_HOLD_TICKS
    assert map_flow.boss_sortie_menu_escape_step(mem, 'card_select', []) is None
    mem['tick'] += map_flow.BOSS_SORTIE_MENU_HOLD_TICKS
    assert map_flow.boss_sortie_menu_escape_step(mem, 'card_select', []) == [policy.pad('b')]


def test_the_orchestrator_escapes_a_held_general_list(monkeypatch):
    from docich.hanjuku import orchestrator
    from docich.hanjuku_pixels import Frame
    from docich.hanjuku_screen import Screen as ScreenType

    screen = ScreenType(lines=[], hand=None, text='', kind='general_list')
    monkeypatch.setattr(orchestrator, 'classify', lambda frame: 'field_menu')
    monkeypatch.setattr('docich.hanjuku.os_layer.screen.parse',
                        lambda frame, phase=None: screen)
    mem = memory(tick=1000)
    mem['active'] = '4-B1'
    state = {'policy': mem}
    first, state = orchestrator.decide(Frame(256, 224, bytes(256 * 224 * 3)), state)
    assert first == []
    state['policy']['tick'] += map_flow.BOSS_SORTIE_MENU_HOLD_TICKS
    second, state = orchestrator.decide(Frame(256, 224, bytes(256 * 224 * 3)), state)
    assert second == [policy.pad('b')]
    assert any(record.get('decision') == 'boss_sortie_menu_escape'
               for record in state['_records'])


def test_the_escape_stops_after_the_step_is_rearmed():
    mem = memory()
    assert map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop') == [policy.pad('b')]
    mem['tick'] += map_flow.BOSS_STALL_ESCAPE_TICKS
    mem['orders']['4-B1'] = 'pending'
    assert map_flow.boss_stall_escape_step(mem, 'shop_list', 'shop') is None
