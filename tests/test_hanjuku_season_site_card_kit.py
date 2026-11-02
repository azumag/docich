"""A 四季王 site sortie leaves behind a chart card the army does not hold (#3048).

g1075 2026-10-30 08:14-08:16 (v327): 4-B1 sent アルテミス, scrolled to the
list's last row (クースカン..バグストーム(24)) and waited for マグネガキン(27)
and ファバード(31), cancelling and restarting the sortie every ~30 s. The base
boss kit is strict because a month plan buys its cards back
(strategy.boss_cards), but that plan covers the single boss order only: the
four site orders had nothing that could ever supply the missing cards.
"""
from docich import hanjuku_chart as chart, hanjuku_policy as policy
from docich.hanjuku.strategy.kit import _deploy_cards, _strict_boss_cards
from test_hanjuku_sortie_evidence import actor_panel, measured_card_select

BOTTOM = ('クースカン', 'バルムンク', 'ミックミー', 'バグストーム')   # g1075 08:15:14-19


def memory():
    normal = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
    return {'rare_scan_month': 'chapter-4:unknown', 'chapter': 4, 'active': '4-B1',
            'captured': normal, 'lost': [], 'variant': 'chart', 'orders': {'4-B1': 'pending'},
            'picked': [], 'sortie_general': {'4-B1': 'アルテミス'}}


def panel():
    screen = measured_card_select(BOTTOM, selected=3, stocks=('5', '9', '4', '50'), remaining='3')
    return actor_panel(screen, 'アルテミス')


def test_the_site_kit_is_not_strict_but_the_chapter_boss_kit_is():
    site = next(o for o in chart.orders(4) if o['step'] == '4-B1')
    assert not _strict_boss_cards(site, memory())
    boss = next(o for o in chart.orders(3) if o['target'] == 'ボス')
    assert _strict_boss_cards(boss, {'chapter': 3})
    # The enemy-egg budget (card IDs under 48) applies like any other sortie.
    assert sum(policy.CARD_IDS[c] for c in _deploy_cards(site, memory())) < 48


def test_g1075_site_sortie_leaves_the_missing_cards_and_goes_on():
    mem = memory()
    for _ in range(20):
        actions = policy.deploy_step(panel(), mem)
        if mem['_records'][-1]['decision'] == 'sortie_input':
            break
    records = mem['_records']
    assert 'boss_card_selection_unconfirmed' not in [r['decision'] for r in records]
    dropped = [r['card'] for r in records if r['decision'] == 'card_dropped']
    assert dropped == ['エンジェリン', 'マグネガキン', 'ファバード']
    # Then a card the list does hold is picked; the sortie is still this one.
    assert records[-1]['decision'] == 'sortie_input' and records[-1]['card'] in BOTTOM
    assert actions == [policy.pad('up')]
    assert mem['orders']['4-B1'] == 'pending' and mem['active'] == '4-B1'
