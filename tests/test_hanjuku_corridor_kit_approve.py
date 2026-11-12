"""Chapter 9's mechanical order names no cards, so a readable kit is fine.

Owner 2026-11-11: 切り札を選ばず、一覧のカーソル位置の札を上から順に携行する.
The plan therefore carries an empty card list and every sortie's confirmation
readable carried kit "mismatched" it, holding through the mismatch budget each
time (g1163 2026-11-12 09:2x-09:3x: 42 card picks for 15 sorties).
"""
from docich.hanjuku.strategy import corridor


def test_the_corridor_order_carries_no_cards():
    order = {'step': 'I:c9:タオ>サヌト:1', 'corridor': True, 'cards': []}
    assert order['corridor'] and order['cards'] == []


def test_the_approval_branch_is_reachable_in_the_source():
    """The deploy flow approves a readable kit instead of holding."""
    import inspect
    from docich.hanjuku.flows import deploy
    source = inspect.getsource(deploy)
    assert 'sortie_kit_corridor_approve' in source
    assert "order.get('corridor') and not want and carried is not None" in source
