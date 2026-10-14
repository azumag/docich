"""Durable, generation-bound cleared chapters; no network or game input.

A visible chapter N proves N-1 clears, not N. Route steps, years, months and
an unconfirmed boss HP reading are never progress. The final chart boss's
classified win is the explicit exception because chapter 13 does not exist.
"""
from __future__ import annotations

import re
from pathlib import Path

from .adapters.base import AdapterError
from .game_switch import atomic_write_json
from .naming import runtime_id_generation
from .retroarch_boundary import read_record

FILE = 'hanjuku_progress.json'
KEYS = ('game', 'runtime_id', 'generation', 'lease_id')
MAX_CHAPTER = 12


def checked_identity(value):
    if (not isinstance(value, dict) or value.get('game') != 'hanjuku-hero'
            or type(value.get('generation')) is not int
            or runtime_id_generation(value.get('runtime_id')) != value['generation']
            or not isinstance(value.get('lease_id'), str)
            or not 1 <= len(value['lease_id']) <= 128):
        raise ValueError('invalid Hanjuku progress identity')
    return {k: value[k] for k in KEYS}


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_CHAPTER:
        raise ValueError('invalid cleared chapters')
    return value


def policy_cleared(policy):
    """Unknown is None, never a fabricated zero-clears losing outcome."""
    if not isinstance(policy, dict):
        return None
    chapter = policy.get('chapter')
    if type(chapter) is not int or not 1 <= chapter <= MAX_CHAPTER:
        return None
    if chapter == 1:
        return 0
    evidence = policy.get('chapter_evidence')
    # Legacy home-alias migration incorrectly labelled chapter 1 as chapter 2.
    # ほんじょう is the chart label older builds persisted for the home castle;
    # kept so a memory written before the rename still reports no clear.
    if (not isinstance(evidence, str) or not evidence
            or evidence in {'reverted_home_name', 'アルマムーン', 'あるまむーん', 'ほんじょう'}):
        return None
    return chapter - 1


def read(runtime: Path, identity: dict):
    identity = checked_identity(identity)
    row = read_record(runtime / FILE)
    if row:
        if (type(row.get('schema')) is not int or row['schema'] != 1
                or any(row.get(k) != v for k, v in identity.items())
                or type(row.get('ambiguous')) is not bool
                or not re.fullmatch('[0-9a-f]{64}', row.get('frame_sha256', ''))):
            raise ValueError('invalid Hanjuku progress record')
        count(row.get('cleared'))
    return row


def record(runtime: Path, identity: dict, policy: dict, records: list, digest: str):
    """Called by the bot's single writer; only changes need durable writes."""
    identity = checked_identity(identity)
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('invalid progress frame')
    old = read(runtime, identity)
    cleared = policy_cleared(policy)
    ambiguous = old.get('ambiguous', False) or any(
        r.get('decision') == 'new_game_detected' for r in records)
    # Use the existing classified, named final-boss result, not a guessed
    # terminal screen or arbitrary caller-supplied score/chapter counter.
    if (cleared == 11 and any(
            r.get('decision') == 'battle_result' and r.get('outcome') == 'win'
            and r.get('enemy') == 'クーモン'
            and r.get('resulting_event') == 'chapter_12_boss_defeated'
            for r in records)):
        cleared = 12
    if cleared is None and not old:
        return
    cleared = max(old.get('cleared', 0), cleared or 0)
    if old and (cleared, ambiguous) == (old['cleared'], old['ambiguous']):
        return
    atomic_write_json(runtime / FILE, {
        'schema': 1, **identity, 'cleared': cleared,
        'ambiguous': ambiguous, 'frame_sha256': digest,
    })


def completed_count(runtime: Path, identity: dict):
    """Use saved progress plus an identity-bound legacy bot snapshot on rollout.

    Reading the snapshot does not rewrite it or race the bot's progress writer.
    Corruption/identity mismatch remains an error rather than a zero score.
    """
    saved = read(runtime, identity)
    if saved.get('ambiguous'):
        return None
    best = saved.get('cleared')
    bot = read_record(runtime / 'hanjuku_bot.json', limit=256 * 1024)
    if bot:
        trace = bot.get('decision_trace')
        if not isinstance(trace, dict) or any(trace.get(k) != v for k, v in identity.items()):
            raise AdapterError('Hanjuku prediction bot identity mismatch')
        current = policy_cleared(bot.get('policy'))
        if current is not None:
            best = max(best or 0, current)
    return best
