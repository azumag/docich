"""章末セーブ確認＋先発隊の選択＝次章（g1059 / g1153 の実測、合成フレーム）.

g1153 (2026-11-07): クイーン撃破後に「セーブをしゅうりょうして…」の確認
(22:48:34) と先発隊の選択 (22:48:37) が来たが、章はHUDの「2わ」を見る
22:56:20 まで第1話のままだった。g1059 (2026-10-28) も同じ並びが第3話の
直前にあり、にせヒーロー戦が戦闘メモリに残っていた。
"""
import pytest

from docich.hanjuku_bot import decide
from docich.hanjuku.state.regions import plan as _plan_region
from test_hanjuku_starter import Canvas, keys, picture, state


def save_prompt():
    """The measured chapter-end yes/no prompt (text from g1153 22:48:34)."""
    c = Canvas()
    c.text(48, 15, '2ねん12のつき48G')
    c.text(16, 151, 'セーブをしゅうりょうして')
    c.text(16, 167, 'よろしいですかな?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    return c.frame()


def boss_memory(**mem):
    """Chapter 1 with the クイーン fight still in memory (as at g1153 22:48)."""
    return state(chapter=1, month='2-12', tick=100,
                 battle={'enemy': 'クイーン', 'ally': 'どうし', 'castle': 'けっかい'},
                 orders={'1-A1': 'launched'}, map_registration={'home': [1, 2]}, **mem)


def test_the_save_prompt_arms_the_marker_without_advancing_the_chapter():
    actions, got = decide(save_prompt(), boss_memory())
    policy = got['policy']
    assert keys(actions) == ['a']
    assert policy['chapter'] == 1
    assert policy['chapter_end_save']['chapter'] == 1
    assert got['_records'][-1]['strategy_variant'] == 'chapter_end_save'


def test_the_advance_party_after_the_save_prompt_enters_the_next_chapter():
    _, got = decide(save_prompt(), boss_memory())
    actions, got = decide(picture().frame(), got)
    policy = got['policy']
    assert keys(actions) == ['a']
    assert policy['chapter'] == 2
    assert 'chapter_end_save' not in policy
    # Route state of the finished chapter is reset with the chapter.
    assert 'orders' not in policy and 'map_registration' not in policy
    seen = [r for r in got['_records'] if r['decision'] == 'chapter_seen']
    assert seen[-1]['observed_metric'] == {'chapter': 2, 'evidence': 'chapter_end_save_party'}


def test_a_measured_boss_defeat_also_allows_the_pair():
    _, got = decide(save_prompt(), state(chapter=1, month='2-12', tick=100, boss_defeated=1))
    _, got = decide(picture().frame(), got)
    assert got['policy']['chapter'] == 2


def test_the_advance_party_without_the_save_prompt_never_advances():
    # The same party screen opens chapter 1 at game start; only the measured
    # chapter-end save unlocks the next chapter.
    _, got = decide(picture().frame(), boss_memory())
    assert got['policy']['chapter'] == 1
    assert 'chapter_end_save' not in got['policy']


def test_the_pair_without_this_chapters_boss_never_advances():
    # A game-over restart party can draw the same advance-party screen. The
    # lost general is not the chapter boss, so the pair is not a chapter change.
    _, got = decide(save_prompt(), state(chapter=1, month='2-12', tick=100,
                                         battle={'enemy': 'キャンディー', 'ally': 'シャルドネ'}))
    _, got = decide(picture().frame(), got)
    assert got['policy']['chapter'] == 1
    assert 'chapter_end_save' not in got['policy']


def test_the_marker_expires_when_no_party_screen_follows():
    _, got = decide(save_prompt(), boss_memory())
    got['policy']['tick'] = 800
    _, got = decide(picture().frame(), got)
    assert got['policy']['chapter'] == 1
    assert 'chapter_end_save' not in got['policy']


def test_a_marker_from_a_finished_chapter_is_dropped():
    _, got = decide(save_prompt(), boss_memory())
    # The next month's HUD「2わ」already advanced the chapter before the party.
    got['policy'].update(chapter=2, chapter_evidence='header')
    _, got = decide(picture().frame(), got)
    assert got['policy']['chapter'] == 2
    assert 'chapter_end_save' not in got['policy']


def test_an_unknown_chapter_never_arms_the_marker():
    _, got = decide(save_prompt(), state(chapter=None, tick=100))
    assert got['policy']['chapter'] is None
    assert 'chapter_end_save' not in got['policy']


def test_the_save_prompt_itself_never_advances_even_with_the_boss_beaten():
    # The chapter's screens end at the save prompt; the next chapter's map
    # starts at the party screen, so the prompt alone resets nothing.
    _, got = decide(save_prompt(), boss_memory(boss_defeated=1))
    assert got['policy']['chapter'] == 1
    assert _plan_region(got['policy']).get('orders') == {'1-A1': 'launched'}
