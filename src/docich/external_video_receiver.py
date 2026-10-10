"""Bounded, off-air SRT receiver for the explicitly started external corner.

One receiver owns the SRT socket; a loopback MPEG-TS relay lets the generation
owned viewer join without reconnecting OBS. No sender control or game input.
"""
from __future__ import annotations

import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager

from .game_switch import atomic_write_json

RECEIVER_FILE = "external_video_receiver.json"
SRT_PORT = 19194
RELAY_PORT = 19195
FRAME_MAX_AGE = 10.0
# systemd unit lifetime ceiling (25 h): the corner itself ends on the operator's End.
MAX_RECEIVER_MINUTES = 25 * 60


class ExternalVideoError(RuntimeError):
    pass


def receiver_id(value):
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise ExternalVideoError("invalid receiver identity")


def listen_ip(value):
    try:
        address = ipaddress.IPv4Address(value)
        if address in ipaddress.IPv4Network("100.64.0.0/10"):
            return str(address)
    except (ValueError, TypeError):
        pass
    raise ExternalVideoError("receiver must bind a Tailscale IPv4 address")


def directory(g, identity):
    return Path(g.state_dir) / "external-video" / receiver_id(identity)


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExternalVideoError("external video operation already running") from None
        yield


def read_json(path):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 32768:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (OSError, ValueError):
        raise ExternalVideoError("external video state is missing or invalid") from None


def process_ticks(pid):
    if type(pid) is not int or pid <= 1:
        return None
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def worker_alive(state):
    ticks = process_ticks(state.get("pid"))
    if ticks is None or ticks != state.get("start_ticks"):
        return False
    try:
        argv = (Path("/proc") / str(state["pid"]) / "cmdline").read_bytes().split(b"\0")
        return (b"external-video-corner" in argv and b"receive" in argv
                and state["receiver_id"].encode() in argv)
    except (OSError, KeyError):
        return False


def read_receiver(g, *, expected=None, fresh=False, now=None):
    state = read_json(Path(g.state_dir) / RECEIVER_FILE)
    identity = receiver_id(state.get("receiver_id"))
    if expected is not None and identity != receiver_id(expected):
        raise ExternalVideoError("receiver ownership changed")
    listen_ip(state.get("listen_ip"))
    expiry = state.get("expires_at")
    if (state.get("schema_version") != 1 or type(expiry) not in (int, float)
            or not math.isfinite(expiry)):
        raise ExternalVideoError("invalid receiver state")
    clock = time.time() if now is None else now
    frame = directory(g, identity) / "current.png"
    age = clock - frame.stat().st_mtime if frame.is_file() else None
    state = dict(state, alive=worker_alive(state), frame_age_sec=age)
    state["fresh"] = (state["alive"] and state.get("status") == "receiving"
                      and clock < expiry and age is not None and 0 <= age <= FRAME_MAX_AGE)
    if fresh and not state["fresh"]:
        raise ExternalVideoError("receiver has no fresh decoded video")
    return state


def ffmpeg_command(g, state):
    root = directory(g, state["receiver_id"])
    ip = listen_ip(state["listen_ip"])
    # Fixed options and destinations; credentials, arbitrary URLs and shell
    # fragments are never accepted. One-second decode evidence stays off-air.
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "2",
        "-i", f"srt://{ip}:{SRT_PORT}?mode=listener&latency=500000&timeout=10000000",
        "-map", "0:v:0", "-an", "-vf", "fps=1,scale=960:540:force_original_aspect_ratio=decrease",
        "-threads", "1", "-update", "1", "-atomic_writing", "1", "-y", str(root / "current.png"),
        "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac",
        "-af", "asetnsamples=n=44100,astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={root / 'audio-levels.log'}:direct=1",
        "-f", "mpegts", f"udp://127.0.0.1:{RELAY_PORT}?pkt_size=1316",
    ]


def prepare(g, ip, minutes=30, *, expected_receiver_id=None):
    ip = listen_ip(ip)
    if type(minutes) is not int or not 1 <= minutes <= MAX_RECEIVER_MINUTES:
        raise ExternalVideoError(f"receiver wait must be 1-{MAX_RECEIVER_MINUTES} minutes")
    with exclusive(Path(g.state_dir) / "external-video-prepare.lock"):
        corner_path = Path(g.state_dir) / "external_video_corner.json"
        if corner_path.exists() and read_json(corner_path).get("status") in {
                "starting", "active", "restoring", "failed"}:
            raise ExternalVideoError("existing corner must finish or recover first")
        path = Path(g.state_dir) / RECEIVER_FILE
        if expected_receiver_id is not None:
            # Recheck under the prepare lock; a queued owner may renew only
            # the reservation it observed, never a concurrently replaced one.
            read_receiver(g, expected=expected_receiver_id)
        if path.exists():
            old = read_receiver(g)
            if old["alive"] or (old.get("status") == "launching"
                                 and time.time() < old["expires_at"]):
                raise ExternalVideoError("receiver is already reserved")
        for address, port in ((ip, SRT_PORT), ("127.0.0.1", RELAY_PORT)):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.bind((address, port))
                except OSError:
                    raise ExternalVideoError("receiver or relay port is occupied") from None
        identity = str(uuid.uuid4())
        now = time.time()
        state = dict(schema_version=1, receiver_id=identity, listen_ip=ip,
                     status="launching", created_at=now, expires_at=now + minutes * 60)
        directory(g, identity).mkdir(parents=True)
        atomic_write_json(path, state)
        launcher = Path(__file__).resolve().parents[2] / "bin/docich"
        unit = "docich-external-video-" + identity
        command = ["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                   f"--property=RuntimeMaxSec={minutes * 60 + 15}", "--property=KillMode=control-group",
                   str(launcher), "--config", str(g.config_path), "external-video-corner",
                   "receive", "--receiver-id", identity]
        result = subprocess.run(command, capture_output=True, timeout=15)
        if result.returncode:
            state["status"] = "failed"
            atomic_write_json(path, state)
            raise ExternalVideoError("receiver unit could not start")
        return state


def request_receiver_stop(g, identity):
    state = read_receiver(g, expected=identity)
    if state["alive"]:
        atomic_write_json(directory(g, identity) / "stop.json", {"receiver_id": identity})


def receive(g, identity):
    identity = receiver_id(identity)
    root = directory(g, identity)
    path = Path(g.state_dir) / RECEIVER_FILE
    child = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with exclusive(Path(g.state_dir) / "external-video-receiver.lock"):
        state = read_receiver(g, expected=identity)
        if state["status"] != "launching" or time.time() >= state["expires_at"]:
            raise ExternalVideoError("receiver launch reservation is invalid")
        state.update(pid=os.getpid(), start_ticks=process_ticks(os.getpid()), status="waiting")
        atomic_write_json(path, state)
        try:
            with (root / "receiver.log").open("wb") as log:
                child = subprocess.Popen(ffmpeg_command(g, state), stdout=log, stderr=log,
                                         start_new_session=True)
                while child.poll() is None and time.time() < state["expires_at"] and not stopping:
                    flag = root / "stop.json"
                    if flag.exists() and read_json(flag).get("receiver_id") == identity:
                        break
                    frame = root / "current.png"
                    if frame.exists():
                        state.update(status="receiving", last_frame_at=frame.stat().st_mtime)
                    audio = root / "audio-levels.log"
                    state["audio_present"] = audio.is_file() and audio.stat().st_size > 0
                    if state["audio_present"]:
                        with audio.open("rb") as stream:
                            stream.seek(max(0, audio.stat().st_size - 1024))
                            for line in stream.read().decode(errors="replace").splitlines():
                                if line.startswith("lavfi.astats.Overall.RMS_level="):
                                    try:
                                        value = float(line.split("=", 1)[1])
                                        state["audio_rms_db"] = value if math.isfinite(value) else None
                                    except ValueError:
                                        pass
                    state["updated_at"] = time.time()
                    atomic_write_json(path, state)
                    time.sleep(0.5)
        finally:
            if child is not None and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            state.update(status="stopped", stopped_at=time.time())
            atomic_write_json(path, state)
    return 0
