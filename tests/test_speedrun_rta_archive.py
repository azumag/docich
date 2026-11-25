"""R2 archive tests for finished hanjuku-speedrun attempts.

Every HTTP test talks to a local dummy S3 server (``http.server``) only.  No
real R2 endpoint, network, docker, VM or ROM is used, and the tests assert
that the secret key never reaches the server or any local output file.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import pytest

from docich.hanjuku_speedrun.rta import archive, hook, sigv4, spool, store, timer
from docich import game_switch

ROOT = Path(__file__).resolve().parents[1]
ACCESS = 'ACCESSKEYEXAMPLE123'
SECRET = 'SECRETKEYEXAMPLE456/with+symbols'
BASE_MS = 1_700_000_000_000
SIGV4_ALGORITHM = 'AWS4-HMAC-SHA256'


# ---------------------------------------------------------------------------
# independent SigV4 verifier used by the dummy server
# ---------------------------------------------------------------------------


def _canonical_value(value):
    return ' '.join(str(value or '').strip().split())


def _canonical_query(raw_query):
    if not raw_query:
        return ''
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    encoded = sorted(
        (quote(str(key), safe='-_.~'), quote(str(value), safe='-_.~'))
        for key, value in pairs)
    return '&'.join(f'{key}={value}' for key, value in encoded)


def _verifier_signature(method, raw_target, headers, body):
    """Recompute the signature from the received request, independently.

    This deliberately does not import the production canonicalisation
    helpers: a double-encoded URI or a wrong signed-header list must fail.
    """
    authorization = headers.get('authorization') or ''
    if not authorization.startswith(SIGV4_ALGORITHM + ' '):
        return None
    try:
        credential_part, signed_part, signature_part = (
            item.strip() for item in authorization[len(SIGV4_ALGORITHM) + 1:].split(','))
        access_key, _slash, scope = credential_part.split('=', 1)[1].partition('/')
        signed_headers = signed_part.split('=', 1)[1]
        supplied = signature_part.split('=', 1)[1]
    except (IndexError, ValueError):
        return None
    amz_date = headers.get('x-amz-date') or ''
    if not amz_date:
        return None
    parts = urlsplit(raw_target)
    raw_path = parts.path
    canonical_headers = ''.join(
        f'{name}:{_canonical_value(headers.get(name))}\n'
        for name in signed_headers.split(';'))
    body_hash = hashlib.sha256(body).hexdigest()
    if headers.get('x-amz-content-sha256') != body_hash:
        return None
    canonical_request = '\n'.join([
        str(method).upper(),
        raw_path,
        _canonical_query(parts.query),
        canonical_headers,
        signed_headers,
        body_hash,
    ])
    string_to_sign = '\n'.join([
        SIGV4_ALGORITHM,
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode('utf-8')).hexdigest(),
    ])
    key = ('AWS4' + SECRET).encode('utf-8')
    for part in scope.split('/'):
        key = hmac.new(key, part.encode('utf-8'), hashlib.sha256).digest()
    expected = hmac.new(key, string_to_sign.encode('utf-8'),
                        hashlib.sha256).hexdigest()
    return access_key, expected, supplied


# ---------------------------------------------------------------------------
# dummy S3 server
# ---------------------------------------------------------------------------


class _ServerState:
    def __init__(self):
        self.lock = threading.Lock()
        self.objects = {}
        self.requests = []
        self.auth_failures = []
        self.fail_put_remaining = 0
        self.fail_head = False
        self.head_size_override = None
        self.fail_keep_copy = False
        self.fail_copy = False
        self.delay_seconds = 0.0

    def keys(self):
        with self.lock:
            return sorted(self.objects)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):  # keep pytest output clean
        pass

    @property
    def state(self):
        return self.server.state

    def _remember(self, body=b''):
        headers = {name.lower(): value for name, value in self.headers.items()}
        with self.state.lock:
            self.state.requests.append({
                'method': self.command,
                'path': unquote(urlsplit(self.path).path),
                'raw_path': urlsplit(self.path).path,
                'headers': headers,
                'body': body,
            })

    def _authorized(self, body=b''):
        headers = {name.lower(): value for name, value in self.headers.items()}
        result = _verifier_signature(self.command, self.path, headers, body)
        if result is None:
            return False
        access_key, expected, supplied = result
        return (access_key == ACCESS
                and hmac.compare_digest(expected, supplied))

    def _reject_signature(self):
        with self.state.lock:
            self.state.auth_failures.append(self.path)
        self._reply(403)

    def _reply(self, status, body=b'', extra=None):
        if self.state.delay_seconds:
            time.sleep(self.state.delay_seconds)
        self.send_response(status)
        extra = dict(extra or {})
        if 'Content-Length' not in extra:
            extra['Content-Length'] = str(len(body))
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def _path(self):
        return unquote(urlsplit(self.path).path)

    def do_PUT(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b''
        self._remember(body)
        if not self._authorized(body):
            self._reject_signature()
            return
        path = self._path()
        copy_source = self.headers.get('x-amz-copy-source')
        with self.state.lock:
            if self.state.fail_put_remaining > 0:
                self.state.fail_put_remaining -= 1
                fail = True
            else:
                fail = False
            if not fail and copy_source and (self.state.fail_keep_copy
                                             or self.state.fail_copy):
                fail = True
            if not fail and self.state.fail_keep_copy and '/keep/' in path:
                fail = True
            if fail:
                self._reply(500)
                return
            if copy_source:
                source = unquote(urlsplit(copy_source).path)
                if source not in self.state.objects:
                    self._reply(404)
                    return
                self.state.objects[path] = self.state.objects[source]
            else:
                self.state.objects[path] = body
        self._reply(200)

    def do_HEAD(self):
        self._remember()
        if not self._authorized(b''):
            self._reject_signature()
            return
        path = self._path()
        with self.state.lock:
            if self.state.fail_head:
                self._reply(500)
                return
            value = self.state.objects.get(path)
            if value is None:
                self._reply(404)
                return
            size = (self.state.head_size_override
                    if self.state.head_size_override is not None
                    else len(value))
        self._reply(200, extra={'Content-Length': str(size)})


class _Server:
    def __init__(self):
        self.state = _ServerState()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.httpd.state = self.state
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                        daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.httpd.server_address[:2]
        return f'http://{host}:{port}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def server():
    instance = _Server()
    try:
        yield instance
    finally:
        instance.close()


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv('DOCICH_R2_ENV_FILE', str(tmp_path / 'missing-r2.env'))
    monkeypatch.delenv('DOCICH_SPEEDRUN_DATA_DIR', raising=False)
    monkeypatch.delenv('DOCICH_STATE_DIR', raising=False)
    monkeypatch.delenv('DOCICH_SPEEDRUN_ATTEMPTS_MAX_BYTES', raising=False)


def write_env(tmp_path, endpoint, *, mode=0o600, access=ACCESS, secret=SECRET,
              bucket='docich-hanjuku-speedrun', account='account123'):
    path = tmp_path / 'r2.env'
    lines = [
        f'R2_ACCOUNT_ID={account}',
        f'R2_ACCESS_KEY_ID={access}',
        f'R2_SECRET_ACCESS_KEY={secret}',
        f'R2_BUCKET={bucket}',
    ]
    if endpoint:
        lines.append(f'R2_ENDPOINT={endpoint}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.chmod(path, mode)
    os.environ['DOCICH_R2_ENV_FILE'] = str(path)
    return path


def make_attempt(tmp_path, attempt_id='a0001-g1-abc123', *, attempt_no=1,
                 status='dnf', runtime_id='g1-abc123', files=None,
                 frames=None, manifest=True, segments=True, row=True,
                 records=None):
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / runtime_id
    runtime.mkdir(parents=True, exist_ok=True)
    base = data / 'attempts' / attempt_id
    spool_dir = base / 'spool'
    spool_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (files if files is not None
                          else {'decisions-0001.jsonl.gz': b'logs'}).items():
        target = spool_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    for name, payload in (frames or {}).items():
        target = spool_dir / 'frames' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    if manifest:
        spool.write_json(base / 'manifest.json', {
            'schema': 1, 'game': store.GAME, 'attempt_id': attempt_id,
            'attempt_no': attempt_no, 'runtime_id': runtime_id,
            'status': status, 'result': status,
            'spool_files_at_build': [{'path': 'spool/ghost.jsonl',
                                      'bytes': 999}],
            'archived': False,
        })
    if segments:
        spool.write_json(base / 'segments.json', {
            'schema': 1, 'game': store.GAME, 'attempt_id': attempt_id,
            'attempt_no': attempt_no, 'status': status,
        })
    if row:
        store.append_attempt(runtime, {
            'schema': 1, 'game': store.GAME, 'runtime_id': runtime_id,
            'attempt_id': attempt_id, 'attempt_no': attempt_no,
            'status': status, 'result': status,
            'started_at_ms': BASE_MS + attempt_no * 1000,
            'ended_at_ms': BASE_MS + attempt_no * 1000 + 500,
            'archived': False,
        })
    if records is not None:
        spool.write_json(data / 'records.json', records)
    return data, runtime, attempt_id


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def manifest_of(data, attempt_id):
    return read_json(data / 'attempts' / attempt_id / 'manifest.json')


def row_of(data, attempt_id):
    for row in store.load_attempts_in(data):
        if row.get('attempt_id') == attempt_id:
            return row
    raise AssertionError('attempt row missing')


def upload_objects(state):
    return [request for request in state.requests
            if request['method'] == 'PUT' and 'x-amz-copy-source' not in request['headers']]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_upload_verifies_then_deletes_and_marks_archived(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'decisions-0001.jsonl.gz': b'compressed decision log'})

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    assert result.code == 'ok'
    assert result.uploaded >= 1
    key = ('/docich-hanjuku-speedrun/hanjuku-speedrun/attempts/'
           '0001-a0001-g1-abc123/spool/decisions-0001.jsonl.gz')
    assert key in server.state.keys()
    assert server.state.objects[key] == b'compressed decision log'
    # The manifest inventory is a pre-compression snapshot and is not trusted:
    # its "ghost" entry was never uploaded.
    assert not any('ghost' in remote for remote in server.state.keys())
    # The request was signed; the secret itself never travelled.
    put = upload_objects(server.state)[0]
    assert put['headers']['authorization'].startswith('AWS4-HMAC-SHA256 ')
    assert put['headers']['x-amz-date']
    assert put['headers']['x-amz-content-sha256']
    assert SECRET.encode() not in put['body']
    assert all(SECRET not in value for value in put['headers'].values())
    # Every request (PUT/HEAD/copy) was checked against an independent
    # recomputation of the SigV4 signature by the dummy server.
    assert server.state.auth_failures == []
    # Local spool is gone only after the HEAD verification.
    assert not (data / 'attempts' / attempt_id / 'spool').exists()
    manifest = manifest_of(data, attempt_id)
    assert manifest['archived'] is True
    archived = {item['key']: item['bytes'] for item in manifest['archived_files']}
    assert archived[key[len('/docich-hanjuku-speedrun/'):]] == len(
        b'compressed decision log')
    assert manifest['archived_at_ms'] > 0
    assert row_of(data, attempt_id)['archived'] is True
    assert not (data / 'attempts' / attempt_id / 'archive_state.json').exists()


def test_keys_with_spaces_plus_colon_and_japanese_are_signed_once(
        tmp_path, server):
    write_env(tmp_path, server.url)
    weird = '日本語 ログ+colon:v1.jsonl.gz'
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={weird: b'weird payload'})
    spool.write_json(data / 'records.json',
                     _records(pb_attempt=attempt_id))

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    assert server.state.auth_failures == []
    prefix = ('/docich-hanjuku-speedrun/hanjuku-speedrun/attempts/'
              f'0001-{attempt_id}/spool/')
    assert prefix + weird in server.state.keys()
    keep_prefix = (f'/docich-hanjuku-speedrun/hanjuku-speedrun/keep/'
                   f'0001-{attempt_id}/spool/')
    assert keep_prefix + weird in server.state.keys()
    copies = [request for request in server.state.requests
              if 'x-amz-copy-source' in request['headers']]
    assert copies
    sources = [request['headers']['x-amz-copy-source'] for request in copies]
    assert any('%E6%97%A5' in value and '%20' in value
               and '%2B' in value and '%3A' in value for value in sources)
    # The raw request target is encoded exactly once (no %25 double escape).
    assert all('%25' not in request['raw_path']
               for request in server.state.requests)


def test_dummy_server_rejects_a_wrong_secret(tmp_path, server):
    endpoint = sigv4.parse_endpoint(server.url, explicit=True)
    credentials = sigv4.Credentials(ACCESS, 'a-different-secret')
    client = sigv4.R2Client(endpoint, credentials, 'docich-hanjuku-speedrun')
    local = tmp_path / 'object.bin'
    local.write_bytes(b'payload')
    with pytest.raises(sigv4.SigV4Error) as caught:
        client.put_file('hanjuku-speedrun/x', local)
    assert caught.value.code == 'http_403'
    assert server.state.auth_failures


def test_pending_uploads_ended_attempts_oldest_first_and_skips_running(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, first = make_attempt(
        tmp_path, 'a0001-g1-abc123', attempt_no=1, status='finished')
    _data, _runtime, second = make_attempt(
        tmp_path, 'a0002-g1-abc123', attempt_no=2, status='dnf')
    data2, runtime2, running = make_attempt(
        tmp_path, 'a0003-g2-def456', attempt_no=3, status='running',
        runtime_id='g2-def456')
    current = timer.start_attempt(
        timer.new_timer('g2-def456', timer.default_records(), BASE_MS + 3000),
        timer.default_records(), BASE_MS + 3000, attempt_no=3)
    store.save_timer(runtime2, current[0])

    summary = archive.upload_pending(data)

    assert summary['ok'] is True
    assert summary['attempted'] == 2 and summary['uploaded'] == 2
    paths = [request['path'] for request in upload_objects(server.state)]
    assert any(first in path for path in paths)
    assert any(second in path for path in paths)
    assert not any(running in path for path in paths)
    assert paths[0].endswith(f'{first}/spool/decisions-0001.jsonl.gz')


def test_unconfigured_sends_nothing_and_reports_normal(tmp_path, server):
    data, _runtime, attempt_id = make_attempt(tmp_path)

    result = archive.upload_attempt(data, attempt_id)
    summary = archive.upload_pending(data)

    assert result.ok is True and result.code == 'unconfigured'
    assert summary['ok'] is True and summary['code'] == 'unconfigured'
    assert server.state.requests == []
    assert (data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl.gz').is_file()


def test_status_is_json_with_fixed_keys(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)

    document = archive.status(data)

    assert set(document) == {'configured', 'config', 'status', 'pending',
                             'archived', 'failed', 'locked'}
    assert document['configured'] is True
    assert document['config'] == 'ok'
    assert document['status'] == 'ok'
    assert document['pending'] == 1
    assert document['archived'] == 0
    assert document['failed'] == 0
    assert document['locked'] is False


# ---------------------------------------------------------------------------
# failures: keep the local bytes, back off, retry idempotently
# ---------------------------------------------------------------------------


def test_size_mismatch_keeps_local_backs_off_and_retry_succeeds(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    server.state.head_size_override = 1

    first = archive.upload_attempt(data, attempt_id)
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl.gz'

    assert first.ok is False and first.code == 'size_mismatch'
    assert local.is_file()
    state = read_json(data / 'attempts' / attempt_id / 'archive_state.json')
    assert state['last_error'] == 'size_mismatch'
    assert state['attempts'] == 1
    assert state['next_retry_at_ms'] > state['last_error_at_ms']
    assert manifest_of(data, attempt_id)['archived'] is False

    server.state.head_size_override = None
    second = archive.upload_attempt(data, attempt_id)
    assert second.ok is True
    assert not local.exists()
    assert row_of(data, attempt_id)['archived'] is True
    # The retry overwrote the same spool key instead of creating a second one.
    spool_keys = [key for key in server.state.keys() if key.endswith(
        '/spool/decisions-0001.jsonl.gz')]
    assert len(spool_keys) == 1


def test_put_500_keeps_local_then_retry_succeeds(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    server.state.fail_put_remaining = 1

    first = archive.upload_attempt(data, attempt_id)
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl.gz'
    assert first.ok is False and first.code == 'put_failed'
    assert local.is_file()
    assert read_json(data / 'attempts' / attempt_id / 'archive_state.json')[
        'last_error'] == 'put_failed'

    assert archive.upload_attempt(data, attempt_id).ok is True
    assert not local.exists()


def test_head_failure_keeps_local(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    server.state.fail_head = True

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is False and result.code == 'head_failed'
    assert (data / 'attempts' / attempt_id / 'spool' /
            'decisions-0001.jsonl.gz').is_file()


def test_connection_refused_keeps_local_and_records_a_fixed_code(tmp_path):
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    write_env(tmp_path, f'http://127.0.0.1:{port}')
    data, _runtime, attempt_id = make_attempt(tmp_path)

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is False and result.code == 'put_failed'
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl.gz'
    assert local.is_file()
    state = read_json(data / 'attempts' / attempt_id / 'archive_state.json')
    assert state['last_error'] == 'put_failed'


def test_timeout_keeps_local_and_records_a_fixed_code(tmp_path, server):
    server.state.delay_seconds = 0.5
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    client = sigv4.R2Client(
        sigv4.parse_endpoint(server.url, explicit=True),
        sigv4.Credentials(ACCESS, SECRET), 'docich-hanjuku-speedrun',
        timeout=0.1)

    result = archive.upload_attempt(data, attempt_id, client=client)

    assert result.ok is False and result.code == 'put_failed'
    assert (data / 'attempts' / attempt_id / 'spool' /
            'decisions-0001.jsonl.gz').is_file()
    assert read_json(data / 'attempts' / attempt_id / 'archive_state.json')[
        'last_error'] == 'put_failed'


def test_file_growth_during_upload_is_local_changed_and_kept(tmp_path):
    data, _runtime, attempt_id = make_attempt(tmp_path)
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl.gz'

    class _GrowingClient:
        def __init__(self):
            self.sizes = {}

        def put_file(self, key, path):
            path = Path(path)
            size = path.stat().st_size
            self.sizes[key] = size
            if key.endswith('/spool/decisions-0001.jsonl.gz'):
                with path.open('ab') as stream:
                    stream.write(b'more bytes')
            return size

        def head_size(self, key):
            return self.sizes.get(key)

    result = archive.upload_attempt(data, attempt_id, client=_GrowingClient())

    assert result.ok is False and result.code == 'local_changed'
    assert local.is_file()
    assert read_json(data / 'attempts' / attempt_id / 'archive_state.json')[
        'last_error'] == 'local_changed'


def test_partial_success_reports_partial_failure(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, first = make_attempt(
        tmp_path, 'a0001-g1-abc123', attempt_no=1, status='finished')
    _data, _runtime, second = make_attempt(
        tmp_path, 'a0002-g1-abc123', attempt_no=2, status='dnf')
    server.state.fail_put_remaining = 1

    summary = archive.upload_pending(data)

    assert summary['attempted'] == 2
    assert summary['uploaded'] == 1
    assert summary['failed'] == 1
    assert summary['ok'] is False
    assert summary['code'] == 'partial_failure'
    assert (data / 'attempts' / first / 'spool' /
            'decisions-0001.jsonl.gz').is_file()
    assert not (data / 'attempts' / second / 'spool').exists()


def test_backoff_skips_pending_but_explicit_upload_works(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    spool.write_json(data / 'attempts' / attempt_id / 'archive_state.json', {
        'schema': 1, 'attempt_id': attempt_id, 'attempts': 1,
        'last_error': 'put_failed', 'last_error_at_ms': BASE_MS,
        'next_retry_at_ms': int(time.time() * 1000) + 3_600_000,
    })

    assert archive.upload_pending(data)['attempted'] == 0
    assert archive.upload_attempt(data, attempt_id).ok is True


# ---------------------------------------------------------------------------
# gzip in progress / frames tar
# ---------------------------------------------------------------------------


def test_live_gzip_lock_defers_without_uploading(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'decisions-0001.jsonl': b'uncompressed logs'})
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl'
    lock = Path(str(local) + spool.GZIP_LOCK_SUFFIX)
    lock.write_text(str(os.getpid()), encoding='ascii')

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is False and result.code == 'gzip_pending'
    assert server.state.requests == []
    assert local.is_file()

    # A dead compressor's stale lock is taken over; the file goes out
    # uncompressed rather than waiting forever.
    lock.write_text('99999999', encoding='ascii')
    second = archive.upload_attempt(data, attempt_id)
    assert second.ok is True
    keys = server.state.keys()
    assert any(key.endswith('spool/decisions-0001.jsonl') for key in keys)
    assert not local.exists()


def test_pending_waits_for_a_live_gzip_lock_then_uploads(tmp_path, server,
                                                         monkeypatch):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'decisions-0001.jsonl': b'uncompressed logs'})
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl'
    lock = Path(str(local) + spool.GZIP_LOCK_SUFFIX)
    sleeper = subprocess.Popen([sys.executable, '-c',
                                'import time; time.sleep(30)'])
    lock.write_text(str(sleeper.pid), encoding='ascii')
    monkeypatch.setattr(archive, 'GZIP_WAIT_MAX_SECONDS', 5.0)
    monkeypatch.setattr(archive, 'GZIP_WAIT_INTERVAL_SECONDS', 0.02)
    remover = threading.Timer(0.2, lambda: lock.unlink(missing_ok=True))
    remover.start()
    try:
        summary = archive.upload_pending(data)
    finally:
        remover.cancel()
        sleeper.terminate()
        sleeper.wait(timeout=10)

    assert summary['attempted'] == 1
    assert summary['uploaded'] == 1
    assert not local.exists()
    assert server.state.auth_failures == []


def test_pending_gzip_wait_timeout_is_not_a_retryable_failure(
        tmp_path, server, monkeypatch):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'decisions-0001.jsonl': b'uncompressed logs'})
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl'
    lock = Path(str(local) + spool.GZIP_LOCK_SUFFIX)
    sleeper = subprocess.Popen([sys.executable, '-c',
                                'import time; time.sleep(30)'])
    lock.write_text(str(sleeper.pid), encoding='ascii')
    monkeypatch.setattr(archive, 'GZIP_WAIT_MAX_SECONDS', 0.2)
    monkeypatch.setattr(archive, 'GZIP_WAIT_INTERVAL_SECONDS', 0.02)
    try:
        summary = archive.upload_pending(data)
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=10)

    assert summary['attempted'] == 1
    assert summary['uploaded'] == 0
    assert summary['failed'] == 0
    assert summary['ok'] is True
    assert local.is_file()
    assert not (data / 'attempts' / attempt_id / 'archive_state.json').exists()
    assert server.state.requests == []


def test_zombie_gzip_lock_is_treated_as_dead(tmp_path):
    if not Path('/proc').is_dir():
        pytest.skip('needs /proc')
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'decisions-0001.jsonl': b'logs'})
    local = data / 'attempts' / attempt_id / 'spool' / 'decisions-0001.jsonl'
    lock = Path(str(local) + spool.GZIP_LOCK_SUFFIX)
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    try:
        deadline = time.time() + 5
        zombie = False
        while time.time() < deadline:
            try:
                stat_text = Path(f'/proc/{child.pid}/stat').read_text(
                    encoding='utf-8', errors='replace')
            except OSError:
                time.sleep(0.02)
                continue
            if stat_text.rsplit(')', 1)[1].strip().startswith('Z'):
                zombie = True
                break
            time.sleep(0.02)
        if not zombie:
            pytest.skip('child was not a zombie')
        lock.write_text(str(child.pid), encoding='ascii')
        assert archive._gzip_lock_live(local) is False
        assert not lock.exists()
    finally:
        child.wait(timeout=10)


def test_frames_are_one_tar_and_not_uploaded_individually(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={}, frames={'a.png': b'aaa', 'b.png': b'bbb'})

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    keys = server.state.keys()
    tar_key = [key for key in keys if key.endswith('/spool/frames.tar')]
    assert len(tar_key) == 1
    assert not any('/spool/frames/' in key for key in keys)
    payload = server.state.objects[tar_key[0]]
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r') as archive_file:
        names = sorted(archive_file.getnames())
    assert names == ['frames/a.png', 'frames/b.png']
    assert not (data / 'attempts' / attempt_id / 'spool' / 'frames').exists()


def test_only_uploaded_files_and_tarred_frames_are_deleted(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'decisions-0001.jsonl.gz': b'logs',
                         '.not-uploaded': b'keep me'},
        frames={'a.png': b'aaa', '.hidden.png': b'keep me too'})

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    spool_path = data / 'attempts' / attempt_id / 'spool'
    assert not (spool_path / 'decisions-0001.jsonl.gz').exists()
    assert not (spool_path / 'frames' / 'a.png').exists()
    assert (spool_path / '.not-uploaded').is_file()
    assert (spool_path / 'frames' / '.hidden.png').is_file()
    uploaded = [key for key in server.state.keys()
                if 'not-uploaded' in key or 'hidden' in key]
    assert uploaded == []


def test_non_archived_old_attempt_survives_local_retention(tmp_path, server):
    write_env(tmp_path, server.url)
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    ids = []
    for number in range(1, 6):
        attempt_id = f'a{number:04d}-g1-abc123'
        ids.append(attempt_id)
        make_attempt(tmp_path, attempt_id, attempt_no=number, status='dnf',
                     files={}, row=False)
        base = data / 'attempts' / attempt_id
        spool.write_json(base / 'manifest.json', {
            'schema': 1, 'game': store.GAME, 'attempt_id': attempt_id,
            'attempt_no': number, 'status': 'dnf',
            'archived': number != 1,
        })
        spool.write_json(base / 'segments.json', {
            'schema': 1, 'game': store.GAME, 'attempt_id': attempt_id,
            'attempt_no': number,
        })

    archive.enforce_local_retention(data, keep=3)

    # The oldest attempt was never archived, so its retry state and manifest
    # must stay even though it is outside the newest three.
    assert (data / 'attempts' / ids[0] / 'manifest.json').is_file()
    assert (data / 'attempts' / ids[0] / 'segments.json').is_file()
    # An archived attempt outside the newest three is pruned.
    assert not (data / 'attempts' / ids[1] / 'manifest.json').exists()


# ---------------------------------------------------------------------------
# PB / segment-best keep copies
# ---------------------------------------------------------------------------


def _records(pb_attempt=None, gold_attempt=None):
    gold = [None] * 12
    if gold_attempt:
        gold[0] = {'segment_ms': 1000, 'attempt_id': gold_attempt,
                   'at_ms': BASE_MS}
    return {
        'schema': 1, 'game': store.GAME, 'next_attempt_no': 2,
        'pb': ({'attempt_id': pb_attempt, 'total_ms': 1000,
                'segments_ms': [1000] * 12} if pb_attempt else None),
        'gold_segments': gold,
        'gold_segments_ms': [1000 if item else None for item in gold],
        'sum_of_best_ms': 1000 if gold_attempt else None,
        'updated_at_ms': BASE_MS,
    }


def test_pb_and_gold_attempts_are_copied_to_keep(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    spool.write_json(data / 'records.json',
                     _records(pb_attempt=attempt_id, gold_attempt=attempt_id))

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    keys = server.state.keys()
    keep_key = ('/docich-hanjuku-speedrun/hanjuku-speedrun/keep/0001-'
                f'{attempt_id}/spool/decisions-0001.jsonl.gz')
    assert keep_key in keys
    assert manifest_of(data, attempt_id)['keep_copied'] is True


def test_non_record_attempt_has_no_keep_copy(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    spool.write_json(data / 'records.json', _records())

    assert archive.upload_attempt(data, attempt_id).ok is True
    assert not any('/keep/' in key for key in server.state.keys())


def test_copy_failure_falls_back_to_a_second_put(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    spool.write_json(data / 'records.json', _records(pb_attempt=attempt_id))
    server.state.fail_copy = True

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    keep_key = ('/docich-hanjuku-speedrun/hanjuku-speedrun/keep/0001-'
                f'{attempt_id}/spool/decisions-0001.jsonl.gz')
    assert keep_key in server.state.keys()
    assert manifest_of(data, attempt_id)['keep_copied'] is True


def test_keep_copy_failure_does_not_fail_the_upload(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    spool.write_json(data / 'records.json', _records(pb_attempt=attempt_id))
    server.state.fail_keep_copy = True

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    manifest = manifest_of(data, attempt_id)
    assert manifest['keep_copied'] is False
    assert manifest['keep_failed'] >= 1
    assert not (data / 'attempts' / attempt_id / 'spool').exists()


def test_keep_manifest_refresh_failure_does_not_fail_the_upload(
        tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    spool.write_json(data / 'records.json', _records(pb_attempt=attempt_id))
    server.state.fail_keep_copy = True
    real = sigv4.R2Client(
        sigv4.parse_endpoint(server.url, explicit=True),
        sigv4.Credentials(ACCESS, SECRET), 'docich-hanjuku-speedrun')

    class _FlakyManifestClient:
        def __init__(self):
            self.manifest_puts = 0

        def put_file(self, key, path):
            if key.endswith('/manifest.json'):
                self.manifest_puts += 1
                if self.manifest_puts >= 2:
                    raise sigv4.SigV4Error('network')
            return real.put_file(key, path)

        def head_size(self, key):
            return real.head_size(key)

        def copy_object(self, source_key, destination_key):
            return real.copy_object(source_key, destination_key)

    result = archive.upload_attempt(
        data, attempt_id, client=_FlakyManifestClient())

    assert result.ok is True and result.code == 'ok'
    manifest = manifest_of(data, attempt_id)
    assert manifest['archived'] is True
    assert manifest['keep_copied'] is False
    assert manifest['keep_failed'] >= 1
    assert not (data / 'attempts' / attempt_id / 'spool').exists()


# ---------------------------------------------------------------------------
# manifest, retention, cap
# ---------------------------------------------------------------------------


def test_manifest_snapshot_is_not_trusted_and_missing_manifest_is_rebuilt(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'real.jsonl.gz': b'real'}, manifest=False)

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    keys = server.state.keys()
    assert any(key.endswith('/spool/real.jsonl.gz') for key in keys)
    assert not any('ghost' in key for key in keys)
    manifest = manifest_of(data, attempt_id)
    assert manifest['archived'] is True
    assert any(key.endswith('/manifest.json') for key in keys)
    assert any(key.endswith('/segments.json') for key in keys)


def test_missing_manifest_rebuild_does_not_steal_a_live_attempts_logs(tmp_path, server, monkeypatch):
    write_env(tmp_path, server.url)
    data, runtime, old = make_attempt(
        tmp_path, 'a0001-g1-abc123', attempt_no=1, files={'real.jsonl.gz': b'real'},
        manifest=False)
    current, _records = timer.start_attempt(
        timer.new_timer(runtime.name, timer.default_records(), BASE_MS + 1000),
        timer.default_records(), BASE_MS + 1000, attempt_no=2)
    store.save_timer(runtime, current)
    # The live attempt is a different one in the same runtime generation.
    (runtime / 'hanjuku_decisions.jsonl').write_text('{live}\n', encoding='utf-8')
    calls = []
    monkeypatch.setattr(spool, 'finalize_logs',
                        lambda *args, **kwargs: calls.append(args) or True)

    result = archive.upload_attempt(data, old)

    assert result.ok is True
    assert calls == []
    assert (runtime / 'hanjuku_decisions.jsonl').is_file()
    assert manifest_of(data, old)['archived'] is True


def test_unbuildable_manifest_still_sends_the_spool(tmp_path, server, monkeypatch):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(
        tmp_path, files={'real.jsonl.gz': b'real'}, manifest=False)
    monkeypatch.setattr(archive, '_ensure_manifest', lambda *args: False)

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is True
    keys = server.state.keys()
    assert any(key.endswith('/spool/real.jsonl.gz') for key in keys)
    assert not any(key.endswith('/manifest.json') for key in keys)
    assert row_of(data, attempt_id)['archived'] is True
    assert not (data / 'attempts' / attempt_id / 'spool').exists()


def test_local_retention_keeps_only_the_newest_three_archived(tmp_path, server):
    write_env(tmp_path, server.url)
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    ids = []
    for number in range(1, 6):
        attempt_id = f'a{number:04d}-g1-abc123'
        ids.append(attempt_id)
        make_attempt(tmp_path, attempt_id, attempt_no=number, status='dnf',
                     files={}, row=False)
        base = data / 'attempts' / attempt_id
        spool.write_json(base / 'manifest.json', {
            'schema': 1, 'game': store.GAME, 'attempt_id': attempt_id,
            'attempt_no': number, 'status': 'dnf', 'archived': True,
        })
        spool.write_json(base / 'segments.json', {
            'schema': 1, 'game': store.GAME, 'attempt_id': attempt_id,
            'attempt_no': number,
        })

    archive.enforce_local_retention(data, keep=3)

    for attempt_id in ids[-3:]:
        base = data / 'attempts' / attempt_id
        assert (base / 'manifest.json').is_file()
        assert (base / 'segments.json').is_file()
    for attempt_id in ids[:2]:
        assert not (data / 'attempts' / attempt_id).exists()


def test_total_attempt_cap_drops_oldest_spool_and_records_it(tmp_path, server):
    write_env(tmp_path, server.url)
    (tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun').mkdir(
        parents=True)
    data, _runtime, old = make_attempt(
        tmp_path, 'a0001-g1-abc123', attempt_no=1, files={'old.bin': b'x' * 200})
    _data, _runtime, newer = make_attempt(
        tmp_path, 'a0002-g1-abc123', attempt_no=2, files={'new.bin': b'y' * 50})
    current_data, runtime3, running = make_attempt(
        tmp_path, 'a0003-g2-def456', attempt_no=3, runtime_id='g2-def456',
        files={'run.bin': b'z' * 100})
    current = timer.start_attempt(
        timer.new_timer('g2-def456', timer.default_records(), BASE_MS + 3000),
        timer.default_records(), BASE_MS + 3000, attempt_no=3)
    store.save_timer(runtime3, current[0])

    dropped = archive.enforce_attempts_cap(data, max_bytes_value=250)

    assert dropped == 200
    assert not (data / 'attempts' / old / 'spool' / 'old.bin').exists()
    assert (data / 'attempts' / old / 'manifest.json').is_file()
    assert (data / 'attempts' / old / 'segments.json').is_file()
    state = read_json(data / 'attempts' / old / 'archive_state.json')
    assert state['dropped_for_cap'] == 200
    assert read_json(data / 'attempts' / old / 'manifest.json')[
        'dropped_for_cap'] == 200
    assert (data / 'attempts' / newer / 'spool' / 'new.bin').is_file()
    assert (data / 'attempts' / running / 'spool' / 'run.bin').is_file()


def test_pending_enforces_the_total_cap_even_when_unconfigured(tmp_path):
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    data.mkdir(parents=True)
    data, _runtime, old = make_attempt(
        tmp_path, 'a0001-g1-abc123', attempt_no=1, files={'old.bin': b'x' * 300})
    os.environ['DOCICH_SPEEDRUN_ATTEMPTS_MAX_BYTES'] = '100'

    summary = archive.upload_pending(data)

    assert summary['code'] == 'unconfigured'
    assert not (data / 'attempts' / old / 'spool' / 'old.bin').exists()


def test_cap_counts_orphan_attempt_directories_and_oldest_mtime_first(tmp_path):
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    attempts = data / 'attempts'
    old = attempts / 'a0001-g9-orphan-old'
    new = attempts / 'a0002-g9-orphan-new'
    for directory, payload in ((old, b'o' * 120), (new, b'n' * 120)):
        (directory / 'spool').mkdir(parents=True)
        (directory / 'spool' / 'data.bin').write_bytes(payload)
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    dropped = archive.enforce_attempts_cap(data, max_bytes_value=150)

    assert dropped == 120
    assert not (old / 'spool' / 'data.bin').exists()
    assert (new / 'spool' / 'data.bin').is_file()
    state = read_json(old / 'archive_state.json')
    assert state['dropped_for_cap'] == 120


def test_unknown_attempt_no_is_not_dropped_before_a_known_old_attempt(tmp_path):
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    attempts = data / 'attempts'
    known = attempts / 'a0001-g1-known'
    orphan = attempts / 'a0002-g1-orphan'
    for directory, payload in ((known, b'k' * 100), (orphan, b'o' * 100)):
        (directory / 'spool').mkdir(parents=True)
        (directory / 'spool' / 'data.bin').write_bytes(payload)
    # The orphan has no attempt_no (unknown) and a newer directory mtime, so
    # it must not be selected first just because its number parses as 0.
    os.utime(known, (1000, 1000))
    os.utime(orphan, (2000, 2000))
    store.append_attempt(
        tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123',
        {'schema': 1, 'game': store.GAME, 'attempt_id': known.name,
         'attempt_no': 1, 'status': 'dnf', 'archived': False})

    dropped = archive.enforce_attempts_cap(data, max_bytes_value=150)

    assert dropped == 100
    assert not (known / 'spool' / 'data.bin').exists()
    assert (orphan / 'spool' / 'data.bin').is_file()


def test_launch_pending_enforces_the_cap_without_an_env_file(
        tmp_path, monkeypatch):
    data = tmp_path / 'run-soren-live' / 'speedrun' / 'hanjuku-speedrun'
    data.mkdir(parents=True)
    _data, _runtime, attempt = make_attempt(
        tmp_path, files={'old.bin': b'x' * 300})
    os.environ['DOCICH_SPEEDRUN_ATTEMPTS_MAX_BYTES'] = '100'
    calls = []
    monkeypatch.setattr(archive.subprocess, 'Popen',
                        lambda *args, **kwargs: calls.append(args))
    monkeypatch.setenv('DOCICH_R2_ENV_FILE', str(tmp_path / 'missing.env'))

    assert archive.launch_pending(data) is False

    assert calls == []
    assert not (data / 'attempts' / attempt / 'spool' / 'old.bin').exists()


# ---------------------------------------------------------------------------
# lock / launcher / CLI
# ---------------------------------------------------------------------------


def test_second_uploader_exits_immediately(tmp_path, server):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    lock_path = data / archive.LOCK_FILE
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT / 'src')
        env['DOCICH_R2_ENV_FILE'] = str(tmp_path / 'r2.env')
        process = subprocess.run(
            [sys.executable, '-m', 'docich.hanjuku_speedrun.rta.archive',
             '--data-dir', str(data), 'upload', '--pending'],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert process.returncode == 0
    assert 'locked' in process.stdout
    assert server.state.requests == []
    assert (data / 'attempts' / attempt_id / 'spool' /
            'decisions-0001.jsonl.gz').is_file()


def test_launch_pending_checks_only_env_existence(tmp_path, monkeypatch):
    env_file = tmp_path / 'r2.env'
    sentinel = 'not-read-here'
    env_file.write_text(f'R2_SECRET_ACCESS_KEY={sentinel}\n', encoding='utf-8')
    monkeypatch.setenv('DOCICH_R2_ENV_FILE', str(env_file))
    calls = []
    opened = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    original_path_open = Path.open
    original_builtin_open = __import__('builtins').open

    def recording_path_open(self, *args, **kwargs):
        opened.append(Path(self))
        return original_path_open(self, *args, **kwargs)

    def recording_builtin_open(file, *args, **kwargs):
        try:
            opened.append(Path(file))
        except TypeError:
            pass
        return original_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', recording_path_open)
    monkeypatch.setattr(__import__('builtins'), 'open', recording_builtin_open)
    monkeypatch.setattr(archive.subprocess, 'Popen', _FakePopen)

    assert archive.launch_pending(tmp_path) is True
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert '-m' in args
    assert 'upload' in args and '--pending' in args
    assert kwargs['start_new_session'] is True
    assert kwargs['stdin'] == subprocess.DEVNULL
    assert kwargs['stdout'] == subprocess.DEVNULL
    assert kwargs['stderr'] == subprocess.DEVNULL
    # The env file is only stat'ed: its contents never reach this process,
    # the child argument list or the child environment.
    assert env_file not in opened
    assert sentinel not in ' '.join(str(value) for value in args)
    assert sentinel not in ' '.join(str(value) for value in kwargs.values())
    assert sentinel not in ' '.join(
        str(value) for value in kwargs.get('env', {}).values())


def test_launch_pending_swallows_a_launch_error(tmp_path, monkeypatch):
    env_file = tmp_path / 'r2.env'
    env_file.write_text('R2_ACCOUNT_ID=x\n', encoding='utf-8')
    monkeypatch.setenv('DOCICH_R2_ENV_FILE', str(env_file))

    def explode(*_args, **_kwargs):
        raise OSError('boom')

    monkeypatch.setattr(archive.subprocess, 'Popen', explode)
    assert archive.launch_pending(tmp_path) is False


def test_hook_launches_the_archiver_once_per_runtime(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive, 'launch_pending',
                        lambda runtime: calls.append(Path(runtime)))
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    meta = {'hanjuku': {'name_entered': True, 'phase': 'name',
                        'gameplay_seen': False, 'clear_ending_seen': False,
                        'terminal_reason': None}}

    hook.observe(runtime, [], {}, meta, screenshot=shot)
    hook.observe(runtime, [], {}, meta, screenshot=shot)

    assert calls == [runtime]
    assert (runtime / '.archive-launched').is_file()
    assert not (store.data_dir(runtime) / '.archive-launched').exists()

    # A new corner must not begin sending while the shared timer still says a
    # trial is running; once that attempt has ended, the next runtime starts
    # its own (single) uploader.
    store.save_timer(runtime, {**store.load_timer(runtime), 'status': 'dnf'})
    other = tmp_path / 'run-soren-live' / 'runtimes' / 'g2-def456'
    other.mkdir(parents=True)
    other_shot = other / 'screenshots' / 'latest.png'
    other_shot.parent.mkdir(parents=True)
    other_shot.write_bytes(b'frame')
    hook.observe(other, [], {}, meta, screenshot=other_shot)
    assert calls == [runtime, other]


def test_hook_does_not_start_an_upload_during_a_running_attempt(
        tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive, 'launch_pending',
                        lambda runtime: calls.append(Path(runtime)))
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    started, _records = timer.start_attempt(
        timer.new_timer(runtime.name, timer.default_records(), BASE_MS),
        timer.default_records(), BASE_MS, attempt_no=1)
    store.save_timer(runtime, started)
    meta = {'hanjuku': {'name_entered': True, 'phase': 'name',
                        'gameplay_seen': False, 'clear_ending_seen': False,
                        'terminal_reason': None}}

    hook.observe(runtime, [], {}, meta, screenshot=shot)
    assert calls == []
    assert (runtime / '.archive-launched').is_file()

    # Even after the attempt ends, this runtime generation does not start a
    # send; the next corner (a new runtime) is the next opportunity.
    ended = {**started, 'status': 'dnf'}
    store.save_timer(runtime, ended)
    hook.observe(runtime, [], {}, meta, screenshot=shot)
    assert calls == []


def test_hook_does_not_start_while_the_shared_timer_is_running(
        tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive, 'launch_pending',
                        lambda runtime: calls.append(Path(runtime)))
    owner = tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    owner.mkdir(parents=True)
    started, _records = timer.start_attempt(
        timer.new_timer(owner.name, timer.default_records(), BASE_MS),
        timer.default_records(), BASE_MS, attempt_no=1)
    store.save_timer(owner, started)
    other = tmp_path / 'run-soren-live' / 'runtimes' / 'g2-def456'
    other.mkdir(parents=True)
    shot = other / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')

    hook.observe(other, [], {}, {'hanjuku': {
        'name_entered': True, 'phase': 'name', 'gameplay_seen': False,
        'clear_ending_seen': False, 'terminal_reason': None}},
        screenshot=shot)

    assert calls == []
    assert (other / '.archive-launched').is_file()


def test_hook_starts_the_upload_for_a_pending_corner(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive, 'launch_pending',
                        lambda runtime: calls.append(Path(runtime)))
    runtime = tmp_path / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    store.save_timer(runtime, timer.new_timer(
        runtime.name, timer.default_records(), BASE_MS))
    meta = {'hanjuku': {'name_entered': True, 'phase': 'name',
                        'gameplay_seen': False, 'clear_ending_seen': False,
                        'terminal_reason': None}}

    hook.observe(runtime, [], {}, meta, screenshot=shot)

    assert calls == [runtime]


def test_game_switch_launches_after_finalizing_teardown(tmp_path, monkeypatch):
    data, runtime, attempt_id = make_attempt(tmp_path, status='dnf')
    # make_attempt only writes a finished row; the hook's start is needed for
    # finalize_speedrun_attempts to have a running timer to close.
    current, records = timer.start_attempt(
        timer.new_timer(runtime.name, timer.default_records(), BASE_MS),
        timer.default_records(), BASE_MS)
    store.save_timer(runtime, current)
    (runtime / 'hanjuku_run.json').write_text(
        json.dumps({'terminal_reason': None, 'clear_ending_seen': False}),
        encoding='utf-8')
    calls = []
    monkeypatch.setattr(archive, 'launch_pending',
                        lambda directory: calls.append(Path(directory)))
    warnings = []

    game_switch.finalize_speedrun_attempts(
        tmp_path / 'run-soren-live',
        [{'game': store.GAME, 'runtime_id': runtime.name}], warnings)

    assert warnings == []
    assert calls == [runtime]


def test_cli_exit_codes(tmp_path, server, capsys):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)

    assert archive.main(['--data-dir', str(data), 'upload']) == 2
    assert archive.main(['--data-dir', str(data), 'status']) == 0
    status_doc = json.loads(capsys.readouterr().out)
    assert status_doc['configured'] is True

    server.state.head_size_override = 1
    assert archive.main(['--data-dir', str(data), 'upload',
                         '--attempt', attempt_id]) == 1
    capsys.readouterr()

    os.unlink(tmp_path / 'r2.env')
    assert archive.main(['--data-dir', str(data), 'upload', '--pending']) == 0
    assert 'unconfigured' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# credential hygiene
# ---------------------------------------------------------------------------


def test_credentials_are_not_written_to_logs_or_state(tmp_path, server, capsys):
    write_env(tmp_path, server.url)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    server.state.head_size_override = 7

    result = archive.upload_attempt(data, attempt_id)
    archive.main(['--data-dir', str(data), 'upload', '--attempt', attempt_id])
    captured = capsys.readouterr()

    assert result.ok is False
    outputs = [captured.out, captured.err,
               str(result), repr(result), str(captured)]
    for path in (data / 'attempts' / attempt_id).rglob('*'):
        if path.is_file():
            outputs.append(path.read_text(encoding='utf-8', errors='replace'))
    outputs.append(json.dumps(archive.status(data)))
    assert ACCESS not in '\n'.join(outputs)
    assert SECRET not in '\n'.join(outputs)
    # The secret is not sent; the Authorization header carries only the
    # access key id, which is the SigV4 credential identifier.
    for request in server.state.requests:
        assert SECRET not in json.dumps(request['headers'])
        assert SECRET.encode() not in request['body']


def test_account_id_never_appears_in_repr_errors_cli_or_state(
        tmp_path, monkeypatch, capsys):
    account = 'ACCOUNTIDUNIQUE987'
    write_env(tmp_path, None, account=account)
    data, _runtime, attempt_id = make_attempt(tmp_path)
    config, code = archive.load_config()
    assert code == 'ok' and config is not None
    assert account not in repr(config)
    assert account not in str(config)

    class _FailingConnection:
        def request(self, *_args, **_kwargs):
            raise OSError('simulated network failure')

        def getresponse(self):  # pragma: no cover - request raises first
            raise OSError('simulated network failure')

        def close(self):
            pass

    monkeypatch.setattr(sigv4.R2Client, '_connection',
                        lambda _self: _FailingConnection())
    result = archive.upload_attempt(data, attempt_id)
    assert result.ok is False and result.code == 'put_failed'
    exit_code = archive.main(
        ['--data-dir', str(data), 'upload', '--attempt', attempt_id])
    captured = capsys.readouterr()
    state_text = (data / 'attempts' / attempt_id / 'archive_state.json').read_text(
        encoding='utf-8')
    outputs = [repr(result), str(result), captured.out, captured.err,
               json.dumps(archive.status(data)), state_text]
    assert exit_code == 1
    assert account not in '\n'.join(outputs)


def test_insecure_env_permissions_are_refused_with_a_fixed_code(tmp_path, server):
    write_env(tmp_path, server.url, mode=0o644)
    data, _runtime, attempt_id = make_attempt(tmp_path)

    result = archive.upload_attempt(data, attempt_id)

    assert result.ok is False and result.code == 'insecure_permissions'
    assert server.state.requests == []
    assert (data / 'attempts' / attempt_id / 'spool' /
            'decisions-0001.jsonl.gz').is_file()
    state_path = data / 'attempts' / attempt_id / 'archive_state.json'
    assert read_json(state_path)['last_error'] == 'insecure_permissions'
    assert SECRET not in state_path.read_text(encoding='utf-8')


def test_missing_credentials_are_reported_as_unconfigured(tmp_path):
    path = tmp_path / 'r2.env'
    path.write_text('R2_ACCOUNT_ID=account\nR2_BUCKET=bucket\n',
                    encoding='utf-8')
    os.chmod(path, 0o600)
    os.environ['DOCICH_R2_ENV_FILE'] = str(path)

    config, code = archive.load_config()

    assert config is None and code == 'unconfigured'
