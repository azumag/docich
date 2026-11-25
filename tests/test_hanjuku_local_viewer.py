"""Read-only viewer tests (127.0.0.1 only, no writes to the runtime)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hanjuku_local import viewer  # noqa: E402


def _runtime(tmp_path):
    runtime = tmp_path / 'run-local' / 'runtimes' / 'g1300-abcdef12'
    (runtime / 'screenshots').mkdir(parents=True)
    (runtime / 'screenshots' / 'latest.png').write_bytes(b'\x89PNG\r\n\x1a\nfake')
    (runtime / 'hanjuku_run.json').write_text(json.dumps({
        'schema': 1,
        'bot_version': 'hanjuku-chart-v1',
        'runtime_id': 'g1300-abcdef12',
        'observations': 42,
        'phase': 'field',
        'terminal_reason': None,
    }), encoding='utf-8')
    (runtime / 'hanjuku_bot.json').write_text(json.dumps({
        'screen_kind': 'map',
        'policy': {'chapter': 3, 'month': '5-2', 'captured': 4},
    }), encoding='utf-8')
    (runtime / 'hanjuku_decisions.jsonl').write_text(
        json.dumps({'decision': 'chart_step', 'reason': 'march',
                    'chapter': 3, 'at': 1.0}) + '\n', encoding='utf-8')
    log = tmp_path / 'driver.log'
    log.write_text('first\n' + ('x' * 300) + '\n', encoding='utf-8')
    return runtime, log


@pytest.fixture()
def served(tmp_path):
    runtime, log = _runtime(tmp_path)
    server = viewer.serve(runtime, log, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield runtime, log, server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(address, path):
    host, port = address
    url = f'http://{host}:{port}{path}'
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.headers, response.read()


def test_viewer_binds_loopback_only(served):
    _runtime, _log, address = served
    assert address[0] == '127.0.0.1'


def test_status_reports_run_state(served):
    _runtime, _log, address = served
    status, headers, body = _get(address, '/status')
    document = json.loads(body)
    assert status == 200
    assert headers['Content-Type'].startswith('application/json')
    assert document['bot'] == 'hanjuku-chart-v1'
    assert document['step'] == 42
    assert document['phase'] == 'field'
    assert document['screen'] == 'map'
    assert document['chapter'] == 3
    assert document['month'] == '5-2'
    assert document['captured'] == 4
    assert document['last']['decision'] == 'chart_step'
    assert len(document['log']) == viewer.LOG_TAIL_CHARS


def test_index_and_frame(served):
    _runtime, _log, address = served
    status, headers, body = _get(address, '/')
    assert status == 200
    assert headers['Content-Type'].startswith('text/html')
    assert b'/frame.png' in body

    status, headers, body = _get(address, '/frame.png')
    assert status == 200
    assert headers['Content-Type'] == 'image/png'
    assert body.startswith(b'\x89PNG')


def test_viewer_is_read_only(served):
    runtime, log, address = served
    before = {path: path.read_bytes() for path in runtime.rglob('*')
              if path.is_file()}
    before_log = log.read_bytes()
    for path in ('/', '/status', '/frame.png'):
        _get(address, path)
    after = {path: path.read_bytes() for path in runtime.rglob('*')
             if path.is_file()}
    assert after == before
    assert log.read_bytes() == before_log


def test_missing_frame_and_unknown_path(served):
    runtime, _log, address = served
    (runtime / 'screenshots' / 'latest.png').unlink()
    with pytest.raises(urllib.error.HTTPError) as error:
        _get(address, '/frame.png')
    assert error.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as error:
        _get(address, '/etc/passwd')
    assert error.value.code == 404


def test_post_is_rejected(served):
    _runtime, _log, address = served
    host, port = address
    request = urllib.request.Request(
        f'http://{host}:{port}/status', data=b'{}', method='POST')
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 405


def test_missing_runtime_files_do_not_crash(tmp_path):
    runtime = tmp_path / 'empty'
    runtime.mkdir()
    status = viewer.build_status(runtime, tmp_path / 'missing.log')
    assert status['step'] == 0
    assert status['last'] is None
    assert status['log'] == ''
