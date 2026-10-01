"""Bounded viewer-title facts; never publish free-form runtime or handoff text.

The output vocabulary is reviewed code. Runtime input contributes validated
counts only. No strategy code is imported or executed to describe a stream.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def _text(path: Path, limit: int = 65536) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError('invalid title evidence')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('oversized title evidence')
    return data.decode('utf-8')


def _json(path: Path) -> dict:
    value = json.loads(_text(path))
    if not isinstance(value, dict):
        raise ValueError('invalid title evidence')
    return value



def _variant(game: str, state_dir: Path, choices: tuple[str, ...]) -> str:
    try:
        state = _json(state_dir / 'game_switch.json')
        active = state.get('active')
        generation = active.get('generation') if isinstance(active, dict) else None
        if (isinstance(active, dict) and state.get('phase') == 'ready' and active.get('game') == game
                and type(generation) is int and 0 < generation < 10**12):
            return choices[generation % len(choices)]
    except (OSError, ValueError, UnicodeError, RecursionError):
        pass
    return choices[0]

def progress_phrase(game: str, state_dir: Path, soren_root: Path) -> str | None:
    """Return a measured past record plus a goal, not a claimed current score.

    Missing/malformed evidence returns None so the caller can retain a neutral
    fallback. Hanjuku best_cleared is the durable, identity-checked prediction
    ledger; an in-progress chapter is deliberately not counted as cleared.
    """
    try:
        if game == 'sorengame':
            raw = _text(soren_root / 'best_score.txt', 32).strip()
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 9:
                return None
            best = int(raw)
            if best <= 0:
                return None
            return _variant(game, state_dir, (
                f'最高{best:,}点を超えられるか？ 合体をつないで建国へ',
                f'合体の先に建国はあるか？ 最高記録{best:,}点に挑む',
                f'次の一手で連鎖を狙う！ 目指すは{best:,}点の先',
            ))
        if game == 'hanjuku-hero':
            data = _json(state_dir / 'hanjuku_predictions.json')
            best = data.get('best_cleared')
            if type(data.get('schema')) is not int or data['schema'] != 1:
                return None
            if type(best) is not int or not 1 <= best <= 12:
                return None
            if best == 12:
                return '全12話突破の記録から、もう一度冒険へ'
            return _variant(game, state_dir, (
                f'第{best}話突破の記録から、第{best + 1}話クリアへ再挑戦',
                f'次は第{best + 1}話突破へ！ 将軍と卵で再挑戦',
                f'第{best}話突破の先へ、今度こそ冒険を進めたい',
            ))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    return None
