"""A status read with no egg clears the remembered egg type.

Devin review on #4414: egg_types was written only when an egg showed, so a
general whose egg was gone kept the old type and the boss wave (which skips
only measured eggless generals) kept sending him. Goes through the real
``house._observe_status`` and the real wave predicate.
"""
from docich import hanjuku_house as house
from docich.hanjuku.strategy import replan
from docich.hanjuku_screen import parse
from test_hanjuku_house import status


def memory(**extra):
    return {'chapter': 1, 'month': '1-6', 'tick': 10,
            'egg_uses': {'ゼウス': 4}, 'egg_types': {'ゼウス': 'スロットエッグ'}, **extra}


def test_a_gone_egg_makes_the_general_eggless_for_the_boss_wave():
    mem = memory()
    info = house._observe_status(parse(status('ゼウス', 'なし')), mem)
    assert info is not None and info['egg'] is None and info['broken'] is False
    assert 'ゼウス' not in mem['egg_types'] and mem['egg_uses'] == {'ゼウス': 4}
    mem['general_statuses'] = {'statuses': {'ゼウス': {
        'hp': info['hp'] or 60, 'egg': None, 'broken': False}}}
    assert replan._boss_eggless(mem, 'ゼウス') is True


def test_the_stale_type_alone_kept_him_a_candidate():
    mem = memory(general_statuses={'statuses': {'ゼウス': {'hp': 60, 'egg': None, 'broken': False}}})
    assert replan._boss_eggless(mem, 'ゼウス') is False


def test_a_broken_egg_keeps_its_remembered_type():
    mem = memory()
    info = house._observe_status(parse(status('ゼウス', 'こわれている')), mem)
    assert info is not None and info['broken'] is True
    assert mem['egg_types'] == {'ゼウス': 'スロットエッグ'}


def test_a_live_egg_still_records_its_type():
    mem = memory(egg_types={}, egg_uses={})
    house._observe_status(parse(status('ゼウス', 'スーパーエッグ3')), mem)
    assert mem['egg_types'] == {'ゼウス': 'スーパーエッグ'} and mem['egg_uses'] == {'ゼウス': 3}


def test_a_recorded_zero_count_survives_an_eggless_read():
    # Devin review: deleting the 0 let orders._holds_egg fall back to the
    # roster's initial egg and call an eggless general an egg carrier.
    from docich.hanjuku.strategy import orders
    mem = memory(egg_uses={'ブカティーニ': 0}, egg_types={'ブカティーニ': 'スーパーエッグ'})
    house._observe_status(parse(status('ブカティーニ', 'なし')), mem)
    assert mem['egg_uses'] == {'ブカティーニ': 0}
    assert orders._holds_egg(mem, 'ブカティーニ') is False
