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


def screen(text='たまごをつかうきりふだたいきゃく', kind='egg_battle_menu'):
    value = Screen([], None, text, kind=kind)
    return value


def memory(castle=BOSS, general='ヘーゼル', uses=None):
    mem = {'chapter': 6, 'tick': 100, '_records': [],
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


def test_a_missing_egg_row_reveals_then_opens_the_carried_card(monkeypatch):
    # Owner 2026-11-25: ボス戦や敵召喚獣戦で卵が使えず切り札を持っている場合、
    # 切り札を使う。A greyed/spent egg row drops out of the white OCR, so the
    # egg row is absent while the count is unknown (no measured zero). The old
    # gate needed the row or a measured zero and attacked with the card in
    # hand (card_miss #5259/#5322/#5331/#5352/#5362/#5373/#5381/#5398).
    mem = memory()  # no egg_uses: the count is unknown, not zero
    mem['battle']['planned_cards'] = ['エンジェリン']
    assert B.egg_battle_step(screen(text='こうげきもうこうげき'), mem) == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    assert any(r['decision'] == 'battle_summon_card_scroll' for r in mem['_records'])

    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: 'here')
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    assert B.egg_battle_step(screen(text='こうげききりふだ'), mem) == [B.pad('a')]
    assert mem['battle']['card_flow']['card'] == 'エンジェリン'
    # The next card in the same fight gets its own bounded look at the box.
    assert mem['battle']['summon_card_scroll'] == 0
    assert any(r['decision'] == 'battle_summon_card_open' for r in mem['_records'])


def test_a_message_panel_does_not_spend_the_card_scroll_budget():
    # A message inside the summon keeps the egg-battle latch and can mention
    # こうげき; it must not burn the bounded scroll before the real box draws.
    mem = memory()
    mem['battle']['planned_cards'] = ['エンジェリン']
    B.egg_battle_step(Screen([], None, 'てきのこうげき', kind='text'), mem)
    assert not mem['battle'].get('summon_card_scroll')
    assert not any(r['decision'] == 'battle_summon_card_scroll' for r in mem['_records'])


def test_an_exhausted_card_scroll_re_arms_after_a_gap():
    # card_miss #5600: the bounded reveal ran its three downs and the row
    # never read -- one eaten press or a half-drawn frame is not proof the
    # row is absent, so a carried card earns another bounded look.
    mem = memory()
    mem['battle'].update(planned_cards=['エンジェリン'], summon_card_scroll=3,
                         summon_card_exhausted_tick=0)
    acts = B.egg_battle_step(screen(text='こうげきもうこうげき'), mem)
    assert acts == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    assert mem['battle']['summon_card_rearms'] == 1
    assert any(r['decision'] == 'battle_summon_card_rearm' for r in mem['_records'])


def test_a_fresh_scroll_exhaustion_holds_the_attack_for_the_rearm_grace(monkeypatch):
    # docich#5632: the first exhaustion only arms the re-arm gap, and the egg
    # battle menu is a command wait -- so こうげき must not be confirmed while
    # a re-arm can still recover the row.  Holding also keeps the same
    # observation from filing card_miss (the old spec did both at once).
    mem = memory()
    mem['battle'].update(planned_cards=['エンジェリン'], summon_card_scroll=3)
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: 'here')
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    acts = B.egg_battle_step(screen(text='こうげきもうこうげき'), mem)
    assert acts == []
    assert mem['battle']['summon_card_exhausted_tick'] == 100
    assert not mem['battle'].get('summon_card_rearms')
    assert mem['battle']['summon_card_scroll_out'] is True
    exhausted = [r for r in mem['_records'] if r['decision'] == 'battle_summon_card_exhausted']
    assert len(exhausted) == 1
    assert exhausted[0]['observed_metric']['rearm_pending'] is True
    assert not any(r['decision'] == 'battle_egg_general_attack' for r in mem['_records'])
    assert not any(r['decision'] == 'battle_boss_card_missed' for r in mem['_records'])
    assert not any(r['decision'] == 'battle_summon_card_reveal_failed' for r in mem['_records'])

    # The grace is a wait, not a spent budget: once the gap elapses the re-arm
    # re-opens the bounded scroll instead of attacking.
    mem['tick'] = 100 + B.SUMMON_CARD_REARM_TICKS
    assert B.egg_battle_step(screen(text='こうげきもうこうげき'), mem) == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    assert mem['battle']['summon_card_rearms'] == 1
    assert not mem['battle'].get('summon_card_scroll_out')
    assert any(r['decision'] == 'battle_summon_card_rearm' for r in mem['_records'])
    assert not any(r['decision'] == 'battle_boss_card_missed' for r in mem['_records'])


def test_the_card_rearm_stops_at_its_limit(monkeypatch):
    # docich#5632: once every re-arm is spent the attack fallback is a reveal
    # failure, not a skipped card -- the durable evidence is
    # battle_summon_card_reveal_failed and no card_miss is filed.
    mem = memory()
    mem['battle'].update(planned_cards=['エンジェリン'], summon_card_scroll=3,
                         summon_card_rearms=B.SUMMON_CARD_REARM_LIMIT,
                         summon_card_exhausted_tick=0)
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: 'here')
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    acts = B.egg_battle_step(screen(text='こうげきもうこうげき'), mem)
    assert acts == [B.pad('a')]
    assert not any(r['decision'] == 'battle_summon_card_rearm' for r in mem['_records'])
    assert mem['battle']['summon_card_reveal_failed'] is True
    failed = [r for r in mem['_records']
              if r['decision'] == 'battle_summon_card_reveal_failed']
    assert len(failed) == 1
    assert failed[0]['card'] == 'エンジェリン'
    assert failed[0]['observed_metric']['rearms'] == B.SUMMON_CARD_REARM_LIMIT
    assert not any(r['decision'] == 'battle_boss_card_missed' for r in mem['_records'])


def test_an_opened_card_resets_the_scroll_and_rearm_budget(monkeypatch):
    mem = memory()
    mem['battle'].update(planned_cards=['エンジェリン'], summon_card_scroll=2,
                         summon_card_rearms=2, summon_card_exhausted_tick=50)
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: 'here')
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    assert B.egg_battle_step(screen(text='こうげききりふだ'), mem) == [B.pad('a')]
    battle = mem['battle']
    assert battle['summon_card_scroll'] == 0
    assert battle['summon_card_rearms'] == 0
    assert 'summon_card_exhausted_tick' not in battle
    assert any(r['decision'] == 'battle_summon_card_open' for r in mem['_records'])


def test_the_missed_card_record_carries_the_battle_identity(monkeypatch):
    # #5398 was filed as 戦闘: 不明 / 城: 不明 / 画面: None because the emit
    # omitted the battle identity the month audit projects.  docich#5632:
    # a scroll exhaustion now holds the attack, so card_miss only files when
    # the row was visible yet still unopened (こうげき confirmed past it).
    mem = memory()
    mem['battle'].update(planned_cards=['ノリウツール'])
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 176))
    monkeypatch.setattr(B, '_menu_move_batch',
                        lambda s, label, target: 'here' if label == 'こうげき' else None)
    B.egg_battle_step(screen(text='こうげききりふだ'), mem)
    row = [r for r in mem['_records'] if r['decision'] == 'battle_boss_card_missed'][-1]
    assert row['ally'] == 'ヘーゼル' and row['enemy'] == 'ハードロボ'
    assert row['castle'] == BOSS and row['side'] == 'attack'
    assert row['screen_kind'] == 'egg_battle_menu'
    metric = row['observed_metric']
    assert metric['rearms'] == 0
    assert metric['summon_card_scroll_out'] is False
    assert metric['summon_card_exhausted_tick'] is None


def test_a_post_exhaustion_attack_with_a_visible_row_still_files_card_miss(monkeypatch):
    # docich#5632: a first reveal exhaustion leaves summon_card_scroll_out.
    # If the きりふだ row then reads but the turn still confirms こうげき,
    # that IS a skipped card -- card_miss stays, and the metric lets the
    # audit tell it from a mid-re-arm wait and a final reveal failure.
    mem = memory()
    mem['battle'].update(planned_cards=['ノリウツール'], summon_card_scroll=3,
                         summon_card_scroll_out=True,
                         summon_card_exhausted_tick=0)
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 176))
    monkeypatch.setattr(B, '_menu_move_batch',
                        lambda s, label, target: 'here' if label == 'こうげき' else None)
    B.egg_battle_step(screen(text='こうげききりふだ'), mem)
    row = [r for r in mem['_records'] if r['decision'] == 'battle_boss_card_missed'][-1]
    assert row['card'] == 'ノリウツール'
    metric = row['observed_metric']
    assert metric['scrolls'] == 3
    assert metric['rearms'] == 0
    assert metric['summon_card_scroll_out'] is True
    assert metric['summon_card_exhausted_tick'] == 0
    assert not any(r['decision'] == 'battle_summon_card_reveal_failed'
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


def test_an_interrupted_defense_kit_read_is_closed_before_reveal():
    # A read-only kit probe cannot advance on the summon menu either.  Its
    # pre-summon purpose is already gone, so close it fail-closed and let the
    # real carried card reveal instead of attacking past it.
    mem = memory()
    battle = mem['battle']
    battle.update(planned_cards=['エンジェリン'],
                  card_flow={'card': None, 'stage': 'menu', 'read_kit': True})
    assert B.egg_battle_step(screen(text='こうげきもうこうげき'), mem) == [B.pad('down')]
    assert battle['card_flow'] is None
    assert battle['defense_egg_kit_read']['read_done'] is True
    assert any(r['decision'] == 'battle_defense_kit_read_unread' for r in mem['_records'])
    assert any(r['decision'] == 'battle_summon_card_scroll' for r in mem['_records'])


def test_a_frozen_announce_flow_holds_attack_then_releases(monkeypatch):
    # docich#5612: after one summon-menu card open (scroll counter reset to
    # 0), a selected-but-unconfirmed card flow can be left frozen when the
    # card list is replaced.  The remaining carried card must not be skipped
    # by a melee confirmation during the bounded misread grace.
    mem = memory()
    battle = mem['battle']
    battle.update({
        'planned_cards': ['ミックミー', 'ノリウツール'],
        'cards_selected': ['ミックミー'],
        'summon_card_scroll': 0,
        'card_flow': {'card': 'ミックミー', 'stage': 'announce',
                      'selection_planned': True},
    })
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: 'here')
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')

    for _ in range(B.SUMMON_CARD_FLOW_STALL_LIMIT - 1):
        assert B.egg_battle_step(screen(text='こうげきもうこうげき'), mem) == []
        assert battle['card_flow']['stage'] == 'announce'
        assert not any(r['decision'] == 'battle_egg_general_attack'
                       for r in mem['_records'])
        assert not any(r['decision'] == 'battle_boss_card_missed'
                       for r in mem['_records'])

    assert B.egg_battle_step(screen(text='こうげきもうこうげき'), mem) == [B.pad('down')]
    assert battle['card_flow'] is None
    assert battle['cards_unclassified'] == ['ミックミー']
    assert not battle.get('summon_card_flow_stall')
    assert any(r['decision'] == 'battle_card_unclassified'
               and r.get('card') == 'ミックミー' for r in mem['_records'])
    assert not any(r['decision'] == 'battle_boss_card_missed'
                   for r in mem['_records'])

    assert B.egg_battle_step(screen(text='こうげききりふだたいきゃく'), mem) == [B.pad('a')]
    assert battle['card_flow']['card'] == 'ノリウツール'
    assert any(r['decision'] == 'battle_summon_card_open' for r in mem['_records'])


def test_boss_only_card_is_not_reported_as_missed_before_boss_entry(monkeypatch):
    # VERIFY-5458 path 2: a carried Balmung is intentionally unavailable
    # during an ordinary battle launched from a boss sortie.
    mem = memory(castle='セレーネ')
    mem['battle'].update(enemy='ニンフ', planned_cards=['バルムンク'],
                         summon_card_scroll=B.SUMMON_CARD_SCROLL_LIMIT)
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: 'here')
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')

    B.egg_battle_step(screen(text='こうげき'), mem)

    assert not any(r['decision'] == 'battle_boss_card_missed' for r in mem['_records'])
    assert not mem['battle'].get('boss_card_missed_noted')


def test_ordinary_carried_card_is_still_reported_when_boss_only_card_is_filtered(monkeypatch):
    # docich#5632: scroll exhaustion now holds instead of emitting, so the
    # miss path is a visible-but-unopened きりふだ row.
    mem = memory(castle='セレーネ')
    mem['battle'].update(enemy='ニンフ', planned_cards=['バルムンク', 'エンジェリン'])
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 176))
    monkeypatch.setattr(B, '_menu_move_batch',
                        lambda s, label, target: 'here' if label == 'こうげき' else None)

    B.egg_battle_step(screen(text='こうげききりふだ'), mem)

    rows = [r for r in mem['_records'] if r['decision'] == 'battle_boss_card_missed']
    assert len(rows) == 1
    assert rows[0]['card'] == 'エンジェリン'


def test_boss_only_card_remains_eligible_after_measured_boss_entry(monkeypatch):
    mem = memory()
    mem['battle'].update(planned_cards=['バルムンク'], entry_evidence='measured_boss_entry')
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 176))
    monkeypatch.setattr(B, '_menu_move_batch',
                        lambda s, label, target: 'here' if label == 'こうげき' else None)

    B.egg_battle_step(screen(text='こうげききりふだ'), mem)

    rows = [r for r in mem['_records'] if r['decision'] == 'battle_boss_card_missed']
    assert len(rows) == 1
    assert rows[0]['card'] == 'バルムンク'
