"""g1190 ch12 2026-11-24: the scrolled five-item battle command list.

The real bot sent DOWN four times to look for an おくのて row that this human
list does not have. The list then showed only きりふだ/たいきゃく (the grey
たまご row is unread), so ``battle_menu_close`` answered it with B 297 times;
B does not close this box. The measured recovery is ↑×4 then A on the exact
こうげき row (7 hits, 23 damage). もうこうげき must never be confirmed: in the
same probe Ku-mon swallowed Athena on that row.

These tests drive ``docich.hanjuku_bot.decide`` (the dispatcher), not just the
flow, and use the exact frames saved by the isolated probe. policy is JSON
round-tripped between observations, and policy.battle keeps the live stale
エストラゴン/カシス context so the egg preempt cannot scroll the box back down.
"""
from __future__ import annotations

import json
from pathlib import Path

from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1190-ch1v9',
            'generation': 1190, 'lease_id': '57DCA681-418A-4146-B0D8-13A591B41CC9'}
FRAMES = {
    'bottom': 'g1190-battle-menu-scrolled-bottom.png',
    'up1': 'g1190-battle-menu-scroll-up1.png',
    'up2': 'g1190-battle-menu-scroll-up2.png',
    'up3': 'g1190-battle-menu-scroll-up3.png',
    'top': 'g1190-battle-menu-scroll-up4.png',
}


def stale_policy(*, guard_downs=4):
    """The live 19:38-20:09 policy.battle, still on エストラゴン vs カシス."""
    return {
        'chapter': 12,
        'tick': 504001,
        'variant': 'chart_adjusted',
        'egg_battle': True,
        'egg_action': 'attack',
        'battle': {
            'ally': 'エストラゴン',
            'enemy': 'カシス',
            'ally_hp': 66,
            'enemy_hp': 889,
            'start_ally_hp': 66,
            'start_enemy_hp': 1200,
            'side': 'defense',
            'castle': 'ファンゴル',
            'egg_battle': True,
            'card_summon_observed': True,
            'enemy_summon_observed': True,
            'summon_egg_unavailable': True,
            'summon_enemy_hp': 889,
            'cards_used': [],
            'cards_selected': [],
            'cards_unclassified': [],
            'planned_cards': [],
            'card_evidence_version': 1,
            'survival': {
                'opens': 1,
                'pending_opens': 0,
                'menu_ticks': 10620,
                'cards_attempted': [],
                'cards_checked': 1,
                'egg_attempted': False,
                'listed_cards': ['クースカン', 'ビッグウェイブ'],
                'cards_exhausted': True,
                'exhausted': True,
                'exhausted_closed': True,
                'exhausted_waits': 10598,
                'exhausted_recloses': 6,
            },
            'okunote_summon_preempt': {
                'downs': guard_downs,
                'opens': 0,
                'stage': 'scrolling',
            },
        },
    }


def state(*, guard_downs=4):
    return {'policy': stale_policy(guard_downs=guard_downs)}


def frame(name):
    return read_png(FIXTURES / FRAMES[name]).resized()


def observe(name, state, records):
    """One dispatcher observation; policy is round-tripped through JSON."""
    actions, updated = decide(frame(name), state, run_identity=IDENTITY)
    records.extend(updated.get('_records') or [])
    next_state = {key: value for key, value in updated.items()
                  if key not in ('_records', '_experience')}
    next_state['policy'] = json.loads(json.dumps(next_state['policy']))
    return [list(a.get('buttons') or []) for a in actions if isinstance(a, dict)], next_state


def test_bottom_page_is_scrolled_up_not_closed_with_b():
    records = []
    actions, _ = observe('bottom', state(), records)
    assert actions == [['up']], actions
    assert [r['decision'] for r in records] == ['battle_menu_scroll_up']


def test_the_dispatcher_walks_all_four_pages_then_confirms_exact_kougeki():
    records = []
    st = state()
    sent = []
    for name in ('bottom', 'up1', 'up2', 'up3', 'top'):
        actions, st = observe(name, st, records)
        sent.extend(actions)
    assert sent == [['up'], ['up'], ['up'], ['up'], ['a']], sent
    assert not any(buttons in (['b'], ['down']) for buttons in sent)
    decisions = [r['decision'] for r in records]
    assert decisions.count('battle_menu_scroll_up') == 4
    assert decisions[-1] == 'battle_menu_scroll_attack'
    # A on こうげき pops the episode; the next attack starts a fresh walk.
    assert 'human_command_scroll' not in st['policy']['battle']


def test_the_middle_page_with_the_cursor_on_moukougeki_sends_up_without_a():
    records = []
    actions, st = observe('up3', state(), records)
    assert actions == [['up']], actions
    assert 'battle_menu_scroll_attack' not in [r['decision'] for r in records]
    # もうこうげき was never confirmed; the next top page confirms こうげき.
    actions, st = observe('top', st, records)
    assert actions == [['a']], actions
    assert [r['decision'] for r in records][-1] == 'battle_menu_scroll_attack'


def test_upper_page_with_stale_battle_context_does_not_scroll_down_again():
    # No persisted scroll marker and a fresh down budget: the only reason not
    # to send down is the measured ally mismatch (アテナ on screen, エストラ
    # ゴン in policy.battle).
    records = []
    actions, st = observe('top', state(guard_downs=0), records)
    assert actions == [['a']], actions
    assert not any(r.get('decision') == 'battle_okunote_summon_scroll' for r in records)
    assert 'human_command_scroll' not in st['policy']['battle']


def test_the_whole_walk_never_sends_b_or_down_with_stale_policy():
    records = []
    st = state(guard_downs=0)
    sent = []
    for name in ('bottom', 'up1', 'up2', 'up3', 'top'):
        actions, st = observe(name, st, records)
        sent.extend(actions)
    flat = [buttons for buttons in sent]
    assert ['b'] not in flat
    assert ['down'] not in flat
    assert flat[-1] == ['a']
    assert not any(r.get('decision') == 'battle_okunote_summon_scroll' for r in records)
