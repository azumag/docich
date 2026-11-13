"""A move into our own castle needs one marker survey, not five.

g1163 2026-11-13 12:3x-14:2x: オシロガ>ドンドン moves spent five world-map
surveys each waiting for a 部隊▲ that a friendly destination never draws,
then read the source roster anyway. One survey is enough; the roster is the
receipt.
"""
from docich.hanjuku.flows.map import _probe_launch_markers
from docich.hanjuku.state.regions import flow as _flow, world as _world

STEP = 'I:c9:オシロガ>ドンドン:1'


def memory(target, captured):
    mem = {'_records': [], 'chapter': 9, 'tick': 1000, 'captured': list(captured)}
    _flow(mem)['launch_probe'] = {STEP: {
        'general': 'キャロット', 'source': 'オシロガ', 'target': target,
        'baseline': [], 'reads': 0}}
    _world(mem)['sorties'] = {STEP: {
        'general': 'キャロット', 'source': 'オシロガ', 'target': target,
        'status': 'en_route', 'tick': 990}}
    return mem


def test_an_own_castle_move_goes_blind_after_one_survey():
    mem = memory('ドンドン', ['ドンドン'])
    _probe_launch_markers(mem, [])
    probe = _flow(mem)['launch_probe'][STEP]
    assert probe.get('blind') is True
    assert [r['decision'] for r in mem['_records']] == ['sortie_launch_unverified']


def test_an_enemy_castle_attack_keeps_the_five_surveys():
    mem = memory('ナッテ', ['ドンドン'])
    _probe_launch_markers(mem, [])
    assert not _flow(mem)['launch_probe'][STEP].get('blind')
    assert mem['_records'] == []
