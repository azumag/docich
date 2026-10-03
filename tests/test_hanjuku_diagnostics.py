import json
import os
import pytest
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('hanjuku_collector', Path(__file__).resolve().parents[1] / 'ops/vm_actions/collect_diagnostics.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def setup(tmp_path):
    active = dict(game='hanjuku-hero', runtime_id='g514-0123abcd', generation=514, lease_id='private-lease')
    runtime = tmp_path/'runtimes'/active['runtime_id']
    runtime.mkdir(parents=True)
    (tmp_path/'game_switch.json').write_text(json.dumps(dict(phase='ready', active=active)))
    (runtime/'hanjuku_run.json').write_text(json.dumps(active))
    bot = dict(decision_trace=active, policy=dict(chapter=1, tick=500,
        captured=['キカンドン', 'PRIVATE'], garrison={'アルマムーン':['private-general', 'second'],
        'キカンドン':[], 'ナキューメラ':['lost']},
        sorties={'x':dict(status='en_route',tick=499,general='second',target='ナキューメラ')},
        general_location_unknown=['lost']), secret='DO NOT EMIT')
    file = runtime/'hanjuku_bot.json'
    file.write_text(json.dumps(bot)); os.utime(file,(100,100))
    return file,bot


def test_projection_fresh_fixed_enum_and_unknown_not_zero(tmp_path):
    file,bot=setup(tmp_path); before=file.read_bytes()
    out=d._collect_hanjuku_tactical(tmp_path,110)
    assert out['status']=='ok' and out['age_sec']==10
    rows={r['castle']:r for r in out['castles']}
    assert rows['アルマムーン']['idle_generals_record']==1
    assert rows['キカンドン']['idle_generals_record']==0
    assert rows['ゴーメン']['idle_generals_record'] is None
    assert rows['ナキューメラ']['target_reserved_record']
    assert 'ナキューメラ' in out['remaining_castles']
    for secret in ['PRIVATE','private-general','second','lost','private-lease','DO NOT EMIT']:
        assert secret not in json.dumps(out,ensure_ascii=False).replace('home_lost_record','')
    assert file.read_bytes()==before


@pytest.mark.parametrize('change',[{'generation':513},{'game':'nethack'},{'lease_id':'other'}])
def test_identity_mismatch_hidden(tmp_path,change):
    file,bot=setup(tmp_path); bot['decision_trace'].update(change)
    file.write_text(json.dumps(bot)); os.utime(file,(100,100))
    assert d._collect_hanjuku_tactical(tmp_path,110)['status']=='unavailable'


@pytest.mark.parametrize('now',[99,131,float('nan')])
def test_stale_future_invalid_clock_hidden(tmp_path,now):
    setup(tmp_path)
    assert 'castles' not in d._collect_hanjuku_tactical(tmp_path,now)


def test_terminal_and_symlink_and_large_records_hidden(tmp_path):
    file,bot=setup(tmp_path)
    run=file.parent/'hanjuku_run.json'; saved=run.read_text()
    run.write_text(json.dumps({**json.loads(saved),'terminal_reason':'game_over'}))
    assert 'castles' not in d._collect_hanjuku_tactical(tmp_path,110)
    run.write_text(saved)
    file.unlink(); file.symlink_to(run)
    assert 'castles' not in d._collect_hanjuku_tactical(tmp_path,110)
    file.unlink(); file.write_text('x'*(d.HANJUKU_TACTICAL_LIMIT+1))
    assert 'castles' not in d._collect_hanjuku_tactical(tmp_path,110)


def test_generation_switch_during_read_hides_projection(tmp_path,monkeypatch):
    setup(tmp_path); original=d._read_hanjuku_record; calls=0
    def read(path):
        nonlocal calls
        data,mtime=original(path)
        if path.name=='game_switch.json':
            calls+=1
            if calls==2:
                data['active']['generation']=515
        return data,mtime
    monkeypatch.setattr(d,'_read_hanjuku_record',read)
    assert d._collect_hanjuku_tactical(tmp_path,110)['status']=='identity_changed'


def test_directory_symlink_and_non_hanjuku_canonical_are_rejected(tmp_path):
    file,bot=setup(tmp_path)
    runtime=file.parent; moved=runtime.with_name('moved');runtime.rename(moved);runtime.symlink_to(moved,target_is_directory=True)
    assert 'castles' not in d._collect_hanjuku_tactical(tmp_path,110)
    p=tmp_path/'game_switch.json'; state=json.loads(p.read_text()); state['active']['game']='sorengame';p.write_text(json.dumps(state))
    assert 'castles' not in d._collect_hanjuku_tactical(tmp_path,110)


def test_current_tactical_record_survives_old_detail_reduction():
    tactical={'status':'ok','basis':'bot_record','remaining_castles':['ナキューメラ']}
    payload={'hanjuku_tactical':tactical.copy(),
             'nethack_history':{'daily':{'records':[]},'completed_runs':{'records':[]}},
             'ai':{'recent_events':['x'*d.MAX_JSON_BYTES], 'anomalous_components':{}},
             'workers':{'details':{}}, 'soren91_drop_profile':{'profileStatus':'missing'}}
    text=d._diagnostics_budget(payload)
    assert payload['hanjuku_tactical']==tactical
    assert len(text.encode())<=d.MAX_JSON_BYTES


def test_tactical_omission_only_when_detail_reductions_insufficient(monkeypatch):
    payload={'hanjuku_tactical':{'status':'ok','castles':['x'*2000]},
             'nethack_history':{'daily':{'records':[]},'completed_runs':{'records':[]}},
             'ai':{'recent_events':[], 'anomalous_components':{}},
             'workers':{'details':{}}, 'soren91_drop_profile':{'profileStatus':'missing'}}
    monkeypatch.setattr(d,'MAX_JSON_BYTES',600)
    text=d._diagnostics_budget(payload)
    assert payload['hanjuku_tactical']['status']=='output_omitted'
    assert len(text.encode())<=600


def damage_record(**changes):
    return {**dict(tick=498, card='ゼンマイン', enemy_hp=27, ally_soldiers=0,
                   enemy_soldiers=6, target_kind='general', raw_damage_min=32,
                   enemy_soldier_hp_upper=60, damage_lower_bound=0, remaining_hp_upper=27,
                   lethal=False, egg_drop_fit=False, allowed=False, reason='nonlethal_egg_risk',
                   secret='DO NOT EMIT'), **changes}


def test_last_card_prediction_is_numeric_bounded_and_not_a_use_receipt(tmp_path):
    file, bot = setup(tmp_path)
    bot['policy']['last_card_assessment'] = damage_record()
    file.write_text(json.dumps(bot)); os.utime(file, (100, 100))
    out = d._collect_hanjuku_tactical(tmp_path, 110)['last_card_assessment']
    assert out == dict(prediction_only=True, age_ticks=2, card='ゼンマイン',
                       target_kind='general', reason='nonlethal_egg_risk', enemy_hp=27,
                       raw_damage_min=32, enemy_soldier_hp_upper=60, damage_lower_bound=0,
                       remaining_hp_upper=27, ally_soldiers=0, enemy_soldiers=6,
                       lethal=False, egg_drop_fit=False, allowed=False)
    assert 'DO NOT EMIT' not in json.dumps(out)
    assert 'cards_used' not in out


@pytest.mark.parametrize('value', [None, {}, {'tick': 501}, {'tick': True}, {'tick': -1}])
def test_invalid_or_future_card_prediction_is_absent(value):
    assert d._project_hanjuku_card_assessment(value, 500) is None


def test_card_prediction_rejects_arbitrary_text_types_and_out_of_range_values():
    record = damage_record()
    record.update(card='SECRET', reason=['SECRET'], target_kind={'SECRET': 1},
                  enemy_hp=True, raw_damage_min='32', enemy_soldier_hp_upper=10000,
                  damage_lower_bound=-1, remaining_hp_upper=10000,
                  ally_soldiers=7, enemy_soldiers=False, lethal='yes', egg_drop_fit=1,
                  allowed='SECRET')
    out = d._project_hanjuku_card_assessment(record, 500)
    assert out['prediction_only'] is True and out['age_ticks'] == 2
    assert all(value is None for key, value in out.items()
               if key not in ('prediction_only', 'age_ticks'))
    assert 'SECRET' not in json.dumps(out)


def test_later_chapter_can_report_last_card_without_inventing_castle_coverage(tmp_path):
    file, bot = setup(tmp_path)
    bot['policy'].update(chapter=3, last_card_assessment=damage_record())
    file.write_text(json.dumps(bot)); os.utime(file, (100, 100))
    out = d._collect_hanjuku_tactical(tmp_path, 110)
    assert out['status'] == 'unsupported_chapter' and out['chapter'] == 3
    assert out['last_card_assessment']['card'] == 'ゼンマイン'
    assert 'castles' not in out and 'remaining_castles' not in out


def test_later_chapter_card_prediction_still_checks_generation_after_read(tmp_path, monkeypatch):
    file, bot = setup(tmp_path)
    bot['policy'].update(chapter=3, last_card_assessment=damage_record())
    file.write_text(json.dumps(bot)); os.utime(file, (100, 100))
    original = d._read_hanjuku_record
    calls = 0
    def read(path):
        nonlocal calls
        data, mtime = original(path)
        if path.name == 'game_switch.json':
            calls += 1
            if calls == 2:
                data['active']['generation'] += 1
        return data, mtime
    monkeypatch.setattr(d, '_read_hanjuku_record', read)
    out = d._collect_hanjuku_tactical(tmp_path, 110)
    assert out['status'] == 'identity_changed' and 'last_card_assessment' not in out
