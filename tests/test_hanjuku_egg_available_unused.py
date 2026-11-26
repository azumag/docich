"""owner 2026-11-26: 卵メニューを開いていない戦闘でも実測の白行で検出する。"""
from docich.hanjuku.flows import battle as battle_flow


def _cur(**changes):
    cur = {'ally': 'カペリーニ', 'enemy': 'ハデス', 'step': 'A:9:M1'}
    cur.update(changes)
    return cur


def test_a_white_egg_row_observed_in_the_battle_counts_as_available():
    mem = {'_records': [{'decision': 'battle_egg_white_available',
                         'chart_step': 'A:9:M1'}]}
    assert battle_flow._egg_available_unused(mem, _cur()) is True


def test_the_positive_menu_flag_also_counts():
    assert battle_flow._egg_available_unused({}, _cur(own_egg_positive_observed=True)) is True


def test_an_egg_battle_or_used_egg_is_not_a_miss():
    mem = {'_records': [{'decision': 'battle_egg_white_available', 'chart_step': 'A:9:M1'}]}
    assert battle_flow._egg_available_unused(mem, _cur(egg_battle=True)) is False
    assert battle_flow._egg_available_unused(mem, _cur(egg_used=True)) is False


def test_an_unread_egg_stays_excluded():
    assert battle_flow._egg_available_unused({}, _cur()) is False


def test_a_white_row_from_another_step_does_not_count():
    mem = {'_records': [{'decision': 'battle_egg_white_available',
                         'chart_step': 'A:9:M9'}]}
    assert battle_flow._egg_available_unused(mem, _cur()) is False

def test_the_white_row_latch_counts_as_available():
    assert battle_flow._egg_available_unused({}, {'ally': 'ビシソワーズ',
                                               'egg_white_seen': True}) is True


def test_the_latch_does_not_override_an_egg_battle():
    assert battle_flow._egg_available_unused(
        {}, {'ally': 'ビシソワーズ', 'egg_white_seen': True, 'egg_battle': True}) is False

