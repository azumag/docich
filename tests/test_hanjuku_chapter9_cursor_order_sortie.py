"""Chapter 9 sends whoever the list cursor is on; the hero goes last.

Owner 2026-11-11: 9話にかぎり、出撃の時に将軍を選ぶ必要ないので、カーソルがあった
順に出撃させれば短縮になる。ただし主人公だけはゲームオーバーにならないために最後とする.
"""
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy

from test_hanjuku_off_chart import _move_list, _move_memory

HOME = chart.home_castle(9)
ROAD = chart.corridor(9)


def _memory(purpose, chapter=9, general='シナモン'):
    mem = _move_memory()
    order = mem['chart_adjust']['interim_order']
    # Moves walk アルマムーン→ハヤク; attacks leave the front ハヤク for タオ.
    source, target = (HOME, ROAD[1]) if purpose == 'move' else (ROAD[1], ROAD[2])
    order.update(general=general, source=source, target=target, purpose=purpose)
    mem.update(chapter=chapter, captured=[ROAD[1]] if chapter == 9 else ['ジョンリギ'])
    return mem


def _step(mem, names):
    with chart.map_context(mem):
        return policy.deploy_step(_move_list(names), mem)


def picks(mem):
    return [r for r in mem['_records'] if r.get('decision') == 'cursor_order_pick']


def test_an_attack_sends_the_cursor_general_without_walking_the_list():
    mem = _memory('attack')
    actions = _step(mem, ['ミント', 'ジェラート', 'シナモン', 'マフィン'])
    assert actions == [policy.pad('a')]
    assert mem['general_override']['I:m:1'] == 'ミント'
    assert picks(mem) and picks(mem)[-1]['observed_metric']['planned'] == 'シナモン'
    assert not mem.get('general_list_scan')


def test_the_cursor_steps_past_the_hero():
    mem = _memory('move')
    assert _step(mem, [chart.HERO, 'ミント', 'ジェラート']) == [policy.pad('down')]
    assert not picks(mem)


def test_the_hero_moves_only_when_he_is_the_last():
    mem = _memory('move', general=chart.HERO)
    assert _step(mem, [chart.HERO]) == [policy.pad('a')]
    assert mem['general_override']['I:m:1'] == chart.HERO


def test_an_attack_never_takes_the_hero_by_cursor():
    mem = _memory('attack')
    _step(mem, [chart.HERO])
    assert not picks(mem)
    assert mem.get('general_override', {}).get('I:m:1') != chart.HERO


def test_other_chapters_keep_choosing_the_planned_general():
    mem = _memory('attack', chapter=8)
    _step(mem, ['ミント', 'ジェラート', 'シナモン'])
    assert not picks(mem)
