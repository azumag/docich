"""Local preview of the real broadcast overlay with a synthetic timer.

Run it next to the driver::

    python3 -m docich.hanjuku_speedrun.rta.preview --root run-local --port 8780

The server binds to ``127.0.0.1`` only and is read-only.  It serves the real
``overlays/direct_broadcast_overlay.html`` (not a copy) and answers
``/__soren_overlay/broadcast/state`` from the local ``timer.json``.

When ``node`` is available it calls ``buildDirectBroadcastOverlayState`` from
``overlays/direct_broadcast_overlay_state.mjs`` unchanged: the source files are
pointed at the local timer and a synthetic ``game_switch.json`` in a temporary
directory.  Without node, a small Python equivalent produces only the
``speedrunTimer`` part plus empty feeds; the timer is what the preview is for.

Two things are different from production on purpose:

* the timer is written by the local driver with an emulated-frame clock, so
  the preview shifts its timestamps to the host clock for display (the
  duration is untouched);
* only the timer is real.  Everything else is a quiet placeholder.
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

GAME = 'hanjuku-speedrun'
SCHEMA = 1
MAX_SPLITS = 12
STATUSES = ('pending', 'running', 'finished', 'dnf')
VERDICTS = frozenset({'gold', 'blue', 'red', 'white'})
TOTAL_VERDICTS = frozenset({'gold', 'red', 'white'})
STATE_ROUTE = '/__soren_overlay/broadcast/state'
NODE_TIMEOUT_SECONDS = 15


def _repo_root():
    try:
        return Path(__file__).resolve().parents[4]
    except IndexError:  # pragma: no cover - a very unusual install layout
        return Path.cwd()


def overlay_html_path():
    return _repo_root() / 'overlays' / 'direct_broadcast_overlay.html'


def overlay_state_module_path():
    return _repo_root() / 'overlays' / 'direct_broadcast_overlay_state.mjs'


def _read_json(path):
    try:
        with Path(path).open('r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (OSError, ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_data_dir(root):
    """Accept ``run-local``, its data directory, or the task directory."""
    path = Path(root).expanduser()
    if path.name == 'hanjuku-speedrun' and path.parent.name == 'speedrun':
        return path
    if (path / 'timer.json').is_file():
        return path
    if path.name == 'speedrun':
        return path / 'hanjuku-speedrun'
    if (path / 'speedrun' / 'hanjuku-speedrun').is_dir():
        return path / 'speedrun' / 'hanjuku-speedrun'
    return path / 'speedrun' / 'hanjuku-speedrun'


def _int(value, *, minimum=0):
    return value if type(value) is int and value >= minimum else None



def _shift_timer(raw, *, now_ms, mtime_ms):
    """Return a copy whose timestamps are positioned on the host clock.

    The local driver records emulated-frame time, whose numbers are unrelated
    to the host epoch.  The overlay's running clock uses ``Date.now()``, so
    keeping the raw values would display a nonsense total.  Only timestamps
    move; segment/cumulative/total durations stay exactly as recorded.
    """
    timer = dict(raw)
    started = _int(raw.get('started_at_ms'))
    heartbeat = _int(raw.get('heartbeat_at_ms'))
    status = raw.get('status')
    if status == 'running' and started is not None:
        reference = heartbeat if heartbeat is not None else started
        elapsed = max(0, reference - started)
        timer['started_at_ms'] = max(0, now_ms - elapsed)
        timer['heartbeat_at_ms'] = min(now_ms, mtime_ms or now_ms)
        timer['ended_at_ms'] = None
    elif status in ('finished', 'dnf'):
        ended = _int(raw.get('ended_at_ms'))
        if started is not None and ended is not None and ended >= started:
            total = ended - started
            timer['started_at_ms'] = max(0, now_ms - total)
            timer['ended_at_ms'] = now_ms
            timer['heartbeat_at_ms'] = now_ms
    elif status == 'pending':
        timer['heartbeat_at_ms'] = min(now_ms, mtime_ms or now_ms)
    return timer


def _synthetic_game_switch(timer):
    runtime_id = timer.get('runtime_id') if isinstance(timer, dict) else None
    if not isinstance(runtime_id, str) or not runtime_id:
        runtime_id = 'local-preview'
    return {
        'schema': 1,
        'phase': 'active',
        'active': {'game': GAME, 'runtime_id': runtime_id},
    }


def _node_script():
    return """
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const module = await import(pathToFileURL(payload.module).href);
const state = module.buildDirectBroadcastOverlayState({
  sources: {
    speedrunTimerFile: payload.timerFile,
    gameSwitchFile: payload.switchFile,
  },
}, payload.nowMs);
process.stdout.write(JSON.stringify(state));
"""


def _node_state(module_path, timer_file, switch_file, now_ms, node):
    payload = json.dumps({
        'module': str(module_path),
        'timerFile': str(timer_file),
        'switchFile': str(switch_file),
        'nowMs': int(now_ms),
    })
    result = subprocess.run(
        [node, '--input-type=module', '-e', _node_script()],
        input=payload, text=True, capture_output=True,
        timeout=NODE_TIMEOUT_SECONDS, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError('node_failed')
    document = json.loads(result.stdout)
    if not isinstance(document, dict):
        raise RuntimeError('node_invalid')
    return document


def _python_timer_state(timer, game_switch, now_ms):
    active = game_switch.get('active')
    active_game = active.get('game') if isinstance(active, dict) else active
    active_runtime = (active.get('runtime_id')
                      if isinstance(active, dict) else None)
    if active_game != GAME or not isinstance(active_runtime, str):
        return None
    if (timer.get('schema') != SCHEMA or timer.get('game') != GAME
            or timer.get('status') not in STATUSES):
        return None
    if timer.get('runtime_id') != active_runtime:
        return None
    status = timer['status']
    started = _int(timer.get('started_at_ms'))
    ended = _int(timer.get('ended_at_ms'))
    if status != 'pending' and started is None:
        return None
    if status in ('finished', 'dnf') and ended is None:
        return None
    heartbeat = _int(timer.get('heartbeat_at_ms'))
    stale = (status == 'running'
             and (heartbeat is None or now_ms - heartbeat > 30_000))
    splits = []
    for item in timer.get('splits') or ():
        if not isinstance(item, dict) or len(splits) >= MAX_SPLITS:
            continue
        index = _int(item.get('index'), minimum=0)
        end_index = item.get('end_index', index)
        end_index = _int(end_index, minimum=0)
        segment = _int(item.get('segment_ms'))
        cumulative = _int(item.get('cumulative_ms'))
        if (index is None or index >= MAX_SPLITS or end_index is None
                or end_index < index or end_index >= MAX_SPLITS
                or segment is None or cumulative is None):
            continue
        splits.append({
            'index': index,
            'end_index': end_index,
            'segment_ms': segment,
            'cumulative_ms': cumulative,
            'verdict': item.get('verdict') if item.get('verdict') in VERDICTS
            else 'white',
            'delta_ms': item.get('delta_ms')
            if type(item.get('delta_ms')) is int else None,
        })
    splits.sort(key=lambda item: item['index'])
    current_index = _int(timer.get('current_index'), minimum=0)
    if current_index is None:
        current_index = 0
    if status in ('pending', 'running') and current_index >= MAX_SPLITS:
        return None

    def times(value):
        if not isinstance(value, list):
            return []
        return [item if type(item) is int and item >= 0 else None
                for item in value[:MAX_SPLITS]]

    comparison = timer.get('comparison') if isinstance(
        timer.get('comparison'), dict) else {}
    return {
        'attempt_no': _int(timer.get('attempt_no'), minimum=1),
        'status': status,
        'started_at_ms': None if status == 'pending' else started,
        'ended_at_ms': ended if status in ('finished', 'dnf') else None,
        'current_index': current_index,
        'splits': splits,
        'comparison': {
            'pb_total_ms': _int(comparison.get('pb_total_ms')),
            'pb_segments_ms': times(comparison.get('pb_segments_ms')),
            'pb_cumulative_ms': times(comparison.get('pb_cumulative_ms')),
            'gold_segments_ms': times(comparison.get('gold_segments_ms')),
            'sum_of_best_ms': _int(comparison.get('sum_of_best_ms')),
        },
        'heartbeat_at_ms': heartbeat,
        'pb_eligible': (timer.get('pb_eligible')
                        if isinstance(timer.get('pb_eligible'), bool) else None),
        'total_verdict': (timer.get('total_verdict')
                          if timer.get('total_verdict') in TOTAL_VERDICTS
                          else None),
        'total_ms': _int(timer.get('total_ms')),
        'stale': stale,
        'nowMs': now_ms,
    }


def _python_state(timer, game_switch, now_ms):
    return {
        'version': 1,
        'updatedAt': now_ms // 1000,
        'nextCorner': None,
        'speedrunTimer': _python_timer_state(timer, game_switch, now_ms),
        'feeds': {},
        'notifications': {
            'generators': [],
            'work': {},
            'sourceUpdatedAt': 0,
        },
        'topOverride': None,
        'gameGapEnabled': False,
    }


def build_preview_state(data_dir, *, now_ms=None, prefer_node=True,
                        node=None):
    """Build the state the overlay would receive from the real server."""
    data_dir = resolve_data_dir(data_dir)
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    timer_file = data_dir / 'timer.json'
    raw = _read_json(timer_file)
    try:
        mtime_ms = int(timer_file.stat().st_mtime * 1000)
    except OSError:
        mtime_ms = None
    if raw.get('schema') == SCHEMA and raw.get('game') == GAME:
        display_timer = _shift_timer(raw, now_ms=now_ms, mtime_ms=mtime_ms)
    else:
        display_timer = {}
    game_switch = _synthetic_game_switch(raw)
    node = node or shutil.which('node')
    if prefer_node and node:
        try:
            with tempfile.TemporaryDirectory(prefix='hanjuku-preview-') as temp:
                temp = Path(temp)
                timer_path = temp / 'timer.json'
                switch_path = temp / 'game_switch.json'
                timer_path.write_text(json.dumps(display_timer), encoding='utf-8')
                switch_path.write_text(json.dumps(game_switch),
                                       encoding='utf-8')
                return _node_state(overlay_state_module_path(), timer_path,
                                   switch_path, now_ms, node)
        except Exception as exc:
            print(f'preview: node state failed ({type(exc).__name__}); '
                  'using the Python fallback', file=sys.stderr)
    return _python_state(display_timer, game_switch, now_ms)


def _handler_for(data_dir, html_bytes):
    data_dir = Path(data_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'HanjukuPreview/1'

        def log_message(self, _format, *_args):
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
                           html_bytes, head=head)
                return
            if path == STATE_ROUTE:
                document = build_preview_state(data_dir)
                body = json.dumps(document, ensure_ascii=False,
                                  sort_keys=True).encode('utf-8')
                self._send(HTTPStatus.OK, 'application/json; charset=utf-8',
                           body, head=head)
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


def create_server(root, port=8780, *, host='127.0.0.1'):
    """Create the preview server bound to ``127.0.0.1`` (read-only)."""
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('the preview only binds to 127.0.0.1')
    data_dir = resolve_data_dir(root)
    html_path = overlay_html_path()
    try:
        html_bytes = html_path.read_bytes()
    except OSError:
        raise FileNotFoundError(f'overlay html not found: {html_path}')
    server = ThreadingHTTPServer((host, int(port)),
                                 _handler_for(data_dir, html_bytes))
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog='preview')
    parser.add_argument('--root', default='run-local',
                        help='run root (or the speedrun data directory)')
    parser.add_argument('--port', type=int, default=8780)
    args = parser.parse_args(argv)
    try:
        server = create_server(args.root, args.port)
    except (OSError, ValueError) as exc:
        print(f'preview: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    print(f'preview: http://127.0.0.1:{server.server_address[1]}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
