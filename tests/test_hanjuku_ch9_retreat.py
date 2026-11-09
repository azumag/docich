"""Chapter 9: only the hero (どうし) may retreat (owner 2026-11-09)."""
from docich.hanjuku.flows import battle_menus as B
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.os_layer.world import NAME


def cur(ally='アルテミス', hp=10):
    return {'ally': ally, 'enemy': 'ダークエルフ', 'castle': 'ロキュータ', 'side': 'attack',
            'ally_hp': hp, 'enemy_hp': 80, 'step': 'A:x:M1'}


def test_a_non_hero_general_may_not_retreat_in_chapter_9():
    mem = {'chapter': 9, '_records': [], 'battle': cur()}
    state = cur()
    assert B._retreat_forbidden(mem, state) is True


def test_the_hero_may_still_retreat_in_chapter_9():
    mem = {'chapter': 9, '_records': [], 'battle': cur(ally=NAME)}
    assert B._retreat_forbidden(mem, cur(ally=NAME)) is False


def test_other_chapters_are_unchanged():
    mem = {'chapter': 8, '_records': [], 'battle': cur()}
    assert B._retreat_forbidden(mem, cur()) is False


def test_the_blocked_retreat_is_logged_once():
    from docich.hanjuku.os_layer.readers import BATTLE_MENU
    screen = Screen([], None, '', kind='battle_menu')
    mem = {'chapter': 9, '_records': [], 'battle': cur()}
    state = cur()
    B._hero_retreat_menu(screen, mem, state)
    decisions = [r['decision'] for r in mem['_records']]
    assert decisions.count('battle_retreat_forbidden') == 1
    B._hero_retreat_menu(screen, mem, state)
    assert [r['decision'] for r in mem['_records']].count('battle_retreat_forbidden') == 1
