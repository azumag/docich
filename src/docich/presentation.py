"""Present a native-size X11 game inside a fixed broadcast rectangle.

Own only the private X server, game viewer and local ffplay window. The live
encoder, display and audio bus belong to the existing broadcast runtime.
"""
from __future__ import annotations

import argparse
import os
import re
import select
import signal
import subprocess
import time
import json
from pathlib import Path
import tempfile


def _write_state(path, **fields):
    if not path:
        return
    target = Path(path)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.presentation-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(fields, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _groups_stopped(children):
    # A reaped wrapper (e.g. dbus-run-session) does not prove its children
    # exited. Never publish stopped while its process group still exists.
    for process in children:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            continue
        return False
    return True


CELL_ASPECT_MIN = 0.25
CELL_ASPECT_MAX = 4.0


def cell_aspect_scale(value: str) -> float:
    """Return the horizontal scale that shows ``W:H`` cells as square cells.

    Terminal fonts are roughly twice as tall as wide, so a tile-based CLI game
    (pacman4console) draws its square-tile maze vertically stretched.  Callers
    pass the terminal's real cell ratio explicitly; the presentation then
    widens only the captured window before the contain fit.  Never guessed
    from the window geometry here: the cell ratio is a property of the font.
    """
    if not isinstance(value, str):
        raise ValueError(f"cell aspect must be a W:H string (got {value!r})")
    match = re.fullmatch(r"\s*([0-9]+)\s*:\s*([0-9]+)\s*", value)
    if match is None:
        raise ValueError(f"cell aspect must be a W:H string (got {value!r})")
    cell_width, cell_height = int(match.group(1)), int(match.group(2))
    if not (1 <= cell_width <= 64 and 1 <= cell_height <= 64):
        raise ValueError("cell aspect values must be between 1 and 64")
    scale = cell_height / cell_width
    if not (CELL_ASPECT_MIN <= scale <= CELL_ASPECT_MAX):
        raise ValueError("cell aspect correction must stay within 1/4..4")
    return scale


def _cell_aspect(value: str) -> float:
    try:
        return cell_aspect_scale(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def contain_filter(width: int, height: int, *, cell_stretch: float = 1.0) -> str:
    filters = []
    if cell_stretch != 1.0:
        # 端末セルを正方形として見せるための水平補正。等倍 (既定) では従来と
        # 同一のフィルタ列を出し、配信経路の差分を増やさない。中間フレームの
        # 幅が奇数になり得るが、x11grab の bgra 入力は間引きが無く、最終段の
        # pad が 960x540 (偶数) に正規化するため配信フォーマットは変わらない。
        filters.append(f"scale=iw*{cell_stretch:g}:ih:flags=neighbor")
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
    filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    filters.append("setsar=1")
    return ",".join(filters)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
    return number


def _volume_percent(value: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"must be an integer percent (got {value!r})")
    if not 1 <= number <= 150:
        raise argparse.ArgumentTypeError(f"must be between 1 and 150 (got {value!r})")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument('--display', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--x', type=int, required=True)
    parser.add_argument('--y', type=int, required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    # How long to wait for the native viewer window before giving up.
    # SRT listeners (e.g. the Soren91 Mac remote renderer) show no window
    # until the first frame arrives, which can take a minute or more, so
    # callers on that path pass a longer budget. The default stays at 10s
    # so local-render CLI/paper corners behave exactly as before.
    parser.add_argument('--viewer-wait-sec', type=_positive_int, default=10)
    # Terminal games only: the terminal cell ratio (W:H). Setting it widens
    # the captured window in the presentation so square tiles are displayed
    # square (pacman4console's maze). The game window itself is untouched.
    parser.add_argument('--cell-aspect', type=_cell_aspect, dest='cell_stretch')
    # Optional PulseAudio sink for the viewer's own audio. SRT viewers (the
    # Soren91 Mac remote renderer) carry game audio that must reach the
    # broadcast encoder, which listens on the shared `soren_null` sink.
    parser.add_argument('--audio-sink', default=None)
    # Optional per-game playback volume for the viewer's own PulseAudio
    # streams only (never the shared sink or other workers' streams).
    parser.add_argument('--audio-volume-percent', type=_volume_percent, default=None)
    # Used by RetroArch only: dbus-run-session owns the process group, but
    # the X window belongs to its child. Search only the private X server.
    parser.add_argument('--window-pattern')
    parser.add_argument('--runtime-state')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    return parser


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or min(args.width, args.height) <= 0:
        parser.error('command and positive presentation dimensions are required')
    children = []

    def stop(_sig, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)

    def launch(argv, **kwargs):
        process = subprocess.Popen(argv, start_new_session=True, **kwargs)
        children.append(process)
        return process

    display_read_fd = None
    try:
        read_fd, write_fd = os.pipe()
        display_read_fd = read_fd
        try:
            launch(['Xvfb', '-displayfd', str(write_fd), '-screen', '0',
                    '4096x2160x24', '-noreset', '-nolisten', 'tcp'], pass_fds=(write_fd,))
            os.close(write_fd)
            write_fd = -1
            # Xvfb writes the display number and its trailing newline through
            # this fd.  Keep the read end open until Xvfb exits: closing it
            # after a short read can make a split displayfd write fail with
            # EPIPE, even though the display itself is ready.
            data = bytearray()
            deadline = time.monotonic() + 10
            while b'\n' not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('private display startup timed out')
                if not select.select([read_fd], [], [], remaining)[0]:
                    raise RuntimeError('private display startup timed out')
                chunk = os.read(read_fd, 32)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 32:
                    raise RuntimeError('private display startup failed')
            if b'\n' not in data:
                raise RuntimeError('private display startup failed')
            number = data.split(b'\n', 1)[0].decode().strip()
            if not number.isdigit():
                raise RuntimeError('private display startup failed')
        finally:
            if write_fd >= 0:
                os.close(write_fd)
        source_env = dict(os.environ, DISPLAY=f':{number}')
        source_env.pop('TMUX', None)
        # Route the viewer's audio to the requested PulseAudio sink (the
        # broadcast encoder captures `<sink>.monitor`). Only the viewer gets
        # this; the x11grab projection below stays video-only.
        if args.audio_sink:
            source_env['PULSE_SINK'] = str(args.audio_sink)
            source_env.setdefault('SDL_AUDIODRIVER', 'pulseaudio')
        viewer = launch(command, env=source_env)
        if args.audio_volume_percent is not None and args.runtime_state:
            from .pulse_volume import keep_applied
            keep_applied(viewer, args.audio_volume_percent,
                         Path(args.runtime_state).with_name('audio_volume.json'), env=source_env)
        deadline = time.monotonic() + args.viewer_wait_sec
        window = ''
        while time.monotonic() < deadline and viewer.poll() is None:
            selector = (['--name', args.window_pattern] if args.window_pattern
                        else ['--pid', str(viewer.pid)])
            found = subprocess.run(
                ['xdotool', 'search', '--onlyvisible', *selector],
                env=source_env, capture_output=True, text=True, timeout=2)
            if found.returncode == 0 and found.stdout.strip():
                windows = found.stdout.strip().splitlines()
                if args.window_pattern and len(windows) != 1:
                    raise RuntimeError('native game window is ambiguous')
                window = windows[0]
                break
            time.sleep(0.1)
        if not window:
            raise RuntimeError('native game viewer did not appear')
        geometry = subprocess.check_output(
            ['xdotool', 'getwindowgeometry', '--shell', window],
            env=source_env, text=True, timeout=2)
        dimensions = dict(re.findall(r'^(WIDTH|HEIGHT)=(\d+)$', geometry, re.M))
        width, height = int(dimensions['WIDTH']), int(dimensions['HEIGHT'])
        if width > 4096 or height > 2160:
            raise RuntimeError('native viewer exceeds private display capacity')
        cell_note = '' if args.cell_stretch is None else f' cell-stretch={args.cell_stretch:g}'
        print(f'native={width}x{height} output={args.width}x{args.height} fit=contain{cell_note}',
              flush=True)
        output_env = dict(os.environ, DISPLAY=args.display)
        player = launch([
            'ffplay', '-loglevel', 'warning', '-nostats', '-an', '-sn',
            '-f', 'x11grab', '-framerate', '15', '-draw_mouse', '0',
            '-window_id', window, '-video_size', f'{width}x{height}',
            '-i', f':{number}',
            '-vf', contain_filter(args.width, args.height,
                                  cell_stretch=args.cell_stretch or 1.0),
            '-noborder', '-window_title', args.title,
            '-left', str(args.x), '-top', str(args.y),
            '-x', str(args.width), '-y', str(args.height),
        ], env=output_env)
        _write_state(args.runtime_state, status='ready', display=f':{number}',
                     window=window, width=width, height=height,
                     groups=[child.pid for child in children])
        # In the runtime-tracked RetroArch path, loss of the projection alone
        # must not end gameplay. Keep supervising the native display/game;
        # ordinary TERM/HUP handling remains responsive for owned cleanup.
        watched = children[:-1] if args.runtime_state else children
        projection_failed = False
        while all(process.poll() is None for process in watched):
            if args.runtime_state and player.poll() is not None and not projection_failed:
                _write_state(args.runtime_state, status='presentation_failed',
                             display=f':{number}', window=window, width=width, height=height,
                             groups=[child.pid for child in children])
                projection_failed = True
            time.sleep(0.2)
        return player.returncode or 1
    finally:
        for process in reversed(children):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in reversed(children):
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if args.runtime_state:
            # Do not escalate against detached/unknown processes or declare
            # cleanup complete solely because the owned tmux pane vanished.
            limit = time.monotonic() + 3
            while not _groups_stopped(children) and time.monotonic() < limit:
                time.sleep(0.05)
            _write_state(args.runtime_state,
                         status='stopped' if _groups_stopped(children) else 'cleanup_failed')
        if display_read_fd is not None:
            os.close(display_read_fd)


if __name__ == '__main__':
    raise SystemExit(main())
