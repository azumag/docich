"""owner 2026-11-26: 卵メニューを開いていない戦闘でも実測の白行で検出する。"""
from docich.hanjuku.flows import battle as battle_flow


def _cur(**changes):
    cur = {'ally': 'カペリーニ', 'enemy': 'ハデス', 'step': 'A:9:M1'}
    cur.update(changes)
    return cur


def test_battle_records_do_not_count_because_the_orchestrator_clears_them():
    # 旧実装は mem['_records'] の battle_egg_white_available を走査したが、
    # orchestrator が毎フレーム _records を空にするため戦闘終了時には残らない。
    # 実測は battle の egg_white_seen / own_egg_positive_observed に保持する。
    mem = {'_records': [{'decision': 'battle_egg_white_available',
                         'chart_step': 'A:9:M1'}]}
    assert battle_flow._egg_available_unused(mem, _cur()) is False


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


def test_the_white_row_latch_survives_a_reset_egg_battle_flag():
    # egg_battle はパネル再読で False に戻るため、温存の判定には使わない。
    # 選択前（egg_selected=0）なら白行の実測だけで「使える卵を残した」。
    assert battle_flow._egg_available_unused(
        {}, {'ally': 'ビシソワーズ', 'egg_white_seen': True, 'egg_battle': False}) is True


def test_a_selected_egg_without_a_reobserved_white_row_is_not_a_leftover():
    # 卵を選択すると _note_egg_selected が白行ラッチを落とす。以後に白行を
    # 見直していなければ、選択済みの卵を「残した」扱いにしない。
    assert battle_flow._egg_available_unused(
        {}, {'ally': 'ビシソワーズ', 'egg_white_seen': False,
             'egg_selected': 1}) is False


def test_a_white_row_reobserved_after_a_selection_counts_as_available():
    # 新仕様: egg_selected>0 のときは、再度実測した白行だけを残留の証拠にする。
    assert battle_flow._egg_available_unused(
        {}, {'ally': 'ビシソワーズ', 'egg_white_seen': True,
             'egg_selected': 1}) is True


def test_a_melee_only_summon_keeps_the_egg_and_is_not_a_miss():
    assert battle_flow._egg_available_unused(
        {}, {'ally': 'ビシソワーズ', 'egg_white_seen': True,
             'egg_selected': 0, 'egg_melee_only': True}) is False

