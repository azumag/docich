"""Common-foreground adapter for the existing Soren direct-stream entrypoint.

Reuse the existing config, command builder, relay, caption and reconnect helpers;
only the FFmpeg invocation adds a private RGBA input. The renderer is owned by
this common stream, not by any game, presenter, or individual FFmpeg reconnect.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from .twica_config import load_common_config
from .twica_overlay import (OverlayPipe, SnapshotReader, ffmpeg_input_args,
                            ffmpeg_overlay_filter, private_directory)
from .twica_state import heartbeat, owner, identity


def load_legacy(filename: Path):
    filename = filename.resolve(strict=True)
    if filename.name != 'direct_stream.py':
        raise ValueError('unexpected legacy runner')
    spec = importlib.util.spec_from_file_location('_docich_soren_direct_stream', filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OwnedReader(SnapshotReader):
    def __init__(self, common, width, height):
        super().__init__(common.frames, width, height)
        self.control_directory = common.state

    def read(self, now_ns=None):
        if owner(self.control_directory)['mode'] != 'common':
            self.state = 'inactive'
            return self.transparent
        return super().read(now_ns)


def compose_command(command: list[str], fd: int, width: int, height: int,
                    fps: int, *, live_clock: bool = True) -> list[str]:
    """Preserve both existing inputs, audio sync, captions and output options."""
    result = list(command)
    if '-filter_complex' in result or result.count('-i') != 2:
        raise ValueError('unsupported legacy stream graph')
    if result.count('-vf') > 1:
        raise ValueError('ambiguous legacy video filter')
    tail_filter = ''
    if '-vf' in result:
        index = result.index('-vf')
        tail_filter = result[index + 1]
        del result[index:index + 2]
    index = result.index('-map')
    result[index:index] = ffmpeg_input_args(fd, width, height, fps,
                                          sync_to=0 if live_clock else None)
    video_map = result.index('-map') + 1
    if result[video_map] != '0:v:0':
        raise ValueError('unsupported legacy video map')
    result[video_map] = '[twica_video]'
    graph = ffmpeg_overlay_filter(0, 2)
    if tail_filter:
        graph += ',' + tail_filter
    result[video_map - 1:video_map - 1] = [
        '-filter_complex_threads', '1', '-filter_complex', graph + '[twica_video]']
    return result


def _group_alive(pgid: int) -> bool:
    # Only inspect the group created by our Popen handle, not process name matches.
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw = path.read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if int(fields[2]) == pgid and fields[0] != 'Z':
                return True
        except (OSError, ValueError, IndexError):
            pass
    return False


def close_owned_group(child, timeout=3.0):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + timeout
    while _group_alive(child.pid) and time.monotonic() < deadline:
        time.sleep(.05)
    if _group_alive(child.pid):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.wait(timeout=3)
    deadline = time.monotonic() + 2
    while _group_alive(child.pid) and time.monotonic() < deadline:
        time.sleep(.05)
    if _group_alive(child.pid):
        raise RuntimeError('renderer group did not stop')


class RendererSupervisor:
    """Restart only the renderer. Never restart FFmpeg/a game on renderer failure."""
    def __init__(self, common, stream_config, soren_root):
        self.common, self.stream_config, self.soren_root = common, stream_config, soren_root
        self.stop = threading.Event()
        self.thread = None
        self.state = 'prepared'
        self.restarts = 0
        self.error = None

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, name='twica-renderer-supervisor', daemon=True)
        self.thread.start()
        return self

    def _environment(self):
        allowed = {'HOME', 'PATH', 'LANG', 'LC_ALL', 'DISPLAY', 'XDG_RUNTIME_DIR',
                   'TMPDIR', 'PULSE_SERVER', 'PULSE_COOKIE', 'SOREN_CHROME_EXECUTABLE_PATH',
                   'SOREN_DIRECT_TWICA_OVERLAY_URL', 'SOREN_DIRECT_TWICA_PROXY_PORT',
                   'SOREN_DIRECT_TWICA_OVERLAY_ENABLED'}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        parent = identity()
        env.update(DOCICH_TWICA_PARENT_PID=str(parent['pid']),
                   DOCICH_TWICA_PARENT_BIRTH=parent['birth'],
                   DOCICH_TWICA_PARENT_BOOT=parent['boot'],
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                   DOCICH_TWICA_COMMON_ENABLED='1', DOCICH_TWICA_STATE_DIR=str(self.common.state),
                   DOCICH_TWICA_FRAME_DIR=str(self.common.frames),
                   DOCICH_TWICA_FPS=str(self.common.fps),
                   DOCICH_TWICA_AUDIO_SINK=self.common.audio_sink)
        return env

    def _run(self):
        failures = 0
        try:
            while not self.stop.is_set():
                child = None
                started = time.monotonic()
                try:
                    child = subprocess.Popen([self.common.python, '-m', 'docich.twica_renderer_service',
                        '--soren-root', str(self.soren_root), '--width', str(self.stream_config.width),
                        '--height', str(self.stream_config.height)], env=self._environment(),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)
                    self.state = 'running'
                    while child.poll() is None and not self.stop.wait(.2):
                        pass
                except OSError:
                    self.state = 'unavailable'
                finally:
                    if child is not None:
                        close_owned_group(child)
                if self.stop.is_set():
                    break
                failures = 0 if time.monotonic() - started >= 30 else failures + 1
                self.restarts += 1
                self.state = 'backoff'
                self.stop.wait(min(30, 2 ** min(failures, 5)))
        except Exception:
            self.error = 'renderer_cleanup_failed'
            self.state = 'failed'
        finally:
            if self.error is None:
                self.state = 'stopped'

    def __exit__(self, *_exc):
        self.stop.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive() or self.error:
            raise RuntimeError('renderer supervisor cleanup failed')


def run_once(legacy, common, config, command, *, mode, reconnect, captions_active):
    """One encoder lifetime; inherited FD and feeder are always closed together."""
    started_at = int(time.time())
    stopping = False
    monitor = {'stopping': False, 'reconnect': False, 'reconnect_reason': None,
               'offline_streak': 0, 'last_live': None}
    monitor_thread = None
    with OverlayPipe(OwnedReader(common, config.width, config.height),
                     min(common.fps, config.fps)) as feed:
        effective = compose_command(command, feed.read_fd, config.width, config.height,
                                    min(common.fps, config.fps))
        with config.log_file.open('a', encoding='utf-8') as log:
            child = subprocess.Popen(effective, cwd=legacy.REPO_ROOT, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1,
                start_new_session=True, pass_fds=(feed.read_fd,))
            previous_term = previous_int = None
            latest, batch = {}, []
            base = {'backend': 'ffmpeg', 'mode': mode, 'running': True, 'state': 'running',
                    'pid': os.getpid(), 'ffmpeg_pid': child.pid, 'started_at': started_at,
                    'config': config.public_dict(),
                    'closed_captions': {'requested': config.closed_captions_enabled,
                                        'active': captions_active}}
            if reconnect is not None:
                base['reconnect'] = reconnect.public_dict()
            def stop_child(_signum, _frame):
                nonlocal stopping
                if not stopping:
                    stopping = True
                    monitor['stopping'] = True
                    legacy._graceful_stop_ffmpeg(child)
            def publish():
                heartbeat(common.state, 'compositor', state='running' if feed.alive else 'degraded',
                    ffmpeg_pid=child.pid, frames_sent=feed.frames_sent,
                    frame_state=feed.state, generation=owner(common.state)['generation'])
                legacy._atomic_json(config.state_dir / 'status.json', {
                    **base, **latest, 'updated_at': int(time.time()),
                    'twica_common': {'enabled': True, 'owner': owner(common.state)['mode'],
                                     'frame_state': feed.state, 'frames_sent': feed.frames_sent}})
            try:
                feed.start()
                previous_term = signal.signal(signal.SIGTERM, stop_child)
                previous_int = signal.signal(signal.SIGINT, stop_child)
                publish()
                if mode == 'live' and reconnect and reconnect.enabled and reconnect.twitch_channel:
                    monitor_thread = legacy._start_reconnect_monitor(config, reconnect, child, monitor)
                for line in child.stdout:
                    batch.append(line)
                    if line.startswith('progress='):
                        latest.update(legacy.parse_progress_lines(batch))
                        batch.clear()
                        publish()
                return_code = child.wait()
            finally:
                monitor['stopping'] = True
                if child.poll() is None:
                    close_owned_group(child)
                if previous_term is not None:
                    signal.signal(signal.SIGTERM, previous_term)
                if previous_int is not None:
                    signal.signal(signal.SIGINT, previous_int)
                if monitor_thread is not None:
                    monitor_thread.join(timeout=5)
                heartbeat(common.state, 'compositor', state='stopped', ffmpeg_pid=child.pid,
                          frames_sent=feed.frames_sent, frame_state='closed')
            if monitor.get('reconnect'):
                exit_code, state = 0, 'reconnect'
            else:
                exit_code, state = legacy.classify_ffmpeg_exit(return_code, stopping=stopping)
            legacy._atomic_json(config.state_dir / 'status.json', {
                **base, **latest, 'running': False, 'state': state, 'exit_code': exit_code,
                'ffmpeg_exit_code': return_code, 'ended_at': int(time.time()),
                'updated_at': int(time.time()),
                'twica_common': {'enabled': True, 'frame_state': 'closed'}})
            return legacy.RunOutcome(exit_code=exit_code, state=state, started_at=started_at,
                reconnect_reason=monitor.get('reconnect_reason'), ffmpeg_exit_code=return_code)


def run_stream(legacy, common, config, *, mode, output_path=None, duration_sec=None):
    """Keep the original stream lock and reconnect policy, plus one common child."""
    import fcntl
    captions = legacy.validate_runtime(config, mode=mode)
    captions = captions and legacy.prepare_caption_runtime(config)
    filters = legacy._checked([config.ffmpeg_bin, '-hide_banner', '-filters'])
    if not re.search(r'\boverlay\s', filters):
        raise legacy.RuntimeCheckError('FFmpeg overlay filter is unavailable')
    command = legacy.build_ffmpeg_command(config, mode=mode, output_path=output_path,
                                          duration_sec=duration_sec, captions_active=captions)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    private_directory(common.state)
    private_directory(common.frames)
    with (config.state_dir / 'direct_stream.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise legacy.AlreadyRunningError('direct stream is already running') from None
        reconnect = legacy.load_reconnect_config() if mode == 'live' else None
        stop = threading.Event()
        def requested(_sig, _frame):
            stop.set()
        previous = {s: signal.signal(s, requested) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            with RendererSupervisor(common, config, legacy.REPO_ROOT):
                consecutive = 0
                while not stop.is_set():
                    result = run_once(legacy, common, config, command, mode=mode,
                                      reconnect=reconnect, captions_active=captions)
                    if stop.is_set() or result.state == 'stopped':
                        return 0
                    if (result.state == 'completed' or not reconnect or not reconnect.enabled):
                        return result.exit_code
                    consecutive += 1
                    if consecutive > reconnect.max_consecutive_restarts:
                        raise legacy.RuntimeCheckError('direct stream reconnect limit reached')
                    reason = result.reconnect_reason or 'ffmpeg_exit'
                    backoff = reconnect.backoff_sec * min(2 ** (consecutive - 1), 16)
                    legacy._record_reconnect(config, reason=reason, consecutive=consecutive,
                                             offline_streak=0, backoff_sec=backoff)
                    if reconnect.reload_relay and reason == 'twitch_offline':
                        legacy._reload_relay(config)
                    stop.wait(backoff)
                return 0
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--runner', type=Path, required=True)
    selected, remaining = parser.parse_known_args(argv)
    legacy = load_legacy(selected.runner)
    common = load_common_config(legacy.REPO_ROOT)
    if not common.enabled or not remaining or remaining[0] not in {'run', 'record', 'command'}:
        return legacy.main(remaining)
    action = remaining[0]
    args = argparse.ArgumentParser()
    if action == 'record':
        args.add_argument('--output', type=Path, required=True)
        args.add_argument('--duration', type=legacy._positive_duration, default=60)
    elif action == 'command':
        args.add_argument('--mode', choices=('live', 'record'), default='live')
        args.add_argument('--output', type=Path)
        args.add_argument('--duration', type=legacy._positive_duration)
    parsed = args.parse_args(remaining[1:])
    config = legacy.load_config()
    if action == 'command':
        command = legacy.build_ffmpeg_command(config, mode=parsed.mode, output_path=parsed.output,
                                              duration_sec=parsed.duration)
        print(json.dumps(compose_command(command, 3, config.width, config.height,
                                        min(common.fps, config.fps))))
        return 0
    return run_stream(legacy, common, config,
        mode='live' if action == 'run' else 'record',
        output_path=parsed.output.resolve() if action == 'record' else None,
        duration_sec=parsed.duration if action == 'record' else None)

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('twica common stream: startup or runtime failed', file=sys.stderr)
        raise SystemExit(2)
