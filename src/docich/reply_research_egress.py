"""A private Unix-socket egress bridge for the read-only OpenCode namespace.

The child network namespace has no route to the host or container network. Its
only network path is a loopback HTTP CONNECT listener which relays through the
single Unix socket created here. This side accepts only opencode.ai:443 and
pins each connection to a globally routable DNS answer before connecting.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time

ALLOWED_HOST = "opencode.ai"
ALLOWED_PORT = 443
MAX_HEADERS = 8192
CONNECT_TIMEOUT_SEC = 5.0
DNS_TIMEOUT_SEC = 3.0
RELAY_IDLE_TIMEOUT_SEC = 30.0
HALF_CLOSE_GRACE_SEC = 2.0
MAX_DNS_ANSWERS = 64
MAX_DNS_OUTPUT = 8192

_DNS_SCRIPT = """import json, socket
try:
    rows = socket.getaddrinfo('opencode.ai', 443, type=socket.SOCK_STREAM)
    out = [[family, socktype, proto, address]
           for family, socktype, proto, _canon, address in rows[:64]]
    print(json.dumps(out, separators=(',', ':')))
except OSError:
    print('[]')
"""


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


def _resolve_api_addresses() -> list[tuple[int, int, int, tuple]]:
    """Resolve the fixed host in a bounded, credential-free subprocess."""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _DNS_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        return []
    try:
        try:
            output, _ = proc.communicate(timeout=DNS_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            return []
        if proc.returncode != 0 or len(output) > MAX_DNS_OUTPUT:
            return []
        raw = json.loads(output)
        if type(raw) is not list or len(raw) > MAX_DNS_ANSWERS:
            return []
        answers = []
        for row in raw:
            if (type(row) is not list or len(row) != 4
                    or type(row[0]) is not int or type(row[1]) is not int
                    or type(row[2]) is not int or type(row[3]) is not list):
                continue
            family, socktype, proto, address = row
            if (family not in {socket.AF_INET, socket.AF_INET6}
                    or type(address) is not list or len(address) < 2
                    or type(address[0]) is not str or type(address[1]) is not int):
                continue
            sockaddr = tuple(address)
            answers.append((family, socktype, proto, sockaddr))
        return answers
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()


class _ConnectionState:
    """Own every socket for one request so server shutdown can cancel it."""

    def __init__(self, client: socket.socket):
        self._lock = threading.Lock()
        self._sockets = {client}
        self._closed = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._closed

    def add(self, conn: socket.socket) -> bool:
        with self._lock:
            if not self._closed:
                self._sockets.add(conn)
                return True
        _close_socket(conn)
        return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = tuple(self._sockets)
        for conn in sockets:
            _close_socket(conn)


def _close_socket(conn: socket.socket) -> None:
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _public_api_socket(state: _ConnectionState | None = None) -> socket.socket | None:
    """Resolve the one fixed destination, rejecting every non-global answer."""
    for answer in _resolve_api_addresses():
        if len(answer) == 5:
            family, socktype, proto, _canonname, address = answer
        else:
            family, socktype, proto, address = answer
        try:
            ip = ipaddress.ip_address(address[0])
        except (ValueError, IndexError):
            continue
        if not ip.is_global:
            continue
        try:
            conn = socket.socket(family, socktype, proto)
        except OSError:
            continue
        if state is not None and not state.add(conn):
            return None
        try:
            conn.settimeout(CONNECT_TIMEOUT_SEC)
            conn.connect(address)
            if state is not None and state.cancelled:
                _close_socket(conn)
                return None
            conn.settimeout(None)
            return conn
        except OSError:
            _close_socket(conn)
    return None


def _relay(left: socket.socket, right: socket.socket) -> None:
    """Relay with bounded idle and half-close periods."""
    active = {left, right}
    peer = {left: right, right: left}
    now = time.monotonic()
    idle_deadline = now + RELAY_IDLE_TIMEOUT_SEC
    half_close_deadline: float | None = None
    while active:
        now = time.monotonic()
        deadline = min(idle_deadline, half_close_deadline) if half_close_deadline else idle_deadline
        remaining = deadline - now
        if remaining <= 0:
            return
        try:
            ready, _, _ = select.select(list(active), [], [], min(0.25, remaining))
        except (OSError, ValueError):
            return
        for source in ready:
            destination = peer[source]
            try:
                data = source.recv(16384)
            except OSError:
                return
            if not data:
                active.discard(source)
                if not active:
                    return
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    return
                half_close_deadline = time.monotonic() + HALF_CLOSE_GRACE_SEC
                continue
            try:
                destination.settimeout(CONNECT_TIMEOUT_SEC)
                destination.sendall(data)
                destination.settimeout(None)
            except OSError:
                return
            idle_deadline = time.monotonic() + RELAY_IDLE_TIMEOUT_SEC


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        state = _ConnectionState(client)
        server = self.server
        if not server.register_connection(state):
            state.close()
            return
        try:
            client.settimeout(CONNECT_TIMEOUT_SEC)
            headers = _read_headers(client)
            if state.cancelled:
                return
            if headers is None or not allowed_connect(headers):
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                return
            upstream = _public_api_socket(state)
            if upstream is None or state.cancelled:
                if not state.cancelled:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(None)
            _relay(client, upstream)
        except OSError:
            pass
        finally:
            state.close()
            server.unregister_connection(state)


class _UnixProxyServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = False
    daemon_threads = False
    block_on_close = True
    request_queue_size = 8

    def __init__(self, *args, **kwargs):
        self._connections_lock = threading.Lock()
        self._connections: set[_ConnectionState] = set()
        self._closing = False
        super().__init__(*args, **kwargs)

    @property
    def active_connections(self) -> frozenset[_ConnectionState]:
        with self._connections_lock:
            return frozenset(self._connections)

    def register_connection(self, state: _ConnectionState) -> bool:
        with self._connections_lock:
            if self._closing:
                return False
            self._connections.add(state)
            return True

    def unregister_connection(self, state: _ConnectionState) -> None:
        with self._connections_lock:
            self._connections.discard(state)

    def stop_active_connections(self) -> None:
        with self._connections_lock:
            self._closing = True
            states = tuple(self._connections)
        for state in states:
            state.close()


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
                                       kwargs={"poll_interval": 0.05},
                                       name="docich-research-egress", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.server is not None:
            self.server.stop_active_connections()
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.socket_path.unlink(missing_ok=True)
