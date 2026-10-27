import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from docich import corner_rotation, webui
from docich.adapters.base import AdapterError
from test_hanjuku_resume import setup, recovery_setup, automatic_recovery, failed_derived_attempt


def _handler(tmp_path):
    handler = object.__new__(webui._Handler)
    handler.g = SimpleNamespace(state_dir=tmp_path)
    sent = {}
    handler._send_json = lambda status, payload: sent.update(status=status, payload=payload)

    def fail_error(*args, **kwargs):
        pytest.fail(f"unexpected WebUI error: args={args!r} kwargs={kwargs!r}")

    handler._send_error_json = fail_error
    return handler, sent


@pytest.mark.parametrize(
    ("resume_latest", "expected_kwargs", "expected_action"),
    [
        (False, {}, "start"),
        (True, {"resume_latest": True}, "resume-latest"),
    ],
)
def test_hanjuku_webui_start_and_resume_share_durable_queue(
    tmp_path, monkeypatch, resume_latest, expected_kwargs, expected_action
):
    calls = []
    request_id = "req-hanjuku-webui"

    class FakeManager:
        def __init__(self, config):
            self.config = config

        def queue_manual(self, game, **kwargs):
            calls.append((game, kwargs))
            (tmp_path / "corner_manual_queue.json").write_text(
                json.dumps({"corner": game, "request_id": request_id}),
                encoding="utf-8",
            )
            return {"status": "queued", "corner": game, "request_id": request_id}

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", FakeManager)
    handler, sent = _handler(tmp_path)
    row = SimpleNamespace(id="hanjuku-hero", game="hanjuku-hero", adapter="game")

    assert handler._handle_hanjuku_corner_start(row, resume_latest=resume_latest) == 200
    assert calls == [("hanjuku-hero", expected_kwargs)]
    assert sent["status"] == 200
    assert sent["payload"]["action"] == expected_action
    assert sent["payload"]["corner"]["target"] == "rotation"


def test_hanjuku_resume_control_is_explicit_in_webui_resources():
    html = webui.INDEX_HTML
    assert 'id="corners-resume-latest"' in html
    assert 'action:"resume-latest"' in html
    assert 'c.game==="hanjuku-hero"' in html
    assert "通常の開始予約は新規開始のままです" in html


@pytest.mark.skipif(shutil.which('node') is None,reason='Node is required for WebUI JavaScript')
def test_resume_success_message_describes_reservation_not_stop():
    source='async function cornerAction'+webui.INDEX_HTML.split('async function cornerAction',1)[1].split('async function loadStatus',1)[0]
    script='''
const msg={textContent:''};
const document={getElementById:()=>msg};
const confirm=()=>true,toast=()=>{},loadCorners=async()=>{};
const api=async()=>({ok:true,action:'resume-latest',corner:{id:'hanjuku-hero',game:'hanjuku-hero',target:'rotation'}});
''' + source + '''
(async()=>{await cornerAction({action:'resume-latest'},'confirm');process.stdout.write(msg.textContent);})();
'''
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10,check=True)
    assert 'セーブからの再開を予約' in result.stdout
    assert '停止を要求' not in result.stdout


# A fail-closed checkpoint decision is a domain conflict, not a WebUI crash.
def test_hanjuku_resume_adapter_refusal_is_a_safe_conflict(tmp_path, monkeypatch):
    class RefusingManager:
        def __init__(self, config):
            self.config = config

        def queue_manual(self, game, **kwargs):
            assert game == "hanjuku-hero"
            assert kwargs == {"resume_latest": True}
            raise AdapterError("private checkpoint detail must not escape")

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", RefusingManager)
    handler, _ = _handler(tmp_path)
    error = {}
    handler._send_error_json = lambda status, code, message: error.update(
        status=status, code=code, message=message
    )
    row = SimpleNamespace(id="hanjuku-hero", game="hanjuku-hero", adapter="game")

    assert handler._handle_hanjuku_corner_start(row, resume_latest=True) == 409
    assert error == {
        "status": 409,
        "code": "hanjuku_resume_unavailable",
        "message": "最新セーブから再開できる検証済み状態を確認できませんでした",
    }
    assert "private checkpoint" not in error["message"]


def test_webui_resume_requeues_interrupted_automatic_checkpoint(recovery_setup,monkeypatch):
    from docich import hanjuku_resume_recovery as op
    s=recovery_setup
    automatic_recovery(s,released=True,reused_owner=True)
    ledger=s.manager.load(1000000.0)
    ledger['failed_corners']=[]
    s.manager.save(ledger)
    queue=op.queue_failed_start
    monkeypatch.setattr(op,'queue_failed_start',lambda g:queue(g,tmux=s.tmux,now=lambda:1000000.0))
    handler,sent=_handler(s.g.state_dir);handler.g=s.g
    row=SimpleNamespace(id='hanjuku-hero',game='hanjuku-hero',adapter='game')
    assert handler._handle_hanjuku_corner_start(row,resume_latest=True)==200
    assert sent['payload']['action']=='resume-latest'
    request=json.loads((s.g.state_dir/'hanjuku-resume/hanjuku_resume.json').read_text())
    assert request['startup_recovery']['original']==s.claim
    assert request['sha256']==s.claim['sha256']
    assert s.manager.load(1000000.0)['queued_manual']['resume_request_id']==s.claim['request_id']
    assert handler._handle_hanjuku_corner_start(row,resume_latest=True)==200


def test_webui_resume_returns_safe_conflict_for_failed_recovery(recovery_setup,monkeypatch):
    from docich import hanjuku_resume_recovery as op
    s=recovery_setup;automatic_recovery(s,released=True,reused_owner=True)
    (s.spec.runtime_dir/'hanjuku_run.json').touch()
    queue=op.queue_failed_start
    monkeypatch.setattr(op,'queue_failed_start',lambda g:queue(g,tmux=s.tmux,now=lambda:1000000.0))
    handler,_=_handler(s.g.state_dir);handler.g=s.g;error={}
    handler._send_error_json=lambda status,code,message:error.update(status=status,code=code,message=message)
    row=SimpleNamespace(id='hanjuku-hero',game='hanjuku-hero',adapter='game')
    assert handler._handle_hanjuku_corner_start(row,resume_latest=True)==409
    assert error['code']=='hanjuku_resume_unavailable'
    assert 'gameplay_started' not in error['message']


def test_webui_retries_failed_derived_attempt_with_same_save(failed_derived_attempt,monkeypatch):
    from docich import hanjuku_resume_recovery as op
    s=failed_derived_attempt;queue=op.queue_failed_start
    monkeypatch.setattr(op,'queue_failed_start',lambda g:queue(g,tmux=s.tmux,now=lambda:1000000.0))
    handler,sent=_handler(s.g.state_dir);handler.g=s.g
    row=SimpleNamespace(id='hanjuku-hero',game='hanjuku-hero',adapter='game')
    assert handler._handle_hanjuku_corner_start(row,resume_latest=True)==200
    assert sent['payload']['action']=='resume-latest'
    request=json.loads((s.g.state_dir/'hanjuku-resume/hanjuku_resume.json').read_text())
    assert request['sha256']==s.claim['sha256']
    assert request['startup_recovery']['failed_attempt']['claim']==s.retry_claim
