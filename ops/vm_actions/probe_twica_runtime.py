#!/usr/bin/env python3
"""Read-only common-overlay probe. Exit categories only; no raw data leaves VM.

No environment/checkpoint/page/credential reads, network requests, subprocesses,
state writes, locks, signals, service operations, or synthetic live events.
Pixel evidence is an input sample, NOT proof of viewer-visible output.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import struct
import sys
import time

ROOT = Path('/home/ubuntu/docich')
SOREN = Path('/home/ubuntu/soren')
HEADER = struct.Struct('<8sIIQ')
MAX_PIXELS = 3840 * 2160


def read_file(path: Path, limit: int, *, private: bool = True) -> bytes:
    if path.parent.resolve() != path.parent.absolute():
        raise ValueError('linked parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_size > limit
                or (private and info.st_mode & 0o077)):
            raise ValueError('unsafe file')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('oversized file')
        return data
    finally:
        os.close(fd)


def read_record(path: Path, *, private: bool = True) -> dict:
    try:
        value = json.loads(read_file(path, 65536, private=private))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def process(pid: int) -> dict:
    if type(pid) is not int or pid < 2:
        return {}
    try:
        directory = Path('/proc') / str(pid)
        if directory.stat().st_uid != os.geteuid():
            return {}
        # Only the exact heartbeat/runner PIDs are inspected. Never enumerate
        # processes or publish their arguments (which may contain destinations).
        with (directory / 'stat').open() as stream:
            raw = stream.read(8193)
        fields = raw[raw.rindex(')') + 2:].split()
        if len(raw) > 8192 or fields[0] == 'Z':
            return {}
        with (directory / 'cmdline').open('rb') as stream:
            command = stream.read(131073)
        if len(command) > 131072:
            return {}
        args = [os.fsdecode(arg) for arg in command.split(b'\0') if arg]
        return {'parent': int(fields[1]), 'ticks': fields[19], 'args': args,
                'cwd': directory.joinpath('cwd').resolve()}
    except (OSError, ValueError, IndexError):
        return {}


def fresh(record: dict, now_ns: int) -> bool:
    timestamp, pid = record.get('monotonic_ns'), record.get('pid')
    if type(timestamp) is not int or type(pid) is not int or record.get('schema') != 1:
        return False
    if not 0 <= now_ns - timestamp < 3_000_000_000:
        return False
    current = process(pid)
    if not current:
        return False
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return record.get('identity') == boot + ':' + current['ticks']


def gate(owner: dict, pipeline: dict, renderer: dict, now_ns: int) -> int | None:
    generation = owner.get('generation')
    if (owner.get('schema') != 1 or not isinstance(generation, str)
            or len(generation) != 32 or any(c not in '0123456789abcdef' for c in generation)):
        return 30
    if owner.get('owner') == 'legacy':
        return 31
    if owner.get('owner') != 'common':
        return 32
    if not fresh(pipeline, now_ns) or pipeline.get('ready') is not True:
        return 33
    if not fresh(renderer, now_ns):
        return 34
    if renderer.get('state') != 'active':
        return 35
    if renderer.get('generation') != generation:
        return 36
    return None


def descendant(pid: int, ancestor: int) -> bool:
    if type(pid) is not int or type(ancestor) is not int or min(pid, ancestor) < 2:
        return False
    for _ in range(24):
        if pid == ancestor:
            return bool(process(pid))
        pid = process(pid).get('parent', 0)
        if pid < 2:
            break
    return False


def native_gate(pipeline: dict, runner: dict) -> int | None:
    native = pipeline.get('encoder_pid')
    if not descendant(native, pipeline.get('pid')):
        return 37
    runner_pid = runner.get('pid')
    live_runner = process(runner_pid)
    if (live_runner.get('cwd') != SOREN or not any(
            Path(arg).name == 'direct_stream.py' for arg in live_runner.get('args', []))
            or not descendant(pipeline.get('pid'), runner_pid)):
        return 38
    args = process(native).get('args', [])
    graphs = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '-filter_complex']
    maps = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '-map']
    if (len(graphs) != 1 or '[0:v:0][2:v:0]overlay=x=round(main_w/3):y=0:' not in graphs[0]
            or '[twica_out]' not in maps):
        return 39
    return None


def classify_frame(packet: bytes, width: int, height: int, now_ns: int) -> int:
    if (type(width) is not int or type(height) is not int or min(width, height) < 1
            or width * height > MAX_PIXELS or len(packet) != HEADER.size + width * height * 4):
        return 42
    magic, w, h, captured = HEADER.unpack_from(packet)
    if magic != b'TWICARG1' or (w, h) != (width, height):
        return 42
    if captured <= 0 or not 0 <= now_ns - captured < 1_000_000_000:
        return 41
    alpha = packet[HEADER.size + 3::4]
    if not any(alpha):
        return 20  # Transparent at observation; an idle overlay is valid.
    if all(alpha):
        return 22  # Entire viewport has alpha, not a normal transparent idle.
    visible_width = width - int(width / 3 + 0.5)
    if any(any(alpha[y * width:y * width + visible_width]) for y in range(height)):
        return 0  # Pixels inside the compositor region, NOT final output proof.
    return 21  # All nontransparent pixels are clipped by the rightward shift.


def probe() -> int:
    directory = ROOT / 'run/twica-common'
    owner = read_record(directory / 'control.json')
    pipeline = read_record(directory / 'pipeline.json')
    renderer = read_record(directory / 'renderer.json')
    reason = gate(owner, pipeline, renderer, time.monotonic_ns())
    if reason is not None:
        return reason
    runner = read_record(SOREN / 'tmp/state/direct_stream/status.json', private=False)
    reason = native_gate(pipeline, runner)
    if reason is not None:
        return reason
    frame = Path('/dev/shm') / ('docich-twica-' + str(os.geteuid())) / 'frame.rgba'
    observed = set()
    # Eight bounded samples are enough to distinguish a missing producer from
    # an input frame. No event is triggered; no card/audio is recorded or sent.
    for _ in range(8):
        try:
            packet = read_file(frame, HEADER.size + MAX_PIXELS * 4)
            observed.add(classify_frame(packet, pipeline.get('width'), pipeline.get('height'), time.monotonic_ns()))
        except FileNotFoundError:
            observed.add(40)
        except (OSError, ValueError):
            observed.add(42)
        time.sleep(0.25)
    after = read_record(directory / 'pipeline.json')
    if (read_record(directory / 'control.json') != owner
            or after.get('identity') != pipeline.get('identity')
            or after.get('encoder_pid') != pipeline.get('encoder_pid')
            or not fresh(after, time.monotonic_ns())):
        return 43
    for code in (0, 22, 21, 20, 41, 42, 40):
        if code in observed:
            return code
    return 59


def main() -> int:
    if len(sys.argv) != 1:
        return 59
    try:
        return probe()
    except Exception:
        return 59


if __name__ == '__main__':
    raise SystemExit(main())
