"""Safe optional metadata only. Local Python fixtures; no model/search/reply calls."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from docich import reply_research as r
from docich.reply_research_diagnostic import allowed_row, structured_error


def test_web_diagnostic_projection_has_no_content_or_cross_stage_reason():
    assert allowed_row({'stage':'web_fetch','web_reason':'text_limit','url':'PRIVATE',
                        'body':'PRIVATE','exception':'PRIVATE'}) == {'stage':'web_fetch','web_reason':'text_limit'}
    assert allowed_row({'stage':'web_fetch','web_reason':'PRIVATE'}) == {'stage':'web_fetch'}
    assert allowed_row({'stage':'model_call','web_reason':'text_limit'}) == {'stage':'model_call'}


def local_run(code, rows, *, observer=None, timeout=1):
    return r._run([sys.executable, '-c', code], b'SYNTHETIC_PRIVATE_PROMPT', {}, timeout,
                  observer=observer, diagnostic=rows.append)


@pytest.mark.parametrize('status,category', [(401, 'model_auth'), (403, 'model_auth'),
                                             (429, 'rate_limit'), (500, 'provider_http')])
def test_http_metadata_never_includes_private_fields(status, category):
    event = {'type': 'error', 'error': {'name': 'APIError', 'data': {
        'statusCode': status, 'message': 'SYNTHETIC_PRIVATE_PROMPT',
        'responseBody': 'SYNTHETIC_PRIVATE_BODY', 'path': 'SYNTHETIC_PRIVATE_VM',
        'requestHeaders': {'Authorization': 'SYNTHETIC_PRIVATE_KEY'}}}}
    result = structured_error(event)
    assert result == {'stage': 'cli_error_event', 'error_name': 'APIError',
                      'http_status': status, 'category': category}
    assert 'PRIVATE' not in json.dumps(result)


def test_official_provider_auth_error_without_http_status_drops_identity_and_message():
    # OpenCode 907b3bc5 core/v1/session.ts: AuthError serializes this name/data.
    event = {'type': 'error', 'error': {'name': 'ProviderAuthError', 'data': {
        'providerID': 'SYNTHETIC_PRIVATE_PROVIDER', 'message': 'SYNTHETIC_PRIVATE_CREDENTIAL'}}}
    assert structured_error(event) == {
        'stage': 'cli_error_event', 'error_name': 'ProviderAuthError', 'category': 'model_auth'}


def test_provider_auth_jsonl_metadata_survives_observer_rejection_without_content():
    rows = []
    event = {'type': 'error', 'error': {'name': 'ProviderAuthError', 'data': {
        'providerID': 'SYNTHETIC_PRIVATE_PROVIDER', 'message': 'SYNTHETIC_PRIVATE_CREDENTIAL'}}}
    def reject(event):
        raise ValueError('unexpected_event')
    with pytest.raises(ValueError, match='unexpected_event'):
        local_run('print(' + repr(json.dumps(event)) + ')', rows, observer=reject)
    assert {'stage': 'cli_error_event', 'error_name': 'ProviderAuthError', 'category': 'model_auth'} in rows
    assert rows[-1]['stage'] == 'cli_reaped'
    assert 'PRIVATE' not in json.dumps(rows)


@pytest.mark.parametrize('error,category', [
    ({'name': 'APIError', 'data': {'code': 'ECONNRESET'}}, 'network_tls'),
    ({'name': 'NetworkError'}, 'network_tls'),
    ({'name': 'TimeoutError'}, 'timeout'),
    ({'name': 'ConfigInvalidError'}, 'cli_config_or_model'),
    ({'name': 'ProviderModelNotFoundError'}, 'cli_config_or_model'),
    ({'name': ['SYNTHETIC_PRIVATE'], 'data': {'statusCode': True, 'code': []}}, 'unclassified_error'),
    ({'name': 'SYNTHETIC_PRIVATE_NAME', 'data': {'statusCode': 'SYNTHETIC_PRIVATE', 'code': {}}}, 'unclassified_error'),
])
def test_transport_and_unknown_errors_do_not_expose_identifiers(error, category):
    result = structured_error({'type': 'error', 'error': error})
    assert result['category'] == category
    assert 'PRIVATE' not in json.dumps(result)


def test_projection_rejects_forged_fields_and_unbounded_integers():
    assert allowed_row({'stage': ['SYNTHETIC_PRIVATE']}) is None
    assert allowed_row({'stage': 'cli_failure', 'reason': [], 'stderr': 'SYNTHETIC_PRIVATE',
                        'returncode': True, 'elapsed_ms': 100001, 'http_status': 600}) == {'stage': 'cli_failure'}
    assert structured_error({'type': 'error', 'error': []}) == {
        'stage': 'cli_error_event', 'error_name': 'unrecognized', 'category': 'unclassified_error'}
    assert structured_error({'type': 'text', 'part': {'text': 'SYNTHETIC_PRIVATE'}}) is None


def test_nonzero_exit_is_recorded_without_stderr():
    rows = []
    with pytest.raises(ValueError, match='provider_failed'):
        local_run("import sys;sys.stderr.write('SYNTHETIC_PRIVATE');raise SystemExit(2)", rows)
    assert any(row.get('stage') == 'cli_exit' and row.get('returncode') == 2 for row in rows)
    assert rows[-1]['stage'] == 'cli_reaped'
    assert 'PRIVATE' not in json.dumps(rows)


@pytest.mark.parametrize('newline', [True, False])
def test_invalid_json_at_newline_or_eof_preserves_observer_contract(newline):
    # Keep the process alive after EOF so Darwin does not signal a zombie group.
    code = ("import os,sys,time;sys.stdout.write('SYNTHETIC_PRIVATE' + " +
            repr('\n' if newline else '') + ");sys.stdout.flush();os.close(1);time.sleep(.1)")
    rows = []
    with pytest.raises(ValueError):
        local_run(code, rows, observer=lambda event: None)
    assert any(row.get('reason') == 'invalid_json' for row in rows)
    assert rows[-1]['stage'] == 'cli_reaped'
    # Diagnostic-only parsing is observational and does not reject previously accepted bytes.
    assert local_run(code, rows) == ('SYNTHETIC_PRIVATE' + ('\n' if newline else '')).encode()
    assert 'PRIVATE' not in json.dumps(rows)


def test_error_metadata_emitted_before_observer_rejection():
    rows = []
    event = {'type': 'error', 'error': {'name': 'APIError', 'data': {
        'statusCode': 401, 'responseBody': 'SYNTHETIC_PRIVATE'}}}
    def reject(event):
        raise ValueError('unexpected_event')
    with pytest.raises(ValueError, match='unexpected_event'):
        local_run('print(' + repr(json.dumps(event)) + ')', rows, observer=reject)
    assert any(row.get('category') == 'model_auth' for row in rows)
    assert rows[-1]['stage'] == 'cli_reaped'
    assert 'PRIVATE' not in json.dumps(rows)


def test_deep_json_diagnostics_cannot_change_runner_result(monkeypatch):
    rows = []
    raw = '[' * 2000 + '0' + ']' * 2000
    def recursion_limit(line):
        raise RecursionError('SYNTHETIC_PRIVATE')
    monkeypatch.setattr(r, '_json', recursion_limit)
    assert local_run('print(' + repr(raw) + ')', rows) == (raw + '\n').encode()
    assert any(row.get('reason') == 'invalid_json' for row in rows)


@pytest.mark.parametrize('code,reason', [('import time;time.sleep(10)', 'timeout'),
                                        ('print("x" * 300000)', 'output_limit')])
def test_process_bounds_record_failure_then_reap(code, reason):
    rows = []
    with pytest.raises(ValueError, match=reason):
        local_run(code, rows, timeout=.15)
    assert any(row.get('reason') == reason for row in rows)
    assert rows[-1]['stage'] == 'cli_reaped'


def test_spawn_failure_records_no_argv_or_oserror_text(monkeypatch):
    rows = []
    def fail(*args, **kwargs):
        raise OSError('SYNTHETIC_PRIVATE_PATH')
    monkeypatch.setattr(r.subprocess, 'Popen', fail)
    with pytest.raises(OSError):
        local_run('', rows)
    assert rows[-1]['reason'] == 'spawn_failed'
    assert 'PRIVATE' not in json.dumps(rows)


def test_wait_timeout_is_classified_and_cleanup_still_reaps(monkeypatch):
    original = r.subprocess.Popen.wait
    calls = []
    def wait(proc, timeout=None):
        calls.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired('SYNTHETIC_PRIVATE_ARGV', timeout)
        return original(proc)
    monkeypatch.setattr(r.subprocess.Popen, 'wait', wait)
    rows = []
    with pytest.raises(subprocess.TimeoutExpired):
        local_run('import os,time;os.close(1);time.sleep(10)', rows)
    assert any(row.get('reason') == 'timeout' for row in rows)
    assert rows[-1]['stage'] == 'cli_reaped' and None in calls
    assert 'PRIVATE' not in json.dumps(rows)


def test_sink_failure_never_changes_output_or_prevents_cleanup():
    def broken_sink(row):
        raise RuntimeError('SYNTHETIC_PRIVATE')
    assert r._run([sys.executable, '-c', "print('ok')"], b'', {}, 1, diagnostic=broken_sink) == b'ok\n'
    with pytest.raises(ValueError, match='timeout'):
        r._run([sys.executable, '-c', 'import time;time.sleep(10)'], b'', {}, .1, diagnostic=broken_sink)


def test_coordinate_records_invalid_proposal_while_result_stays_fail_closed(tmp_path):
    rows = []
    result = r.coordinate([{'role': 'user', 'text': 'SYNTHETIC_PRIVATE_PROMPT'}], 'web',
        source=tmp_path, manifest=None, model_call=lambda *_: b'SYNTHETIC_PRIVATE_OUTPUT',
        broker=None, searcher=lambda *_: pytest.fail('search called'),
        deadline=time.monotonic()+1, diagnostic=rows.append)
    assert result.status == 'research_unavailable' and not result.notes
    assert rows == [{'stage': 'model_call'}, {'stage': 'model_proposal', 'reason': 'invalid_proposal'}]


def test_different_failures_keep_same_terminal_result_and_safe_inner_metadata(tmp_path):
    cases = [("raise SystemExit(2)", 'provider_failed'), ("print('not JSON')", 'invalid_json'),
             ('import time;time.sleep(10)', 'timeout'),
             ("print('{\"type\":\"error\",\"error\":{\"name\":\"APIError\",\"data\":{\"statusCode\":401}}}')", 'model_auth'),
             ("print('{\"type\":\"error\",\"error\":{\"name\":\"APIError\",\"data\":{\"code\":\"ECONNRESET\"}}}')", 'network_tls')]
    for code, expected in cases:
        rows = []
        def call(*args):
            def observer(event):
                if event.get('type') == 'error':
                    raise ValueError('unexpected_event')
            return local_run(code, rows, observer=observer, timeout=.1)
        result = r.coordinate([], 'web', source=tmp_path, manifest=None, model_call=call,
            broker=None, searcher=lambda *_: pytest.fail('search called'),
            deadline=time.monotonic()+1, diagnostic=rows.append)
        assert result.status == 'research_unavailable' and not result.notes
        assert any(row.get('reason') == expected or row.get('category') == expected for row in rows)
        assert any(row.get('stage') == 'cli_reaped' for row in rows)


def test_research_threads_optional_hook_without_changing_credentials_or_sandbox(monkeypatch):
    monkeypatch.setattr(r.sys, 'platform', 'linux')
    monkeypatch.setattr(r.shutil, 'which', lambda name, **kw: '/usr/bin/'+name)
    monkeypatch.setattr(r, '_opencode_binary', lambda launcher: launcher)
    seen, rows = {}, []
    class Proxy:
        def __init__(self, path): seen['workspace'] = Path(path).parent
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(r, 'EgressProxy', Proxy)
    def engine(argv, prompt, env, remaining, *, diagnostic):
        seen.update(argv=argv, env=env)
        assert diagnostic == rows.append
        return b'{"type":"text","part":{"type":"text","text":"{\\"action\\":\\"clarify\\"}"}}\n{"type":"step_finish","part":{"type":"step-finish","reason":"stop"}}'
    monkeypatch.setattr(r, '_run', engine)
    env = {'DOCICH_ALLOW_REAL_AI': '1', 'DOCICH_REPLY_RESEARCH_ENABLED': '1',
           'DOCICH_REPLY_WEB_SEARCH_ENABLED': '1', 'DOCICH_REPLY_OPENCODE_MODEL': 'opencode-go/existing-model',
           'DOCICH_REPLY_OPENCODE_API_KEY': 'SYNTHETIC_ONLY', 'HOME': '/SYNTHETIC_PRIVATE',
           'DISCORD_TOKEN': 'SYNTHETIC_PRIVATE', 'TYPESAFE_API_KEY': 'SYNTHETIC_PRIVATE'}
    assert r.research([{'role':'user', 'content':'この用語は？'}], 'web', env=env, diagnostic=rows.append).status == 'clarify'
    assert set(seen['env']) == {'PATH', 'LANG', 'OPENCODE_GO_API_KEY'}
    assert '--unshare-net' in seen['argv'] and '/SYNTHETIC_PRIVATE' not in seen['argv']
    assert rows == [{'stage':'model_call'}, {'stage':'model_proposal'}]
    assert not seen['workspace'].exists()
