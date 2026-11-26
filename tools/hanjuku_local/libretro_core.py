"""Thin ctypes binding for a libretro core (snes9x and compatible cores).

The driver only needs a small part of the libretro API:

* ``retro_init`` / ``retro_deinit`` / ``retro_load_game`` / ``retro_run``
* ``retro_serialize`` / ``retro_unserialize`` (battery-independent save state)
* ``retro_get_memory_data`` (battery SRAM)
* the video, audio and input callbacks

Keeping this layer small means the driver logic can be exercised with a fake
core object that implements the same Python surface (see
``tests/test_hanjuku_local_core_driver.py``); a real snes9x shared library is
not required for the tests.

Only the Python standard library is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import (
    CFUNCTYPE, POINTER, Structure, byref, c_bool, c_char_p, c_int,
    c_int16, c_size_t, c_uint, c_void_p, cast,
)
import os
from pathlib import Path
import struct
import sys

# ---------------------------------------------------------------------------
# libretro constants
# ---------------------------------------------------------------------------

RETRO_API_VERSION = 1

RETRO_DEVICE_ID_JOYPAD_B = 0
RETRO_DEVICE_ID_JOYPAD_Y = 1
RETRO_DEVICE_ID_JOYPAD_SELECT = 2
RETRO_DEVICE_ID_JOYPAD_START = 3
RETRO_DEVICE_ID_JOYPAD_UP = 4
RETRO_DEVICE_ID_JOYPAD_DOWN = 5
RETRO_DEVICE_ID_JOYPAD_LEFT = 6
RETRO_DEVICE_ID_JOYPAD_RIGHT = 7
RETRO_DEVICE_ID_JOYPAD_A = 8
RETRO_DEVICE_ID_JOYPAD_X = 9
RETRO_DEVICE_ID_JOYPAD_L = 10
RETRO_DEVICE_ID_JOYPAD_R = 11

RETRO_DEVICE_NONE = 0
RETRO_DEVICE_JOYPAD = 1

PIXEL_FORMAT_0RGB1555 = 0
PIXEL_FORMAT_XRGB8888 = 1
PIXEL_FORMAT_RGB565 = 2
PIXEL_FORMATS = frozenset({
    PIXEL_FORMAT_0RGB1555, PIXEL_FORMAT_XRGB8888, PIXEL_FORMAT_RGB565,
})

RETRO_MEMORY_SAVE_RAM = 0
RETRO_MEMORY_RTC = 1
RETRO_MEMORY_SYSTEM_RAM = 2
RETRO_MEMORY_VIDEO_RAM = 3

# Environment commands used by snes9x (the rest are answered with false).
RETRO_ENVIRONMENT_GET_CAN_DUPE = 3
RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY = 9
RETRO_ENVIRONMENT_SET_PIXEL_FORMAT = 10
RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS = 11
RETRO_ENVIRONMENT_GET_VARIABLE = 15
RETRO_ENVIRONMENT_SET_VARIABLES = 16
RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE = 17
RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME = 18
RETRO_ENVIRONMENT_GET_LOG_INTERFACE = 27
RETRO_ENVIRONMENT_GET_CORE_ASSETS_DIRECTORY = 30
RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY = 31
RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO = 32
RETRO_ENVIRONMENT_SET_CONTROLLER_INFO = 35
RETRO_ENVIRONMENT_SET_GEOMETRY = 37
RETRO_ENVIRONMENT_GET_LANGUAGE = 39
RETRO_ENVIRONMENT_GET_PREFERRED_HW_RENDER = 56

RETRO_LANGUAGE_JAPANESE = 1

#: ``a``/``b`` are the SNES face buttons, matching the production RetroArch
#: adapter's semantic names (``src/docich/adapters/retroarch.py``).
BUTTON_IDS = {
    'a': RETRO_DEVICE_ID_JOYPAD_A,
    'b': RETRO_DEVICE_ID_JOYPAD_B,
    'x': RETRO_DEVICE_ID_JOYPAD_X,
    'y': RETRO_DEVICE_ID_JOYPAD_Y,
    'l': RETRO_DEVICE_ID_JOYPAD_L,
    'r': RETRO_DEVICE_ID_JOYPAD_R,
    'start': RETRO_DEVICE_ID_JOYPAD_START,
    'select': RETRO_DEVICE_ID_JOYPAD_SELECT,
    'up': RETRO_DEVICE_ID_JOYPAD_UP,
    'down': RETRO_DEVICE_ID_JOYPAD_DOWN,
    'left': RETRO_DEVICE_ID_JOYPAD_LEFT,
    'right': RETRO_DEVICE_ID_JOYPAD_RIGHT,
}


def buttons_mask(buttons):
    """Return the libretro input bitmask for semantic button names."""
    mask = 0
    for button in buttons or ():
        bit = BUTTON_IDS.get(button)
        if bit is not None:
            mask |= 1 << bit
    return mask


def _scale5(value):
    return (value * 255 + 15) // 31


def _scale6(value):
    return (value * 255 + 31) // 63


def _rgb_table(pixel_format):
    """16-bit value -> packed RGB888, cached (the SNES cores are 16-bit)."""
    if pixel_format == PIXEL_FORMAT_RGB565:
        return [bytes((_scale5((value >> 11) & 0x1F),
                       _scale6((value >> 5) & 0x3F),
                       _scale5(value & 0x1F)))
                for value in range(1 << 16)]
    return [bytes((_scale5((value >> 10) & 0x1F),
                   _scale5((value >> 5) & 0x1F),
                   _scale5(value & 0x1F)))
            for value in range(1 << 15)]


_RGB_TABLES = {}


def _table(pixel_format):
    table = _RGB_TABLES.get(pixel_format)
    if table is None:
        table = _rgb_table(pixel_format)
        _RGB_TABLES[pixel_format] = table
    return table


def convert_pixels(data, width, height, pitch, pixel_format):
    """Convert one libretro framebuffer to packed RGB888 bytes.

    ``data`` may be ``bytes``, ``bytearray``, ``memoryview`` or a ctypes
    pointer.  ``pitch`` is the row stride in bytes and may be larger than the
    visible width (or negative, in which case rows are read bottom-up).  A
    ``width``/``height`` of zero is a "dupe" frame: the caller keeps the
    previous image, so this function returns ``None`` for it.

    The conversion runs at C speed for the common SNES formats: 16-bit values
    are looked up in a cached table and ``XRGB8888`` rows are reordered with
    slice assignment.  A per-pixel Python loop would cap ``--fast`` at about
    real-time speed.
    """
    width = int(width or 0)
    height = int(height or 0)
    if width <= 0 or height <= 0:
        return None
    pixel_format = int(pixel_format)
    if pixel_format not in PIXEL_FORMATS:
        raise ValueError('unknown pixel format')
    if isinstance(data, ctypes.c_void_p):
        raw = ctypes.string_at(data, abs(int(pitch)) * height)
    elif isinstance(data, bytes):
        raw = data
    elif isinstance(data, (bytearray, memoryview)):
        raw = bytes(data)
    else:
        raw = ctypes.string_at(data, abs(int(pitch)) * height)
    stride = int(pitch)
    if stride == 0:
        raise ValueError('zero pitch')
    bottom_up = stride < 0
    stride = abs(stride)
    bpp = 2 if pixel_format in (PIXEL_FORMAT_0RGB1555, PIXEL_FORMAT_RGB565) else 4
    if stride < width * bpp or len(raw) < stride * height:
        raise ValueError('framebuffer is smaller than width/pitch/height')
    little = sys.byteorder == 'little'
    chunks = []
    for y in range(height):
        source_y = (height - 1 - y) if bottom_up else y
        base = source_y * stride
        if pixel_format == PIXEL_FORMAT_XRGB8888:
            row = raw[base:base + width * 4]
            out = bytearray(width * 3)
            if little:
                out[0::3] = row[2::4]
                out[1::3] = row[1::4]
                out[2::3] = row[0::4]
            else:
                out[0::3] = row[1::4]
                out[1::3] = row[2::4]
                out[2::3] = row[3::4]
            chunks.append(bytes(out))
            continue
        values = struct.unpack_from(
            ('<' if little else '>') + f'{width}H', raw, base)
        table = _table(pixel_format)
        if pixel_format == PIXEL_FORMAT_0RGB1555:
            chunks.append(b''.join(table[value & 0x7FFF] for value in values))
        else:
            chunks.append(b''.join(table[value] for value in values))
    return b''.join(chunks)


class RetroGameInfo(Structure):
    _fields_ = [
        ('path', c_char_p),
        ('data', c_void_p),
        ('size', c_size_t),
        ('meta', c_char_p),
    ]


class RetroSystemInfo(Structure):
    _fields_ = [
        ('library_name', c_char_p),
        ('library_version', c_char_p),
        ('valid_extensions', c_char_p),
        ('need_fullpath', c_bool),
        ('block_extract', c_bool),
    ]


class RetroGameGeometry(Structure):
    _fields_ = [
        ('base_width', c_uint),
        ('base_height', c_uint),
        ('max_width', c_uint),
        ('max_height', c_uint),
        ('aspect_ratio', ctypes.c_float),
    ]


class RetroSystemTiming(Structure):
    _fields_ = [
        ('fps', ctypes.c_double),
        ('sample_rate', ctypes.c_double),
    ]


class RetroSystemAVInfo(Structure):
    _fields_ = [
        ('geometry', RetroGameGeometry),
        ('timing', RetroSystemTiming),
    ]


class RetroVariable(Structure):
    _fields_ = [('key', c_char_p), ('value', c_char_p)]


@dataclass
class VideoFrame:
    width: int
    height: int
    rgb: bytes


EnvironmentCallback = CFUNCTYPE(c_bool, c_uint, c_void_p)
VideoCallback = CFUNCTYPE(None, c_void_p, c_uint, c_uint, c_size_t)
AudioSampleCallback = CFUNCTYPE(None, c_int16, c_int16)
AudioBatchCallback = CFUNCTYPE(c_size_t, c_void_p, c_size_t)
InputPollCallback = CFUNCTYPE(None)
InputStateCallback = CFUNCTYPE(c_int16, c_uint, c_uint, c_uint, c_uint)


class CoreError(Exception):
    """A fixed-code libretro failure suitable for an operator log."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class LibretroCore:
    """Load one shared core and expose the operations the driver needs.

    ``pixel_format_override`` forces interpretation of 16-bit frames as RGB565
    (``HANJUKU_FORCE_565``); this is a diagnostic switch, not the default.
    """

    def __init__(self, library_path, *, system_dir=None, save_dir=None,
                 variables=None):
        self.library_path = str(library_path)
        try:
            self._lib = ctypes.CDLL(self.library_path)
        except OSError as exc:
            raise CoreError('core_load_failed') from exc
        self.system_dir = str(system_dir or Path(self.library_path).parent)
        self.save_dir = str(save_dir or self.system_dir)
        self.variables = dict(variables or {})
        self.pixel_format = PIXEL_FORMAT_0RGB1555
        self.pixel_format_override = None
        self._raw_frame = None
        self.fps = 60.0988
        self.sample_rate = 32040.0
        self._mask = 0
        self._loaded = False
        self._initialized = False
        self._system_info = RetroSystemInfo()
        self._av_info = RetroSystemAVInfo()
        self._keep_alive = []
        self._bind()

    # -- binding -----------------------------------------------------------

    def _bind(self):
        lib = self._lib
        for name in ('retro_init', 'retro_deinit', 'retro_load_game',
                     'retro_unload_game', 'retro_run', 'retro_get_system_info',
                     'retro_serialize_size',
                     'retro_serialize', 'retro_unserialize',
                     'retro_get_memory_data', 'retro_get_memory_size',
                     'retro_get_system_av_info', 'retro_set_environment',
                     'retro_set_video_refresh', 'retro_set_audio_sample',
                     'retro_set_audio_sample_batch', 'retro_set_input_poll',
                     'retro_set_input_state'):
            try:
                getattr(lib, name)
            except AttributeError as exc:
                raise CoreError(f'core_symbol_missing:{name}') from exc
        lib.retro_set_environment.argtypes = [EnvironmentCallback]
        lib.retro_set_video_refresh.argtypes = [VideoCallback]
        lib.retro_set_audio_sample.argtypes = [AudioSampleCallback]
        lib.retro_set_audio_sample_batch.argtypes = [AudioBatchCallback]
        lib.retro_set_input_poll.argtypes = [InputPollCallback]
        lib.retro_set_input_state.argtypes = [InputStateCallback]
        lib.retro_load_game.argtypes = [POINTER(RetroGameInfo)]
        lib.retro_load_game.restype = c_bool
        lib.retro_get_system_info.argtypes = [POINTER(RetroSystemInfo)]
        lib.retro_get_system_info.restype = None
        lib.retro_serialize_size.restype = c_size_t
        lib.retro_serialize.argtypes = [c_void_p, c_size_t]
        lib.retro_serialize.restype = c_bool
        lib.retro_unserialize.argtypes = [c_void_p, c_size_t]
        lib.retro_unserialize.restype = c_bool
        lib.retro_get_memory_data.argtypes = [c_uint]
        lib.retro_get_memory_data.restype = c_void_p
        lib.retro_get_memory_size.argtypes = [c_uint]
        lib.retro_get_memory_size.restype = c_size_t
        lib.retro_get_system_av_info.argtypes = [POINTER(RetroSystemAVInfo)]

    # -- callbacks ---------------------------------------------------------

    def _environment(self):
        def callback(cmd, data):
            command = int(cmd)
            if command == RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
                if not data:
                    return False
                value = int(cast(data, POINTER(c_uint)).contents.value)
                if value not in PIXEL_FORMATS:
                    return False
                self.pixel_format = value
                return True
            if command in (RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY,
                           RETRO_ENVIRONMENT_GET_CORE_ASSETS_DIRECTORY,
                           RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY):
                if not data:
                    return False
                directory = (self.system_dir
                             if command == RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY
                             else self.save_dir)
                pointer = c_char_p(os.fsencode(directory))
                self._keep_alive.append(pointer)
                cast(data, POINTER(c_char_p))[0] = pointer
                return True
            if command == RETRO_ENVIRONMENT_GET_CAN_DUPE:
                if data:
                    cast(data, POINTER(c_bool))[0] = True
                return True
            if command == RETRO_ENVIRONMENT_GET_VARIABLE:
                if not data:
                    return False
                variable = cast(data, POINTER(RetroVariable)).contents
                key = variable.key.decode('utf-8', 'ignore') if variable.key else ''
                value = self.variables.get(key)
                if value is None:
                    return False
                pointer = c_char_p(str(value).encode('utf-8'))
                self._keep_alive.append(pointer)
                variable.value = pointer
                return True
            if command in (RETRO_ENVIRONMENT_SET_VARIABLES,
                           RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS,
                           RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME,
                           RETRO_ENVIRONMENT_SET_CONTROLLER_INFO,
                           RETRO_ENVIRONMENT_SET_GEOMETRY,
                           RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO):
                return True
            if command == RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE:
                if data:
                    cast(data, POINTER(c_bool))[0] = False
                return True
            if command == RETRO_ENVIRONMENT_GET_LOG_INTERFACE:
                return False
            if command == RETRO_ENVIRONMENT_GET_LANGUAGE:
                if data:
                    cast(data, POINTER(c_uint))[0] = RETRO_LANGUAGE_JAPANESE
                return True
            if command == RETRO_ENVIRONMENT_GET_PREFERRED_HW_RENDER:
                if data:
                    cast(data, POINTER(c_uint))[0] = 0
                return True
            return False
        return EnvironmentCallback(callback)

    @property
    def frame(self):
        """The latest frame as RGB888, converted on demand.

        Copying the raw framebuffer in the video callback is cheap; the
        per-pixel conversion is not.  In ``--fast`` the observer only needs
        one converted frame per observation (about 90 emulated frames), so a
        lazy conversion keeps the emulator near its own speed.
        """
        raw = self._raw_frame
        if raw is None:
            return None
        data, width, height, pitch = raw
        rgb = convert_pixels(
            data, width, height, pitch,
            self.pixel_format_override
            if self.pixel_format_override is not None else self.pixel_format)
        if rgb is None:
            return None
        return VideoFrame(int(width), int(height), rgb)

    def _video(self):
        def callback(data, width, height, pitch):
            width = int(width or 0)
            height = int(height or 0)
            if width <= 0 or height <= 0:
                return  # dupe frame: keep the previous one
            stride = int(pitch or 0)
            if stride == 0:
                return
            if not data:
                return
            try:
                raw = ctypes.string_at(data, abs(stride) * height)
            except (ValueError, OSError):
                return
            self._raw_frame = (raw, width, height, stride)
        return VideoCallback(callback)

    def _audio_sample(self):
        return AudioSampleCallback(lambda _left, _right: None)

    def _audio_batch(self):
        return AudioBatchCallback(lambda _data, frames: int(frames or 0))

    def _input_poll(self):
        return InputPollCallback(lambda: None)

    def _input_state(self):
        def callback(port, device, _index, button):
            if int(port) != 0 or int(device) != RETRO_DEVICE_JOYPAD:
                return 0
            bit = int(button)
            return c_int16(-1 if self._mask & (1 << bit) else 0).value
        return InputStateCallback(callback)

    # -- lifecycle ---------------------------------------------------------

    def setup(self):
        """Install callbacks and call ``retro_init`` once."""
        if self._initialized:
            return
        self._callbacks = [
            self._environment(), self._video(), self._audio_sample(),
            self._audio_batch(), self._input_poll(), self._input_state(),
        ]
        lib = self._lib
        lib.retro_set_environment(self._callbacks[0])
        lib.retro_set_video_refresh(self._callbacks[1])
        lib.retro_set_audio_sample(self._callbacks[2])
        lib.retro_set_audio_sample_batch(self._callbacks[3])
        lib.retro_set_input_poll(self._callbacks[4])
        lib.retro_set_input_state(self._callbacks[5])
        lib.retro_init()
        self._initialized = True
        set_port = getattr(lib, 'retro_set_controller_port_device', None)
        if set_port is not None:
            set_port(0, RETRO_DEVICE_JOYPAD)

    def load_game(self, rom_path):
        self.setup()
        rom_path = Path(rom_path)
        path = os.fsencode(str(rom_path))
        # A core that sets need_fullpath uses the path only; every other core
        # may dereference data, so it must receive the ROM bytes and keep them
        # alive for as long as the game is loaded.
        system_info = RetroSystemInfo()
        self._lib.retro_get_system_info(byref(system_info))
        self._system_info = system_info
        if system_info.need_fullpath:
            info = RetroGameInfo(path=path, data=None, size=0, meta=None)
        else:
            try:
                rom = rom_path.read_bytes()
            except OSError as exc:
                raise CoreError('rom_read_failed') from exc
            buffer = ctypes.create_string_buffer(bytes(rom), max(1, len(rom)))
            self._keep_alive.append(buffer)
            self._keep_alive.append(path)
            info = RetroGameInfo(path=path, data=cast(buffer, c_void_p),
                                 size=len(rom), meta=None)
        if not self._lib.retro_load_game(byref(info)):
            raise CoreError('game_load_failed')
        self._loaded = True
        try:
            self._lib.retro_get_system_av_info(byref(self._av_info))
            if self._av_info.timing.fps > 1:
                self.fps = float(self._av_info.timing.fps)
            if self._av_info.timing.sample_rate > 0:
                self.sample_rate = float(self._av_info.timing.sample_rate)
        except Exception:
            pass

    def run(self):
        if not self._loaded:
            raise CoreError('game_not_loaded')
        self._lib.retro_run()

    def set_buttons(self, buttons):
        self._mask = buttons_mask(buttons)

    def clear_buttons(self):
        self._mask = 0

    @property
    def current_mask(self):
        return self._mask

    def serialize_size(self):
        try:
            return int(self._lib.retro_serialize_size())
        except Exception:
            return 0

    def serialize(self):
        size = self.serialize_size()
        if size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not self._lib.retro_serialize(buffer, size):
            return None
        return bytes(buffer.raw[:size])

    def unserialize(self, data):
        if not isinstance(data, (bytes, bytearray)) or not data:
            return False
        buffer = ctypes.create_string_buffer(bytes(data), len(data))
        return bool(self._lib.retro_unserialize(buffer, len(data)))

    def save_ram(self):
        size = int(self._lib.retro_get_memory_size(RETRO_MEMORY_SAVE_RAM))
        if size <= 0:
            return b''
        pointer = self._lib.retro_get_memory_data(RETRO_MEMORY_SAVE_RAM)
        if not pointer:
            return b''
        return ctypes.string_at(pointer, size)

    def load_ram(self, data):
        if not isinstance(data, (bytes, bytearray)) or not data:
            return False
        size = int(self._lib.retro_get_memory_size(RETRO_MEMORY_SAVE_RAM))
        if size <= 0:
            return False
        pointer = self._lib.retro_get_memory_data(RETRO_MEMORY_SAVE_RAM)
        if not pointer:
            return False
        count = min(size, len(data))
        ctypes.memmove(pointer, bytes(data), count)
        return True

    def reset(self):
        reset = getattr(self._lib, 'retro_reset', None)
        if reset is not None:
            reset()

    def close(self):
        if getattr(self, '_lib', None) is None:
            return
        try:
            if self._loaded:
                self._lib.retro_unload_game()
        except Exception:
            pass
        try:
            if self._initialized:
                self._lib.retro_deinit()
        except Exception:
            pass
        self._loaded = False
        self._initialized = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False
