"""RetroArch UDP network command client (SAVE_STATE / LOAD_STATE / ...)."""
from __future__ import annotations

import socket


def send_ra_cmd(
    cmd: str,
    host: str = "127.0.0.1",
    port: int = 55355,
    wait_reply_s: float = 0.5,
) -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(wait_reply_s)
        sock.sendto(cmd.encode("utf-8"), (host, port))
        try:
            data, _addr = sock.recvfrom(4096)
        except (TimeoutError, socket.timeout):
            return None
        return data.decode("utf-8", errors="replace")
