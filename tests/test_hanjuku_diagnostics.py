import copy
import json
import os
import pytest
from docich import hanjuku_diagnostics as d


def setup(tmp_path):
    active = dict(game='hanjuku-hero', runtime_id='g514-0123abcd', generation=514, lease_id='private-lease')
    runtime = tmp_path/'runtimes'/active['runtime_id']
    runtime.mkdir(parents=True)
    (tmp_path/'game_switch.json').write_text(json.dumps(dict(phase='ready', active=active)))
    (runtime/'hanjuku_run.json').write_text(json.dumps(active))
    bot = dict(decision_trace=active, policy=dict(chapter=1, tick=500,
        captured=['キカンドン', 'PRIVATE'], garrison={'ほんじょう':['private-general', 'second'],
        'キカンドン':[], 'ナキューメラ':['lost']},
        sorties={'x':dict(status='en_route',tick=499,general='second',target='ナキューメラ')},
        general_location_unknown=['lost']), secret='DO NOT EMIT')
    file = runtime/'hanjuku_bot.json'
    file.write_text(json.dumps(bot)); os.utime(file,(100,100))
    return file,bot


def test_projection_fresh_fixed_enum_and_unknown_not_zero(tmp_path):
    file,bot=setup(tmp_path); before=file.read_bytes()
    out=d.collect(tmp_path,110)
    assert out['status']=='ok' and out['age_sec']==10
    rows={r['castle']:r for r in out['castles']}
    assert rows['ほんじょう']['idle_generals_record']==1
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
    assert d.collect(tmp_path,110)['status']=='unavailable'


@pytest.mark.parametrize('now',[99,131,float('nan')])
def test_stale_future_invalid_clock_hidden(tmp_path,now):
    setup(tmp_path)
    assert 'castles' not in d.collect(tmp_path,now)


def test_terminal_and_symlink_and_large_records_hidden(tmp_path):
    file,bot=setup(tmp_path)
    run=file.parent/'hanjuku_run.json'; saved=run.read_text()
    run.write_text(json.dumps({**json.loads(saved),'terminal_reason':'game_over'}))
    assert 'castles' not in d.collect(tmp_path,110)
    run.write_text(saved)
    file.unlink(); file.symlink_to(run)
    assert 'castles' not in d.collect(tmp_path,110)
    file.unlink(); file.write_text('x'*(d.LIMIT+1))
    assert 'castles' not in d.collect(tmp_path,110)


def test_generation_switch_during_read_hides_projection(tmp_path,monkeypatch):
    setup(tmp_path); original=d._read; calls=0
    def read(path):
        nonlocal calls
        data,mtime=original(path)
        if path.name=='game_switch.json':
            calls+=1
            if calls==2:
                data['active']['generation']=515
        return data,mtime
    monkeypatch.setattr(d,'_read',read)
    assert d.collect(tmp_path,110)['status']=='identity_changed'


def test_directory_symlink_and_non_hanjuku_canonical_are_rejected(tmp_path):
    file,bot=setup(tmp_path)
    runtime=file.parent; moved=runtime.with_name('moved');runtime.rename(moved);runtime.symlink_to(moved,target_is_directory=True)
    assert 'castles' not in d.collect(tmp_path,110)
    p=tmp_path/'game_switch.json'; state=json.loads(p.read_text()); state['active']['game']='sorengame';p.write_text(json.dumps(state))
    assert 'castles' not in d.collect(tmp_path,110)
