"""docich#4958 reproduction: 2枚目のミックミーを残しての退却は欠陥ではない。

ch10 44-9 (run g1190-ch1v4): シャルドネ vs アニス HP 6 vs 37。監査の
battle_retreat_with_resources は「選択済みだが使用未確定の札」だけを数える
(_remaining_cards + pending)。選択されなかった残り札は一切数えないため、
シグネチャが出た時点で2枚目は selection_planned に達していたことが確定する。

このファイルは、その状況で bot がたどる各分岐が現行の設計どおりであることを
固定する。修正ではない (欠陥ではなく正しい判断、の裏付け)。
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku import month_audit as audit
from docich.hanjuku.judgment import battle as rules
from test_hanjuku_card_damage_gate import memory, cards
from test_hanjuku_survival import battle as battle_screen, memory as panel_memory, menu


def _cur(enemy_soldiers, *, enemy_hp=37, ally_hp=6):
    """The incident panel: シャルドネ vs the eggless アニス at 6 vs 37."""
    return {'enemy': 'アニス', 'ally': 'シャルドネ', 'enemy_hp': enemy_hp,
            'ally_hp': ally_hp, 'start_enemy_hp': 87, 'start_ally_hp': 56,
            'side': 'attack', 'card_soldiers_current': enemy_soldiers is not None,
            'ally_soldiers': 0, 'enemy_soldiers': enemy_soldiers}


def test_micmy_gate_against_eggless_anise():
    # ミックミーは general_damage 64。兵士3人なら吸収30の下向き34 < 37 で非致死。
    mem = {'chapter': 10, 'tick': 1}
    gate = rules._card_damage_gate(mem, _cur(3), 'ミックミー', listed_cards=['ミックミー'])
    assert gate['allowed'] is True and gate['role'] == 'no_autonomous_egg_trigger'
    assert gate['estimate']['lethal'] is False
    # 兵士2人以下なら下向き44 >= 37 で単発致死。
    gate = rules._card_damage_gate(mem, _cur(2), 'ミックミー', listed_cards=['ミックミー'])
    assert gate['allowed'] is True and gate['role'] == 'single_card_lethal'
    assert gate['estimate']['lethal'] is True
    # 兵数未読は最悪6人扱い (60吸収→下向き4) で非致死だが卵なし敵なので使用自体は許可。
    gate = rules._card_damage_gate(mem, _cur(None), 'ミックミー', listed_cards=['ミックミー'])
    assert gate['allowed'] is True and gate['role'] == 'no_autonomous_egg_trigger'
    assert gate['estimate']['lethal'] is False


def test_critical_safe_card_defers_retreat_only_for_lethal_or_heal():
    mem = {'chapter': 10, 'tick': 1}
    rescue = {'listed_cards': ['ミックミー'], 'cards_attempted': [], 'cards_checked': 0}
    # 非致死の残り札は退却を遅らせない (HP6の将軍を救わない札を温存する)。
    assert rules._critical_safe_card(mem, _cur(3), rescue, for_retreat=True) is False
    # 一覧に読めた致死札なら退却より先に救済メニューへ送る (g460 の教訓の逆側)。
    assert rules._critical_safe_card(mem, _cur(2), rescue, for_retreat=True) is True
    # ただし同名札が attempt/selected 済みなら消費未確定として数えない。
    rescue['cards_attempted'] = ['ミックミー']
    assert rules._critical_safe_card(mem, _cur(2), rescue, for_retreat=True) is False


def test_pending_second_copy_is_preempted_at_critical_hp():
    """2枚目の選択Aを送った直後にHPが危険域へ下がった incident の主経路。

    使用告知は未校正のまま待てない (g460: メニュー待ちで将軍が死ぬ) ので、
    退却を優先して記録する。card は unclassified＝使用実績なしで監査の残り札に載る。
    """
    mem = panel_memory(6, 37)
    mem['battle'].update(ally='シャルドネ', enemy='アニス', start_ally_hp=56,
                         start_enemy_hp=87, side='attack',
                         cards_selected=['ミックミー', 'ミックミー'],
                         cards_used=['ミックミー'],
                         card_flow={'stage': 'announce', 'card': 'ミックミー',
                                    'selection_planned': True,
                                    'enemy_hp_at_open': 37})
    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    cur = mem['battle']
    assert cur['cards_used'] == ['ミックミー']
    assert cur['cards_unclassified'] == ['ミックミー']
    preempted = next(r for r in mem['_records']
                     if r['decision'] == 'battle_retreat_preempted_card')
    assert preempted['card'] == 'ミックミー'
    event = audit._battle_resource_event(preempted)
    assert event['kind'] == 'battle_retreat_with_resources'
    assert event['resources'] == ['card'] and event['cards_remaining'] == ['ミックミー']


def test_critical_retreat_select_reports_the_unconfirmed_copy():
    """preempt 後に退却行を選んだ記録は監査の残り札シグネチャを再現する。"""
    mem = panel_memory(6, 37)
    cur = mem['battle']
    cur.update(ally='シャルドネ', enemy='アニス', start_ally_hp=56, side='attack',
               cards_selected=['ミックミー', 'ミックミー'],
               cards_used=['ミックミー'], hero_retreat={'opens': 1})
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    record = next(r for r in mem['_records'] if r['decision'] == 'battle_hero_retreat_select')
    event = audit._battle_resource_event(record)
    assert event['kind'] == 'battle_retreat_with_resources'
    assert event['resources'] == ['card'] and event['cards_remaining'] == ['ミックミー']


def test_survival_list_reoffers_the_second_copy_after_confirmed_use():
    """救済一覧が1枚目の消費を確認すれば、2枚目は普通に選べる (見逃しではない)。"""
    mem = memory('ミックミー', enemy='アニス', enemy_hp=37, ally='シャルドネ',
                 ally_hp=20, soldiers=(0, 3))
    cur = mem['battle']
    cur.update(side='attack', cards_selected=['ミックミー'], cards_used=['ミックミー'],
               survival={'cards_checked': 1, 'listed_cards': ['ミックミー'],
                         'cards_attempted': ['ミックミー'],
                         'card_counts_at_selection': {'ミックミー': 2}},
               card_flow={'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0})
    # 同名札の消費は2回一致した実一覧 (2 -> 1) だけが根拠になる。
    assert p.card_list_step(cards(['ミックミー']), mem) == []
    assert p.card_list_step(cards(['ミックミー']), mem) == [p.pad('a')]
    assert cur['cards_selected'] == ['ミックミー', 'ミックミー']
    assert cur['card_assessment']['reason'] == 'no_autonomous_egg_trigger'


def test_unselected_nonlethal_list_is_closed_at_critical_hp():
    """未選択の非致死一覧は危険HPで閉じる (battle_hero_retreat_cancel_card)。

    この経路では2枚目が cards_selected に載らないため監査シグネチャは出ない。
    今回のシグネチャが出た以上、2枚目はこの経路ではなかったことの裏付け。
    """
    mem = memory('ミックミー', enemy='アニス', enemy_hp=37, ally='シャルドネ',
                 ally_hp=6, soldiers=(0, 3))
    cur = mem['battle']
    cur.update(side='attack', cards_selected=['ミックミー'], cards_used=['ミックミー'],
               survival={'listed_cards': ['ミックミー'], 'cards_attempted': ['ミックミー'],
                         'card_counts_at_selection': {'ミックミー': 2}},
               card_flow={'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0})
    assert p.card_list_step(cards(['ミックミー']), mem) == [p.pad('b')]
    assert any(r['decision'] == 'battle_hero_retreat_cancel_card' for r in mem['_records'])
    assert cur['cards_selected'] == ['ミックミー']
