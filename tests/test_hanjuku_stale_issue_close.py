"""owner 2026-11-28: \u9673\u8150\u5316\u3057\u305f issue \u3092\u81ea\u52d5\u3067\u9589\u3058\u308b\u3002"""
import json
from pathlib import Path

from docich.hanjuku import month_audit as audit


def _write_run(runtime, decision_id):
    row = {'decision_id': decision_id, 'decision': 'month_done'}
    (runtime / 'hanjuku_decisions.jsonl').write_text(
        json.dumps(row, ensure_ascii=False) + chr(10), encoding='utf-8')


def _issue(number, run, created, marker=True):
    body = ''
    if marker:
        body += '<!-- hanjuku-month-audit fingerprint=abc -->' + chr(10)
    body += '- \u30e9\u30f3: `' + run + '` / generation `1` / `v1`'
    return {'number': number, 'title': 't', 'body': body, 'createdAt': created}


def test_a_stale_run_issue_is_closed(tmp_path, monkeypatch):
    _write_run(tmp_path, 'g200-new:2:1')
    issues = [_issue(1, 'g100-old', '2026-11-20T00:00:00Z')]
    monkeypatch.setattr(audit, '_issues', lambda repo, **kw: issues)
    calls = []
    monkeypatch.setattr(audit, '_run_gh', lambda args, **kw: calls.append(args) or '')
    result = audit.close_stale_issues(tmp_path, now=1796000000, hours=1)
    assert result['status'] == 'closed'
    assert result['closed'][0]['number'] == 1
    assert any(a[0:2] == ['issue', 'close'] for a in calls)


def test_the_current_run_is_never_closed(tmp_path, monkeypatch):
    _write_run(tmp_path, 'g200-new:2:1')
    issues = [_issue(2, 'g200-new', '2026-11-20T00:00:00Z')]
    monkeypatch.setattr(audit, '_issues', lambda repo, **kw: issues)
    monkeypatch.setattr(audit, '_run_gh', lambda args, **kw: '')
    assert audit.close_stale_issues(tmp_path, now=1796000000, hours=1)['status'] == 'nothing'


def test_a_fresh_issue_is_kept(tmp_path, monkeypatch):
    _write_run(tmp_path, 'g200-new:2:1')
    issues = [_issue(3, 'g100-old', '2026-11-29T23:00:00Z')]
    monkeypatch.setattr(audit, '_issues', lambda repo, **kw: issues)
    monkeypatch.setattr(audit, '_run_gh', lambda args, **kw: '')
    assert audit.close_stale_issues(tmp_path, now=1796000000, hours=24)['status'] == 'nothing'


def test_a_non_audit_issue_is_never_touched(tmp_path, monkeypatch):
    _write_run(tmp_path, 'g200-new:2:1')
    issues = [_issue(4, 'g100-old', '2026-11-20T00:00:00Z', marker=False)]
    monkeypatch.setattr(audit, '_issues', lambda repo, **kw: issues)
    monkeypatch.setattr(audit, '_run_gh', lambda args, **kw: '')
    assert audit.close_stale_issues(tmp_path, now=1796000000, hours=1)['status'] == 'nothing'


def test_dry_run_reports_without_closing(tmp_path, monkeypatch):
    _write_run(tmp_path, 'g200-new:2:1')
    issues = [_issue(5, 'g100-old', '2026-11-20T00:00:00Z')]
    monkeypatch.setattr(audit, '_issues', lambda repo, **kw: issues)
    calls = []
    monkeypatch.setattr(audit, '_run_gh', lambda args, **kw: calls.append(args) or '')
    result = audit.close_stale_issues(tmp_path, now=1796000000, hours=1, dry_run=True)
    assert result['closed'][0]['dry_run'] is True
    assert calls == []
