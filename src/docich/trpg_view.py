"""Read-only, loopback-only TRPG/murder-mystery presentation (Issue #684).

Synthetic view for the participatory TRPG corner. Minimal scaffold (PR-3):
display-only, default disabled, manual_only. Full overlay/TTS lands in PR-4.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import uuid

DEFAULT_PORT = 8809
VIEW_NAME = "trpg-view"


class TrpgViewError(ValueError):
    """The view request violates a fixed bound."""


def handler_for(runtime_id: str = "preview", *, generation=None, lease_id=None):
    if not isinstance(runtime_id, str) or len(runtime_id) < 1 or len(runtime_id) > 128:
        raise TrpgViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise TrpgViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise TrpgViewError("invalid lease id") from None

    HTML = (
        "<!doctype html><html lang=\"ja\" translate=\"no\"><meta charset=\"utf-8\">"
        "<title>TRPG</title><body style=\"margin:0;background:#101317;color:#f4f5f7;"
        "font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;"
        "height:100vh\"><h1>TRPG / マーダーミステリー</h1></body></html>"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send_response(403)
                self.end_headers()
                return
            if self.path in {"/", "/broadcast"}:
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def serve(*, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise TrpgViewError("invalid port")
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TRPG synthetic view")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id")
    args = parser.parse_args(argv)
    try:
        serve(port=args.port, runtime_id=args.runtime_id,
              generation=args.generation, lease_id=args.lease_id)
    except (OSError, TrpgViewError):
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
