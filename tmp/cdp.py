#!/usr/bin/env python3
"""Minimal Chrome DevTools Protocol client over WebSocket (no deps).

Usage:
  python3 tmp/cdp.py list
  python3 tmp/cdp.py eval <target-substr> <js-file>
  python3 tmp/cdp.py open <url>
"""
import base64, json, os, socket, struct, sys, time, urllib.parse

PROFILE = os.path.expanduser("~/Library/Application Support/Google/Chrome")
PORT_FILE = os.path.join(PROFILE, "DevToolsActivePort")


class CDP:
    def __init__(self, ws_path=None):
        port = 9222
        if os.path.exists(PORT_FILE):
            with open(PORT_FILE) as f:
                port = int(f.readline().strip())
                if ws_path is None:
                    ws_path = f.readline().strip()
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        self.status = head.split(b"\r\n")[0].decode(errors="replace")
        if "101" not in self.status:
            raise RuntimeError("WS handshake failed: " + head.decode(errors="replace")[:400])
        self.buf = rest
        self.next_id = 0

    def _send_frame(self, payload: bytes):
        mask = os.urandom(4)
        n = len(payload)
        header = bytearray([0x81])
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_frame(self):
        b1, b2 = self._read_exact(2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        masked = b2 & 0x80
        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def recv(self):
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x1:
                return json.loads(payload)
            if opcode == 0x8:
                raise RuntimeError("WS closed by peer")
            if opcode == 0x9:
                # pong
                mask = os.urandom(4)
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def call(self, method, params=None, session=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._send_frame(json.dumps(msg).encode())
        while True:
            resp = self.recv()
            if resp.get("id") == self.next_id:
                if "error" in resp:
                    raise RuntimeError(f"{method}: {resp['error']}")
                return resp.get("result", {})


def list_targets(cdp):
    res = cdp.call("Target.getTargets")
    return [t for t in res.get("targetInfos", []) if t.get("type") == "page"]


def attach(cdp, target_id):
    res = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    return res["sessionId"]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    cdp = CDP()
    print("WS:", cdp.status)
    if cmd == "list":
        for t in list_targets(cdp):
            print(t["targetId"], urllib.parse.urlparse(t["url"]).netloc, "|", t["title"][:80])
    elif cmd == "open":
        url = sys.argv[2]
        res = cdp.call("Target.createTarget", {"url": url, "background": False})
        print("created", res.get("targetId"))
    elif cmd == "eval":
        needle = sys.argv[2]
        jsfile = sys.argv[3]
        with open(jsfile) as f:
            expr = f.read()
        targets = list_targets(cdp)
        hits = [t for t in targets if needle in t["url"]]
        if not hits:
            raise SystemExit("no target matching " + needle)
        t = hits[0]
        sid = attach(cdp, t["targetId"])
        cdp.call("Runtime.enable", {}, sid)
        res = cdp.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, sid)
        print(json.dumps(res, ensure_ascii=False)[:8000])


if __name__ == "__main__":
    main()
