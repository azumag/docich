"""Owner 2026-11-02: 敵の召喚獣が出たら味方もおくのてで対抗する.

An observed enemy summon with our egg spent and no usable cards opens the
command menu boundedly and selects おくのて. Synthetic state, not evidence of
live inputs.
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_egg_reference import general_max_hp
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Battle, Screen


def memory(*, chapter=1, enemy='リーキ', enemy_hp=30, ally='トレビス', ally_hp=66,
           side='defense', castle=None, egg_empty=False):
    mem = {'chapter': chapter, 'battle': {
        'enemy': enemy, 'ally': ally, 'side': side,
        'castle': castle if castle is not None else p.chart.home_castle(chapter),
        'start_enemy_hp': general_max_hp(enemy), 'enemy_hp': enemy_hp,
        'start_ally_hp': ally_hp, 'ally_hp': ally_hp, 'step': None,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [],
        'card_summon_observed': True, 'enemy_summon_observed': True,
        'survival': {'opens': 1, 'menu_ticks': 0, 'cards_checked': 0,
                     'cards_attempted': [], 'egg_attempted': False, 'exhausted': True}}}
    if egg_empty:
        mem['egg_zero_owners'] = [ally]
    return mem


def panel(mem, *, enemy_hp=None, ally_hp=None):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'] if enemy_hp is None else enemy_hp,
        cur['ally'], cur['ally_hp'] if ally_hp is None else ally_hp))


def menu(labels=('おくのて',), cursor=208, *, hidden=False):
    first_y = 208 - 16 * (len(labels) - 1)
    lines = [TextLine(first_y + 16*i, tuple((176+8*j, ch) for j, ch in enumerate(label)))
             for i, label in enumerate(labels)]
    return Screen(lines, None, ''.join(labels), kind='battle_menu', menu_cursor=cursor,
                  hidden_battle_commands=hidden)


def test_egg_exhausted_defense_opens_okunote_against_enemy_summon():
    mem = memory(egg_empty=True)
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    guard = mem['battle']['okunote_summon_preempt']
    assert guard['opens'] == 1 and guard['reason'] == 'enemy_summon_present'
    # With the real menu open the preempt records and selects おくのて.
    mem['battle']['okunote_only_observed'] = True
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert guard['selected'] and guard['stage'] == 'selected'
    assert any(r['decision'] == 'battle_okunote_summon_preempt' for r in mem['_records'])


def test_menu_observed_okunote_only_also_preempts_without_egg_flag():
    mem = memory()
    mem['battle']['okunote_only_observed'] = True
    assert p._defender_summon_preempt(mem, mem['battle'])['due']


def test_bounded_opens_exhaust_and_return_to_melee():
    mem = memory(egg_empty=True)
    opens = 0
    for _ in range(40):
        if p.battle_step(panel(mem), mem) == [p.pad('b')]:
            opens += 1
        if mem['battle']['okunote_summon_preempt'].get('exhausted'):
            break
    assert opens == p.DEFENDER_SUMMON_OPEN_LIMIT
    assert mem['battle']['okunote_summon_preempt']['exhausted']
    assert any(r['decision'] == 'battle_okunote_summon_open_failed' for r in mem['_records'])
    assert p.battle_step(panel(mem), mem) != [p.pad('b')]


@pytest.mark.parametrize('changes', [
    {'card_summon_observed': False, 'enemy_summon_observed': False},  # no summon seen
    {'egg_battle': True},                     # our own egg is fighting instead
    {'ally': 'だいじん'}, {'ally': None},
    {'enemy': 'クイーン'},                    # boss kit keeps its own rules
    {'side': 'attack'},                       # attack needs observed own-egg-empty
    {'card_flow': {'stage': 'list', 'card': 'グリンボー'}},
])
def test_exclusions_keep_old_policy(changes):
    mem = memory(egg_empty=changes.get('side') != 'attack')
    mem['battle'].update(changes)
    assert p._defender_summon_preempt(mem, mem['battle']) is None


def test_observed_okunote_only_menu_overrides_a_stale_card_plan():
    # The real menu already proved egg/cards unusable; a stale plan must not
    # reopen a card path during the enemy summon.
    mem = memory()
    mem['battle'].update(okunote_only_observed=True, planned_cards=['クースカン'])
    assert p._defender_summon_preempt(mem, mem['battle'])['due']


def test_attack_side_without_observed_empty_egg_does_not_preempt():
    mem = memory(side='attack', castle='けっかい')
    assert p._defender_summon_preempt(mem, mem['battle']) is None


def test_attack_side_preempts_once_own_egg_is_observed_empty():
    mem = memory(side='attack', castle='けっかい', egg_empty=True)
    window = p._defender_summon_preempt(mem, mem['battle'])
    assert window['due'] and window['resources_basis'] == 'own_egg_empty_no_cards'


# ---- owner 2026-11-02: 城防衛で対召喚獣戦、卵もない時はおくのて ----
# g1097 18:03-18:06 ジュメルズ: ペコリーノ(卵なし)は敵の召喚獣ローラーキラーに
# 卵戦で応戦し、卵が使えなくなった後も もうこうげき で HP0 まで戦った。
# v362: B は卵戦メニューでは効かない (v361 実測)。おくのては灰色の
# たまごをつかうの下の4行目で、カーソルを下げて箱をスクロールさせて選ぶ。
def egg_menu_screen(mem, *, egg_row=False, labels=None, cursor=176, ally_hp=40,
                    ys=None):
    from dataclasses import replace
    from test_hanjuku_naruto_damage import native
    s = native(8549)
    s.kind = 'egg_battle_menu'
    shown = list(labels if labels is not None else ('こうげき', 'もうこうげき'))
    s.text = ''.join(shown) + ('たまごをつかう' if egg_row else '')
    s.hand = None
    s.menu_cursor = cursor
    if ys is None:
        ys = [176 + 16 * i for i in range(len(shown))]
    rows = [TextLine(y, tuple((176 + 8 * j, ch) for j, ch in enumerate(label)))
            for y, label in zip(ys, shown)]
    if egg_row:
        y = 176 + 16 * len(rows)
        rows.append(TextLine(y, tuple((176 + 8 * j, ch)
                                      for j, ch in enumerate('たまごをつかう'))))
    s.lines = rows
    s.menu_rows = rows
    s.egg_rows = [replace(r, name=mem['battle']['ally'], hp=ally_hp) if r.side == 'ally'
                  else replace(r, name='ローラーキラー', hp=308) for r in s.egg_rows]
    return s


def egg_fight_memory(**battle):
    mem = memory(chapter=5, enemy='ビシソワーズ', ally='ペコリーノ', ally_hp=40,
                 castle='ジュメルズ')
    mem['battle'].update(egg_battle=True, **battle)
    mem['egg_battle'] = True
    return mem


def no_b(actions):
    return all(a != p.pad('b') for a in actions)


def test_spent_egg_in_a_castle_defense_summon_fight_scrolls_down_never_b():
    mem = egg_fight_memory()
    actions = p.egg_battle_step(egg_menu_screen(mem), mem)
    cur = mem['battle']
    assert actions == [p.pad('down')]
    assert cur['summon_egg_unavailable'] is True
    assert cur['okunote_summon_preempt']['downs'] == 1
    assert cur['okunote_summon_preempt']['stage'] == 'scrolling'
    scroll = [r for r in mem['_records'] if r['decision'] == 'battle_okunote_summon_scroll']
    assert scroll and scroll[-1]['observed_metric']['down_attempt'] == 1
    assert p._defender_summon_preempt(mem, cur)['resources_basis'] == 'summon_menu_egg_unavailable'


def test_the_scrolled_box_selects_okunote_under_the_cursor():
    mem = egg_fight_memory()
    s = egg_menu_screen(mem, labels=('もうこうげき', 'おくのて'), ys=(176, 208), cursor=208)
    assert p.egg_battle_step(s, mem) == [p.pad('a')]
    cur = mem['battle']
    guard = cur['okunote_summon_preempt']
    assert guard['selected'] and guard['stage'] == 'selected'
    assert cur['okunote_attempt']['phase'] == 'parent_selected'
    decisions = [r['decision'] for r in mem['_records']]
    assert 'battle_okunote_summon_preempt' in decisions
    assert 'battle_okunote_select' in decisions
    assert cur['survival']['exhausted'] is True


def test_a_visible_okunote_is_moved_to_not_confirmed_blindly():
    mem = egg_fight_memory()
    s = egg_menu_screen(mem, labels=('もうこうげき', 'おくのて'), ys=(176, 208), cursor=176)
    assert p.egg_battle_step(s, mem) == [p.pad('down')]
    assert not any(r['decision'] == 'battle_okunote_summon_scroll' for r in mem['_records'])
    assert not mem['battle']['okunote_summon_preempt'].get('selected')


def test_the_scroll_is_bounded_and_then_the_old_attack_resumes():
    mem = egg_fight_memory(ally_hp=50)  # 50*2 > max 88: the old path picks こうげき
    seen = []
    for _ in range(8):
        actions = p.egg_battle_step(egg_menu_screen(mem, ally_hp=50), mem)
        seen += actions
        if mem['battle']['okunote_summon_preempt'].get('exhausted'):
            break
    guard = mem['battle']['okunote_summon_preempt']
    assert guard['exhausted'] and guard['downs'] == p.DEFENDER_SUMMON_SCROLL_LIMIT
    assert any(r['decision'] == 'battle_okunote_summon_open_failed' for r in mem['_records'])
    # Exhausted: the old attack path runs and never sends B on this box.
    actions = p.egg_battle_step(egg_menu_screen(mem, ally_hp=50), mem)
    assert actions == [p.pad('a')]
    assert no_b(seen + actions)


def test_a_repeated_frame_after_selection_never_resends_a():
    mem = egg_fight_memory()
    s = egg_menu_screen(mem, labels=('もうこうげき', 'おくのて'), ys=(176, 208), cursor=208)
    assert p.egg_battle_step(s, mem) == [p.pad('a')]
    for _ in range(8):
        actions = p.egg_battle_step(s, mem)
        assert actions != [p.pad('a')] and no_b(actions)


def summon_defense_memory():
    """An egg battle whose general panel also proves the summon reading."""
    from copy import deepcopy
    ID = {'game': 'hanjuku-hero', 'runtime_id': 'okunote-test',
          'generation': 1, 'lease_id': 'test'}
    mem = memory(chapter=1, enemy='ヘーゼル', ally='アルテミス', ally_hp=49,
                 castle='ゴーメン')
    mem['battle'].update(egg_battle=True)
    mem['egg_battle'] = True
    mem['_run_identity'] = deepcopy(ID)
    mem['battle'].update(summon_general_reading={
        'identity': deepcopy(ID), 'ally': 'アルテミス', 'enemy': 'ヘーゼル',
        'hp': 49, 'max_hp': 49})
    return mem


def summon_egg_screen(mem):
    """egg_battle_menu with the native g684 panel rows and the scrolled box."""
    s = egg_menu_screen(mem, labels=('もうこうげき', 'おくのて'), ys=(176, 208), cursor=208)
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    from pathlib import Path
    s.egg_rows = parse(read_png(
        Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png')).egg_rows
    return s


def candidate_menu():
    from test_hanjuku_defender_egg_preempt import menu as choice_menu
    s = choice_menu(('ヤケクソ', 'あやまる', 'しんだフリ'))
    s.kind = 'okunote_menu'
    return s


def test_candidate_then_egg_menu_return_redraws_within_the_fight():
    mem = summon_defense_memory()
    s = summon_egg_screen(mem)
    assert p.egg_battle_step(s, mem) == [p.pad('a')]
    assert p.okunote_step(candidate_menu(), mem) == [p.pad('a')]
    # Returning to the egg menu ends the attempt: the preempt guard is cleared
    # and the draw counted, so the next turn selects おくのて again.
    assert p.egg_battle_step(s, mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['okunote_draws'] == 1
    assert any(r['decision'] == 'battle_okunote_combat_return' for r in mem['_records'])
    assert cur['okunote_summon_preempt']['selected']  # a fresh guard, selected again
    # The enemy summon remains: the preempt keeps drawing おくのて even past
    # the old redraw limit (owner 2026-11-03).
    cur['okunote_draws'] = p.OKUNOTE_REDRAW_LIMIT
    mem['battle'].pop('okunote_summon_preempt')
    mem['battle'].pop('okunote_attempt', None)
    assert p.egg_battle_step(s, mem) == [p.pad('a')]
    # The no-summon exclusions (test_exclusions_keep_old_policy) resume the
    # ordinary fight; this screen still shows the monster, so it stays here.


def test_scrolled_egg_box_classifies_as_egg_battle_menu():
    # 灰色のたまごをつかう行が OCR に残らず、箱が下へスクロールした表示。
    from pathlib import Path
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    from test_hanjuku_chart_bot import Canvas
    native_frame = read_png(
        Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png')
    c = Canvas(); c.rgb = bytearray(native_frame.rgb)
    for y in range(167, 216):
        for x in range(144, 248):
            c.put(x, y, (16, 72, 57))
    c.text(176, 176, 'もうこうげき')
    c.text(176, 208, 'おくのて')
    for y in range(200, 212):
        for x in range(152, 164):
            c.put(x, y, (230, 105, 74))
    s = parse(c.frame())
    assert s.kind == 'egg_battle_menu'
    assert len(s.egg_rows) == 2


def test_a_usable_egg_row_keeps_the_egg_fight_and_never_opens_the_okunote():
    mem = egg_fight_memory()
    p.egg_battle_step(egg_menu_screen(mem, egg_row=True), mem)
    assert not mem['battle'].get('summon_egg_unavailable')
    assert 'okunote_summon_preempt' not in mem['battle']


@pytest.mark.parametrize('changes', [
    {'side': 'attack'},                  # only castle defense
    {'enemy': 'クイーン'},               # boss kit keeps its own rules
    {'ally': 'だいじん'},
])
def test_other_fights_keep_the_old_egg_battle_policy(changes):
    mem = egg_fight_memory(**changes)
    p.egg_battle_step(egg_menu_screen(mem), mem)
    assert not mem['battle'].get('okunote_summon_preempt')


def test_a_planned_card_still_to_play_comes_before_the_okunote():
    mem = egg_fight_memory(planned_cards=['クースカン'])
    p.egg_battle_step(egg_menu_screen(mem), mem)
    assert not mem['battle'].get('okunote_summon_preempt')


# ---- g1097 22:09: 召喚戦の将軍コマンドでBループ ----
# たいきゃく の下におくのてが隠れた箱 (hidden_battle_commands) で、
# summon_reading が照合不能のとき B は効かず停滞した。有界な down で
# おくのて行へ向かい、尽きたら通常のメニュー処理へ戻す。
def summon_menu_screen(mem, *, hidden=True, labels=('たまごをつかう', 'きりふだ', 'たいきゃく')):
    from docich.hanjuku_screen import EggRow
    first_y = 176
    lines = [TextLine(first_y + 16*i, tuple((176+8*j, ch) for j, ch in enumerate(label)))
             for i, label in enumerate(labels)]
    s = Screen(lines, None, ''.join(labels), kind='battle_menu', menu_cursor=first_y,
               hidden_battle_commands=hidden)
    s.egg_rows = [EggRow(mem['battle']['ally'], 30, 'ally', 183),
                  EggRow('ランプキン', 37, 'enemy', 207)]
    return s


def unbindable_summon_memory(**battle):
    mem = memory(chapter=5, enemy='レモン', ally='カスパチョ', ally_hp=25,
                 castle='ジュメルズ')
    mem['battle'].update(egg_battle=True, **battle)
    mem['egg_battle'] = True
    return mem


def test_unconfirmed_summon_menu_scrolls_down_bounded_then_falls_back():
    mem = unbindable_summon_memory()
    s = summon_menu_screen(mem)
    downs = 0
    for _ in range(8):
        actions = p.battle_menu_step(s, mem)
        if actions == [p.pad('down')]:
            downs += 1
        else:
            break
    assert downs == p.OKUNOTE_SUMMON_UNCONFIRMED_LIMIT
    # 尽きた後は B の打ち続けではなく通常のコマンド判定が走る
    assert any(r['decision'] == 'battle_okunote_summon_unconfirmed_exhausted'
               for r in mem['_records'])


def test_unconfirmed_counter_resets_when_the_panel_binds_again():
    mem = unbindable_summon_memory()
    s = summon_menu_screen(mem)
    p.battle_menu_step(s, mem)
    assert mem['battle'].get('okunote_summon_unconfirmed') == 1
    # A later bindable observation must not inherit stale unconfirmed counts.
    mem['battle']['okunote_summon_unconfirmed'] = None
    assert int(mem['battle'].get('okunote_summon_unconfirmed') or 0) == 0


# ---- g1097 22:28: 照合不能期間が ticks を使い切り救済メニューでBループ ----
# 召喚照合が付くまでの入力で okunote_flow.ticks > 16 に達した後、実メニュー
# (おくのてのみ有効) が読めると親メニュー予算が毎 tick B を返した。この箱
# では B は効かないため無言のBループになった。preempt / last_resort が記録
# 済みなら予算を超えてもおくのて行へ進む。
def test_a_burned_menu_budget_never_bs_out_of_a_committed_rescue():
    mem = memory(egg_empty=True)
    mem['battle']['okunote_flow'] = {'ticks': 20}
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    guard = mem['battle']['okunote_summon_preempt']
    assert guard['selected'] and guard['stage'] == 'selected'


def test_an_uncommitted_menu_still_uses_the_budgeted_b_close():
    mem = memory(side='attack', castle='けっかい', ally_hp=90)
    mem['battle']['okunote_flow'] = {'ticks': 20}
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]


# ---- g1097 23:06: 選択済み親メニューが残ったままの無記録Bループ ----
# guard.selected で親フレームを「再抽選でない」としてBを返していたが、
# 実画面が親メニュー (きりふだ/たいきゃく/おくのて) のまま残るとBは無効。
# おくのて行が見えるなら有界でAを再送する。
def test_a_stuck_selected_parent_menu_resends_a_bounded():
    mem = memory(egg_empty=True)
    s = menu()
    assert p.battle_menu_step(s, mem) == [p.pad('a')]
    # 選択が届かず親メニューが残る = 同じフレームが繰り返し戻る
    for _ in range(12):
        p.battle_menu_step(s, mem)
    # menu_pending 4回 + guard 4回の有界再送で尽き、以後は戻る
    assert mem['battle']['okunote_attempt']['resends'] == 4
    assert mem['battle']['okunote_summon_preempt'].get('parent_retries') == 4
    assert any(r['decision'] == 'battle_okunote_committed_unread'
               for r in mem['_records'])


# ---- g1097 23:40: ticks上限超過で確定済み救済がBループ化 ----
# ticks > OKUNOTE_COMMITTED_TICKS_LIMIT では「予算Bを返す」条件が先に
# 成立し、おくのて行が読めていても永久に選択へ進まなかった (実測: 同じ
# battle_menu にBを2600観測以上)。読める行は tick 数に関わらず選ぶ。
def test_a_committed_rescue_past_the_tick_limit_still_selects_okunote():
    mem = memory(egg_empty=True)
    mem['battle']['okunote_flow'] = {'ticks': p.OKUNOTE_COMMITTED_TICKS_LIMIT + 100}
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    guard = mem['battle']['okunote_summon_preempt']
    assert guard['selected'] and guard['stage'] == 'selected'


def test_an_unreadable_committed_menu_past_the_limit_marks_unavailable():
    mem = memory(egg_empty=True)
    mem['battle']['okunote_flow'] = {'ticks': p.OKUNOTE_COMMITTED_TICKS_LIMIT + 100}
    assert p.battle_menu_step(menu(labels=('こうげき',), hidden=True), mem) == [p.pad('b')]
    guard = mem['battle']['okunote_summon_preempt']
    assert guard['exhausted'] and guard['stage'] == 'unavailable'
    assert any(r['decision'] == 'battle_okunote_committed_unread'
               for r in mem['_records'])


def test_enemy_summon_keeps_drawing_okunote_past_the_old_limit():
    mem = memory()
    mem['battle'].update(okunote_only_observed=True, okunote_draws=4)
    assert p._defender_summon_preempt(mem, mem['battle'])['due']
    mem['battle']['okunote_draws'] = 50
    assert p._defender_summon_preempt(mem, mem['battle'])['due']
    # Once the summon is gone the old menu policy returns.
    mem['battle'].update(card_summon_observed=False, enemy_summon_observed=False)
    assert p._defender_summon_preempt(mem, mem['battle']) is None


def test_enemy_summon_at_one_hit_hp_does_not_open_okunote():
    """Owner 2026-11-04: 相手のHPが一撃で倒せる残りなら奥の手は不要."""
    for hp in (1, 2, 3, 4):
        mem = memory()
        mem['battle'].update(okunote_only_observed=True, summon_enemy_hp=hp)
        assert p._defender_summon_preempt(mem, mem['battle']) is None, hp
    for hp in (5, 30):
        mem = memory()
        mem['battle'].update(okunote_only_observed=True, summon_enemy_hp=hp)
        assert p._defender_summon_preempt(mem, mem['battle'])['due'], hp


# ---- g1139 17:38: 敵召喚獣ちきゅうちゃんのHP9999が照合の上限(999)を超え停止 ----
# 実フレーム(キッシュ26 vs 召喚獣ちきゅうちゃん9999、おくのてのみ有効)。
# 旧コードは summon_reading が None を返し続け、3回の down の後は毎 tick B
# (この箱では無効)で止まり、unconfirmed が300回を超えた。
def _chikyu_frame_and_memory(*, burned=False):
    from copy import deepcopy
    from pathlib import Path
    from docich.hanjuku_pixels import read_png
    ident = {'game': 'hanjuku-hero', 'runtime_id': 'chikyu-test', 'generation': 1, 'lease_id': 'test'}
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g1139-kisshu-chikyu-chan-9999-okunote-only.png')
    cur = {'ally': 'キッシュ', 'enemy': 'レモン', 'castle': 'ジュメルズ', 'side': 'defense',
           'start_ally_hp': 26, 'ally_hp': 26, 'start_enemy_hp': 27, 'enemy_hp': 19,
           'cards_used': [], 'planned_cards': [], 'card_evidence_version': 1, 'egg_battle': True,
           'card_summon_observed': True, 'summon_egg_unavailable': True,  # 召喚時に自軍の卵が使えない
           'summon_general_reading': {'identity': deepcopy(ident), 'ally': 'キッシュ',
                                      'enemy': 'レモン', 'hp': 26, 'max_hp': 26}}
    if burned:  # live: scrolling 済み・unconfirmed と ticks が焼き切れた状態
        cur.update(okunote_flow={'ticks': 328}, okunote_summon_unconfirmed=328,
                   okunote_summon_preempt={'downs': 4, 'opens': 0, 'stage': 'scrolling'})
    mem = {'chapter': 5, 'tick': 100, 'egg_battle': True, 'battle': cur,
           '_run_identity': deepcopy(ident), 'survey_run_identity': deepcopy(ident)}
    return frame, mem, ident


def test_real_frame_reads_the_9999hp_summoned_monster():
    from docich.hanjuku_screen import parse
    frame, _, _ = _chikyu_frame_and_memory()
    s = parse(frame)
    assert s.kind == 'battle_menu'
    assert [(r.side, r.name, r.hp) for r in s.egg_rows] == [
        ('ally', 'キッシュ', 26), ('enemy', 'ちきゅうちゃん', 9999)]


@pytest.mark.parametrize('burned', [False, True])
def test_9999hp_summoned_monster_binds_and_selects_okunote_instead_of_b_loop(burned):
    from docich.hanjuku_screen import parse
    from docich.hanjuku_bot import decide
    from docich.hanjuku.flows import okunote
    frame, mem, ident = _chikyu_frame_and_memory(burned=burned)
    s = parse(frame)
    assert okunote.summon_reading(s, mem, mem['battle']) == {
        'ally_hp': 26, 'enemy_monster': 'ちきゅうちゃん', 'enemy_monster_hp': 9999}
    actions, state = decide(frame, {'policy': mem}, run_identity=ident)
    assert actions == [p.pad('a')]  # 旧コードは [b] を無限に返した
    assert not state['policy']['battle'].get('okunote_summon_unconfirmed')
