"""Measured map event routing; no ROM or production screenshots are embedded."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_bot, hanjuku_screen
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import Screen, classify_text

MESSAGE = 'いばらのとうをとりまいていたすべてのいばらがしょうめつしました!'


def screen(text=MESSAGE):
    value = Screen(lines=[], hand=None, text=text)
    value.kind = classify_text(value)
    return value


def run(monkeypatch, memory, text=MESSAGE):
    monkeypatch.setattr(hanjuku_bot, 'classify', lambda frame: 'field')
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda frame, **kwargs: screen(text))
    return hanjuku_bot.decide(Frame(256, 224, bytes(256 * 224 * 3)), {'policy': memory})


def test_exact_barrier_message_is_not_generic_field_text():
    assert screen().kind == 'barrier_removed'
    assert screen(MESSAGE[:-1]).kind == 'text'
    assert screen('いばらのとう').kind == 'text'


def test_barrier_ack_allows_confirmed_final_battle_to_close_without_advancing_chapter(monkeypatch):
    actions, state = run(monkeypatch, {
        'chapter': 1,
        'battle': {'ally': 'ゼウス', 'enemy': 'デュオニソス', 'ally_hp': 85,
                   'enemy_hp': 0, 'castle': 'スペンソニア', 'side': 'attack',
                   'step': '1-C2', 'away': 1, 'cards_used': []},
    })
    assert actions == [hanjuku_bot.pad('a')]
    assert state['policy']['chapter'] == 1
    assert state['policy']['captured'] == ['スペンソニア']
    records = state['_records']
    assert any(r['decision'] == 'battle_result' and r['outcome'] == 'win' for r in records)
    event = next(r for r in records if r['decision'] == 'barrier_removed')
    assert event['observed_metric'] == {'message': MESSAGE}
    assert event['resulting_stage'] is None


@pytest.mark.parametrize('chapter', [None, 2])
def test_barrier_with_unknown_or_other_chapter_holds(monkeypatch, chapter):
    actions, state = run(monkeypatch, {'chapter': chapter})
    assert actions == []
    assert state['_records'][-1]['decision'] == 'situation_held'


def test_unrecognized_field_text_does_not_get_blind_confirm(monkeypatch):
    actions, _ = run(monkeypatch, {'chapter': 1}, 'しらないメッセージ')
    assert actions == []


def test_retry_commentary_does_not_claim_chart_strategy():
    from docich.hanjuku_commentary import compose
    _, text = compose({'decision': 'battle_start', 'ally': 'ゼウス', 'enemy': 'ラズベリー',
        'ally_hp': 85, 'enemy_hp': 90, 'planned_cards': ['イッテツーン'],
        'strategy_variant': 'retry_with_opening_cards'})
    assert '再攻撃の作戦として' in text
    assert 'チャートの予定どおり' not in text
