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


def contain_filter(width: int, height: int) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
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
        viewer = launch(command, env=source_env)
        deadline = time.monotonic() + args.viewer_wait_sec
        window = ''
        while time.monotonic() < deadline and viewer.poll() is None:
            found = subprocess.run(
                ['xdotool', 'search', '--onlyvisible', '--pid', str(viewer.pid)],
                env=source_env, capture_output=True, text=True, timeout=2)
            if found.returncode == 0 and found.stdout.strip():
                window = found.stdout.splitlines()[0]
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
        print(f'native={width}x{height} output={args.width}x{args.height} fit=contain', flush=True)
        output_env = dict(os.environ, DISPLAY=args.display)
        player = launch([
            'ffplay', '-loglevel', 'warning', '-nostats', '-an', '-sn',
            '-f', 'x11grab', '-framerate', '15', '-draw_mouse', '0',
            '-window_id', window, '-video_size', f'{width}x{height}',
            '-i', f':{number}', '-vf', contain_filter(args.width, args.height),
            '-noborder', '-window_title', args.title,
            '-left', str(args.x), '-top', str(args.y),
            '-x', str(args.width), '-y', str(args.height),
        ], env=output_env)
        while all(process.poll() is None for process in children):
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
        if display_read_fd is not None:
            os.close(display_read_fd)


if __name__ == '__main__':
    raise SystemExit(main())
