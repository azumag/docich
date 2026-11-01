"""Owner 2026-11-02 (VSヴィーナス at オトラント): a timed-out card announce means
the list is still open — the carry may never have fired. Retry the same card
once instead of backing out and retreating with it unused."""
from docich import hanjuku_policy as p
from test_hanjuku_card_damage_gate import memory, cards


def _first_timeout(mem, names):
    # CARD_ANNOUNCE_LIMIT is 6: the 7th call hits the retry branch.
    for _ in range(7):
        p.card_list_step(cards(names), mem)


def test_announce_timeout_retries_same_card_once():
    # ゼンマイン deals 32: lethal against a 32-HP enemy, so the gate allows.
    mem = memory(enemy_hp=32, soldiers=(0, 0))
    flow = mem['battle']['card_flow']
    flow.update(stage='announce', selection_planned=True)
    _first_timeout(mem, ['ゼンマイン'])
    assert flow.get('announce_retried') is True
    assert flow['stage'] == 'list'
    assert any(r.get('decision') == 'battle_card_announce_retry'
               for r in mem.get('_records') or [])


def test_announce_retry_still_fires_card_then_second_timeout_backs_out():
    mem = memory(enemy_hp=32, soldiers=(0, 0))
    flow = mem['battle']['card_flow']
    flow.update(stage='announce', selection_planned=True)
    _first_timeout(mem, ['ゼンマイン'])
    assert flow.get('announce_retried') is True and flow['stage'] == 'list'
    # List stage re-selects the carried card (A) then re-enters announce.
    out = p.card_list_step(cards(['ゼンマイン']), mem)
    assert out == [p.pad('a')]
    assert flow['stage'] == 'announce' and flow.get('selection_planned')
    # Second timeout gives up bounded — unclassified and B out.
    last = None
    for _ in range(12):
        last = p.card_list_step(cards(['ゼンマイン']), mem)
        if last == [p.pad('b')]:
            break
    assert last == [p.pad('b')]


def test_announce_retry_consumed_card_falls_into_missing_path():
    mem = memory(enemy_hp=32, soldiers=(0, 0))
    flow = mem['battle']['card_flow']
    flow.update(stage='announce', selection_planned=True)
    _first_timeout(mem, ['ゼンマイン'])
    assert flow.get('announce_retried') is True and flow['stage'] == 'list'
    # The card is gone from the list (it actually fired): the missing-card
    # path classifies it instead of re-selecting forever.
    outs = [p.card_list_step(cards(['イッテツーン']), mem) for _ in range(6)]
    assert mem['battle']['card_flow'] is None
    assert 'ゼンマイン' in mem['battle'].get('cards_missing', [])


def test_announce_retry_gate_refusal_stays_bounded():
    # The original fixture's ゼンマイン vs クミン 27 fails the damage gate:
    # the retry still exits boundedly through the refusal path, not loop.
    mem = memory()
    flow = mem['battle']['card_flow']
    flow.update(stage='announce', selection_planned=True)
    _first_timeout(mem, ['ゼンマイン'])
    assert flow.get('announce_retried') is True and flow['stage'] == 'list'
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert mem['battle']['card_flow'] is None


def test_announce_retry_is_bounded_to_one_attempt():
    mem = memory(enemy_hp=32, soldiers=(0, 0))
    flow = mem['battle']['card_flow']
    flow.update(stage='announce', selection_planned=True, announce_retried=True)
    # A flow already retried goes straight to unclassified + B on timeout.
    last = None
    for _ in range(10):
        last = p.card_list_step(cards(['ゼンマイン']), mem)
        if last == [p.pad('b')]:
            break
    assert last == [p.pad('b')]
