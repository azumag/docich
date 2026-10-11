import json
from pathlib import Path
import pytest
from test_collect_diagnostics import load_collector


def setup(tmp_path,status='verified'):
    owner={'game':'hanjuku-hero','runtime_id':'g700-abcdef12','generation':700,'lease_id':'PRIVATE-lease'}
    data={**owner,'status':status,'request_id':'PRIVATE-request','corner':'hanjuku-hero','chapter':1,
          'text':'PRIVATE-text','sha256':'a'*64,'selected_source':'PRIVATE-file'}
    root=tmp_path/'hanjuku-resume';root.mkdir()
    (root/'hanjuku_resume.json').write_text(json.dumps(data))
    runtime=tmp_path/'runtimes'/owner['runtime_id'];runtime.mkdir(parents=True)
    (runtime/'hanjuku_resume.json').write_text(json.dumps(data))
    (tmp_path/'game_switch.json').write_text(json.dumps({'phase':'probing' if status=='claimed' else 'ready',
        'candidate' if status=='claimed' else 'active':owner}))
    (tmp_path/'corner_rotation.json').write_text(json.dumps({'pending':{'request_id':data['request_id'],
        'corner':'hanjuku-hero','resume_latest':True}}))
    return data,runtime


@pytest.mark.parametrize('status,expected',[('prepared','queued'),('queued','queued'),('claimed','claimed'),('verified','verified_receipt')])
def test_resume_metadata_bound_to_current_owner(tmp_path,status,expected):
    setup(tmp_path,status);result=load_collector()._collect_hanjuku_resume(tmp_path)
    assert result['status']==expected
    assert 'PRIVATE' not in json.dumps(result) and 'a'*64 not in json.dumps(result)


def test_unpublished_prepared_snapshot_is_not_a_queue(tmp_path):
    setup(tmp_path,'prepared')
    (tmp_path/'corner_rotation.json').write_text('{}')
    result=load_collector()._collect_hanjuku_resume(tmp_path)
    assert result['status']=='prepared' and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('bad',['other_runtime','wrong_lease','wrong_request','missing','symlink','free_status','invalid_chapter'])
def test_stale_or_invalid_receipt_never_claims_live_restoration(tmp_path,bad):
    data,runtime=setup(tmp_path);path=runtime/'hanjuku_resume.json'
    if bad=='other_runtime':(tmp_path/'game_switch.json').write_text(json.dumps({'phase':'ready','active':{'game':'nethack'}}))
    elif bad=='wrong_lease':data['lease_id']='other';path.write_text(json.dumps(data))
    elif bad=='wrong_request':data['request_id']='other';path.write_text(json.dumps(data))
    elif bad=='missing':path.unlink()
    elif bad=='symlink':path.unlink();path.symlink_to(tmp_path/'hanjuku-resume/hanjuku_resume.json')
    else:
        data['status' if bad=='free_status' else 'chapter']='PRIVATE-invalid'
        path.write_text(json.dumps(data))
    result=load_collector()._collect_hanjuku_resume(tmp_path)
    assert result['status'] in {'unavailable','unverified'} and 'PRIVATE' not in json.dumps(result)


def test_diagnostics_never_read_state_bytes(tmp_path,monkeypatch):
    _,runtime=setup(tmp_path)
    def forbid(*a,**kw):raise AssertionError('binary state reader forbidden')
    monkeypatch.setattr(Path,'read_bytes',forbid)
    result=load_collector()._collect_hanjuku_resume(tmp_path)
    assert result['status']=='verified_receipt' and result['core_roundtrip_record'] is True
