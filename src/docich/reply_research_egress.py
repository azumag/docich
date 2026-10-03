"""A private Unix-socket egress bridge for the read-only Codex namespace.

The child network namespace has no route to the host or container network. Its
only network path is a loopback HTTP CONNECT listener which relays through the
single Unix socket created here. This side accepts only api.openai.com:443 and
pins each connection to a globally routable DNS answer before connecting.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import select
import socket
import socketserver
import threading

ALLOWED_HOST = "api.openai.com"
ALLOWED_PORT = 443
MAX_HEADERS = 8192
CONNECT_TIMEOUT_SEC = 5.0


def allowed_connect(headers: bytes) -> bool:
    """Accept exactly one canonical CONNECT authority and Host header."""
    if not headers.endswith(b"\r\n\r\n") or len(headers) > MAX_HEADERS:
        return False
    try:
        lines = headers[:-4].decode("ascii").split("\r\n")
    except UnicodeDecodeError:
        return False
    request = lines[0].split(" ")
    if request != ["CONNECT", f"{ALLOWED_HOST}:{ALLOWED_PORT}", "HTTP/1.1"]:
        return False
    host_values = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator:
            return False
        if name.casefold() == "host":
            host_values.append(value.strip().casefold())
    return host_values == [f"{ALLOWED_HOST}:{ALLOWED_PORT}"]


def _read_headers(conn: socket.socket) -> bytes | None:
    data = bytearray()
    while len(data) <= MAX_HEADERS:
        chunk = conn.recv(1)
        if not chunk:
            return None
        data.extend(chunk)
        if data.endswith(b"\r\n\r\n"):
            return bytes(data)
    return None


def _public_api_socket() -> socket.socket | None:
    """Resolve the one fixed destination, rejecting every non-global answer."""
    try:
        answers = socket.getaddrinfo(ALLOWED_HOST, ALLOWED_PORT, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for family, socktype, proto, _, address in answers:
        try:
            ip = ipaddress.ip_address(address[0])
        except (ValueError, IndexError):
            continue
        if not ip.is_global:
            continue
        conn = socket.socket(family, socktype, proto)
        try:
            conn.settimeout(CONNECT_TIMEOUT_SEC)
            conn.connect(address)
            conn.settimeout(None)
            return conn
        except OSError:
            conn.close()
    return None


def _relay(left: socket.socket, right: socket.socket) -> None:
    active = {left, right}
    peer = {left: right, right: left}
    while active:
        ready, _, _ = select.select(list(active), [], [], 0.25)
        for source in ready:
            destination = peer[source]
            try:
                data = source.recv(16384)
            except OSError:
                data = b""
            if not data:
                active.discard(source)
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            destination.sendall(data)


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(CONNECT_TIMEOUT_SEC)
        headers = _read_headers(client)
        if headers is None or not allowed_connect(headers):
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        upstream = _public_api_socket()
        if upstream is None:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            return
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(None)
            _relay(client, upstream)
        except OSError:
            pass
        finally:
            upstream.close()


class _UnixProxyServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 8


class EgressProxy:
    """Context-managed, mode-0600 Unix listener with a fixed egress allowlist."""

    def __init__(self, socket_path: Path):
        self.socket_path = Path(socket_path)
        self.server: _UnixProxyServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "EgressProxy":
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise ValueError("egress_socket_exists")
        self.server = _UnixProxyServer(str(self.socket_path), _ProxyHandler)
        os.chmod(self.socket_path, 0o600, follow_symlinks=False)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="docich-research-egress", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.socket_path.unlink(missing_ok=True)
