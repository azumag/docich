"""Regression coverage for selected card flows interrupted outside the list-open path."""
from types import SimpleNamespace

from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle as battle_flow
from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku.judgment import battle as battle_rules

from test_hanjuku_card_damage_gate import cards, memory


def _interrupted_memory(selected=True):
    mem = memory(enemy_hp=32, soldiers=(0, 0))
    cur = mem['battle']
    if selected:
        cur['cards_selected'].append('ゼンマイン')
    cur['card_flow'].update(stage='list', selection_planned=False,
                            announce_retried=selected)
    return mem


def _unclassified(mem):
    return next(r for r in reversed(mem['_records'])
                if r['decision'] == 'battle_card_unclassified')


def test_gate_close_books_a_selected_interrupted_flow():
    mem = _interrupted_memory()
    cur = mem['battle']

    assert battle_flow._card_gate_close(mem, cur, ['ゼンマイン']) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True


def test_gate_close_before_selection_keeps_the_kit_clean():
    mem = _interrupted_memory(selected=False)
    cur = mem['battle']

    assert battle_flow._card_gate_close(mem, cur, ['ゼンマイン']) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_selected'] == cur['cards_unclassified'] == []
    assert not mem.get('kit_spent')
    assert not any(r['decision'] == 'battle_card_unclassified' for r in mem['_records'])


def test_one_hit_close_books_a_selected_interrupted_flow():
    mem = _interrupted_memory()
    cur = mem['battle']
    cur.update(enemy_hp=1, enemy_soldiers=0, card_hp_unread=False,
               card_context_unclassified=False)

    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True


def test_retreat_never_closes_the_card_list(monkeypatch):
    # owner 2026-11-28: 退却は完全に廃止したため、退却のために一覧を閉じる分岐は無い。
    # 危険な戦況でも一覧は札を選ぶために開いたまま使う。
    mem = _interrupted_memory()
    cur = mem['battle']
    cur['card_flow']['announce_retried'] = False
    monkeypatch.setattr(menus, '_one_hit_lethal', lambda cur: False)
    monkeypatch.setattr(menus, '_hp1_resource_hold', lambda cur: False)
    monkeypatch.setattr(menus, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(menus, '_critical_safe_card', lambda *a, **kw: None)

    assert p.card_list_step(cards(['ゼンマイン']), mem) != [p.pad('b')]

    assert cur['card_flow'] is not None
    assert not any(r['decision'] == 'battle_hero_retreat_cancel_card' for r in mem['_records'])
    assert not cur.get('card_retreat_cancels')

def test_unclassified_close_uses_the_selection_ledger_after_a_retry_reset():
    mem = _interrupted_memory()
    cur = mem['battle']

    battle_rules._card_use_unclassified(mem, cur, 'テスト用の巻き戻し中断')

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['confirmation'] == 'unclassified'
    assert not any(r['decision'] == 'battle_card_open_unclassified' for r in mem['_records'])


def test_replacing_a_retry_reset_flow_books_the_selection():
    mem = _interrupted_memory()
    cur = mem['battle']

    battle_rules._begin_card_flow(
        mem, cur, {'card': 'ノリウツール', 'stage': 'list', 'survival': True},
        source='survival_menu')

    assert cur['card_flow']['card'] == 'ノリウツール'
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True


def test_monster_skill_menu_settles_the_interrupted_selected_flow():
    mem = _interrupted_memory()
    cur = mem['battle']
    cur['monster_menu_choices'] = menus.MONSTER_MENU_TOTAL_CHOICES + 1
    screen = SimpleNamespace(
        menu_rows=[SimpleNamespace(known=s, y=160 + i * 16)
                   for i, s in enumerate(['ふくらむ', 'シャウト', 'たまごに もどれ'])],
        egg_rows=[], menu_cursor=160)

    assert menus.monster_menu_step(screen, mem) == [menus.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True


def test_retreat_waits_for_retry_reset_flow_above_critical_hp(monkeypatch):
    # docich#5799: HP13では、announce retryでlistへ戻った選択済み・消費未確認
    # flowを退却openでsupersedeせず、同じ札の有界な再選択へ進める。
    mem = _interrupted_memory()
    cur = mem['battle']
    cur.update(ally_hp=13, start_ally_hp=24)
    cur['survival'] = {'opens': 12, 'pending_opens': 3, 'menu_ticks': 0}
    monkeypatch.setattr(battle_flow, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(battle_flow, '_critical_safe_card', lambda *a, **kw: None)
    monkeypatch.setattr(battle_flow, '_egg_before_retreat', lambda *a: False)

    assert battle_flow._hero_retreat_open(mem, cur) is None

    assert cur['card_flow']['stage'] == 'list'
    assert cur['card_flow']['card'] == 'ゼンマイン'
    assert cur['cards_unclassified'] == []
    assert 'hero_retreat' not in cur
    assert not any(r['decision'] == 'battle_retreat_preempted_card' for r in mem.get('_records', []))
    assert not any(r['decision'] == 'battle_card_unclassified' for r in mem.get('_records', []))

    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]
    assert cur['card_flow']['stage'] == 'announce'
    assert cur['card_flow']['selection_planned'] is True


def test_retreat_preempts_retry_reset_flow_at_immediate_loss_hp(monkeypatch):
    mem = _interrupted_memory()
    cur = mem['battle']
    cur['ally_hp'] = 4
    cur['survival'] = {'opens': 12, 'pending_opens': 3, 'menu_ticks': 0}
    monkeypatch.setattr(battle_flow, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(battle_flow, '_critical_safe_card', lambda *a, **kw: None)
    monkeypatch.setattr(battle_flow, '_egg_before_retreat', lambda *a: False)

    assert battle_flow._hero_retreat_open(mem, cur) is not None

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert any(r['decision'] == 'battle_retreat_preempted_card' for r in mem['_records'])


def test_retreat_supersede_books_retry_reset_flow_at_critical_hp_and_audit_downgrades(tmp_path, monkeypatch):
    mem = _interrupted_memory()
    cur = mem['battle']
    cur['ally_hp'] = 5
    cur['cards_selected'].append('ゼンマイン')
    cur['cards_unclassified'].append('ゼンマイン')
    cur['survival'] = {'opens': 12, 'pending_opens': 3, 'menu_ticks': 0}
    monkeypatch.setattr(battle_flow, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(battle_flow, '_critical_safe_card', lambda *a, **kw: None)
    monkeypatch.setattr(battle_flow, '_egg_before_retreat', lambda *a: False)

    for _ in range(battle_flow.RETREAT_CARD_GRACE_LIMIT):
        assert battle_flow._hero_retreat_open(mem, cur) is None
    assert cur['card_flow'] is not None
    assert battle_flow._hero_retreat_open(mem, cur) is not None

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン', 'ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True
    preempted = next(r for r in reversed(mem['_records'])
                     if r['decision'] == 'battle_retreat_preempted_card')
    metric = preempted['observed_metric']
    assert metric['card_confirmation'] == 'unclassified'
    assert metric['selected_interrupted'] is True
    assert metric['cards_selected'] == ['ゼンマイン', 'ゼンマイン']
    assert metric['cards_used'] == []
    assert metric['cards_unclassified'] == ['ゼンマイン', 'ゼンマイン']

    from docich.hanjuku import month_audit as audit
    from test_hanjuku_month_audit import FakeGh, state as audit_state

    value = audit_state(5, '5-3', 0)
    audit.observe(tmp_path, audit.capture(value), value, [preempted], now=1000)
    incident = audit.load_incidents(tmp_path)[0]
    assert incident['confidence'] == 'medium'
    assert incident['confidence_reasons'] == ['cards_unconfirmed_signature']
    battle = incident['battle_event']
    assert battle['cards_unconfirmed_only'] is True
    assert battle['cards_selected'] == ['ゼンマイン', 'ゼンマイン']
    assert battle['cards_remaining'] == ['ゼンマイン', 'ゼンマイン']
    assert battle['cards_unclassified'] == ['ゼンマイン', 'ゼンマイン']
    assert audit.file_issue(tmp_path, gh=FakeGh(),
                            scanner=lambda path: True)['status'] == 'no_incident'
