"""FFmpeg executable adapter for the existing Soren direct-stream runner.

Capability probes exec the real binary unchanged. Encoding gets one independent
RGBA FD while its original stdin/stdout/stderr, audio maps, CC and relay survive.
The wrapper is a child of the existing runner and never owns/restarts a game.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from .twica_overlay import OverlayPipe, SnapshotReader, ffmpeg_input_args, ffmpeg_overlay_filter
from .twica_state import control, exclusive, frame_directory, heartbeat, state_directory


def encoding_geometry(argv: list[str]) -> tuple[int, int, int] | None:
    # A capability probe (including -h encoder=libx264) has no input.
    if '-i' not in argv:
        return None
    if argv.count('-i') != 2 or '-filter_complex' in argv:
        raise ValueError('unsupported direct-stream command')
    if [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == '-map'] != ['0:v:0', '1:a:0']:
        raise ValueError('unsupported direct-stream maps')
    first_input = argv.index('-i')
    video = argv[:first_input]
    if '-f' not in video or video[video.index('-f') + 1] != 'x11grab':
        raise ValueError('unsupported video capture')
    size = video[video.index('-video_size') + 1]
    match = re.fullmatch(r'(\d+)x(\d+)', size)
    if not match:
        raise ValueError('invalid capture size')
    width, height = map(int, match.groups())
    fps = int(video[video.index('-framerate') + 1])
    if not 320 <= width <= 3840 or not 180 <= height <= 2160 or not 1 <= fps <= 60:
        raise ValueError('unsupported capture geometry')
    return width, height, fps


def compose_args(argv: list[str], fd: int, geometry: tuple[int, int, int]) -> list[str]:
    width, height, fps = geometry
    args = list(argv)
    post_filter = ''
    if '-vf' in args:
        if args.count('-vf') != 1:
            raise ValueError('ambiguous video filter')
        index = args.index('-vf')
        post_filter = args[index + 1]
        # Only the existing CC surface. Do not reinterpret arbitrary graphs.
        if not re.fullmatch(r'docichcc=socket=/[A-Za-z0-9_./@+-]+', post_filter):
            raise ValueError('unsupported video filter')
        del args[index:index + 2]
    start = args.index('-map')
    args[start:start] = ffmpeg_input_args(fd, width, height, fps, sync_to=0)
    start = args.index('-map')
    args[start + 1] = '[twica_out]'
    graph = ffmpeg_overlay_filter(0, 2)
    if post_filter:
        graph += ',' + post_filter
    graph += '[twica_out]'
    args[start:start] = ['-filter_complex_threads', '1', '-filter_complex', graph]
    return args


class OwnedReader(SnapshotReader):
    def __init__(self, directory, *args, **kwargs):
        super().__init__(frame_directory(), *args, **kwargs)
        self.directory = directory

    def read(self, now_ns=None):
        if control(self.directory)['owner'] != 'common':
            self.state = 'inactive'
            return self.transparent
        return super().read(now_ns)


def run(real: str, argv: list[str], directory: Path) -> int:
    geometry = encoding_geometry(argv)
    if geometry is None:
        os.execv(real, [real, *argv])
    width, height, fps = geometry
    # One compositor may consume this stream directory; recording tests use
    # their own directory instead of taking the live stream's lease.
    with exclusive(directory, 'pipeline.lock'):
        with OverlayPipe(OwnedReader(directory, width, height), fps) as feed:
            command = [real, *compose_args(argv, feed.read_fd, geometry)]
            child = subprocess.Popen(command, pass_fds=(feed.read_fd,))
            previous = {}
            stop = threading.Event()
            def forward(signum, _frame):
                if child.poll() is None:
                    child.send_signal(signum)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                previous[sig] = signal.signal(sig, forward)
            def report():
                while not stop.wait(0.4):
                    try:
                        heartbeat(directory, 'pipeline.json', ready=feed.frames_sent >= 2 and child.poll() is None,
                                  width=width, height=height, fps=fps, frame_state=feed.state,
                                  encoder_pid=child.pid, frames_sent=feed.frames_sent)
                    except (OSError, ValueError):
                        pass  # diagnostics IO cannot stop the encoder
            reporter = None
            try:
                feed.start()
                reporter = threading.Thread(target=report, daemon=True, name='twica-pipeline-health')
                reporter.start()
                return_code = child.wait()
                return return_code if return_code >= 0 else 128 - return_code
            finally:
                stop.set()
                if reporter:
                    reporter.join(timeout=2)
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                try:
                    heartbeat(directory, 'pipeline.json', ready=False, frame_state='closed')
                except (OSError, ValueError):
                    pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    real = shutil.which(os.environ.get('DOCICH_TWICA_REAL_FFMPEG', 'ffmpeg'))
    if not real or Path(real).name == 'docich-twica-ffmpeg':
        print('twica common: real FFmpeg unavailable', file=sys.stderr)
        return 2
    try:
        return run(real, argv, state_directory())
    except Exception:
        # Never emit a command, output target, page URL, or raw exception.
        print('twica common: pipeline setup failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
