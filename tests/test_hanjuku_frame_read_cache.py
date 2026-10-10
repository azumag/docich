"""Cache isolation contracts plus a small uncached/cached real-policy comparison."""
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from hanjuku_frame_read_cache import reuse_frame_reads


@dataclass(frozen=True)
class SampleFrame:
    width: int
    height: int
    rgb: bytes


def readers():
    calls = {'classify': 0, 'parse': 0}

    def classify(frame):
        calls['classify'] += 1
        return 'field'

    def parse(frame, *, phase=None):
        calls['parse'] += 1
        return {'kind': phase, 'lines': [{'tiles': [1, 2]}]}

    return SimpleNamespace(classify=classify), SimpleNamespace(parse=parse), calls


def test_reads_once_per_equal_immutable_frame_and_preserves_phase_key():
    bot, screen, calls = readers()
    one = SampleFrame(2, 1, b'\x00' * 6)
    same = SampleFrame(2, 1, b'\x00' * 6)
    with reuse_frame_reads(bot, screen):
        for frame in (one, same, one):
            assert bot.classify(frame) == 'field'
            assert screen.parse(frame, phase='field')['kind'] == 'field'
        assert calls == {'classify': 1, 'parse': 1}
        assert screen.parse(one, phase='transition')['kind'] == 'transition'
        assert screen.parse(one)['kind'] is None
        assert calls['parse'] == 3


@pytest.mark.parametrize('other', [SampleFrame(1, 2, b'\x00' * 6),
                                  SampleFrame(2, 1, b'\x00' * 5 + b'\x01')])
def test_changed_dimensions_or_pixels_must_be_read_again(other):
    bot, screen, calls = readers()
    with reuse_frame_reads(bot, screen):
        for frame in (SampleFrame(2, 1, b'\x00' * 6), other):
            bot.classify(frame)
            screen.parse(frame, phase='field')
        assert calls == {'classify': 2, 'parse': 2}


def test_first_and_cached_results_never_share_mutable_screen_fields():
    bot, screen, calls = readers()
    frame = SampleFrame(1, 1, b'\x00' * 3)
    with reuse_frame_reads(bot, screen):
        first = screen.parse(frame, phase='field')
        first['kind'] = 'summer_bonus'
        first['lines'][0]['tiles'].append(99)
        second = screen.parse(frame, phase='field')
        assert second == {'kind': 'field', 'lines': [{'tiles': [1, 2]}]}
        second['lines'].clear()
        assert screen.parse(frame, phase='field')['lines'] == [{'tiles': [1, 2]}]
        assert calls['parse'] == 1


def test_context_restores_readers_and_discards_entries_even_on_error():
    bot, screen, calls = readers()
    original = bot.classify, screen.parse
    frame = SampleFrame(1, 1, b'\x00' * 3)
    with pytest.raises(RuntimeError, match='test failed'):
        with reuse_frame_reads(bot, screen) as caches:
            bot.classify(frame)
            screen.parse(frame)
            raise RuntimeError('test failed')
    assert (bot.classify, screen.parse) == original
    assert all(cache.cache_info().currsize == 0 for cache in caches)
    with reuse_frame_reads(bot, screen):
        bot.classify(frame)
        screen.parse(frame)
    assert calls == {'classify': 2, 'parse': 2}
    assert (bot.classify, screen.parse) == original


def test_cache_is_bounded_and_evicted_frames_are_read_again():
    bot, screen, calls = readers()
    with reuse_frame_reads(bot, screen) as caches:
        for value in range(9):
            frame = SampleFrame(1, 1, bytes([value]) * 3)
            bot.classify(frame)
            screen.parse(frame)
        assert all(cache.cache_info().currsize == 8 for cache in caches)
        bot.classify(SampleFrame(1, 1, b'\x00' * 3))
        screen.parse(SampleFrame(1, 1, b'\x00' * 3))
        assert calls == {'classify': 10, 'parse': 10}


@pytest.mark.parametrize('name', ['classify', 'parse'])
def test_reader_exceptions_propagate_and_are_not_cached(name):
    bot, screen, _ = readers()
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ValueError('reader failed')

    module = bot if name == 'classify' else screen
    setattr(module, name, fail)
    with reuse_frame_reads(bot, screen):
        for _ in range(2):
            with pytest.raises(ValueError, match='reader failed'):
                getattr(module, name)(SampleFrame(1, 1, b'\x00' * 3))
    assert len(calls) == 2
    assert getattr(module, name) is fail


@pytest.mark.parametrize('kind', ['field', 'blink', 'redraw', 'menu', 'name', 'text'])
def test_real_decisions_match_without_cache_near_the_no_input_boundary(kind):
    # Imported here so the synthetic helper contracts can also run standalone.
    # This module has no no-input fixture: the first trace uses real readers.
    from docich import hanjuku_bot as bot, hanjuku_screen as screen
    from test_hanjuku_no_input_gate import (
        FIELD, DARK, field_text, flat, red_border_menu, unreadable_name,
    )

    frames = {
        'field': lambda: [FIELD] * 4,
        'blink': lambda: [FIELD, DARK, FIELD, DARK],
        'redraw': lambda: [FIELD, flat((20, 140, 60)), DARK, FIELD],
        'menu': lambda: [red_border_menu()] * 4,
        'name': lambda: [unreadable_name()] * 4,
        'text': lambda: [field_text()] * 4,
    }[kind]()
    _, initial = bot.decide(frames[0], {})
    initial['no_input_streak'] = bot.NO_INPUT_HOLD_MAX - 1

    def trace():
        state = deepcopy(initial)
        result = []
        for frame in frames:
            actions, state = bot.decide(frame, state)
            result.append(deepcopy((actions, state)))
        return result

    original = bot.classify, screen.parse
    expected = trace()
    with reuse_frame_reads(bot, screen):
        assert trace() == expected
    assert (bot.classify, screen.parse) == original
