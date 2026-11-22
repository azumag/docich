"""A castle-status panel's labels are not generals standing in the castle.

g1163 2026-11-11 22:2x stored ハヤク's garrison as
['ハヤク', 'じょう', 'しゅうにゅう', 'G', 'レベル', 'しょうぐん', 'めい',
'ラズベリー', 'セルリアク', 'セミヨン'] — ten members for a castle holding three.
The plan then believed it had spares, ordered sorties from that castle and spent
a whole cycle each time finding nobody: 13 order starts produced 6
「出撃元に出せる将軍がいない」.
"""
from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import roster

PANEL = ['ハヤク', 'じょう', 'しゅうにゅう', 'G', 'レベル', 'しょうぐん', 'めい']


def test_panel_labels_are_dropped():
    names = [*PANEL, 'ラズベリー', 'セルリアク', 'セミヨン']
    assert house._general_names(names) == ['ラズベリー', 'セルリアク', 'セミヨン']


def test_a_panel_only_read_leaves_no_members():
    assert house._general_names(list(PANEL)) == []


def test_real_names_keep_their_order():
    names = ['バトウラ', 'ラズベリー', 'セルリアク']
    assert house._general_names(names) == names


def test_the_hero_name_is_kept():
    assert house._general_names(['どうし', 'めい']) == ['どうし']


def test_every_known_wage_name_survives_the_filter():
    names = list(roster.GENERAL_WAGES)
    assert house._general_names(names) == names
