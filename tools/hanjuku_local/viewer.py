#!/usr/bin/env python3
"""Read-only viewer for the local Hanjuku driver.

Endpoints (bound to ``127.0.0.1`` only):

* ``/``          256x224 screen at 3x plus a one-second status refresh
* ``/frame.png`` the latest screenshot
* ``/status``    bot version, step, phase, screen, chapter/month/captured,
                 the last decision and the tail of the driver log

The viewer never writes to the runtime, never sends input and does not follow
a symlink out of the runtime directory.  Configuration follows the operations
memo:

======================  =====================================================
``HANJUKU_VIEW_RUNTIME`` runtime directory to display
``HANJUKU_VIEW_LOG``     driver log for ``/status``
``HANJUKU_VIEW_PORT``    listen port (default 8765)
======================  =====================================================
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_RUNTIME = 'run-local/runtimes/g2001-ch9-local'
DEFAULT_LOG = 'tmp/hanjuku-local/ch9.log'
DEFAULT_PORT = 8765
LOG_TAIL_CHARS = 160
DECISION_TAIL_BYTES = 256 * 1024


def _read_json(path):
    try:
        with Path(path).open('r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (OSError, ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _tail_text(path, limit):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            data = stream.read()
    except OSError:
        return ''
    return data.decode('utf-8', 'ignore')


def _nested(state, *keys):
    current = state
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _last_decision(runtime):
    for line in reversed(
            _tail_text(Path(runtime) / 'hanjuku_decisions.jsonl',
                       DECISION_TAIL_BYTES).splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if not isinstance(document, dict):
            continue
        return {
            'decision': document.get('decision'),
            'reason': document.get('reason'),
            'chapter': document.get('chapter'),
            'at': document.get('at'),
        }
    return None


def build_status(runtime, log_path):
    """The JSON document served by ``/status``; read-only."""
    runtime = Path(runtime)
    run = _read_json(runtime / 'hanjuku_run.json')
    bot = _read_json(runtime / 'hanjuku_bot.json')
    policy = bot.get('policy') if isinstance(bot.get('policy'), dict) else {}
    log_tail = _tail_text(log_path, 8192).replace('\r', '')
    return {
        'runtime': str(runtime),
        'runtime_id': run.get('runtime_id'),
        'bot': run.get('bot_version') or bot.get('bot_version'),
        'step': run.get('observations', bot.get('observations',
                                                  bot.get('step', 0))),
        'phase': run.get('phase') or bot.get('phase'),
        'screen': bot.get('screen_kind') or bot.get('screen'),
        'chapter': policy.get('chapter', bot.get('chapter')),
        'month': policy.get('month', bot.get('month')),
        'captured': policy.get('captured', bot.get('captured')),
        'terminal': run.get('terminal_reason'),
        'terminal_candidate': run.get('terminal_candidate'),
        'last': _last_decision(runtime),
        'log': log_tail[-LOG_TAIL_CHARS:],
    }


def _index_html():
    return """<!doctype html><html lang="ja" translate="no"><head>
<meta charset="utf-8"><meta name="google" content="notranslate">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>半熟英雄 ローカル</title>
<style>
html,body{margin:0;background:#0b1020;color:#e5e7eb;font:13px/1.5 monospace}
main{display:flex;gap:16px;padding:12px;align-items:flex-start;flex-wrap:wrap}
.screen{background:#000;border:1px solid #334155;line-height:0}
.screen img{width:768px;height:auto;image-rendering:pixelated}
#status{white-space:pre-wrap;min-width:320px;max-width:42vw}
h1{font-size:14px;margin:0 0 6px;color:#93c5fd}
</style></head><body><main>
<div class="screen"><img id="frame" src="/frame.png" alt="frame"></div>
<div><h1>status</h1><div id="status">loading...</div></div>
</main>
<script>
async function tick(){
  try{
    const r = await fetch('/status?t='+Date.now(), {cache:'no-store'});
    const s = await r.json();
    document.getElementById('status').textContent = JSON.stringify(s, null, 2);
  }catch(e){ document.getElementById('status').textContent = 'viewer: driver stopped'; }
  document.getElementById('frame').src = '/frame.png?t=' + Date.now();
}
tick(); setInterval(tick, 1000);
</script></body></html>"""


def _handler_for(runtime, log_path):
    runtime = Path(runtime)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'HanjukuLocalViewer/1'

        def log_message(self, _format, *_args):  # keep the console quiet
            return

        def _send(self, status, content_type, body, *, head=False):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _route(self, head=False):
            path = urlsplit(self.path).path
            if path in ('/', '/index.html'):
                self._send(HTTPStatus.OK, 'text/html; charset=utf-8',
                           _index_html().encode('utf-8'), head=head)
                return
            if path == '/status':
                document = build_status(runtime, log_path)
                body = json.dumps(document, ensure_ascii=False,
                                  sort_keys=True).encode('utf-8')
                self._send(HTTPStatus.OK, 'application/json; charset=utf-8',
                           body, head=head)
                return
            if path == '/frame.png':
                frame = (runtime / 'screenshots' / 'latest.png').resolve()
                try:
                    inside = frame.is_relative_to(runtime.resolve())
                except (AttributeError, ValueError):
                    inside = False
                if not inside or not frame.is_file() or frame.is_symlink():
                    self._send(HTTPStatus.NOT_FOUND, 'text/plain; charset=utf-8',
                               b'no frame\n', head=head)
                    return
                try:
                    body = frame.read_bytes()
                except OSError:
                    self._send(HTTPStatus.NOT_FOUND,
                               'text/plain; charset=utf-8', b'no frame\n',
                               head=head)
                    return
                self._send(HTTPStatus.OK, 'image/png', body, head=head)
                return
            self._send(HTTPStatus.NOT_FOUND, 'text/plain; charset=utf-8',
                       b'not found\n', head=head)

        def do_GET(self):
            self._route(head=False)

        def do_HEAD(self):
            self._route(head=True)

        def _reject(self):
            self._send(HTTPStatus.METHOD_NOT_ALLOWED,
                       'text/plain; charset=utf-8', b'method not allowed\n')

        do_POST = _reject
        do_PUT = _reject
        do_DELETE = _reject
        do_PATCH = _reject

    return Handler


def serve(runtime, log_path, port=DEFAULT_PORT, *, host='127.0.0.1'):
    """Create a server bound to ``host`` (always 127.0.0.1 by default)."""
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('the viewer only binds to 127.0.0.1')
    server = ThreadingHTTPServer((host, int(port)), _handler_for(runtime, log_path))
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog='viewer')
    parser.add_argument('--runtime', default=None)
    parser.add_argument('--log', default=None)
    parser.add_argument('--port', type=int, default=None)
    args = parser.parse_args(argv)
    runtime = Path(args.runtime or os.environ.get(
        'HANJUKU_VIEW_RUNTIME', DEFAULT_RUNTIME)).expanduser()
    log_path = Path(args.log or os.environ.get(
        'HANJUKU_VIEW_LOG', DEFAULT_LOG)).expanduser()
    port = args.port if args.port is not None else int(
        os.environ.get('HANJUKU_VIEW_PORT', DEFAULT_PORT) or DEFAULT_PORT)
    server = serve(runtime, log_path, port)
    print(f'viewer: http://127.0.0.1:{server.server_address[1]}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
