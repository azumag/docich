"""A greyed egg row in a boss fight marks the general as unable to summon.

g1163 chapter 6 2026-11-09 13:36: ヘーゼル entered ハードロボ (152 HP), the egg
row read as unavailable, the fight dealt no damage, and the same general was
queued for the next wave.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import battle_menus as B
from docich.hanjuku.os_layer.screen import Screen

BOSS = chart.boss_castle(6)


def screen(text='たまごをつかうきりふだたいきゃく', kind='egg_battle_menu', egg_rows=None):
    return Screen([], None, text, kind=kind, egg_rows=egg_rows or [])


def memory(castle=BOSS, general='ヘーゼル', uses=None, chapter=6):
    mem = {'chapter': chapter, 'tick': 100, '_records': [],
           'attack': {'general': general, 'castle': castle, 'side': 'attack', 'step': 'A:x:M1'},
           'battle': {'ally': general, 'enemy': 'ハードロボ', 'step': 'A:x:M1',
                      'castle': castle, 'side': 'attack'},
           'sorties': {'A:x:M1': {'general': general, 'target': castle,
                                  'status': 'arrived', 'tick': 90}}}
    if uses is not None:
        mem['egg_uses'] = {general: uses}
    return mem


def test_a_boss_entry_with_a_grey_egg_row_is_logged_but_not_benched():
    """g1163 2026-11-09 14:55: ペコリーノ's grey row still took 152 -> 70."""
    mem = memory()
    B.egg_battle_step(screen(text='こうげき'), mem)
    assert not (mem.get('egg_uses') or {})
    assert any(r['decision'] == 'egg_row_unavailable' for r in mem['_records'])
    assert not any(r['decision'] == 'egg_unusable_recorded' for r in mem['_records'])


def test_a_normal_castle_battle_never_logs_it():
    mem = memory(castle='セレーネ')
    B.egg_battle_step(screen(text='こうげき'), mem)
    assert not any(r['decision'] == 'egg_row_unavailable' for r in mem['_records'])

def test_a_carried_card_is_opened_before_attacking_a_summon(monkeypatch):
    # Owner 2026-11-22: enemy summon + our general used こうげき while a
    # carried card was still in planned_cards; the card menu was usable.
    mem = memory()
    mem['battle']['planned_cards'] = ['エンジェリン']
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 176))
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    acts = B.egg_battle_step(screen(text='こうげききりふだたいきゃく'), mem)
    assert acts == [B.pad('a')]
    assert mem['battle']['card_flow']['card'] == 'エンジェリン'
    assert any(r['decision'] == 'battle_summon_card_open' for r in mem['_records'])

def test_the_summon_card_row_is_revealed_by_scrolling_down():
    # Owner 2026-11-22: in a summon fight きりふだ sits below たまごをつかう;
    # the command box must scroll down before the card can be opened.
    mem = memory(uses=0)
    mem['battle']['planned_cards'] = ['エンジェリン']
    acts = B.egg_battle_step(screen(text='たまごをつかうおくのて'), mem)
    assert acts == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    assert any(r['decision'] == 'battle_summon_card_scroll' for r in mem['_records'])

def test_a_carried_card_scrolls_when_the_grey_egg_row_is_omitted():
    # #5259 / #5331: the grey egg row can disappear from OCR while the
    # lower carried-card row remains in the command box.
    mem = memory()
    mem['battle']['enemy'] = 'ゾンビ'
    mem['battle']['planned_cards'] = ['イッテツーン']
    rows = [
        SimpleNamespace(name='ヘーゼル', hp=30, side='ally', y=176),
        SimpleNamespace(name='ゾンビ', hp=30, side='enemy', y=192),
    ]

    actions = B.egg_battle_step(
        screen(text='こうげきたいきゃく', egg_rows=rows), mem)

    assert actions == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    scroll = [r for r in mem['_records']
              if r['decision'] == 'battle_summon_card_scroll'][-1]
    assert scroll['observed_metric']['egg_row_visible'] is False
    assert scroll['observed_metric']['own_attack_menu'] is True
    assert scroll['observed_metric']['cards_left'] == ['イッテツーン']
    assert not any(r['decision'] in {
        'battle_egg_general_attack', 'battle_boss_card_missed'
    } for r in mem['_records'])


def test_issue_5331_uses_the_confirmed_carried_kit_when_battle_plan_is_empty(monkeypatch):
    # The summon-battle context may omit planned_cards even though sortie
    # confirmation bound a card. Keep the measured-kit fallback covered.
    mem = memory()
    mem['battle']['enemy'] = 'ゾンビ'
    monkeypatch.setattr(B, '_confirmed_carried_kit', lambda *_: ['イッテツーン'])
    rows = [
        SimpleNamespace(name='ヘーゼル', hp=30, side='ally', y=176),
        SimpleNamespace(name='ゾンビ', hp=30, side='enemy', y=192),
    ]

    actions = B.egg_battle_step(
        screen(text='こうげきたいきゃく', egg_rows=rows), mem)

    assert actions == [B.pad('down')]
    scroll = [r for r in mem['_records']
              if r['decision'] == 'battle_summon_card_scroll'][-1]
    assert scroll['observed_metric']['cards_left'] == ['イッテツーン']
    assert scroll['observed_metric']['own_attack_menu'] is True


def test_issue_5322_chapter11_reveals_confirmed_card_when_grey_egg_row_is_omitted():
    # #5322: the audit confirmed ころぼぐんだん was carried, but the grey
    # egg row was absent from OCR. Only the measured boss/summon context may
    # authorize a navigation-only scroll; the card itself is not selected yet.
    card = 'ころぼぐんだん'
    mem = memory(castle=chart.boss_castle(11), general='ヘーゼル', chapter=11)
    mem['battle'].update(enemy='ゾンビ', planned_cards=[card])
    rows = [
        SimpleNamespace(name='ヘーゼル', hp=30, side='ally', y=176),
        SimpleNamespace(name='ゾンビ', hp=30, side='enemy', y=192),
    ]

    actions = B.egg_battle_step(
        screen(text='こうげきたいきゃく', egg_rows=rows), mem)

    assert actions == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    scroll = [r for r in mem['_records']
              if r['decision'] == 'battle_summon_card_scroll'][-1]
    assert scroll['observed_metric']['cards_left'] == [card]
    assert scroll['observed_metric']['own_attack_menu'] is True
    assert not any(r['decision'] == 'battle_boss_card_missed' for r in mem['_records'])


def test_hidden_egg_row_fallback_requires_a_live_own_attack_menu():
    cases = [
        (BOSS, '不明', True),       # no recognized enemy summon
        ('セレーネ', 'ゾンビ', True), # not a boss-castle battle
        (BOSS, 'ゾンビ', False),    # no readable own-general panel
    ]
    for castle, enemy, include_ally in cases:
        mem = memory(castle=castle)
        mem['battle']['planned_cards'] = ['イッテツーン']
        rows = ([SimpleNamespace(name='ヘーゼル', hp=30, side='ally', y=176)]
                if include_ally else [])
        rows.append(SimpleNamespace(name=enemy, hp=30, side='enemy', y=192))

        B.egg_battle_step(screen(text='こうげきたいきゃく', egg_rows=rows), mem)

        assert 'summon_card_scroll' not in mem['battle']
        assert not any(r['decision'] == 'battle_summon_card_scroll'
                       for r in mem['_records'])


def test_interrupted_opening_card_flow_resumes_the_exact_carried_card(monkeypatch):
    # A chart card was planned in the normal battle menu, but the enemy's
    # summon dialogue replaced that menu before the card list opened.
    mem = memory(uses=0)
    battle = mem['battle']
    battle.update({
        'planned_cards': ['ミックミー', 'ノリウツール'],
        'cards_selected': ['ミックミー'],
        'cards_used': ['ミックミー'],
        'card_flow': {'card': 'ノリウツール', 'stage': 'menu',
                      'tactic_id': 'opening:2'},
    })

    # The spent egg row is grey and omitted from OCR.  The carried card row
    # remains below the visible box, so the summon handler must scroll.
    assert B.egg_battle_step(screen(text='こうげき'), mem) == [B.pad('down')]
    assert battle.get('card_flow') is None
    assert battle['summon_card_resume']['card'] == 'ノリウツール'
    assert not any(r['decision'] == 'battle_egg_general_attack' for r in mem['_records'])

    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 207))
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    assert B.egg_battle_step(screen(text='こうげききりふだたいきゃく'), mem) == [B.pad('a')]

    flow = battle['card_flow']
    assert flow['card'] == 'ノリウツール'
    assert flow['stage'] == 'list'
    assert 'force_boss_card' not in flow  # the ordinary damage gate still applies
    opened = [r for r in mem['_records'] if r['decision'] == 'battle_summon_card_open'][-1]
    assert opened['observed_metric']['resumed_pending_flow'] is True
    assert opened['observed_metric']['resumed_tactic_id'] == 'opening:2'


def test_selected_or_announcement_card_flow_is_not_reopened():
    mem = memory(uses=0)
    battle = mem['battle']
    battle.update({
        'planned_cards': ['ノリウツール'],
        'cards_selected': ['ノリウツール'],
        'card_flow': {'card': 'ノリウツール', 'stage': 'announce',
                      'selection_planned': True},
    })
    B.egg_battle_step(screen(text='こうげききりふだたいきゃく'), mem)
    assert battle['card_flow']['stage'] == 'announce'
    assert not any(r['decision'] == 'battle_summon_card_open' for r in mem['_records'])
