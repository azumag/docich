"""Driver-logic tests for ``tools/hanjuku_local`` using a stub core.

A real snes9x core and a ROM are not available in this environment (and must
not be added to the repository), so these tests inject a Python stub core and
a stub observation module.  They exercise the parts that do not depend on the
emulator: the observation loop, pad delivery, the frame-derived clock, the
``.state.prev``/SRAM save on stop, terminal exit and ``--noload``/``--fresh``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docich.hanjuku_pixels import Frame  # noqa: E402
from tools.hanjuku_local import core_driver, libretro_core  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_speedrun_environment():
    """The driver sets the process environment; never leak it to other tests."""
    keys = ('DOCICH_SPEEDRUN_DATA_DIR', 'DOCICH_SPEEDRUN_ENV')
    saved = {key: os.environ.get(key) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _config(tmp_path, **overrides):
    config = core_driver.DriverConfig.from_environment(
        code_root=ROOT, game='hanjuku-speedrun', fast=True, noload=True)
    values = {
        'runtime': tmp_path / 'run-local' / 'runtimes' / 'g1300-abcdef12',
        'runtime_id': 'g1300-abcdef12',
        'generation': 1300,
        'lease_id': 'test-lease',
        'state_path': tmp_path / 'states' / 'hanjuku.state',
        'data_dir': tmp_path / 'run-local' / 'speedrun' / 'hanjuku-speedrun',
        'sram_out': tmp_path / 'saves' / 'hanjuku.srm',
        'save_every': 1000,
        'fps': 60.0,
    }
    values.update(overrides)
    return core_driver.DriverConfig(**{**config.__dict__, **values})


class StubCore:
    def __init__(self, *, serialized=b'NEW-STATE', ram=b'NEW-SRAM',
                 unserialize_ok=True):
        self.serialized = serialized
        self.ram = ram
        self.unserialize_ok = unserialize_ok
        self.frames = 0
        self.runs = 0
        self.button_history = []
        self.buttons = []
        self.loaded_game = None
        self.loaded_state = None
        self.closed = False
        self.pixel_format_override = None
        self.frame = libretro_core.VideoFrame(
            4, 2, bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255,
                         10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]))

    def setup(self):
        pass

    def load_game(self, path):
        self.loaded_game = str(path)

    def run(self):
        self.runs += 1
        self.frames += 1
        value = self.runs % 251
        self.frame = libretro_core.VideoFrame(
            4, 2, bytes([value, (value * 3) % 256, (value * 7) % 256] * 8))

    def set_buttons(self, buttons):
        self.buttons = list(buttons)
        self.button_history.append(tuple(buttons))

    def clear_buttons(self):
        self.buttons = []

    def serialize(self):
        return self.serialized

    def unserialize(self, data):
        self.loaded_state = bytes(data)
        return self.unserialize_ok

    def save_ram(self):
        return self.ram

    def load_ram(self, data):
        self.ram = bytes(data)
        return True

    def close(self):
        self.closed = True


class StubRun:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []
        self.actions = []

    def observe(self, runtime, identity, frame, *, now=None, wall=None,
                playing=True):
        self.calls.append({
            'runtime': Path(runtime),
            'identity': dict(identity),
            'frame': frame,
            'now': now,
            'wall': wall,
            'playing': playing,
        })
        index = min(len(self.calls) - 1, len(self.states) - 1)
        return dict(self.states[index])

    def action_sent(self, runtime, identity, action, *, now=None):
        self.actions.append((list(action.buttons), action.hold_ms))


def _run_state(**overrides):
    state = {
        'phase': 'field',
        'terminal_reason': None,
        'terminal_candidate': False,
        'observations': 1,
    }
    state.update(overrides)
    return state


def _driver(tmp_path, *, states=None, bot=None, core=None, **overrides):
    logs = []
    config = _config(tmp_path, **overrides)
    stub_core = core or StubCore()
    stub_run = StubRun(states or [_run_state()])
    observation = {'captured': None}

    def default_bot(obs):
        observation['value'] = obs
        return {'actions': [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}],
                'observation_interval_ms': 1500}

    driver = core_driver.CoreDriver(
        config, stub_core, run_module=stub_run, frame_factory=Frame,
        bot_runner=bot or default_bot, sleep=lambda _seconds: None,
        logger=logs.append)
    return driver, stub_core, stub_run, observation, logs


def test_observation_sends_pad_and_reports_frame_clock(tmp_path):
    driver, core, run, observation, _logs = _driver(tmp_path)
    driver.prepare()
    outcome = driver.observe_once()

    assert outcome == 'continue'
    assert core.button_history[0] == ('a',)
    assert core.buttons == []
    assert driver.observations == 1
    assert run.actions == [(['a'], 100)]

    expected_wall_ms = driver.base_epoch_ms + round(
        driver.frames_per_observation * 1000 / 60.0)
    call = run.calls[0]
    assert call['wall'] == pytest.approx(expected_wall_ms / 1000.0)
    assert call['playing'] is True

    obs = observation['value']
    assert obs['game'] == 'hanjuku-speedrun'
    assert obs['meta']['observed_at_ms'] == expected_wall_ms
    assert obs['meta']['hanjuku']['observed_at_ms'] == expected_wall_ms
    assert obs['meta']['runtime_dir'] == str(driver.config.runtime)

    screenshot = driver.config.screenshot
    assert screenshot.is_file()
    assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')


def test_stop_copies_state_to_prev_and_saves_state_and_sram(tmp_path):
    state_path = tmp_path / 'states' / 'hanjuku.state'
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'OLD-PROGRESS')
    holder = {}

    def bot(_obs):
        # Equivalent to the installed SIGTERM handler firing mid-observation.
        holder['driver']._handle_signal(15, None)
        return {'actions': []}

    driver, core, _run, _observation, logs = _driver(
        tmp_path, bot=bot, state_path=state_path, noload=False)
    holder['driver'] = driver
    code = driver.run()

    assert code == 0
    assert core.loaded_state == b'OLD-PROGRESS'
    assert state_path.with_name('hanjuku.state.prev').read_bytes() == b'OLD-PROGRESS'
    assert state_path.read_bytes() == b'NEW-STATE'
    assert driver.config.sram_out.read_bytes() == b'NEW-SRAM'
    assert core.closed is True
    assert any('stop requested' in line for line in logs)


def test_stop_finalizes_speedrun_attempt_as_stopped_dnf(tmp_path):
    """SIGTERM closes the local RTA attempt like the corner teardown."""
    driver, _core, _run, _observation, logs = _driver(tmp_path)
    runtime = driver.config.runtime
    runtime.mkdir(parents=True)
    data = driver.config.data_dir
    data.mkdir(parents=True)
    timer = {
        'schema': 1, 'game': 'hanjuku-speedrun',
        'runtime_id': runtime.name, 'generation': 1300,
        'attempt_id': f'a0001-{runtime.name}', 'attempt_no': 1,
        'status': 'running', 'started_at_ms': 1000, 'ended_at_ms': None,
        'current_index': 0, 'splits': [], 'comparison': {},
        'heartbeat_at_ms': 4000,
    }
    (data / 'timer.json').write_text(json.dumps(timer), encoding='utf-8')
    (runtime / 'hanjuku_run.json').write_text(
        json.dumps({'observed_at_ms': 4321, 'phase': 'field'}),
        encoding='utf-8')
    driver.request_stop()
    code = driver.run()

    assert code == 0
    saved = json.loads((data / 'timer.json').read_text(encoding='utf-8'))
    assert saved['status'] == 'dnf'
    assert saved['dnf_reason'] == 'stopped'
    # The frame-derived observation clock, not the host wall clock.
    assert saved['ended_at_ms'] == 4321
    rows = [json.loads(line) for line in
            (data / 'attempts.jsonl').read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['attempt_id'] == timer['attempt_id']
    assert rows[-1]['status'] == 'dnf'
    attempt = data / 'attempts' / timer['attempt_id']
    assert (attempt / 'manifest.json').is_file()
    assert (attempt / 'segments.json').is_file()
    assert any('rta finalized: dnf' in line for line in logs)

    # A restart after the attempt ended (and was archived) must not rebuild
    # the manifest: that would clear the archived flags.
    manifest_path = attempt / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['archived'] = True
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    driver.request_stop()
    assert driver.run() == 0
    assert json.loads(manifest_path.read_text(encoding='utf-8'))['archived'] is True
    assert any('rta already ended: dnf' in line for line in logs)


def test_unreadable_state_exits_without_overwriting(tmp_path):
    state_path = tmp_path / 'states' / 'hanjuku.state'
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'OLD-PROGRESS')
    driver, core, _run, _observation, logs = _driver(
        tmp_path, core=StubCore(unserialize_ok=False),
        state_path=state_path, noload=False)
    code = driver.run()

    assert code == 4
    assert state_path.read_bytes() == b'OLD-PROGRESS'
    assert not state_path.with_name('hanjuku.state.prev').exists()
    assert core.closed is True
    assert any('load state -> False' in line for line in logs)


def test_noload_skips_state_but_still_backs_it_up_on_stop(tmp_path):
    state_path = tmp_path / 'states' / 'hanjuku.state'
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'OLD-PROGRESS')

    def bot(_obs):
        return {'actions': [{'type': 'pad', 'buttons': ['start'], 'hold_ms': 50}]}

    driver, core, _run, _observation, _logs = _driver(
        tmp_path, bot=bot, state_path=state_path, noload=True)
    driver.prepare()
    assert core.loaded_state is None
    driver.observe_once()
    driver.request_stop()
    driver._handle_signal(15, None)
    driver.save_state(keep_previous=True)

    assert state_path.with_name('hanjuku.state.prev').read_bytes() == b'OLD-PROGRESS'
    assert state_path.read_bytes() == b'NEW-STATE'


def test_fresh_does_not_load_state_and_sends_boot_sequence(tmp_path):
    state_path = tmp_path / 'states' / 'hanjuku.state'
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'OLD-PROGRESS')
    driver, core, _run, _observation, logs = _driver(
        tmp_path, state_path=state_path, noload=False, fresh=True)
    driver.prepare()

    assert core.loaded_state is None
    assert core.runs >= 600
    assert ('start',) in core.button_history
    assert ('a',) in core.button_history
    assert any('fresh boot' in line for line in logs)


def test_terminal_stops_without_calling_the_bot(tmp_path):
    calls = []

    def bot(obs):
        calls.append(obs)
        return {'actions': []}

    driver, _core, run, _observation, logs = _driver(
        tmp_path, states=[_run_state(phase='title',
                                     terminal_reason='game_over')],
        bot=bot)
    code = driver.run()

    assert code == 0
    assert calls == []
    assert len(run.calls) == 1
    assert any('terminal: game_over' in line for line in logs)


def test_stop_at_name_saves_and_exits(tmp_path):
    driver, _core, _run, _observation, _logs = _driver(
        tmp_path, states=[_run_state(phase='name')], stop_at_name=True)
    driver.prepare()
    assert driver.observe_once() == 'stop_at_name'


def test_signal_handler_sets_stop_flag(tmp_path):
    driver, _core, _run, _observation, _logs = _driver(tmp_path)
    driver.prepare()
    driver._handle_signal(15, None)
    assert driver._stop_requested is True


def test_pad_and_wait_actions_are_bounded(tmp_path):
    def bot(_obs):
        return {'actions': [
            {'type': 'pad', 'buttons': ['down', 'bogus'], 'hold_ms': 1},
            {'type': 'wait', 'ms': 5000},
            {'type': 'key', 'keys': ['x'], 'hold_ms': 10},
        ]}

    driver, core, _run, observation, _logs = _driver(tmp_path, bot=bot)
    driver.prepare()
    driver.observe_once()

    assert ('down',) in core.button_history
    assert all('bogus' not in buttons for buttons in core.button_history)
    assert driver.observations == 1


def test_convert_pixels_0rgb1555(tmp_path):
    data = b'\x00\x7c' + b'\xe0\x03' + b'\x1f\x00' + b'\xff\x7f'
    rgb = libretro_core.convert_pixels(
        data, 4, 1, 8, libretro_core.PIXEL_FORMAT_0RGB1555)
    assert tuple(rgb[0:3]) == (255, 0, 0)
    assert tuple(rgb[3:6]) == (0, 255, 0)
    assert tuple(rgb[6:9]) == (0, 0, 255)
    assert tuple(rgb[9:12]) == (255, 255, 255)


def test_convert_pixels_rgb565(tmp_path):
    data = b'\x00\xf8' + b'\xe0\x07' + b'\x1f\x00'
    rgb = libretro_core.convert_pixels(
        data, 3, 1, 6, libretro_core.PIXEL_FORMAT_RGB565)
    assert tuple(rgb[0:3]) == (255, 0, 0)
    assert tuple(rgb[3:6]) == (0, 255, 0)
    assert tuple(rgb[6:9]) == (0, 0, 255)


def test_convert_pixels_xrgb8888_and_padding(tmp_path):
    # Two pixels followed by 4 bytes of row padding.
    data = b'\x00\x00\xff\x00' + b'\x00\xff\x00\x00' + b'pad!'
    rgb = libretro_core.convert_pixels(
        data, 2, 1, 12, libretro_core.PIXEL_FORMAT_XRGB8888)
    assert tuple(rgb[0:3]) == (255, 0, 0)
    assert tuple(rgb[3:6]) == (0, 255, 0)


def test_convert_pixels_bottom_up_and_dupe():
    assert libretro_core.convert_pixels(b'', 0, 0, 0, 0) is None
    first = b'\x00\xf8'
    second = b'\x1f\x00'
    bottom_up = libretro_core.convert_pixels(
        first + second, 1, 2, -2, libretro_core.PIXEL_FORMAT_RGB565)
    assert tuple(bottom_up[0:3]) == (0, 0, 255)
    assert tuple(bottom_up[3:6]) == (255, 0, 0)


def test_buttons_mask():
    mask = libretro_core.buttons_mask(['a', 'start', 'b', 'bogus'])
    assert mask == ((1 << libretro_core.RETRO_DEVICE_ID_JOYPAD_A)
                    | (1 << libretro_core.RETRO_DEVICE_ID_JOYPAD_START)
                    | (1 << libretro_core.RETRO_DEVICE_ID_JOYPAD_B))


def test_video_callback_copies_raw_and_converts_lazily():
    import ctypes
    core = object.__new__(libretro_core.LibretroCore)
    core.pixel_format = libretro_core.PIXEL_FORMAT_RGB565
    core.pixel_format_override = None
    core._raw_frame = None
    callback = core._video()
    buffer = ctypes.create_string_buffer(b'\x00\xf8\xe0\x07\x1f\x00')
    callback(ctypes.cast(buffer, ctypes.c_void_p), 3, 1, 6)
    assert core._raw_frame is not None
    frame = core.frame
    assert (frame.width, frame.height) == (3, 1)
    assert tuple(frame.rgb[0:3]) == (255, 0, 0)
    assert tuple(frame.rgb[3:6]) == (0, 255, 0)
    assert tuple(frame.rgb[6:9]) == (0, 0, 255)
    # A dupe frame (width/height 0) keeps the previous raw frame.
    callback(ctypes.cast(buffer, ctypes.c_void_p), 0, 0, 0)
    assert core.frame is not None


def test_load_flow_leaves_a_failed_title_recovery_unsaved(tmp_path):
    state_path = tmp_path / 'states' / 'hanjuku.state'
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'TITLE-PROGRESS')
    driver, core, run, _observation, logs = _driver(
        tmp_path, states=[_run_state(phase='title')],
        state_path=state_path, noload=False, load_flow=True)
    driver._screen_text = lambda _frame: ''
    code = driver.run()

    assert code == 2
    assert state_path.read_bytes() == b'TITLE-PROGRESS'
    assert not state_path.with_name('hanjuku.state.prev').exists()
    assert ('start',) in core.button_history
    assert ('a',) in core.button_history
    assert len(run.calls) == 6
    assert any('still on title' in line for line in logs)
