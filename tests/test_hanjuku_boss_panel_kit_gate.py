"""A boss read in the battle panel authorizes carried boss cards.

Owner 2026-11-21: 卵を使われる前に切り札バルムンク×2を使うのが正解.
The ch9 boss is met by a field ambush (no charted boss order); the panel's
ハデス or せいめいたい reading must still unlock the carried kit, and the
survival card list must also fire バルムンク against measured bosses while
still refusing it against ordinary foes (#4855).
"""
from docich import hanjuku_policy as policy
from docich.hanjuku.judgment import battle as J


def test_the_chapter_boss_name_in_the_panel_allows_the_kit():
    cur = {'enemy': 'ハデス', 'step': 'A', 'ally': 'ポセイドン'}
    assert J._boss_tactics_allowed({'chapter': 9}, cur) is True


def test_ch9_boss_general_seimeitai_in_panel_allows_the_kit():
    cur = {'enemy': 'せいめいたい', 'step': 'A', 'ally': 'バタール'}
    assert J._boss_tactics_allowed({'chapter': 9}, cur) is True
    assert J._boss_tactics_allowed({'chapter': 8}, cur) is False


def test_an_ordinary_enemy_does_not():
    cur = {'enemy': 'コリアンダー', 'step': 'A', 'ally': 'ポセイドン'}
    assert J._boss_tactics_allowed({'chapter': 9}, cur) is False


def test_survival_card_list_selects_balmung_against_seimeitai():
    from test_hanjuku_card_damage_gate import cards, memory
    mem = memory(card=None, enemy='せいめいたい', enemy_hp=122,
                 ally='バタール', ally_hp=99, soldiers=(None, None))
    mem['chapter'] = 9
    mem['battle']['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    mem['battle']['survival'] = {'cards_attempted': []}

    out = policy.card_list_step(cards(['バルムンク', 'バルムンク']), mem)
    assert out == [policy.pad('a')]
    assert mem['battle']['cards_selected'] == ['バルムンク']
    assert mem['battle']['card_flow']['card'] == 'バルムンク'
    assert mem['battle']['card_flow']['stage'] == 'announce'


def test_survival_card_list_still_refuses_balmung_against_ordinary_enemy():
    from test_hanjuku_card_damage_gate import assert_not_spent, cards, memory
    mem = memory(card=None, enemy='コリアンダー', enemy_hp=50,
                 ally='バタール', ally_hp=99, soldiers=(0, 0))
    mem['chapter'] = 9
    mem['battle']['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    mem['battle']['survival'] = {'cards_attempted': []}

    out = policy.card_list_step(cards(['バルムンク', 'バルムンク']), mem)
    assert out == [policy.pad('b')]
    assert_not_spent(mem)
    assert mem['battle']['card_assessment']['reason'] == 'boss_only_balmung'


def test_orchestrator_routes_balmung_only_card_list_even_when_stage_is_menu(monkeypatch):
    from docich.hanjuku import orchestrator
    from docich.hanjuku.os_layer import screen as screen_mod
    from test_hanjuku_card_damage_gate import cards, memory
    mem = memory(card='バルムンク', enemy='せいめいたい', enemy_hp=122,
                 ally='バタール', ally_hp=99, soldiers=(None, None))
    mem['chapter'] = 9
    mem['battle']['card_flow'] = {'card': 'バルムンク', 'stage': 'menu', 'note': 'test'}
    screen = cards(['バルムンク', 'バルムンク'])
    monkeypatch.setattr(screen_mod, 'parse', lambda frame, phase=None: screen)
    monkeypatch.setattr(orchestrator, 'classify', lambda frame: 'battle')

    class DummyFrame:
        def digest(self):
            return 'balmung-list-frame'

    state = {'policy': mem}
    out, updated = orchestrator.decide(DummyFrame(), state)
    assert out == [policy.pad('a')]
    assert updated['policy']['battle']['cards_selected'] == ['バルムンク']
