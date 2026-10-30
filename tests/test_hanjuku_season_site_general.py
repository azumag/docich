"""The strongest general with an egg goes to a 四季王 site (#3048, owner 2026-10-30).

g1075 08:20-08:27 (v328): each site order went from its chart source because
that castle held one fit companion, so the lone アルテミス (HP 49 / combat 7),
カペリーニ (63/7, no egg) and ミュスカデ (57/9, no egg) were sent. The 四季王
fights as a summoned monster from the first turn with no きりふだ row
(08:27:56 サマカンテ), and ミュスカデ lost a field summon battle with neither.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import orders

NORMAL = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
HOME = chart.home_castle(4)


def site_order(target='四季王城(南西)'):
    return next(o for o in chart.orders(4) if o['target'] == target)


def memory(**garrison):
    # 08:20 lists; the hero's castle, where joiners stand, was never read.
    lists = {'ミヒラギアン': ['アルテミス'], 'ネクスドリア': ['カペリーニ'],
             'フヌイヌム': ['ミュスカデ'], 'アルカ': ['キール'], **garrison}
    return {'chapter': 4, 'tick': 100, 'captured': list(NORMAL), 'lost': [], 'orders': {},
            'garrison': lists, 'egg_types': {'アルテミス': 'かどまつエッグ'},
            'egg_uses': {'アルテミス': 4}}


def test_g1075_lists_read_the_heros_castle_before_sending_a_weak_lone_general():
    mem = memory()
    assert orders._order_hero_alternatives(site_order(), mem, ['カペリーニ']) == []    # no egg
    assert orders._order_hero_alternatives(site_order(), mem, ['ミュスカデ']) == []
    assert orders._boss_companion_source(site_order(), mem) == HOME
    assert orders._ready(site_order(), mem)


def test_the_strongest_companion_of_every_read_list_goes():
    mem = memory(アルカ=['ヴィーナス', 'ブカティーニ'])
    assert orders._boss_companion_source(site_order(), mem) == 'アルカ'
    mem['garrison'][HOME] = [chart.HERO, 'アポロン', 'ゼウス', 'ココット', 'アテナ']
    assert orders._boss_companion_source(site_order(), mem) == HOME
    assert orders._order_hero_alternatives(site_order(), mem, mem['garrison'][HOME])[:2] == ['ゼウス', 'アポロン']


def test_a_read_heros_castle_without_a_strong_spare_falls_back_to_the_best_read_list():
    mem = memory(ミヒラギアン=['アルテミス', 'ブカティーニ'])
    mem['garrison'][HOME] = [chart.HERO, 'ゼウス', 'ココット']      # ゼウス would leave one guard
    assert orders._boss_companion_source(site_order(), mem) == 'ミヒラギアン'


def test_a_lone_general_never_leaves_its_castle_for_a_site():
    """Owner 2026-10-30 (1人残す): g1075 lost フヌイヌム (08:34:54) and
    ミヒラギアン (08:47:06) after their lone generals left for the sites."""
    mem = memory()
    assert orders._order_hero_alternatives(site_order(), mem, ['アルテミス']) == []
    assert orders._order_hero_alternatives(site_order(), mem, ['アルテミス', 'ブカティーニ'])[0] == 'アルテミス'
    # A marching general is no defender.
    mem['sorties'] = {'x': {'general': 'ブカティーニ', 'status': 'en_route', 'tick': 90, 'target': 'アルカ'}}
    assert orders._order_hero_alternatives(site_order(), mem, ['アルテミス', 'ブカティーニ']) == []
    # Every read list lone: the strict pool is empty, so a relaxed egg-holder
    # still goes while the hero keeps one other defender (owner 2026-10-30:
    # 定常的にボス城へ進行).
    mem = memory()
    mem['garrison'][HOME] = [chart.HERO, 'ゼウス', 'ココット']
    assert orders._boss_companion_source(site_order(), mem) == HOME
    # A lone hero still holds his castle: nobody else remains to defend.
    mem['garrison'][HOME] = [chart.HERO, 'ゼウス']
    assert orders._boss_companion_source(site_order(), mem) is None
    assert not orders._ready(site_order(), mem)


def test_an_egg_is_the_screens_reading_else_the_rosters():
    mem = memory()
    mem['egg_types'].update({'キャラウェイ': 'いっぱつエッグ', 'ヘスティア': 'くさってる'})
    assert orders._holds_egg(mem, 'キャラウェイ')            # bought or given since
    assert not orders._holds_egg(mem, 'ヘスティア')
    assert orders._holds_egg(mem, 'ゼウス') and not orders._holds_egg(mem, 'カペリーニ')
    mem['egg_uses']['アルテミス'] = 0
    assert not orders._holds_egg(mem, 'アルテミス')


def test_other_chapters_keep_the_chart_source_first():
    boss = next(o for o in chart.orders(3) if o['target'] == 'ボス')
    mem = {'chapter': 3, 'captured': [c for c in chart.CASTLE_NAMES[3] if c not in ('アルマムーン', 'ボス')],
           'garrison': {boss['source']: ['カペリーニ'], 'グリン': ['ゼウス']}}
    assert orders._boss_companion_source(boss, mem) == boss['source']


def test_an_unconfirmed_site_launch_binds_its_entry_after_a_long_march():
    """g1075 08:34:57: 4-B2 was launched_unconfirmed at tick 131676 (a field
    battle cut in after the target was chosen), so its record had target None
    and planned_target 四季王城(南東); the entry 476 ticks later matched nothing."""
    from docich import hanjuku_policy as policy
    from docich.hanjuku_screen import Screen, classify_text
    screen = Screen(lines=[], hand=None, text='カペリーニしょうぐんがボスじょうにせめこんだ!!')
    screen.kind = classify_text(screen)
    mem = {'chapter': 4, 'tick': 132152, 'orders': {'4-B2': 'launched'},
           'sorties': {'4-B2': {'general': 'カペリーニ', 'source': 'ネクスドリア', 'status': 'en_route',
                                'target': None, 'planned_target': '四季王城(南東)', 'tick': 131676}}}
    assert policy.message_step(screen, mem) == [policy.pad('a')]
    assert mem['attack'] == {'general': 'カペリーニ', 'castle': '四季王城(南東)', 'side': 'attack',
                             'step': '4-B2', 'entry_evidence': 'measured_boss_entry'}


def test_the_same_site_entry_on_the_next_frame_keeps_its_site_and_step():
    """g1075 08:41:12-22: ヴィーナス's 4-B4 entry bound to 四季王城(北西) once;
    the text stayed on screen, matched nothing and after 7 holds became ボス."""
    from docich import hanjuku_policy as policy
    from docich.hanjuku_screen import Screen, classify_text
    def entry():
        screen = Screen(lines=[], hand=None, text='ヴィーナスしょうぐんがボスじょうにせめこんだ!!')
        screen.kind = classify_text(screen)
        return screen
    mem = {'chapter': 4, 'tick': 132700, 'orders': {'4-B4': 'launched'},
           'sorties': {'4-B4': {'general': 'ヴィーナス', 'source': 'アルカ', 'status': 'en_route',
                                'target': '四季王城(北西)', 'tick': 131798}}}
    bound = {'general': 'ヴィーナス', 'castle': '四季王城(北西)', 'side': 'attack',
             'step': '4-B4', 'entry_evidence': 'measured_boss_entry'}
    assert policy.message_step(entry(), mem) == [policy.pad('a')]
    assert mem['attack'] == bound and mem['sorties']['4-B4']['status'] == 'arrived'
    for _ in range(policy.BOSS_ENTRY_HOLD_LIMIT + 1):
        assert policy.message_step(entry(), mem) == [policy.pad('a')]
    assert mem['attack'] == bound
    assert 'situation_held' not in [r['decision'] for r in mem['_records']]


def test_the_general_kept_behind_holds_an_egg():
    """Owner 2026-10-30 (卵持ちを残す): g1075 08:27:07 sent ヴィーナス from アルカ
    ['ヴィーナス', 'キール']. The egg-less キール (HP 65) held it alone until
    リーキ summoned おーでーん and won at 09:01:32 (キール HP 0, リーキ HP 4)."""
    north_west = site_order('四季王城(北西)')
    mem = memory(アルカ=['ヴィーナス', 'キール'])
    assert orders._order_hero_alternatives(north_west, mem, ['ヴィーナス', 'キール']) == []
    assert orders._boss_companion_source(north_west, mem) == HOME      # read the hero's castle instead
    # An egg holder kept behind lets the stronger one go.
    assert orders._order_hero_alternatives(north_west, mem, ['ヴィーナス', 'ブカティーニ']) == ['ヴィーナス']
    # An egg read as used up is none.
    mem['egg_uses']['ブカティーニ'] = 0
    assert orders._order_hero_alternatives(north_west, mem, ['ヴィーナス', 'ブカティーニ']) == []
    # The hero's castle keeps its two guards as before, with or without eggs.
    assert orders._order_hero_alternatives(north_west, mem, [chart.HERO, 'ゼウス', 'キール', 'カペリーニ']) == ['ゼウス']
