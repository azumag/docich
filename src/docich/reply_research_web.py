"""Credential-free, four-host text retrieval and trusted per-run receipts.

Only the broker opens external sockets. Its short-lived worker has no inherited
credentials/config/proxies, pins DNS answers, verifies TLS and rejects redirects.
The sandbox receives this file read-only as a fixed Unix-socket client helper;
receipt authority stays in the broker, never in model stdout or writable files.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import re
import uuid

HOSTS = frozenset({'ja.wikipedia.org', 'en.wikipedia.org', 'github.com', 'raw.githubusercontent.com'})
SOCKET = '/tmp/.docich-web-fetch.sock'
HELPER = '/tmp/docich-web-fetch.py'
MAX_BODY = 131072
MAX_TEXT = 16384
MAX_REPLY = 65536
MAX_FETCHES = 4
FETCH_TIMEOUT = 8.0


def canonical_url(value: str) -> str | None:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value):
        return None
    try:
        p = urlsplit(value)
        if (p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password
                or p.port not in (None, 443) or p.query or p.fragment
                or p.netloc not in {p.hostname, p.hostname + ':443'}):
            return None
        path = p.path or '/'
        if (re.search(r'%(?![0-9a-fA-F]{2})', path)
                or any(ord(c) < 33 or ord(c) == 127 or c == '\\' for c in unquote(path))):
            return None
        path = quote(path, safe="/%:,@!()*+=-._~")
        path = re.sub(r'%[0-9a-fA-F]{2}', lambda m: m[0].upper(), path)
        result = urlunsplit(('https', p.hostname, path, '', ''))
        return result if len(result) <= 512 else None
    except ValueError:
        return None


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'template', 'head', 'svg', 'noscript'}:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_text(body: bytes, content_type: str) -> str:
    parts = [part.strip().lower() for part in content_type.split(';')]
    if parts[0] not in {'text/plain', 'text/html'}:
        raise ValueError('mime')
    if any(part not in {'charset=utf-8', 'charset="utf-8"'} for part in parts[1:]):
        raise ValueError('charset')
    text = body.decode('utf-8', errors='strict')
    if '\x00' in text or len(body) > MAX_BODY:
        raise ValueError('body')
    if parts[0] == 'text/html':
        parser = _HTMLText()
        parser.feed(text)
        parser.close()
        text = ' '.join(' '.join(parser.parts).split())
    # No successful truncated evidence. Oversized extracted text holds.
    if not text.strip() or len(text.encode('utf-8')) > MAX_TEXT:
        raise ValueError('text_limit')
    return text


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('deadline')
    return remaining


def fetch_worker(url: str, timeout: float) -> dict:
    """Runs only in a bounded, credential-free child. Never uses urlopen/proxy."""
    url = canonical_url(url)
    if url is None or not 0 < timeout <= FETCH_TIMEOUT:
        raise ValueError('url')
    p = urlsplit(url)
    deadline = time.monotonic() + timeout
    answers = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not 1 <= len(answers) <= 64:
        raise ValueError('dns')
    for family, kind, proto, _, address in answers:
        ip = ipaddress.ip_address(address[0])
        if (family not in {socket.AF_INET, socket.AF_INET6} or kind != socket.SOCK_STREAM
                or not ip.is_global or address[1] != 443
                or (family == socket.AF_INET6 and (len(address) != 4 or address[3] != 0))):
            raise ValueError('nonpublic_dns')
    # The validated sockaddr is used directly, never re-resolved on connection.
    family, kind, proto, _, address = answers[0]
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    # This process owns these stdlib limits; nothing changes in the host process.
    http.client._MAXLINE = 4096
    http.client._MAXHEADERS = 32
    with socket.socket(family, kind, proto) as raw:
        raw.settimeout(min(3.0, _remaining(deadline)))
        raw.connect(address)
        raw.settimeout(_remaining(deadline))
        with context.wrap_socket(raw, server_hostname=p.hostname) as conn:
            target = p.path or '/'
            conn.settimeout(_remaining(deadline))
            conn.sendall((f'GET {target} HTTP/1.1\r\nHost: {p.hostname}\r\n'
                          'User-Agent: docich-public-evidence/1\r\n'
                          'Accept: text/plain, text/html\r\nAccept-Encoding: identity\r\n'
                          'Connection: close\r\n\r\n').encode('ascii'))
            response = http.client.HTTPResponse(conn)
            response.begin()
            if response.status != 200:
                raise ValueError('http_status')  # All redirects denied, including same-host.
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('encoding')
            types = response.headers.get_all('Content-Type', [])
            lengths = response.headers.get_all('Content-Length', [])
            transfers = response.headers.get_all('Transfer-Encoding', [])
            if (len(types) != 1 or len(lengths) > 1 or len(transfers) > 1
                    or (lengths and transfers)
                    or (transfers and transfers[0].lower() != 'chunked')):
                raise ValueError('headers')
            if lengths and (not lengths[0].isascii() or not lengths[0].isdigit()
                            or int(lengths[0]) > MAX_BODY):
                raise ValueError('body_limit')
            chunks, total = [], 0
            while True:
                conn.settimeout(_remaining(deadline))
                chunk = response.read1(min(8192, MAX_BODY + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk); total += len(chunk)
                if total > MAX_BODY:
                    raise ValueError('body_limit')
            body = b''.join(chunks)
            if lengths and len(body) != int(lengths[0]):
                raise ValueError('incomplete_body')
            text = extract_text(body, types[0])
            return {'url': url, 'content_type': types[0],
                    'body_b64': base64.b64encode(body).decode('ascii'),
                    'sha256': hashlib.sha256(body).hexdigest(), 'text': text}


@dataclass(frozen=True)
class Receipt:
    url: str
    receipt: str
    sha256: str
    text_sha256: str
    text: str

    def wire(self):
        return {'status': 'ok', **self.__dict__}


def _kill(proc):
    # The leader may already have exited while its descendants retain stdout.
    # start_new_session gives this worker its own group: always kill that group
    # before draining the pipe, including successful/malformed leader exits.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _bounded_output(proc, deadline):
    output = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            for key, _ in selector.select(min(.2, _remaining(deadline))):
                data = os.read(key.fd, 8192)
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(data)
                    if len(output) > 262144:
                        raise ValueError('worker_output_limit')
    proc.wait(timeout=_remaining(deadline))
    return bytes(output)


def _read_line(conn, limit):
    data = bytearray()
    while len(data) <= limit:
        chunk = conn.recv(1)
        if not chunk:
            raise ValueError('incomplete_wire')
        if chunk == b'\n':
            return bytes(data)
        data.extend(chunk)
    raise ValueError('wire_limit')


class WebBroker:
    def __init__(self, path: Path, deadline: float):
        self.path, self.deadline = Path(path), deadline
        self._lock = threading.Lock()
        self._gate = threading.Lock()
        self._processes = set()
        self._receipts = {}
        self._candidates = set()
        self._attempts = 0
        self._closing = False

    @property
    def receipts(self):
        with self._lock:
            return dict(self._receipts)

    def observe(self, event):
        """Only the parent CLI JSONL reader calls this, never the socket client.

        Metadata authorizes a candidate GET; it is not body/quote evidence.
        """
        if type(event) is not dict:
            return
        item = event.get('item')
        action = item.get('action') if type(item) is dict else None
        if (event.get('type') != 'item.completed' or type(item) is not dict
                or item.get('type') != 'web_search' or type(action) is not dict
                or action.get('type') != 'search' or not isinstance(item.get('query'), str)
                or not item['query'].strip() or type(item.get('results')) is not list):
            return
        with self._lock:
            for result in item['results'][:64]:
                url = canonical_url(result.get('url')) if type(result) is dict else None
                if url and len(self._candidates) < 64:
                    self._candidates.add(url)

    def fetch(self, url):
        url = canonical_url(url)
        if url is None or not self._gate.acquire(blocking=False):
            return None
        proc = None
        try:
            with self._lock:
                if self._closing or url not in self._candidates:
                    return None
                for receipt in self._receipts.values():
                    if receipt.url == url:
                        return receipt
                if self._attempts >= MAX_FETCHES:
                    return None
                self._attempts += 1
            timeout = min(FETCH_TIMEOUT, _remaining(self.deadline))
            proc = subprocess.Popen(
                [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--fetch', url, str(timeout)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
                close_fds=True, start_new_session=True)
            with self._lock:
                self._processes.add(proc)
                if self._closing:
                    _kill(proc)
            output = _bounded_output(proc, min(self.deadline, time.monotonic() + timeout))
            if proc.returncode != 0 or len(output) > 262144:
                return None
            value = json.loads(output)
            body = base64.b64decode(value['body_b64'], validate=True)
            if value['url'] != url or len(body) > MAX_BODY:
                return None
            text = extract_text(body, value['content_type'])
            digest = hashlib.sha256(body).hexdigest()
            if value['sha256'] != digest or value['text'] != text:
                return None
            _remaining(self.deadline)
            receipt = Receipt(url, uuid.uuid4().hex, digest,
                              hashlib.sha256(text.encode()).hexdigest(), text)
            with self._lock:
                if self._closing:
                    return None
                self._receipts[receipt.receipt] = receipt
            return receipt
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            return None
        finally:
            if proc is not None:
                _kill(proc)
                proc.communicate()  # Reap on success, timeout, malformed output and cancellation.
                with self._lock:
                    self._processes.discard(proc)
            self._gate.release()

    def __enter__(self):
        from .reply_research_egress import _UnixProxyServer, _ConnectionState
        import socketserver
        broker = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                state = _ConnectionState(self.request)
                if not self.server.register_connection(state):
                    state.close(); return
                try:
                    self.request.settimeout(min(2.0, _remaining(broker.deadline)))
                    request = json.loads(_read_line(self.request, 2048))
                    if type(request) is not dict or set(request) != {'url'}:
                        raise ValueError('request')
                    receipt = broker.fetch(request['url'])
                    value = receipt.wire() if receipt else {'status': 'unavailable'}
                    self.request.settimeout(min(2.0, _remaining(broker.deadline)))
                    self.request.sendall(json.dumps(value, ensure_ascii=False).encode() + b'\n')
                except (OSError, ValueError, TypeError):
                    pass
                finally:
                    state.close(); self.server.unregister_connection(state)
        if self.path.exists() or self.path.is_symlink():
            raise ValueError('socket_exists')
        class LimitedServer(_UnixProxyServer):
            slots = threading.BoundedSemaphore(4)
            def process_request(self, request, address):
                if not self.slots.acquire(blocking=False):
                    request.close(); return
                try:
                    super().process_request(request, address)
                except Exception:
                    self.slots.release(); raise
            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()
        self.server = LimitedServer(str(self.path), Handler)
        try:
            os.chmod(self.path, 0o600, follow_symlinks=False)
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={'poll_interval': .05}, daemon=True)
            self.thread.start()
        except Exception:
            self.server.server_close()
            self.path.unlink(missing_ok=True)
            raise
        return self

    def __exit__(self, *_):
        with self._lock:
            self._closing = True
            for proc in tuple(self._processes):
                _kill(proc)
        self.server.stop_active_connections()
        self.server.shutdown(); self.server.server_close()
        self.thread.join(timeout=1)
        self.path.unlink(missing_ok=True)


def client(url):
    if canonical_url(url) is None:
        raise ValueError('url')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(FETCH_TIMEOUT + 2)
        conn.connect(SOCKET)
        conn.sendall(json.dumps({'url': url}).encode() + b'\n')
        value = json.loads(_read_line(conn, MAX_REPLY))
        if type(value) is not dict or value.get('status') != 'ok':
            raise ValueError('unavailable')
        return value


def main():
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--fetch':
            value = fetch_worker(sys.argv[2], float(sys.argv[3]))
        elif len(sys.argv) == 3 and sys.argv[1] == '--client':
            value = client(sys.argv[2])
        else:
            return 2
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Exception:
        print('{"status":"unavailable"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
