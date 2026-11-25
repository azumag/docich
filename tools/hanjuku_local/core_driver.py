#!/usr/bin/env python3
"""Headless libretro driver for the local Hanjuku runs.

This is the repository copy of the owner's Mac tool (``tmp/hanjuku-local``).
It runs a snes9x libretro core directly, observes the frame with the same
``hanjuku_run`` and bot code the corner uses, and sends the bot's pad actions
back into the core.  ``--fast`` removes the 60 fps pacing so a trial runs as
fast as the host allows; the split timer then uses an emulated-frame clock
(``observed_at_ms``) instead of the host wall clock.

Supported games:

* ``hanjuku-hero`` (default): ``brains/hanjuku/bot.py`` and ``docich.hanjuku_run``
* ``hanjuku-speedrun``: ``brains/hanjuku_speedrun/bot.py`` and
  ``docich.hanjuku_speedrun.hanjuku_run``; records go to
  ``run-local/speedrun/hanjuku-speedrun`` with ``env=local``.

Only the Python standard library is used.  A real core/ROM is required to
play; the control flow is tested with a stub core.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import types


def _sibling(name):
    """Import a sibling module both as a script and as a package member."""
    try:
        return __import__(f'{__package__}.{name}', fromlist=['*'])
    except Exception:
        directory = str(Path(__file__).resolve().parent)
        if directory not in sys.path:
            sys.path.insert(0, directory)
        return __import__(name)


libretro_core = _sibling('libretro_core')

GAME_BOT_DIRS = {
    'hanjuku-hero': 'hanjuku',
    'hanjuku-speedrun': 'hanjuku_speedrun',
}
DEFAULT_FPS = 60.0988
DEFAULT_OBSERVATION_MS = 1500
MAX_OBSERVATION_FRAMES = 600
MAX_HOLD_FRAMES = 120


def _env_text(name, default=''):
    value = os.environ.get(name)
    return default if value is None or value == '' else value


def _env_int(name, default):
    try:
        value = int(str(os.environ.get(name, '')).strip())
    except (TypeError, ValueError):
        return default
    return value


def _env_bool(name):
    return str(os.environ.get(name, '')).strip().lower() in {
        '1', 'true', 'yes', 'on',
    }


def _env_path(name, default):
    raw = _env_text(name)
    return Path(raw).expanduser() if raw else Path(default)


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp-{os.getpid()}')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _repo_root():
    return Path(__file__).resolve().parents[2]


@dataclass
class DriverConfig:
    game: str
    code_root: Path
    runtime: Path
    runtime_id: str
    generation: int
    lease_id: str
    state_path: Path
    save_every: int
    sram_out: Path | None
    sram_in: Path | None
    inproc: bool
    load_flow: bool
    core_path: Path
    rom_path: Path
    force_565: bool
    stop_at_name: bool
    fast: bool
    noload: bool
    fresh: bool
    bot_path: Path
    data_dir: Path
    fps: float = DEFAULT_FPS

    @property
    def identity(self):
        return {
            'game': self.game,
            'runtime_id': self.runtime_id,
            'generation': self.generation,
            'lease_id': self.lease_id,
        }

    @property
    def screenshot(self):
        return self.runtime / 'screenshots' / 'latest.png'

    @classmethod
    def from_environment(cls, *, game=None, fast=None, noload=None,
                         fresh=None, code_root=None):
        root = Path(code_root) if code_root is not None else _env_path(
            'HANJUKU_CODE_ROOT', _repo_root())
        game = game or _env_text('HANJUKU_GAME', 'hanjuku-hero')
        if game not in GAME_BOT_DIRS:
            raise ValueError(f'unknown game: {game}')
        runtime_id = _env_text('HANJUKU_RUNTIME_ID', 'g1163-42bfb85e')
        generation = _env_int('HANJUKU_GENERATION', 1163)
        runtime = _env_path(
            'HANJUKU_RUNTIME',
            root / 'run-local' / 'runtimes' / runtime_id)
        state_default = root / 'tmp' / 'hanjuku-local' / 'states' / f'{game}.state'
        state_path = _env_path('HANJUKU_STATE', state_default)
        data_dir = _env_path(
            'DOCICH_SPEEDRUN_DATA_DIR',
            root / 'run-local' / 'speedrun' / 'hanjuku-speedrun')
        bot_default = root / 'brains' / GAME_BOT_DIRS[game] / 'bot.py'
        bot_path = _env_path('HANJUKU_BOT', bot_default)
        core_default = (root / 'tmp' / 'hanjuku-local' / 'cores_arm64'
                        / 'snes9x_libretro.dylib')
        rom_default = root / 'games' / 'roms' / 'hanjuku-hero.sfc'
        return cls(
            game=game,
            code_root=root,
            runtime=runtime,
            runtime_id=runtime_id,
            generation=generation,
            lease_id=_env_text('HANJUKU_LEASE', 'local'),
            state_path=state_path,
            save_every=max(1, _env_int('HANJUKU_SAVE_EVERY', 10)),
            sram_out=(_env_path('HANJUKU_SRAM_OUT', '')
                      if _env_text('HANJUKU_SRAM_OUT') else None),
            sram_in=(_env_path('HANJUKU_SRAM_IN', '')
                     if _env_text('HANJUKU_SRAM_IN') else None),
            inproc=_env_bool('HANJUKU_INPROC'),
            load_flow=_env_bool('HANJUKU_LOAD_FLOW'),
            core_path=_env_path('HANJUKU_CORE', core_default),
            rom_path=_env_path('HANJUKU_ROM', rom_default),
            force_565=_env_bool('HANJUKU_FORCE_565'),
            stop_at_name=_env_bool('HANJUKU_STOP_AT_NAME'),
            fast=_env_bool('HANJUKU_FAST') if fast is None else bool(fast),
            noload=_env_bool('HANJUKU_NOLOAD') if noload is None else bool(noload),
            fresh=_env_bool('HANJUKU_FRESH') if fresh is None else bool(fresh),
            bot_path=bot_path,
            data_dir=data_dir,
            fps=float(_env_text('HANJUKU_FPS', DEFAULT_FPS) or DEFAULT_FPS),
        )


class _BotRunner:
    """Call the production bot either in-process or as a child process."""

    def __init__(self, config, *, timeout=120):
        self.config = config
        self.timeout = timeout
        self._module = None

    def _environment(self):
        env = dict(os.environ)
        source = str(self.config.code_root / 'src')
        existing = env.get('PYTHONPATH')
        env['PYTHONPATH'] = source + (os.pathsep + existing if existing else '')
        env['DOCICH_SPEEDRUN_DATA_DIR'] = str(self.config.data_dir)
        env['DOCICH_SPEEDRUN_ENV'] = 'local'
        env['GIT_TERMINAL_PROMPT'] = '0'
        return env

    def _load_module(self):
        if self._module is not None:
            return self._module
        path = Path(self.config.bot_path)
        if not path.is_file():
            raise RuntimeError('bot_missing')
        name = f'_hanjuku_local_bot_{self.config.game.replace("-", "_")}'
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError('bot_unimportable')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._module = module
        return module

    def __call__(self, observation):
        payload = json.dumps(observation, ensure_ascii=False)
        if self.config.inproc:
            return self._call_inproc(payload)
        return self._call_subprocess(payload)

    def _parse(self, text):
        for line in reversed((text or '').splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                document = json.loads(line)
            except ValueError:
                continue
            if isinstance(document, dict):
                return document
        return {}

    def _call_subprocess(self, payload):
        command = [sys.executable, str(self.config.bot_path)]
        result = subprocess.run(
            command,
            input=payload,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            env=self._environment(),
            cwd=str(self.config.code_root),
            check=False,
        )
        if result.returncode != 0:
            print(f'[drv] bot exit={result.returncode}', file=sys.stderr)
        return self._parse(result.stdout)

    def _call_inproc(self, payload):
        module = self._load_module()
        stdout = io.StringIO()
        original_stdin, original_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(payload)
            sys.stdout = stdout
            code = module.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.stdin, sys.stdout = original_stdin, original_stdout
        if code not in (0, None):
            print(f'[drv] bot exit={code}', file=sys.stderr)
        return self._parse(stdout.getvalue())


class DriverExit(Exception):
    """Stop the driver with a specific exit code."""

    def __init__(self, code):
        self.code = int(code)
        super().__init__(str(code))


class CoreDriver:
    """One headless run: load, observe, decide, send pad, save state."""

    def __init__(self, config, core, *, run_module=None, frame_factory=None,
                 bot_runner=None, sleep=None, logger=None):
        self.config = config
        self.core = core
        self._run_module = run_module
        self._frame_factory = frame_factory
        self._bot = bot_runner
        self._sleep = sleep or time.sleep
        self._log = logger or (lambda message: print(message, file=sys.stderr))
        self.frame_count = 0
        self.observations = 0
        self.frames_per_observation = max(
            1, round(DEFAULT_OBSERVATION_MS * config.fps / 1000.0))
        self.base_epoch_ms = int(time.time() * 1000)
        self._started_monotonic = None
        self._stop_requested = False
        self._loaded = False
        self._save_on_exit = False
        self._last_state = {}
        self._last_frame = None

    # -- module loading ----------------------------------------------------

    def _modules(self):
        if self._run_module is not None:
            return self._run_module
        source = str(self.config.code_root / 'src')
        if source not in sys.path:
            sys.path.insert(0, source)
        if self.config.game == 'hanjuku-speedrun':
            from docich.hanjuku_speedrun.hanjuku_pixels import Frame
            from docich.hanjuku_speedrun import hanjuku_run
        else:
            from docich.hanjuku_pixels import Frame
            from docich import hanjuku_run
        self._run_module = hanjuku_run
        if self._frame_factory is None:
            self._frame_factory = Frame
        return hanjuku_run

    def _frame_class(self):
        if self._frame_factory is not None:
            return self._frame_factory
        source = str(self.config.code_root / 'src')
        if source not in sys.path:
            sys.path.insert(0, source)
        if self.config.game == 'hanjuku-speedrun':
            from docich.hanjuku_speedrun.hanjuku_pixels import Frame
        else:
            from docich.hanjuku_pixels import Frame
        self._frame_factory = Frame
        return Frame

    # -- lifecycle ---------------------------------------------------------

    def prepare(self):
        config = self.config
        config.runtime.mkdir(parents=True, exist_ok=True)
        config.screenshot.parent.mkdir(parents=True, exist_ok=True)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.state_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ['DOCICH_SPEEDRUN_ENV'] = 'local'
        os.environ['DOCICH_SPEEDRUN_DATA_DIR'] = str(config.data_dir)

        self._modules()
        if self._bot is None:
            self._bot = _BotRunner(config)
        self.core.setup()
        if config.force_565:
            self.core.pixel_format_override = (
                libretro_core.PIXEL_FORMAT_RGB565)
        self.core.load_game(config.rom_path)
        self._started_monotonic = time.monotonic()
        self._loaded = True
        self._save_on_exit = True

        if config.sram_in is not None and config.load_flow:
            try:
                data = config.sram_in.read_bytes()
            except OSError as exc:
                self._log(f'[drv] sram input failed: {type(exc).__name__}')
            else:
                if data and self.core.load_ram(data):
                    self._log(f'[drv] sram injected {len(data)}')
                else:
                    self._log('[drv] sram inject failed')

        if config.fresh:
            self._fresh_boot()
        elif not config.noload and config.state_path.is_file():
            if not self._load_state():
                self._save_on_exit = False
                raise DriverExit(4)
        elif not config.noload:
            self._log('[drv] no state file; booting the ROM')

        if config.load_flow:
            flow_code = self._load_flow()
            if flow_code:
                # A failed recovery must not overwrite the state/SRAM the
                # operator is trying to restore.
                self._save_on_exit = False
                raise DriverExit(flow_code)

    def _load_state(self):
        path = self.config.state_path
        try:
            data = path.read_bytes()
        except OSError as exc:
            self._log(f'[drv] load state failed: {type(exc).__name__}')
            return False
        if not data:
            self._log('[drv] load state failed: empty')
            return False
        try:
            loaded = bool(self.core.unserialize(data))
        except Exception:
            loaded = False
        self._log(f'[drv] load state -> {loaded}')
        return loaded

    def _fresh_boot(self):
        """Deterministic new boot: wait out the attract, then START -> A -> A."""
        self._log('[drv] fresh boot')
        self._run_frames(600)
        for buttons in (['start'], ['a'], ['a']):
            self._tap(buttons, 100)
            self._run_frames(90)

    def _load_flow(self):
        """Title recovery flow from the operations memo (approximate).

        Returns an exit code: 0 on a successful load, 2 when the title remains,
        3 when a new-game introduction is detected (nothing is saved).
        """
        self._log('[drv] load flow: waiting for the attract')
        self._run_frames(760)
        for buttons in (['start'], ['a'], ['a'], ['a'], ['a'], ['a']):
            self._tap(buttons, 100)
            self._run_frames(60)
            state, frame, _shot = self._capture_and_observe()
            if state.get('terminal_reason'):
                self._log('[drv] load flow: terminal; not saving')
                return 2
            text = self._screen_text(frame)
            if self._new_game_text(text):
                self._log('[drv] load flow: new game detected; not saving')
                self._save_on_exit = False
                return 3
            if self._loaded_game_text(text) or state.get('phase') in {
                    'battle', 'field'}:
                self._log('[drv] load flow: gameplay resumed')
                return 0
        state = self._last_state
        if state.get('phase') == 'title':
            self._log('[drv] load flow: still on title')
            return 2
        self._log('[drv] load flow: result unknown; continuing')
        return 0

    def _screen_text(self, frame):
        if frame is None:
            return ''
        module_name = ('docich.hanjuku_speedrun.hanjuku_screen'
                       if self.config.game == 'hanjuku-speedrun'
                       else 'docich.hanjuku_screen')
        try:
            module = __import__(module_name, fromlist=['parse'])
            return str((module.parse(frame).text or ''))
        except Exception:
            return ''

    @staticmethod
    def _new_game_text(text):
        return any(marker in text for marker in (
            'ひとりのゆうしゃ', 'なまえを', 'なまえにゅうりょく'))

    @staticmethod
    def _loaded_game_text(text):
        return any(marker in text for marker in ('せんぱつたい', 'HP'))

    # -- frame loop --------------------------------------------------------

    def _advance(self):
        self.core.run()
        self.frame_count += 1
        if not self.config.fast and self._started_monotonic is not None:
            target = (self._started_monotonic
                      + self.frame_count / max(1.0, self.config.fps))
            delay = target - time.monotonic()
            if delay > 0:
                self._sleep(delay)

    def _run_frames(self, count):
        for _ in range(max(0, int(count))):
            if self._stop_requested:
                return
            self._advance()

    def _run_with_buttons(self, buttons, frames):
        self.core.set_buttons(buttons)
        try:
            self._run_frames(frames)
        finally:
            self.core.clear_buttons()

    def _tap(self, buttons, hold_ms):
        frames = max(1, min(MAX_HOLD_FRAMES, round(
            max(1, int(hold_ms)) * self.config.fps / 1000.0)))
        self._run_with_buttons(buttons, frames)

    def _frame_time_ms(self):
        return self.base_epoch_ms + int(round(
            self.frame_count * 1000.0 / max(1.0, self.config.fps)))

    def _write_screenshot(self, frame):
        data = frame.png_bytes() if hasattr(frame, 'png_bytes') else frame
        _atomic_write(self.config.screenshot, data)

    def _capture_and_observe(self):
        self._run_frames(self.frames_per_observation)
        if self._stop_requested:
            raise DriverExit(0)
        video = getattr(self.core, 'frame', None)
        if video is None:
            raise DriverExit(5)
        frame_class = self._frame_class()
        frame = frame_class(video.width, video.height, video.rgb)
        self._write_screenshot(frame)
        self._last_frame = frame
        wall_ms = self._frame_time_ms()
        state = self._modules().observe(
            self.config.runtime,
            self.config.identity,
            frame,
            now=time.monotonic(),
            wall=wall_ms / 1000.0,
            playing=True,
        )
        if not isinstance(state, dict):
            state = {}
        self._last_state = state
        self.observations += 1
        return state, frame, self.config.screenshot

    def _observation_meta(self, state, wall_ms):
        identity = dict(self.config.identity)
        identity['observed_at_ms'] = wall_ms
        return {
            'runtime_dir': str(self.config.runtime),
            'terminal_reason': state.get('terminal_reason'),
            'terminal_candidate': state.get('terminal_candidate'),
            'observed_at_ms': wall_ms,
            'hanjuku': identity,
        }

    def _decide(self, screenshot, state, wall_ms):
        observation = {
            'schema': 1,
            'game': self.config.game,
            'screenshot': str(screenshot),
            'meta': self._observation_meta(state, wall_ms),
        }
        try:
            answer = self._bot(observation)
        except subprocess.TimeoutExpired:
            self._log('[drv] bot timeout')
            return []
        except Exception as exc:
            self._log(f'[drv] bot failed: {type(exc).__name__}')
            return []
        if not isinstance(answer, dict):
            return []
        interval = answer.get('observation_interval_ms')
        if type(interval) is int and interval > 0:
            self.frames_per_observation = max(
                1, min(MAX_OBSERVATION_FRAMES,
                       round(interval * self.config.fps / 1000.0)))
        actions = answer.get('actions')
        return actions if isinstance(actions, list) else []

    def _apply_actions(self, actions):
        sent = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            kind = action.get('type')
            if kind == 'pad':
                buttons = [button for button in action.get('buttons') or ()
                           if button in libretro_core.BUTTON_IDS]
                hold_ms = action.get('hold_ms', 100)
                if not isinstance(hold_ms, int) or hold_ms < 1:
                    hold_ms = 100
                if not buttons:
                    self._log('[drv] hold (empty pad)')
                    continue
                self._tap(buttons, hold_ms)
                sent.append(types.SimpleNamespace(
                    type='pad', buttons=list(buttons), hold_ms=hold_ms))
            elif kind == 'wait':
                ms = action.get('ms', 0)
                frames = max(1, round((ms if isinstance(ms, int) else 0)
                                      * self.config.fps / 1000.0))
                self._run_frames(min(MAX_HOLD_FRAMES, frames))
        for action in sent:
            try:
                self._modules().action_sent(
                    self.config.runtime, self.config.identity, action,
                    now=time.monotonic())
            except Exception:
                pass
        if not actions:
            self._log('[drv] hold (no action)')
        return sent

    def observe_once(self):
        state, frame, screenshot = self._capture_and_observe()
        wall_ms = self._frame_time_ms()
        terminal = state.get('terminal_reason')
        phase = state.get('phase')
        actions = []
        if terminal:
            self._log(f'[frame] n={self.frame_count} phase={phase} '
                      f'act=terminal({terminal}) t={wall_ms}ms')
            self._log(f'[drv] terminal: {terminal}')
            outcome = 'terminal'
        elif state.get('terminal_candidate'):
            # The production adapter withholds input while a terminal is a
            # candidate.  Keep observing so the candidate can confirm or clear.
            self._log(f'[frame] n={self.frame_count} phase={phase} '
                      f'act=hold(candidate) t={wall_ms}ms')
            outcome = 'candidate'
        elif self.config.stop_at_name and phase == 'name':
            self._log(f'[frame] n={self.frame_count} phase={phase} '
                      f'act=stop_at_name t={wall_ms}ms')
            self._log('[drv] stop at name screen')
            outcome = 'stop_at_name'
        else:
            actions = self._decide(screenshot, state, wall_ms)
            self._apply_actions(actions)
            self._log(f'[frame] n={self.frame_count} phase={phase} '
                      f'act={self._action_label(actions)} t={wall_ms}ms')
            outcome = 'continue'
        # Battery SRAM is the durable progress; write it every observation
        # like the operations memo says, so a crash loses at most one frame
        # batch.  The .state generations stay on the slower save interval.
        self._save_sram()
        return outcome

    @staticmethod
    def _action_label(actions):
        labels = []
        for action in actions if isinstance(actions, list) else ():
            if not isinstance(action, dict):
                continue
            if action.get('type') == 'pad':
                buttons = '+'.join(action.get('buttons') or ())
                labels.append(buttons or 'pad')
            else:
                labels.append(str(action.get('type') or '?'))
        return ','.join(labels) if labels else 'hold'

    # -- saving ------------------------------------------------------------

    def _save_sram(self):
        if self.config.sram_out is None:
            return False
        try:
            sram = self.core.save_ram()
        except Exception:
            return False
        if not sram:
            return False
        _atomic_write(self.config.sram_out, sram)
        return True

    def save_state(self, *, keep_previous):
        config = self.config
        if (keep_previous and config.state_path.is_file()
                and not config.state_path.is_symlink()):
            try:
                previous = config.state_path.with_name(
                    config.state_path.name + '.prev')
                temporary = previous.with_name(f'.{previous.name}.tmp')
                shutil.copyfile(config.state_path, temporary)
                os.replace(temporary, previous)
            except OSError as exc:
                self._log(f'[drv] state backup failed: {type(exc).__name__}')
        try:
            data = self.core.serialize()
        except Exception:
            data = None
        if not data:
            self._log('[drv] state serialize unavailable')
        else:
            _atomic_write(config.state_path, data)
            self._log(f'[drv] state saved {len(data)}')
        if self._save_sram():
            self._log('[drv] sram saved')

    def request_stop(self):
        self._stop_requested = True

    def _handle_signal(self, _signum, _frame):
        self._stop_requested = True

    def run(self):
        previous = {}
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[signum] = signal.signal(signum, self._handle_signal)
            except (ValueError, OSError):
                pass
        code = 0
        try:
            self.prepare()
            while not self._stop_requested:
                outcome = self.observe_once()
                if outcome in {'terminal', 'stop_at_name'}:
                    break
                if (self.config.save_every > 0
                        and self.observations % self.config.save_every == 0):
                    self.save_state(keep_previous=False)
            if self._stop_requested:
                self._log('[drv] stop requested')
        except DriverExit as exc:
            code = exc.code
        except KeyboardInterrupt:
            self._stop_requested = True
            code = 0
        except Exception as exc:
            self._log(f'[drv] fatal: {type(exc).__name__}: {exc}')
            code = 1
            self._save_on_exit = False
        finally:
            if self._loaded and self._save_on_exit:
                self.save_state(keep_previous=True)
            try:
                self.core.close()
            except Exception:
                pass
            for signum, handler in previous.items():
                try:
                    signal.signal(signum, handler)
                except (ValueError, OSError):
                    pass
        return code


def _build_parser():
    parser = argparse.ArgumentParser(
        prog='core_driver',
        description='Headless libretro driver for the local Hanjuku runs.')
    parser.add_argument('--fast', action='store_true',
                        help='run as fast as possible (frame-derived clock)')
    parser.add_argument('--noload', action='store_true',
                        help='do not load HANJUKU_STATE at start')
    parser.add_argument('--fresh', action='store_true',
                        help='deterministic new boot (attract -> START -> A)')
    parser.add_argument('--game', choices=sorted(GAME_BOT_DIRS),
                        default=None,
                        help='hanjuku-hero (default) or hanjuku-speedrun')
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        config = DriverConfig.from_environment(
            game=args.game, fast=args.fast or None,
            noload=args.noload or None, fresh=args.fresh or None)
    except (ValueError, OSError) as exc:
        print(f'[drv] config error: {exc}', file=sys.stderr)
        return 2
    for label, path in (('core', config.core_path), ('rom', config.rom_path)):
        if not Path(path).is_file():
            print(f'[drv] {label} not found: {path}', file=sys.stderr)
            return 2
    try:
        core = libretro_core.LibretroCore(
            config.core_path,
            system_dir=Path(config.core_path).parent,
            save_dir=config.state_path.parent)
    except libretro_core.CoreError as exc:
        print(f'[drv] core error: {exc.code}', file=sys.stderr)
        return 2
    return CoreDriver(config, core).run()


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
