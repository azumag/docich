"""docich#4999 regression: 退却の前に残った切り札を使い切る (owner 2026-11-22)。

g1190-ch1v5 (VERIFY-4999.md): シナモンが選択済み・使用未確認の切り札を残して
退却し、監査の battle_retreat_with_resources が立った。オーナー判断により、
危険HPの退却は「残り札の有界な使用」を先に試す。g460 (カードメニュー待ちで
将軍死亡) の線引きとして、HP <= RETREAT_RESOURCE_FLOOR_HP(4) の即死帯では
使用待ち・使用遠回りを打ち切り、従来どおり退却を優先する。
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku.judgment import battle as rules
from test_hanjuku_survival import battle as battle_screen, menu


def _cur(**over):
    """危険HPの攻撃戦 (退却判定成立): シャルドネ vs 卵なしアニス 6 vs 37。"""
    cur = {'enemy': 'アニス', 'ally': 'シャルドネ', 'enemy_hp': 37,
           'ally_hp': 6, 'start_enemy_hp': 87, 'start_ally_hp': 56,
           'side': 'attack', 'step': None, 'cards_used': [],
           'card_evidence_version': 1}
    cur.update(over)
    return cur


def _mem(**over):
    # 卵は観測済み 0 として固定し、切り札経路だけを検証する。
    return {'chapter': 1, 'tick': 1, 'battle': _cur(**over),
            'egg_uses': {'シャルドネ': 0}}


def _card_list(names, selected=0):
    return menu(tuple(names), selected=selected, kind='text')


def test_retreat_immediate_loss_is_the_g460_floor():
    assert rules._retreat_immediate_loss({'ally_hp': None}) is False
    assert rules._retreat_immediate_loss({'ally_hp': 0}) is False
    assert rules._retreat_immediate_loss({'ally_hp': 4}) is True
    assert rules._retreat_immediate_loss({'ally_hp': 5}) is False
    assert rules._retreat_immediate_loss({'ally_hp': 12}) is False


def test_retreat_remaining_cards_is_the_audit_multiset_rule():
    assert rules._retreat_remaining_cards(
        {'cards_selected': ['a', 'a', 'b'], 'cards_used': ['a']}) == ['a', 'b']
    assert rules._retreat_remaining_cards({'cards_used': ['a']}) == []
    assert rules._retreat_remaining_cards(
        {'cards_selected': [None, 'b'], 'cards_used': []}) == ['b']


def test_retreat_cards_to_spend_bounds():
    cur = {'cards_selected': ['フットバース'], 'cards_used': []}
    assert rules._retreat_cards_to_spend(cur) is True
    # 使用試行を消化済みの同名札はもう回さない。
    assert rules._retreat_cards_to_spend(
        {**cur, 'cards_spend_retry': ['フットバース']}) is False
    # 救済メニューの既存 bound (枯渇・一覧読み上限) で遠回りは止まる。
    for rescue in ({'exhausted': True}, {'cards_exhausted': True},
                   {'cards_checked': 3}):
        assert rules._retreat_cards_to_spend({**cur, 'survival': rescue}) is False
    # 確定使用の差分が空なら遠回りしない。
    assert rules._retreat_cards_to_spend(
        {'cards_selected': ['フットバース'], 'cards_used': ['フットバース']}) is False


def test_remaining_card_opens_survival_before_retreat():
    """使用未確認の残り札がある限り、退却メニューではなく救済メニューを開く。"""
    mem = _mem(cards_selected=['フットバース'])
    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    cur = mem['battle']
    assert cur['command_open_pending']['owner'] == 'survival'
    assert not cur.get('hero_retreat')
    assert any(r['decision'] == 'battle_cards_before_retreat'
               for r in mem['_records'])


def test_retreat_menu_detours_to_survival_and_spends_the_card():
    """退却メニューの手前でも残り札があれば救済メニューへ回し、実一覧で選ぶ。"""
    mem = _mem(cards_selected=['フットバース'], hero_retreat={'opens': 1})
    cur = mem['battle']
    assert p.battle_menu_step(menu(), mem) == [p.pad('down')]
    assert not cur['hero_retreat'].get('selected')
    assert p.battle_menu_step(menu(selected=1), mem) == [p.pad('a')]
    assert cur['survival']['cards_checked'] == 1
    assert cur['card_flow']['stage'] == 'list' and cur['card_flow']['survival']
    # 一覧に残った未選択札は通常の候補として選ばれる
    # (battle_hero_retreat_cancel_card に潰されないことの固定)。
    assert p.card_list_step(_card_list(['フットバース']), mem) == [p.pad('a')]
    assert cur['cards_selected'] == ['フットバース', 'フットバース']
    assert cur['card_flow']['stage'] == 'announce'
    assert cur['survival']['cards_attempted'] == ['フットバース']


def test_unconfirmed_listed_card_gets_one_spend_retry():
    """使用未確認の同名札は、退却待ちの間だけ1回の再選択 (cards_spend_retry) を得る。"""
    mem = _mem(cards_selected=['フットバース'],
               survival={'opens': 1, 'menu_ticks': 1, 'cards_checked': 1,
                         'cards_attempted': ['フットバース'],
                         'card_counts_at_selection': {'フットバース': 1},
                         'listed_cards': ['フットバース']},
               card_flow={'card': None, 'stage': 'list', 'survival': True,
                          'list_ticks': 0, 'counts': {'フットバース': 1},
                          'count_readings': 1})
    cur = mem['battle']
    # 2回一致した実一覧の後で、使用未確認の同名札を1回だけ再選択する。
    assert p.card_list_step(_card_list(['フットバース']), mem) == [p.pad('a')]
    assert cur['cards_spend_retry'] == ['フットバース']
    assert cur['cards_selected'] == ['フットバース', 'フットバース']
    assert cur['card_flow']['stage'] == 'announce'


def test_spent_retries_fall_through_to_the_retreat_row():
    """使い切った後は通常どおり たいきゃく を選ぶ。"""
    mem = _mem(cards_selected=['フットバース'],
               cards_spend_retry=['フットバース'], hero_retreat={'opens': 1})
    cur = mem['battle']
    assert p.battle_menu_step(menu(), mem) == [p.pad('down')]
    assert p.battle_menu_step(menu(selected=1), mem) == [p.pad('down')]
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert cur['hero_retreat']['selected'] == 1
    assert any(r['decision'] == 'battle_hero_retreat_select' for r in mem['_records'])


def test_unspendable_cards_fall_back_to_retreat():
    """救済メニューが枯渇済みなら残り札があっても退却へ落ちる。"""
    mem = _mem(cards_selected=['フットバース'],
               survival={'exhausted': True, 'cards_checked': 3,
                         'cards_attempted': []},
               hero_retreat={'opens': 1})
    cur = mem['battle']
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert cur['hero_retreat']['selected'] == 1
    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    assert cur['command_open_pending']['owner'] == 'retreat'


def test_immediate_loss_floor_preempts_and_skips_the_spend():
    """HP4以下では選択待ち・残り札遠回りを打ち切り、そのまま退却を開く (g460)。"""
    mem = _mem(ally_hp=4, cards_selected=['フットバース'],
               card_flow={'stage': 'announce', 'card': 'フットバース',
                          'selection_planned': True, 'enemy_hp_at_open': 37})
    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    cur = mem['battle']
    assert cur['command_open_pending']['owner'] == 'retreat'
    assert cur['cards_unclassified'] == ['フットバース']
    assert not cur.get('survival')
    preempted = next(r for r in mem['_records']
                     if r['decision'] == 'battle_retreat_preempted_card')
    assert preempted['card'] == 'フットバース'


def test_immediate_loss_floor_goes_straight_to_the_retreat_row():
    """メニュー側でも HP4以下なら残り札の遠回りをせず退却行を選ぶ。"""
    mem = _mem(ally_hp=4, cards_selected=['フットバース'],
               hero_retreat={'opens': 1})
    cur = mem['battle']
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert cur['hero_retreat']['selected'] == 1
    assert not cur.get('survival')


def test_pending_selection_gets_bounded_grace_before_the_spend():
    """即死帯の上では、選択済みの使用告知が有界な grace で完走を待つ。"""
    mem = _mem(cards_selected=['フットバース'],
               card_flow={'stage': 'announce', 'card': 'フットバース',
                          'selection_planned': True, 'enemy_hp_at_open': 37})
    cur = mem['battle']
    assert p.battle_step(battle_screen(mem), mem) == []
    assert cur['retreat_card_grace'] == 1
    assert not cur.get('cards_unclassified')
    assert any(r['decision'] == 'battle_retreat_card_wait' for r in mem['_records'])
    # 告知フロー自身の2観測 bound が使用未確認を正直に確定する。
    assert p.battle_step(battle_screen(mem), mem) == []
    assert cur['cards_unclassified'] == ['フットバース']
    # その後は残り札の使用試行 (生存メニュー) が退却に先立つ。
    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    assert cur['command_open_pending']['owner'] == 'survival'


def test_grace_is_capped_at_the_limit():
    """grace は RETREAT_CARD_GRACE_LIMIT で打ち止めになり退却を優先する。"""
    mem = _mem(cards_selected=['フットバース'], retreat_card_grace=rules.RETREAT_CARD_GRACE_LIMIT,
               card_flow={'stage': 'announce', 'card': 'フットバース',
                          'selection_planned': True, 'enemy_hp_at_open': 37})
    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    cur = mem['battle']
    assert cur['command_open_pending']['owner'] == 'retreat'
    assert cur['cards_unclassified'] == ['フットバース']
    assert any(r['decision'] == 'battle_retreat_preempted_card'
               for r in mem['_records'])
