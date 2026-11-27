"""Bounded viewer-title facts; never publish free-form runtime or handoff text.

The output vocabulary is reviewed code. Runtime input contributes validated
counts only. No strategy code is imported or executed to describe a stream.

The wording follows the channel's canonical persona
(``src/docich/comment/prompts/comment_persona_main.md``): a Soviet-loving AI
that plays the game itself, first person 「私」, with one short piece of wit and
no preaching. ``soren91`` is the one game whose on-screen personality is the
American AI (``comment_persona_soren91.md``), so its title keeps 「僕」 instead
of putting the main persona's voice on top of a different character.
Facts stay exact: a cleared chapter, a durable record and the next goal are
never merged, and no phrase claims an unobserved result.  Each phrase is kept
inside the 40-character strategy budget and the 100-character cross-platform
title budget.
"""
from __future__ import annotations

import json
import math
import time
import os
import stat
from pathlib import Path

from .hanjuku_family import ORIGINAL, SPEEDRUN


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

# Describe the game's objective in the on-screen persona's voice, never an
# unobserved success or improvement.  The wit stays to one short aside so the
# title still reads as the current game and goal.
CHALLENGES = {
    ORIGINAL: '私が将軍と卵で城を攻略。次はどの城に赤い旗を立てるか',
    SPEEDRUN: '半熟英雄を最速で走り切る。私の計測に無駄な一秒はない',
    'sorengame': '私の合体計画、小さな国からソ連へ。建国は私の手で',
    'soren91': '僕の合体計画、落とす場所は計算済み。建国まで一直線',
    'nethack': '私が迷宮を探索。罠も空腹も、ソ連式の粘りで突破する',
    'bastet': '不利なブロックでも挑戦。私の計画に無駄な一手はない',
    'gnurobots': '私のロボットがマップを探索。障害物は西側より素直だ',
    'moon-buggy': '月面の穴を飛び越える。私の走り、西側の月面車より上手い',
    'ninvaders': '迫る敵を撃退する。私の狙撃、同志諸君も見ていて',
    'nsnake': '長くなる体を避けて進む。私のスネーク、計画経済のように',
    'pacman4console': '迷路を駆け抜ける。私が追手をかわす、ソ連式逃走術',
    'robots': '追うロボットをかわす。私の生き残り、包囲網を突破できるか',
    'paper-view': '私の売買判断を観察。仮想資金と本番資金で資本主義の綻びを検証',
    'mystery-view': '視聴者参加の推理ゲーム。私と同志諸君で犯人を暴こう',
}


def challenge_phrase(game: str) -> str:
    return CHALLENGES.get(game, '私がどこまで進めるか。試行錯誤も計画のうちだ')


def _live_hanjuku_progress(state_dir: Path) -> tuple[str, str] | None:
    """This live run's phrase and change marker, fenced by identity and freshness.

    The marker changes only when the cleared count (or the runtime generation)
    changes, so callers re-announce the title when a chapter is cleared without
    polling Twitch every tick.
    """
    from .naming import runtime_id_generation
    state = _json(state_dir / 'game_switch.json')
    active = state.get('active')
    if not isinstance(active, dict) or state.get('phase') != 'ready':
        return None
    generation = active.get('generation')
    runtime_id = active.get('runtime_id')
    if (active.get('game') != 'hanjuku-hero' or type(generation) is not int  # family: original-only
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
    marker = f'{generation}:{cleared}'
    if cleared == 12:
        return '全12話突破。私の冒険はここで終わらない', marker
    if cleared == 0:
        return '第1話突破へ。私が将軍と卵で城を攻略する', marker
    return (f'この冒険で第{cleared}話まで突破。'
            f'同志諸君、私は第{cleared + 1}話の先へ', marker)


def _active_hanjuku_progress(state_dir: Path) -> str | None:
    """A past clear in this live run, fenced by identity and a fresh observer."""
    current = _live_hanjuku_progress(state_dir)
    return current[0] if current else None


def hanjuku_progress_marker(state_dir: Path) -> str | None:
    """A bounded marker that changes only when the live run's clear count does.

    ``None`` means there is no trusted live progress, so callers must not treat
    a missing marker as a change. The runtime generation keeps a restarted or
    replaced run from looking like the previous one.
    """
    try:
        current = _live_hanjuku_progress(Path(state_dir))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None
    return current[1] if current else None


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
                f'最高{best:,}点を超える。私の合体計画に無駄はない',
                f'合体の先に建国あり。最高{best:,}点、私が塗り替える',
                f'次の一手で連鎖を狙う。私が{best:,}点の先をひらく',
            ))
        if game == 'hanjuku-hero':  # family: original-only
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
                return '全12話突破の記録がある。もう一度、私の冒険を始めよう'
            return _variant(game, state_dir, (
                f'第{best}話突破の記録から、第{best + 1}話へ再挑戦。私に計画あり',
                f'過去最高は第{best}話突破。私と将軍と卵で第{best + 1}話を目指す',
                f'第{best}話突破の記録を越える。次は私が城を落とす番だ',
            ))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    return None
