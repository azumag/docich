"""Credential-free, public HTTPS text retrieval and trusted per-run receipts.

Only the broker opens external sockets. Its short-lived worker has no inherited
credentials/config/proxies, pins DNS answers, verifies TLS and rejects redirects.
Only the parent coordinator requests retrieval;
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

MAX_BODY = 131072
MAX_TEXT = 16384
MAX_REPLY = 65536
MAX_FETCHES = 4
FETCH_TIMEOUT = 8.0
WEB_FAILURE_REASONS = frozenset({
    'url', 'dns', 'nonpublic_dns', 'deadline', 'http_status', 'encoding',
    'headers', 'body_limit', 'incomplete_body', 'mime', 'charset', 'body',
    'text_limit', 'worker_output_limit', 'transport_failure', 'invalid_worker',
})


def failure_reason(error):
    """Never export exception text, URLs, headers or DNS addresses."""
    if (type(error) is ValueError and len(error.args) == 1
            and type(error.args[0]) is str and error.args[0] in WEB_FAILURE_REASONS):
        return error.args[0]
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return 'deadline'
    return 'transport_failure'


def canonical_url(value: str) -> str | None:
    """Public candidate syntax only. The worker separately validates ALL DNS IPs."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value):
        return None
    try:
        p = urlsplit(value); host = p.hostname
        if (p.scheme != "https" or not host or p.username or p.password
                or p.port not in (None, 443) or p.fragment or host.endswith(".")):
            return None
        host = host.encode("idna").decode("ascii").lower()
        if ("." not in host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host)
                or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split("."))
                or host.endswith((".localhost", ".local", ".internal", ".home", ".lan"))):
            return None
        try:
            ipaddress.ip_address(host); return None
        except ValueError:
            pass
        from urllib.parse import parse_qsl
        private = {"token", "key", "api_key", "apikey", "access_token", "authorization", "secret", "password", "session", "sessionid", "signature", "sig", "cookie"}
        if any(key.lower() in private for key, _ in parse_qsl(p.query, keep_blank_values=True)):
            return None
        if (re.search(r"%(?![0-9a-fA-F]{2})", value)
                or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in unquote(p.path + p.query))):
            return None
        path = quote(p.path or "/", safe="/%:,@!()*+=-._~")
        query = quote(p.query, safe="%=&/:,@!()*+-._~")
        result = urlunsplit(("https", host, path, query, ""))
        return result if len(result) <= 512 else None
    except (ValueError, UnicodeError):
        return None


def public_address(value):
    ip = ipaddress.ip_address(value)
    return (ip.is_global and not ip.is_multicast and not ip.is_reserved
            and not getattr(ip, "ipv4_mapped", None) and not getattr(ip, "sixtofour", None)
            and not getattr(ip, "teredo", None))


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
                or not public_address(ip) or address[1] != 443
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
            target = (p.path or '/') + ('?' + p.query if p.query else '')
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


def search_worker(query: str, timeout: float) -> dict:
    """Runs only in a bounded, credential-free child. Never uses urlopen/proxy."""
    url = "https://mcp.exa.ai/mcp"
    if not isinstance(query, str) or not 1 <= len(query) <= 256 or any(ord(c) < 32 for c in query):
        raise ValueError("query")
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
                or not public_address(ip) or address[1] != 443
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
            target = (p.path or '/') + ('?' + p.query if p.query else '')
            conn.settimeout(_remaining(deadline))
            payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': 'web_search_exa', 'arguments': {'query': query,
                                             'type': 'fast', 'numResults': 8, 'livecrawl': 'preferred',
                                             'contextMaxCharacters': 5000}}}).encode()
            conn.sendall((f'POST /mcp HTTP/1.1\r\nHost: {p.hostname}\r\n'
                          'User-Agent: docich-public-evidence/1\r\n'
                          'Accept: application/json, text/event-stream\r\nContent-Type: application/json\r\n'
                          f'Content-Length: {len(payload)}\r\nAccept-Encoding: identity\r\n'
                          'Connection: close\r\n\r\n').encode('ascii') + payload)
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
            raw = body.decode("utf-8")
            messages = [raw] if raw.lstrip().startswith("{") else [line[6:] for line in raw.splitlines() if line.startswith("data: ")]
            urls = []
            for message in messages:
                value = json.loads(message)
                result = value.get("result") if type(value) is dict else None
                if type(result) is not dict or result.get("isError"):
                    raise ValueError("search_unavailable")
                for item in result.get("content", [])[:8]:
                    if type(item) is dict and item.get("type") == "text" and isinstance(item.get("text"), str):
                        for match in re.findall(r"https://[^\s<>\"']+", item["text"]):
                            candidate = canonical_url(match.rstrip("),.;"))
                            if candidate and candidate not in urls:
                                urls.append(candidate)
            return {"urls": urls[:8]}


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
    def __init__(self, path: Path, deadline: float, *, diagnostic=None):
        self.path, self.deadline = Path(path), deadline
        self._diagnostic = diagnostic
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

    def authorize(self, urls):
        """Called only with successful parent search results; not exposed over wire."""
        with self._lock:
            for value in urls[:8]:
                url = canonical_url(value)
                if url and len(self._candidates) < 16:
                    self._candidates.add(url)

    def fetch(self, url):
        url = canonical_url(url)
        if url is None or not self._gate.acquire(blocking=False):
            return None
        proc = None
        reason = 'invalid_worker'
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
                # Failed workers can report only a fixed enum. It never creates
                # a receipt, even if success fields appear beside the reason.
                value = json.loads(output)
                if (proc.returncode == 1 and type(value) is dict
                        and set(value) == {'status', 'reason'}
                        and value['status'] == 'unavailable'
                        and type(value['reason']) is str
                        and value['reason'] in WEB_FAILURE_REASONS):
                    reason = value['reason']
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
            reason = None
            return receipt
        except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.TimeoutExpired) as error:
            reason = failure_reason(error)
            return None
        finally:
            if proc is not None:
                _kill(proc)
                proc.communicate()  # Reap on success, timeout, malformed output and cancellation.
                with self._lock:
                    self._processes.discard(proc)
            self._gate.release()
            if proc is not None and reason is not None:
                from .reply_research_diagnostic import emit
                emit(self._diagnostic, {'stage': 'web_fetch', 'web_reason': reason})


def search_public(query, timeout):
    from .reply_routing import _has_private_route_input
    if not isinstance(query, str) or _has_private_route_input(query) or not 0 < timeout <= FETCH_TIMEOUT:
        return []
    proc = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--search', query, str(timeout)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env={'PATH': os.defpath, 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
                            close_fds=True, start_new_session=True)
    try:
        raw = _bounded_output(proc, time.monotonic() + timeout)
        if proc.returncode != 0:
            return []
        value = json.loads(raw)
        return value['urls'] if type(value) is dict and type(value.get('urls')) is list else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    finally:
        _kill(proc); proc.communicate()


def main():
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--fetch':
            value = fetch_worker(sys.argv[2], float(sys.argv[3]))
        elif len(sys.argv) == 4 and sys.argv[1] == '--search':
            value = search_worker(sys.argv[2], float(sys.argv[3]))
        else:
            return 2
        print(json.dumps(value, ensure_ascii=False)); return 0
    except Exception as error:
        print(json.dumps({'status': 'unavailable', 'reason': failure_reason(error)})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
