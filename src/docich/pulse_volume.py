"""Set the PulseAudio volume of one game's own playback streams (Linux).

Only sink inputs whose ``application.process.id`` belongs to the game's own
process tree are touched. The shared sink, the TTS/BGM workers' streams and
other games keep their volumes. Evidence (sink input, sink, volume, mute) is
written to a runtime JSON file for verification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time


def descendants(root: int, proc=Path('/proc')) -> set[int]:
    parents = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            parents[int(entry.name)] = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
    found, frontier = {root}, [root]
    while frontier:
        pid = frontier.pop()
        for child, parent in parents.items():
            if parent == pid and child not in found:
                found.add(child)
                frontier.append(child)
    return found


def parse_sink_inputs(text: str) -> list[dict]:
    items, cur = [], None
    for line in text.splitlines():
        m = re.match(r'^Sink Input #(\d+)', line)
        if m:
            cur = {'index': int(m.group(1))}
            items.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith('Sink:'):
            cur['sink'] = s.split(':', 1)[1].strip()
        elif s.startswith('Mute:'):
            cur['mute'] = s.split(':', 1)[1].strip() == 'yes'
        elif s.startswith('Volume:'):
            percents = [int(p) for p in re.findall(r'(\d+)%', s)]
            if percents:
                cur['volume_percent'] = percents
        else:
            m = re.match(r'application\.process\.id = "(\d+)"', s)
            if m:
                cur['pid'] = int(m.group(1))
    return items


def sink_names(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and parts[0].isdigit():
            out[parts[0]] = parts[1]
    return out


def _pactl(*args, env=None):
    return subprocess.run(['pactl', *args], env=env, capture_output=True, text=True,
                          timeout=5, check=False)


def _write(path: Path, payload: dict):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.audio-volume-')
    with os.fdopen(fd, 'w') as stream:
        json.dump(payload, stream)
    os.replace(tmp, path)


def apply_once(root_pid: int, percent: int, *, env=None, run=_pactl, tree=descendants):
    listing = run('list', 'sink-inputs', env=env)
    if listing.returncode != 0:
        return {'status': 'pactl_failed'}
    pids = tree(root_pid)
    ours = [i for i in parse_sink_inputs(listing.stdout) if i.get('pid') in pids]
    if not ours:
        return {'status': 'no_stream'}
    for item in ours:
        if item.get('volume_percent') != [percent] * len(item.get('volume_percent') or [1]):
            run('set-sink-input-volume', str(item['index']), f'{percent}%', env=env)
    after = [i for i in parse_sink_inputs(run('list', 'sink-inputs', env=env).stdout)
             if i.get('pid') in pids]
    names = sink_names(run('list', 'short', 'sinks', env=env).stdout)
    streams = [{'sink_input': i['index'], 'sink': names.get(i.get('sink', ''), i.get('sink')),
                'volume_percent': i.get('volume_percent'), 'mute': i.get('mute')} for i in after]
    ok = bool(streams) and all(s['volume_percent'] and set(s['volume_percent']) == {percent}
                               for s in streams)
    return {'status': 'applied' if ok else 'unverified', 'target_percent': percent, 'streams': streams}


def keep_applied(root_process, percent: int, evidence: Path, *, env=None, interval=5.0):
    """Daemon thread: (re)apply while the game runs; record the latest evidence."""
    def loop():
        while root_process.poll() is None:
            try:
                result = apply_once(root_process.pid, percent, env=env)
            except Exception:
                result = {'status': 'error'}
            result['at'] = time.time()
            try:
                _write(evidence, result)
            except OSError:
                pass
            time.sleep(interval if result.get('status') == 'applied' else 1.0)
    thread = threading.Thread(target=loop, daemon=True, name='game-audio-volume')
    thread.start()
    return thread
