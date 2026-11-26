"""``LibretroCore.load_game`` contract with a fake shared core.

The local driver never has a real core/ROM in tests.  These tests replace the
CDLL object with a small Python fake and check the two ``retro_load_game``
shapes: a core that sets ``need_fullpath`` receives the path only, and a core
that does not receives the ROM bytes with a live pointer and size.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hanjuku_local import libretro_core  # noqa: E402


def _contents(info):
    # ``byref`` reaches a pure-Python fake as a CArgObject; a real ctypes
    # function would unwrap it.  Support both shapes.
    return info.contents if hasattr(info, 'contents') else info._obj


class FakeLib:
    def __init__(self, *, need_fullpath):
        self.need_fullpath = need_fullpath
        self.loaded = None
        self.load_calls = 0

    def retro_get_system_info(self, info):
        _contents(info).need_fullpath = self.need_fullpath

    def retro_load_game(self, info):
        self.load_calls += 1
        game = _contents(info)
        self.loaded = {
            'path': game.path,
            'size': game.size,
            'data': (ctypes.string_at(game.data, game.size)
                     if game.data else None),
        }
        return True

    def retro_get_system_av_info(self, info):
        _contents(info).timing.fps = 60.0
        _contents(info).timing.sample_rate = 32040.0


def _core(lib):
    # Bypass CDLL/_bind: the fake supplies exactly the entry points load_game
    # uses.  setup() is already done so it is not re-invoked.
    core = object.__new__(libretro_core.LibretroCore)
    core._lib = lib
    core._initialized = True
    core._loaded = False
    core._keep_alive = []
    core._system_info = libretro_core.RetroSystemInfo()
    core._av_info = libretro_core.RetroSystemAVInfo()
    core.fps = 60.0988
    core.sample_rate = 32040.0
    return core


def test_need_fullpath_true_passes_the_path_only(tmp_path):
    rom = tmp_path / 'game.sfc'
    rom.write_bytes(b'ROMDATA')
    lib = FakeLib(need_fullpath=True)
    core = _core(lib)

    core.load_game(rom)

    assert lib.loaded == {
        'path': os.fsencode(str(rom)),
        'size': 0,
        'data': None,
    }
    assert core._keep_alive == []
    assert core._loaded is True


def test_need_fullpath_false_passes_live_rom_bytes(tmp_path):
    rom = tmp_path / 'game.sfc'
    rom.write_bytes(b'ROMDATA')
    lib = FakeLib(need_fullpath=False)
    core = _core(lib)

    core.load_game(rom)

    assert lib.loaded['path'] == os.fsencode(str(rom))
    assert lib.loaded['size'] == len(b'ROMDATA')
    assert lib.loaded['data'] == b'ROMDATA'
    # The buffer outlives retro_load_game; a core may read it during run().
    assert core._keep_alive
    assert core._loaded is True


def test_missing_rom_is_a_fixed_code_before_load(tmp_path):
    lib = FakeLib(need_fullpath=False)
    core = _core(lib)

    with pytest.raises(libretro_core.CoreError) as exc:
        core.load_game(tmp_path / 'missing.sfc')

    assert exc.value.code == 'rom_read_failed'
    assert lib.load_calls == 0
    assert core._loaded is False
