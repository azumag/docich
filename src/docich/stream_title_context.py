"""Bounded viewer-title facts; never publish free-form runtime or handoff text.

The output vocabulary is reviewed code. Runtime input contributes validated
counts only. No strategy code is imported or executed to describe a stream.
"""
from __future__ import annotations

import json
import math
import time
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

# Describe the game's objective, never an unobserved success or improvement.
CHALLENGES = {
    'hanjuku-hero': 'AIが城を攻略！ 将軍とエッグモンスターで物語の先へ',
    'sorengame': '小さな国から大きな合体へ！ AIは建国にたどり着けるか',
    'soren91': '合体の連鎖で建国へ！ AIが落とす場所を考えて挑戦',
    'nethack': 'AIが迷宮を探索！ 道具と知恵で生き延びられるか',
    'bastet': '不利なブロックにも挑戦！ AIは何列消せるか',
    'gnurobots': 'AIロボットがマップを探索！ 障害を越えて進めるか',
    'moon-buggy': '月面の穴を飛び越えろ！ AIの走行チャレンジ',
    'ninvaders': '迫る敵を撃退！ AIが狙うシューティング攻略',
    'nsnake': '長くなる体を避けて進め！ AIのスネーク挑戦',
    'pacman4console': '迷路を駆け抜けろ！ AIが追手をかわして攻略',
    'robots': '追いかけるロボットをかわす！ AIの生き残り挑戦',
    'paper-view': 'AIの売買判断を観察！ 仮想資金で成績を検証',
    'mystery-view': '視聴者参加型の推理ゲーム！ みんなで犯人の謎を解き明かそう',
}


def challenge_phrase(game: str) -> str:
    return CHALLENGES.get(game, 'AIはどこまで進めるか？ 試行錯誤のゲーム挑戦')


def _active_hanjuku_progress(state_dir: Path) -> str | None:
    """A past clear in this live run, fenced by identity and a fresh observer."""
    from .naming import runtime_id_generation
    state = _json(state_dir / 'game_switch.json')
    active = state.get('active')
    if not isinstance(active, dict) or state.get('phase') != 'ready':
        return None
    generation = active.get('generation')
    runtime_id = active.get('runtime_id')
    if (active.get('game') != 'hanjuku-hero' or type(generation) is not int
            or runtime_id_generation(runtime_id) != generation
            or not isinstance(active.get('lease_id'), str) or not active['lease_id']):
        return None
    runtime = state_dir / 'runtimes' / runtime_id
    # Do not follow a redirected runtime directory, even to another local run.
    if (state_dir / 'runtimes').is_symlink() or runtime.is_symlink():
        return None
    run = _json(runtime / 'hanjuku_run.json')
    progress = _json(runtime / 'hanjuku_progress.json')
    keys = ('game', 'runtime_id', 'generation', 'lease_id')
    if any(type(row.get(k)) is not type(active[k]) or row.get(k) != active[k]
           for row in (run, progress) for k in keys):
        return None
    digest = run.get('frame_sha256')
    if (type(run.get('schema')) is not int or run['schema'] != 1
            or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)
            or run.get('terminal_candidate') is not False
            or 'terminal_reason' not in run or run['terminal_reason'] is not None):
        return None
    observed = run.get('observed_at')
    if (type(observed) not in (float, int) or not math.isfinite(observed)
            or not 0 <= time.time() - observed <= 60
            or run.get('playing') is not True or run.get('terminal_reason')
            or run.get('terminal_candidate')):
        return None
    cleared = progress.get('cleared')
    digest = progress.get('frame_sha256')
    if (type(progress.get('schema')) is not int or progress['schema'] != 1
            or progress.get('ambiguous') is not False
            or type(cleared) is not int or not 0 <= cleared <= 12
            or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        return None
    if _json(state_dir / 'game_switch.json') != state:
        return None
    if cleared == 12:
        return 'この冒険で全12話突破！ AIと将軍たちの冒険を見届けよう'
    if cleared == 0:
        return '第1話の突破を目指す！ AIが将軍と卵で城を攻略'
    return f'この冒険で第{cleared}話まで突破！ AIと将軍たちは第{cleared + 1}話の先へ進めるか'


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
            try:
                current = _active_hanjuku_progress(state_dir)
                if current:
                    return current
            except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                pass
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
                f'過去最高は第{best}話突破！ 将軍と卵で第{best + 1}話クリアを目指す',
                f'第{best}話突破の記録を越えたい！ AIが将軍と卵で城を攻略',
            ))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    return None
