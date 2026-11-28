"""Regression tests for scan-backed general-list confirmation recovery (#5833)."""
from docich import hanjuku_chart as chart, hanjuku_policy as policy
from docich.hanjuku.flows import deploy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas

HOME = ['どうし', 'ピスタチオ', 'キッシュ', 'クランベリー', 'アポロン', 'ヘスティア']


def general_list(names, row=0, bad=None):
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    for i, name in enumerate(names):
        y = 39 + 16 * i
        c.text(144, y, name)
        if bad == name:
            c.tile(144 + 8 * len(name), y, 0x0123456789ABCDEF)
    c.hand(122, 33 + 16 * row)
    screen = parse(c.frame())
    assert screen.kind == 'general_list'
    return screen


def boss_site_memory(names=HOME):
    normal = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
    mem = {'chapter': 4, 'tick': 140000, 'active': '4-B2', 'orders': {'4-B2': 'pending'},
           'captured': normal, 'lost': [], 'card_stock': {'クースカン': 3, 'グリンボー': 40},
           'source_override': {'4-B2': 'アルマムーン'}}
    mem['general_list_scan'] = {'step': '4-B2', 'castle': 'アルマムーン', 'anchor': names[0],
                                'last': names[0], 'names': list(names), 'presses': 6,
                                'stalls': 0, 'end': 'wrapped', 'tick': mem['tick']}
    return mem


def test_completed_scan_recovers_when_an_unrelated_row_is_not_strict():
    mem = boss_site_memory()
    screen = general_list(HOME[1:5], row=3, bad='ピスタチオ')

    assert policy.deploy_step(screen, mem) == []
    assert policy.deploy_step(screen, mem) == []
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]

    (recovery,) = [r for r in mem['_records'] if r['decision'] == 'general_list_scan_recovery']
    assert recovery['observed_metric']['strict_failures'] == [
        {'general': 'ピスタチオ', 'condition': 'not_strictly_visible'}]
    assert recovery['observed_metric']['scan_end'] == 'wrapped'
    assert recovery['observed_metric']['hold_count'] == deploy.GENERAL_LIST_SCAN_RECOVERY_AFTER
    (selected,) = [r for r in mem['_records'] if r['decision'] == 'hero_priority_selected']
    assert selected['observed_metric']['selected'] == 'アポロン'


def test_scan_recovery_never_selects_without_the_targets_strict_cursor_row():
    mem = boss_site_memory()
    screen = general_list(HOME[1:5], row=3, bad='アポロン')

    assert policy.deploy_step(screen, mem) == []
    assert policy.deploy_step(screen, mem) == []
    assert policy.deploy_step(screen, mem) == []

    held = [r for r in mem['_records'] if r['decision'] == 'situation_held']
    assert held[-1]['observed_metric']['hold_variant'] == 'cursor_unconfirmed'
    assert held[-1]['observed_metric']['selected_general'] == 'アポロン'
    assert held[-1]['observed_metric']['strict_failures'] == [
        {'general': 'アポロン', 'condition': 'not_strictly_visible'}]


def test_strict_failure_reason_is_recorded_when_scan_cannot_recover():
    names = [*HOME, 'テスト']
    mem = boss_site_memory(names)
    screen = general_list(names[1:5], row=3)

    assert policy.deploy_step(screen, mem) == []

    held = [r for r in mem['_records'] if r['decision'] == 'situation_held']
    assert held[-1]['observed_metric']['hold_variant'] == 'roster_unreadable'
    assert held[-1]['observed_metric']['strict_failures'] == [
        {'general': 'テスト', 'condition': 'max_hp_unknown'}]
    assert not [r for r in mem['_records'] if r['decision'] == 'general_list_scan_recovery']


def test_strict_failure_metric_names_duplicate_visibility_and_max_hp(monkeypatch):
    monkeypatch.setattr(deploy, '_general_visible', lambda screen, general: general != 'ゼウス')

    metric = deploy._strict_roster_failure_metric(
        None, ['ゼウス', 'ゼウス', 'テスト', 'アルファルファ'], {'ゼウス', 'テスト'},
        scan_completed=True)

    assert metric['strict_failures'] == [
        {'general': 'ゼウス', 'condition': 'duplicate'},
        {'general': 'ゼウス', 'condition': 'not_strictly_visible'},
        {'general': 'テスト', 'condition': 'max_hp_unknown'}]
    assert metric['strict_failure_omitted'] == 0


def test_strict_failure_metric_is_bounded(monkeypatch):
    monkeypatch.setattr(deploy, '_general_visible', lambda screen, general: False)
    present = [f'unknown{i}' for i in range(8)]

    metric = deploy._strict_roster_failure_metric(None, present, set(present))

    assert len(metric['strict_failures']) == deploy.STRICT_FAILURE_RECORD_LIMIT
    assert metric['strict_failure_count'] == 16
    assert metric['strict_failure_omitted'] == 10
