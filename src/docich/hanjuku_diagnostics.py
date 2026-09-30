"""Bounded read-only Hanjuku tactical projection; no names, prose or input."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import stat

CASTLES = ('ほんじょう', 'キカンドン', 'ナキューメラ', 'ジョンリギ',
           'ゴーメン', 'スペンソニア', 'カストーラ', 'けっかい')
KEYS = ('game', 'runtime_id', 'generation', 'lease_id')
LIMIT = 256 * 1024


def _read(path):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    file_fd = None
    try:
        parts = Path(path).absolute().parts[1:]
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= LIMIT:
            raise ValueError('invalid bounded record')
        raw = os.read(file_fd, LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('record grew')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('not object')
        return data, info.st_mtime
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(fd)


def collect(state_dir, now):
    """Only chapter-1 fixed castle labels, counts and flags from a fresh run.

    These are the bot's recorded beliefs, not independently verified ownership
    or roster. An absent garrison record remains unknown rather than empty.
    """
    out = {'status': 'unavailable', 'basis': 'bot_record', 'age_sec': None}
    try:
        if type(now) not in (int, float) or not math.isfinite(now):
            return out
        root = Path(state_dir)
        canonical, _ = _read(root / 'game_switch.json')
        active = canonical.get('active')
        if (canonical.get('phase') != 'ready' or not isinstance(active, dict)
                or active.get('game') != 'hanjuku-hero'
                or type(active.get('generation')) is not int
                or not isinstance(active.get('lease_id'), str) or not active['lease_id']
                or not isinstance(active.get('runtime_id'), str)
                or not re.fullmatch(r'g[0-9]+-[a-f0-9]{8}', active['runtime_id'])):
            return out
        runtime = root / 'runtimes' / active['runtime_id']
        bot, modified = _read(runtime / 'hanjuku_bot.json')
        run, _ = _read(runtime / 'hanjuku_run.json')
        trace = bot.get('decision_trace')
        if (not isinstance(trace, dict)
                or any(trace.get(k) != active.get(k) or run.get(k) != active.get(k) for k in KEYS)
                or run.get('terminal_reason') or run.get('terminal_candidate')):
            return out
        age = now - modified
        if not 0 <= age <= 30:
            return {**out, 'status': 'stale'}
        mem = bot.get('policy')
        if not isinstance(mem, dict) or type(mem.get('chapter')) is not int or mem['chapter'] != 1:
            return {**out, 'status': 'unsupported_chapter'}
        captured = mem.get('captured', [])
        garrison = mem.get('garrison', {})
        sorties = mem.get('sorties', {})
        tick = mem.get('tick')
        unknown = mem.get('general_location_unknown', [])
        if (not isinstance(captured, list) or not isinstance(garrison, dict)
                or not isinstance(sorties, dict) or not isinstance(unknown, list)
                or type(tick) is not int or tick < 0):
            return out
        busy = {g for g in unknown if isinstance(g, str)}
        reserved = set()
        for entry in sorties.values():
            if (not isinstance(entry, dict)
                    or entry.get('status') not in ('en_route', 'launched_unconfirmed')
                    or type(entry.get('tick')) is not int or not 0 <= tick-entry['tick'] < 400):
                continue
            if isinstance(entry.get('general'), str):
                busy.add(entry['general'])
            if entry.get('target') in CASTLES:
                reserved.add(entry['target'])
        rows = []
        for castle in CASTLES[:-1]:
            names = garrison.get(castle)
            known = isinstance(names, list) and len(names) <= 64 and all(isinstance(g, str) for g in names)
            idle = len(set(names) - busy) if known else None
            rows.append({'castle': castle, 'captured_record': castle in captured,
                         'garrison_known': known, 'idle_generals_record': idle,
                         'target_reserved_record': castle in reserved})
        again, _ = _read(root / 'game_switch.json')
        if again.get('phase') != 'ready' or again.get('active') != active:
            return {**out, 'status': 'identity_changed'}
        out.update(status='ok', age_sec=int(age), chapter=1,
                   remaining_castles=[c for c in CASTLES[1:-1] if c not in captured],
                   castles=rows, home_lost_record=mem.get('home_lost') is True)
        return out
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return out
