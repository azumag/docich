"""Owner 2026-10-30: a non-finishing okunote is drawn again before the summon.

g1075 08:59 アルカ: キール 65 (no egg) took おくのて at once against リーキ 30
(v323) and drew いあつする/うそなき/しんだフリ, which left リーキ at 30. Back in
melee it fell 30→24→14→8 in 5 s; the window's reopening at 14 lost the race
and リーキ summoned おーでーん. gcgx okunote.html: every draw shows 3 of 16
random choices, so a fresh draw is another chance at a もどき or だいじん.
Also 08:56: ゼウス 85x13 spent ちきゅうちゃん against the eggless バーミセリ
89x12 because its start HP was 4 lower (owner: no egg was needed).
"""

from docich import hanjuku_policy as p
from docich.hanjuku.flows import okunote
from docich.hanjuku.judgment import battle as judgment
from docich.hanjuku.os_layer.screen import Battle, Screen

from test_hanjuku_okunote_early_open import decisions, memory, native


def arca(*, ally_hp=65, enemy_hp=30, **changes):
    mem = memory(ally='キール', ally_hp=ally_hp, enemy='リーキ', enemy_hp=enemy_hp,
                 castle='アルカ', okunote_only_observed=True, **changes)
    mem['battle']['start_ally_hp'], mem['battle']['start_enemy_hp'] = 65, 30
    return mem


def window(mem):
    return p._defender_egg_window(mem, mem['battle'])


def test_a_drawn_okunote_that_left_the_enemy_is_drawn_again_at_once():
    mem = arca(okunote_draws=1)
    got = window(mem)
    # 30 is far above the wall window (14): only the redraw makes it due.
    assert got['open_at_hp'] == 14 and got['enemy_hp'] == 30
    assert got['due'] and got['reason'] == 'okunote_redraw' and got['okunote_draws'] == 1


def test_no_redraw_before_a_first_draw_or_after_the_limit():
    assert not window(arca())['due']
    assert not window(arca(okunote_draws=judgment.OKUNOTE_REDRAW_LIMIT))['due']
    assert window(arca(okunote_draws=judgment.OKUNOTE_REDRAW_LIMIT - 1))['due']


def test_redraw_needs_hp_above_the_self_damage_floor():
    floor = judgment.OKUNOTE_REDRAW_HP_FLOOR
    # 65 → 48 has bled the position guard's budget: that older route may
    # still open, but the redraw itself stops at the floor.
    assert window(arca(okunote_draws=1, ally_hp=floor))['reason'] != 'okunote_redraw'
    assert window(arca(okunote_draws=1, ally_hp=floor + 1))['reason'] == 'okunote_redraw'


def test_the_hero_keeps_the_hp_and_position_deadlines():
    mem = arca(okunote_draws=1)
    mem['battle'].update(ally=p.NAME)
    assert not (window(mem) or {}).get('due')


def test_the_wall_window_still_names_its_own_reason():
    mem = arca(okunote_draws=1, enemy_hp=12)
    assert window(mem)['reason'] == 'wall_hp_window'


def _chosen(mem):
    cur = mem['battle']
    cur['okunote_attempt'] = {'scope': okunote._scope(mem, cur), 'phase': 'candidate_selected'}
    return cur


def _panel(cur, enemy_hp):
    return Screen(lines=[], hand=None, text='', kind='battle',
                  battle=Battle(cur['enemy'], enemy_hp, cur['ally'], cur['ally_hp']))


def test_combat_return_counts_an_egg_route_draw_only():
    mem = arca()
    cur = _chosen(mem)
    cur['okunote_egg_preempt'] = {'opens': 0, 'reason': 'menu_open_contact_unmeasured',
                                  'selected': True}
    okunote.combat_return(_panel(cur, 30), mem, cur)
    assert cur['okunote_draws'] == 1 and 'okunote_egg_preempt' not in cur
    assert window(mem)['reason'] == 'okunote_redraw'

    other = arca()
    cur = _chosen(other)          # a survival / last-resort draw is not counted
    okunote.combat_return(_panel(cur, 30), other, cur)
    assert 'okunote_draws' not in cur


def test_the_open_menu_takes_the_redraw():
    screen = native('g1075-okunote-only-rescue-menu.png')
    mem = arca(okunote_draws=1)
    assert p.battle_menu_step(screen, mem) == [p.pad('down')]
    record, = decisions(mem, 'battle_okunote_egg_preempt')
    assert record['observed_metric']['reason'] == 'okunote_redraw'
    assert '引き直す' in record['reason']


def _survival(ally, ally_hp, enemy, enemy_hp):
    return judgment._survival_needed({
        'ally': ally, 'ally_hp': ally_hp, 'start_ally_hp': ally_hp, 'enemy': enemy,
        'enemy_hp': enemy_hp, 'start_enemy_hp': enemy_hp, 'side': 'defense',
        'castle': 'アルマムーン', 'planned_cards': []})


def test_a_close_start_gap_does_not_spend_a_stronger_generals_egg():
    # 85x13=1105 vs 89x12=1068: ahead in melee, 4 HP behind at the start.
    assert not _survival('ゼウス', 85, 'バーミセリ', 89)


def test_a_wider_start_gap_still_opens_the_rescue():
    # g438: ココット 22 vs キッシュ 26 (85%) died in melee before any card.
    assert _survival('ココット', 22, 'キッシュ', 26)
    # Within 10% but behind in melee: still the rescue.
    assert _survival('ヴィーナス', 80, 'ゼウス', 85)
