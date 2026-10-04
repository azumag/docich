"""Trusted in-namespace HTTP proxy bridge; no direct network route exists."""
from __future__ import annotations

import ctypes
import fcntl
import os
import select
import socket
import socketserver
import struct
import subprocess
import sys
import threading

SOCKET_PATH = "/tmp/.docich-egress.sock"
ALLOWED_AUTHORITY = "opencode.ai:443"
MAX_HEADERS = 8192


def _bring_loopback_up() -> None:
    """The unshared Linux network namespace starts with lo administratively down."""
    if not sys.platform.startswith("linux"):
        raise OSError("linux_required")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        request = bytearray(struct.pack("16sH22s", b"lo", 0, b""))
        fcntl.ioctl(sock.fileno(), 0x8913, request, True)  # SIOCGIFFLAGS mutates ifreq.
        flags = struct.unpack_from("H", request, 16)[0]
        if not flags & 0x1:  # IFF_UP
            struct.pack_into("H", request, 16, flags | 0x1)
            fcntl.ioctl(sock.fileno(), 0x8914, request, True)  # SIOCSIFFLAGS


def _drop_capabilities() -> None:
    """Drop the bridge's namespace-only NET_ADMIN before launching OpenCode."""
    libc = ctypes.CDLL(None, use_errno=True)
    capset = libc.capset
    capset.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    capset.restype = ctypes.c_int
    # Linux capability v3 header and two 32-bit data words cover CAP_LAST_CAP.
    header = struct.pack("II", 0x20080522, 0)
    data = b"\0" * 24
    header_buffer = ctypes.create_string_buffer(header)
    data_buffer = ctypes.create_string_buffer(data)
    if capset(header_buffer, data_buffer) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "capset")
    if libc.prctl(47, 4, 0, 0, 0) != 0:  # PR_CAP_AMBIENT_CLEAR_ALL
        error = ctypes.get_errno()
        raise OSError(error, "ambient_capabilities")
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        error = ctypes.get_errno()
        raise OSError(error, "prctl")
    if libc.prctl(39, 0, 0, 0, 0) != 1:  # PR_GET_NO_NEW_PRIVS
        raise OSError("no_new_privs_not_set")


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


def _allowed(headers: bytes) -> bool:
    try:
        lines = headers[:-4].decode("ascii").split("\r\n")
    except (UnicodeDecodeError, IndexError):
        return False
    if lines[0].split(" ") != ["CONNECT", ALLOWED_AUTHORITY, "HTTP/1.1"]:
        return False
    values = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep:
            return False
        if name.casefold() == "host":
            values.append(value.strip().casefold())
    return values == [ALLOWED_AUTHORITY]


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
            else:
                destination.sendall(data)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(5.0)
        headers = _read_headers(client)
        if headers is None or not _allowed(headers):
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(SOCKET_PATH)
            upstream.sendall(headers)
            response = _read_headers(upstream)
            if response is None:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            client.sendall(response)
            if response.startswith(b"HTTP/1.1 200 "):
                client.settimeout(None)
                _relay(client, upstream)
        except OSError:
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            upstream.close()


class _LoopbackServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 8


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    _bring_loopback_up()
    _drop_capabilities()
    server = _LoopbackServer(("127.0.0.1", 0), _Handler)
    proxy = f"http://127.0.0.1:{server.server_address[1]}"
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": "/home/research",
        "OPENCODE_API_KEY": os.environ.get("OPENCODE_API_KEY", ""),
        "OPENCODE_GO_API_KEY": os.environ.get("OPENCODE_GO_API_KEY", ""),
        "XDG_CONFIG_HOME": "/home/research/config",
        "XDG_DATA_HOME": "/home/research/data",
        "XDG_STATE_HOME": "/home/research/state",
        "XDG_CACHE_HOME": "/home/research/cache",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
        "OPENCODE_DISABLE_CLAUDE_CODE": "true",
        "OPENCODE_CONFIG_CONTENT": __import__("json").dumps({
            "permission": dict.fromkeys(("*", "bash", "read", "grep", "glob", "list", "edit", "write", "apply_patch", "task", "skill", "webfetch", "websearch", "lsp", "question", "todowrite", "external_directory"), "deny"), "instructions": [], "share": "disabled",
            "snapshot": False, "autoupdate": False, "plugin": [], "mcp": {},
            "agent": {"docich-evidence": {"mode": "primary", "permission": dict.fromkeys(("*", "bash", "read", "grep", "glob", "edit", "task", "skill", "webfetch", "websearch", "lsp", "question", "external_directory"), "deny"), "steps": 1}},
        }),
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "ALL_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "all_proxy": proxy,
        "NO_PROXY": "",
        "no_proxy": "",
    }
    thread = threading.Thread(target=server.serve_forever,
                              name="docich-research-loopback", daemon=True)
    thread.start()
    try:
        return subprocess.call(sys.argv[1:], env=env)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


if __name__ == "__main__":
    raise SystemExit(main())
