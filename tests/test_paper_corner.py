import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.config import load_global
from docich.paper_corner import PaperCornerManager


def setup(tmp_path):
    cfg=tmp_path/'config.toml'
    cfg.write_text(f'''[paths]
state_dir = "run"
[trading]
paper_worker_enabled = true
notifications_enabled = true
notification_speech_enabled = true
[webui]
soren_root = "{tmp_path}/soren"
[paper_corner]
enabled = true
start_hour = 22
duration_minutes = 30
''')
    return load_global(tmp_path,cfg)


def test_delayed_boundary_runs_full_duration_and_does_not_repeat(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,8,22,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    due=now[0]; output=[]; voice=[]
    def sleep(seconds):
        if now[0] == due:
            now[0]+=35*60
            state=tmp_path/'soren/tmp/state'
            (state/'corner_boundary_prediction.json').write_text(json.dumps({'completed_at':now[0]}))
        else: now[0]+=seconds
    mgr=PaperCornerManager(g,clock=lambda:now[0],sleep=sleep,overlay=lambda g,p:output.append(p),speech=lambda g,t,**kw:voice.append(kw))
    assert mgr.tick() == 'completed'
    state=json.loads(mgr.path.read_text())
    assert state['started_at']==due+2100
    assert state['completed_at']-state['started_at']==1800
    assert len(output)==len(voice)==7
    assert all('PAPER' in p['body'] for p in output)
    assert json.loads(mgr.presentation.read_text())['mode']=='compact'
    assert mgr.tick()=='not-due'


def test_restart_retries_only_failed_sink_and_preserves_deadline(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,8,22,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    overlay=[]; speech=[]
    mgr=PaperCornerManager(g,clock=lambda:now[0],sleep=lambda t:now.__setitem__(0,now[0]+t),overlay=lambda g,p:overlay.append(p),speech=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('sink unavailable')))
    state={'status':'active','date':'2026-09-08','started_at':now[0],'ends_at':now[0]+1800}
    mgr.save(state)
    import pytest
    with pytest.raises(RuntimeError):mgr.tick()
    assert len(overlay)==1
    mgr.speech=lambda *a,**k:speech.append(k)
    now[0]+=10
    mgr.tick()
    assert len(overlay)==7 and len(speech)==7
    assert json.loads(mgr.path.read_text())['ends_at']==state['ends_at']


def test_another_crashed_active_corner_blocks_new_start(tmp_path):
    import pytest
    from docich.corner_boundary import program_lock
    from docich.game_switch import atomic_write_json
    g=setup(tmp_path)
    other=g.state_dir/'retro_corner.json'
    other.parent.mkdir(parents=True)
    atomic_write_json(other,{'status':'active'})
    with program_lock(g,other):pass
    with pytest.raises(RuntimeError):
        with program_lock(g,g.state_dir/'paper_corner.json'):pass
    atomic_write_json(other,{'status':'completed'})
    with program_lock(g,g.state_dir/'paper_corner.json'):pass
