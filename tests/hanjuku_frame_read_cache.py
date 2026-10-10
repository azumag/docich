"""Test-only reuse of deterministic reads for repeated, immutable Hanjuku frames.

Keep this opt-in and local to no-input gate tests. Policy decisions, frame
hashing, observation counts and thresholds must never be memoized. A parsed
Screen is mutable (decide may reclassify it), so every read returns a deep
copy, including the first miss. No cache survives its context or test.
"""
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache, wraps
from unittest.mock import patch


@contextmanager
def reuse_frame_reads(bot, screen):
    # Frame is frozen and hashes width, height AND all RGB bytes. Equal pixels
    # in a new Frame can reuse a read; different geometry/phase cannot alias.
    classify = lru_cache(maxsize=8)(bot.classify)
    parsed = lru_cache(maxsize=8)(screen.parse)

    @wraps(screen.parse)
    def parse(frame, *, phase=None):
        return deepcopy(parsed(frame, phase=phase))

    try:
        with patch.object(bot, 'classify', classify), patch.object(screen, 'parse', parse):
            yield classify, parsed
    finally:
        classify.cache_clear()
        parsed.cache_clear()
