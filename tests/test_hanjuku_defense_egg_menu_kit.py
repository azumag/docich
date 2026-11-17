"""#4456: defense battles read the real きりふだ list for a bounded kit.

Synthetic screens and persisted state only: no live input, selection or
consumption receipt is claimed. A menu we cannot read stays kit_unknown.
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle, Screen
from test_hanjuku_survival import menu


def defense_mem(*, enemy='オレガノ', ally='バタール', chapter=9):
    return {'chapter': chapter, 'tick': 100, '_records': [],
            'battle': {
                'enemy': enemy, 'ally': ally, 'castle': 'シマウ', 'side': 'defense',
                'step': None, 'enemy_hp': 65, 'ally_hp': 99,
                'start_enemy_hp': 65, 'start_ally_hp': 99,
                'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
                'card_evidence_version': 1, 'planned_cards': []}}


def panel(mem):
    cur = mem['battle']
    return Screen([], None, '', kind='battle',
                  battle=Battle(cur['enemy'], cur['enemy_hp'], cur['ally'], cur['ally_hp']))


def records(mem, decision):
    return [r for r in mem['_records'] if r['decision'] == decision]


def test_defense_kit_unknown_stays_fail_closed():
    mem = defense_mem()
    plan = p._egg_plan(mem, mem['battle'])
    assert plan['has_egg'] is True
    assert plan['max_hp_sum'] == 164 and plan['remainder'] == 4 and plan['threshold'] == 5
    assert plan['kit_source'] == 'unknown' and plan['kit_known'] is False
    assert plan['id_sum'] is None and plan['deny_opening_egg'] is None
    assert plan['droppers'] == [] and plan['dropper'] is None
    assert plan['verdict'] == 'not_droppable'
    assert records(mem, 'battle_egg_drop_trace') == []


def test_defense_menu_read_builds_a_kit_but_never_claims_id_sum():
    mem = defense_mem()
    cur = mem['battle']
    cur['defense_card_menu'] = {'enemy': 'オレガノ', 'ally': 'バタール',
                                'start_enemy_hp': 65, 'start_ally_hp': 99,
                                'cards': ['イッテツーン'], 'readings': 2}
    plan = p._egg_plan(mem, cur)
    assert plan['kit_source'] == 'menu_read' and plan['kit_known'] is True
    assert plan['carried'] == ['イッテツーン']
    assert plan['droppers'] == ['イッテツーン'] and plan['dropper'] == 'イッテツーン'
    assert plan['verdict'] == 'droppable'
    # Menu-read cards cannot prove the enemy's opening-egg gate (#4456 cond. 2).
    assert plan['id_sum'] is None and plan['deny_opening_egg'] is None
    trace = records(mem, 'battle_egg_drop_trace')[-1]
    assert trace['stage'] == 'decided' and trace['card'] == 'イッテツーン'
    assert trace['observed_metric']['kit_source'] == 'menu_read'
    assert trace['tactic_id'] == 'eggdrop:None:イッテツーン'


def test_existing_survival_listed_cards_are_reused_as_the_kit():
    mem = defense_mem()
    cur = mem['battle']
    cur['survival'] = {'listed_cards': ['イッテツーン', 'クースカン']}
    plan = p._egg_plan(mem, cur)
    assert plan['kit_source'] == 'menu_read'
    assert plan['carried'] == ['イッテツーン', 'クースカン']
    assert plan['droppers'] == ['イッテツーン']
    assert plan['dropper'] == 'イッテツーン'
    assert plan['id_sum'] is None and plan['deny_opening_egg'] is None


def test_defense_menu_read_only_offers_droppers_present_in_the_read_list():
    mem = defense_mem()
    cur = mem['battle']
    cur['defense_card_menu'] = {'enemy': 'オレガノ', 'ally': 'バタール',
                                'start_enemy_hp': 65, 'start_ally_hp': 99,
                                'cards': ['ダイチスイム', 'クースカン'], 'readings': 2}
    plan = p._egg_plan(mem, cur)
    assert plan['kit_source'] == 'menu_read'
    assert plan['droppers'] == [] and plan['dropper'] is None


@pytest.mark.parametrize('changes', [
    {'enemy': 'キッシュ'}, {'ally': 'しゅじんこう'},
    {'start_enemy_hp': 60}, {'start_ally_hp': 90},
    {'readings': 0}, {'readings': 99}, {'cards': []}, {'cards': 'イッテツーン'},
])
def test_stale_or_unreadable_menu_is_rejected(changes):
    mem = defense_mem()
    cur = mem['battle']
    cur['defense_card_menu'] = {'enemy': 'オレガノ', 'ally': 'バタール',
                                'start_enemy_hp': 65, 'start_ally_hp': 99,
                                'cards': ['イッテツーン'], 'readings': 2}
    cur['defense_card_menu'].update(changes)
    plan = p._egg_plan(mem, cur)
    assert plan['kit_source'] == 'unknown'
    assert plan['droppers'] == [] and plan['dropper'] is None


def test_attack_path_keeps_order_kit_and_id_sum_unchanged():
    mem = {'chapter': 1, 'tick': 5, '_records': [],
           'orders': {'1-C2': 'pending'},
           'card_override': {'1-C2': ['ダイチスイム', 'ダイチスイム', 'ブラッキー']},
           'battle': {'enemy': 'キッシュ', 'ally': 'ココット', 'side': 'attack',
                      'step': '1-C2', 'castle': 'キカンドン',
                      'enemy_hp': 26, 'ally_hp': 24,
                      'start_enemy_hp': 26, 'start_ally_hp': 24,
                      'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
                      'card_evidence_version': 1, 'planned_cards': []}}
    plan = p._egg_plan(mem, mem['battle'])
    assert plan['kit_source'] == 'order' and plan['kit_known'] is True
    assert plan['id_sum'] == 4 and plan['deny_opening_egg'] is True
    assert plan['droppers'] == ['ブラッキー'] and plan['dropper'] == 'ブラッキー'


def test_read_kit_flow_reads_names_without_selecting_or_consuming():
    mem = defense_mem()
    cur = mem['battle']
    cur['egg_plan'] = p._egg_plan(mem, cur)
    cur['card_flow'] = {'card': None, 'stage': 'list', 'read_kit': True}
    cards = menu(('イッテツーン',), kind='text')
    assert p.card_list_step(cards, mem) == []            # first reading is not stable yet
    assert p.card_list_step(cards, mem) == [p.pad('b'), p.pad('b')]
    assert cur['defense_card_menu']['cards'] == ['イッテツーン']
    assert cur['cards_selected'] == [] and cur['cards_used'] == []
    assert cur['card_flow'] is None
    assert records(mem, 'battle_card_selected') == []
    plan = p._egg_plan(mem, cur)
    assert plan['kit_source'] == 'menu_read' and plan['dropper'] == 'イッテツーン'


def test_unreadable_menu_fails_closed():
    mem = defense_mem()
    cur = mem['battle']
    cur['card_flow'] = {'card': None, 'stage': 'list', 'read_kit': True}
    blank = menu((), kind='text')
    for _ in range(p.DEFENSE_MENU_READ_LIMIT):
        assert p.card_list_step(blank, mem) == []
    assert p.card_list_step(blank, mem) == [p.pad('b'), p.pad('b')]
    assert cur['defense_card_menu']['cards'] == []
    assert p._egg_plan(mem, cur)['kit_source'] == 'unknown'
    assert records(mem, 'battle_defense_kit_read_unread')


def test_bounded_open_budget_exhausts_and_falls_back():
    from docich.hanjuku.flows import battle as flow_battle

    mem = defense_mem()
    cur = mem['battle']
    cur['egg_plan'] = p._egg_plan(mem, cur)
    for attempt in range(p.DEFENDER_EGG_OPEN_LIMIT):
        assert flow_battle._defense_kit_read_open(mem, cur) == [p.pad('b')]
        assert cur['defense_egg_kit_read']['opens'] == attempt + 1
        cur['card_flow'] = None                  # simulate an unopened command menu
    assert flow_battle._defense_kit_read_open(mem, cur) is None
    assert len(records(mem, 'battle_defense_kit_read_open')) == p.DEFENDER_EGG_OPEN_LIMIT
    assert records(mem, 'battle_defense_kit_read_failed')
    assert cur['defense_egg_kit_read']['exhausted'] is True
    assert cur.get('defense_card_menu') is None


def test_trace_links_decision_to_unclassified_execution():
    mem = defense_mem()
    cur = mem['battle']
    cur['defense_card_menu'] = {'enemy': 'オレガノ', 'ally': 'バタール',
                                'start_enemy_hp': 65, 'start_ally_hp': 99,
                                'cards': ['イッテツーン'], 'readings': 2}
    p._egg_plan(mem, cur)                         # 'decided' trace
    cur['card_flow'] = {'card': 'イッテツーン', 'stage': 'announce',
                        'tactic_id': 'eggdrop:None:イッテツーン',
                        'egg_drop_only': True, 'selection_planned': True}
    p._card_use_unclassified(mem, cur, 'test unclassified')
    traces = records(mem, 'battle_egg_drop_trace')
    assert [t['stage'] for t in traces] == ['decided', 'unclassified']
    assert all(t['tactic_id'] == 'eggdrop:None:イッテツーン' for t in traces)
    assert traces[0]['observed_metric']['decision_tick'] == 100
    assert traces[-1]['observed_metric']['kit_source'] == 'menu_read'


def test_enemy_egg_use_trace_uses_the_same_link():
    mem = defense_mem()
    cur = mem['battle']
    cur['egg_plan'] = p._egg_plan(mem, cur)
    cur['egg_drop_decision_tick'] = 100
    cur['enemy_transform_observed'] = True
    p._egg_drop_trace(mem, cur, 'enemy_egg_used', transform_skill=True)
    trace = records(mem, 'battle_egg_drop_trace')[-1]
    assert trace['stage'] == 'enemy_egg_used'
    assert trace['observed_metric']['decision_tick'] == 100
    assert trace['observed_metric']['enemy_transform_observed'] is True
