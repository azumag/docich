"""Single-encoder run/stop supervisor core (#3553, ADR 0007 stage C4).

Ported from soviet_now ``lib/direct_stream.py`` at 0971b36 (``RunOutcome``,
``_wait_poll``, ``_graceful_stop_ffmpeg``, ``_start_reconnect_monitor``,
``_start_audio_capture_monitor``, ``_run_ffmpeg_once``, ``_run_ffmpeg``,
``_stop``). Re-diff against the then-current soviet_now before the production
cutover (ADR 0007 stage E).

Differences from the legacy runner, all deliberate:

* Both locks are **mandatory**, taken in the legacy order (fixed host-wide
  lease first, then ``state_dir/direct_stream.lock``) and held across the whole
  reconnect loop, including the backoff sleeps, until the child is reaped.
  There is no flag: a host without the provisioned lock directory refuses to
  start (fail-closed), never starts an unfenced encoder.
* ``stop`` signals only a process whose command line is *this* entry point
  (``docich.stream_supervisor`` ... ``run``). A runner of the legacy
  ``direct_stream.py`` sharing the same state directory is refused.

Nothing here is started by any service yet (ADR 0007: B-D ship unwired).
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping, Sequence

from .stream_encoder_command import classify_ffmpeg_exit, parse_progress_lines
from .stream_external_probes import probe_audio_capture_state, reload_relay, twitch_channel_is_live
from .stream_host_checks import RuntimeCheckError
from .stream_instance_lock import acquire_nonblocking_encoder_lock, open_canonical_stream_encoder_lock
from .stream_metrics import SystemMetricsSampler
from .stream_monitor_policy import step_audio_capture_loss, step_twitch_offline_streak
from .stream_state_files import atomic_write_json, ensure_private_directory, pid_alive, read_stream_status

STREAM_RUNNER_CONTRACT_VERSION = 1
RECONNECT_STATE_FILE = "reconnect.json"
LEGACY_LOCK_FILE = "direct_stream.lock"
OWNER_ENTRY_MODULE = "docich.stream_supervisor"


class AlreadyRunningError(RuntimeError):
    """Raised when a single-instance lock is already held."""


@dataclass(frozen=True)
class RunOutcome:
    """Result of one FFmpeg publish run."""

    exit_code: int
    state: str
    started_at: int
    reconnect_reason: str | None = None
    ffmpeg_exit_code: int | None = None


def wait_poll(child, timeout: float, step: float = 0.25,
              *, sleep: Callable[[float], None] = time.sleep,
              monotonic: Callable[[], float] = time.monotonic) -> bool:
    """Poll until the child exits or the timeout elapses."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if child.poll() is not None:
            return True
        sleep(step)
    return False


def graceful_stop_ffmpeg(
    child,
    *,
    q_timeout_sec: float = 15.0,
    sigint_timeout_sec: float = 15.0,
    wait: Callable[..., bool] = wait_poll,
) -> threading.Thread:
    """Ask FFmpeg to stop via stdin ``q``, then SIGINT, then SIGKILL.

    ``q`` makes FFmpeg close its muxers and tear the RTMP session down cleanly
    (FCUnpublish / deleteStream). Signals are a timeout fallback and SIGKILL is
    the last resort, so an operator-requested end never skips the RTMP close
    path without a bound.
    """

    def stop() -> None:
        try:
            if child.stdin is not None:
                child.stdin.write("q\n")
                child.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        if wait(child, q_timeout_sec):
            return
        try:
            child.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return
        if wait(child, sigint_timeout_sec):
            return
        try:
            child.kill()
        except ProcessLookupError:
            pass

    thread = threading.Thread(target=stop, name="direct-stream-graceful-stop", daemon=True)
    thread.start()
    return thread


def start_reconnect_monitor(
    reconnect: Mapping[str, object],
    child,
    monitor_state: dict[str, object],
    *,
    probe: Callable[[str], bool | None] = twitch_channel_is_live,
    sleep: Callable[[float], None] = time.sleep,
    alive: Callable[[object], bool] = pid_alive,
    log: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Probe the public Twitch channel while FFmpeg runs; request a reconnect."""
    emit = log or (lambda text: print(text, file=sys.stderr))

    def monitor() -> None:
        while True:
            sleep(float(reconnect["probe_interval_sec"]))
            if monitor_state.get("stopping"):
                return
            if not alive(child.pid):
                return
            live = probe(str(reconnect["twitch_channel"]))
            if live is None:
                # Transient probe failure: never count toward an offline streak.
                continue
            monitor_state["last_live"] = live
            streak, restart = step_twitch_offline_streak(
                int(monitor_state.get("offline_streak", 0)), live, int(reconnect["offline_threshold"])
            )
            monitor_state["offline_streak"] = streak
            if live is True:
                continue
            emit(f"direct_stream: Twitch offline streak={streak}/{reconnect['offline_threshold']}")
            if restart:
                monitor_state["reconnect"] = True
                monitor_state["reconnect_reason"] = "twitch_offline"
                try:
                    child.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                return

    thread = threading.Thread(target=monitor, name="direct-stream-reconnect-monitor", daemon=True)
    thread.start()
    return thread


def start_audio_capture_monitor(
    config: Mapping[str, object],
    watchdog: Mapping[str, object],
    child,
    monitor_state: dict[str, object],
    *,
    probe: Callable[[str, int], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    alive: Callable[[object], bool] = pid_alive,
    log: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Restart FFmpeg when its capture stream is gone but Pulse is reachable."""
    emit = log or (lambda text: print(text, file=sys.stderr))
    probe_fn = probe or (lambda server, pid: probe_audio_capture_state(server, pid, base_env=os.environ))

    def publish(state: str, consecutive: int) -> None:
        monitor_state["audio_capture"] = {
            "status": state,
            "consecutive_unhealthy": consecutive,
            "checked_at": int(time.time()),
            "pulse_server": watchdog["pulse_server"],
            "source": config["pulse_source"],
        }

    def monitor() -> None:
        consecutive = 0
        seen_capture = False
        while True:
            sleep(float(watchdog["probe_interval_sec"]))
            if monitor_state.get("stopping") or monitor_state.get("reconnect"):
                return
            if not alive(child.pid):
                return
            state = probe_fn(str(watchdog["pulse_server"]), child.pid)
            consecutive, seen_capture, lost = step_audio_capture_loss(
                state, consecutive, seen_capture, int(watchdog["loss_threshold"])
            )
            publish(state, consecutive)
            if not lost:
                continue
            emit(f"direct_stream: pulse capture lost ({consecutive} checks) source={config['pulse_source']}")
            monitor_state["reconnect"] = True
            monitor_state["reconnect_reason"] = "pulse_capture_lost"
            try:
                child.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            return

    thread = threading.Thread(target=monitor, name="direct-stream-audio-capture-monitor", daemon=True)
    thread.start()
    return thread


def _status_path(config: Mapping[str, object]) -> Path:
    return Path(str(config["state_dir"])) / "status.json"


def run_ffmpeg_once(
    config: Mapping[str, object],
    public_config: Mapping[str, object],
    command: Sequence[str],
    *,
    mode: str,
    reconnect: Mapping[str, object] | None,
    captions_active: bool = False,
    audio_watchdog: Mapping[str, object] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    stop_timeouts: tuple[float, float] = (15.0, 15.0),
    popen: Callable[..., "subprocess.Popen[str]"] = subprocess.Popen,
    twitch_probe: Callable[[str], bool | None] = twitch_channel_is_live,
    audio_probe: Callable[[str, int], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RunOutcome:
    """Run FFmpeg once and return the outcome of this single publish session."""
    started_at = int(time.time())
    metrics_sampler = SystemMetricsSampler()
    status_file = _status_path(config)
    with Path(str(config["log_file"])).open("a", encoding="utf-8") as log_stream:
        child = popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_stream,
            text=True,
            bufsize=1,
            start_new_session=True,
            close_fds=True,
        )
        base_state: dict[str, object] = {
            "backend": "ffmpeg",
            "mode": mode,
            "running": True,
            "state": "running",
            "pid": os.getpid(),
            "ffmpeg_pid": child.pid,
            "started_at": started_at,
            "updated_at": started_at,
            "system_metrics": metrics_sampler.latest,
            "config": dict(public_config),
            "closed_captions": {
                "requested": config["closed_captions_enabled"],
                "active": captions_active,
            },
        }
        if reconnect is not None:
            base_state["reconnect"] = dict(reconnect)
        watchdog_active = audio_watchdog is not None and bool(audio_watchdog["enabled"]) and mode == "live"
        if watchdog_active:
            base_state["audio_capture"] = {
                "status": "starting",
                "consecutive_unhealthy": 0,
                "checked_at": started_at,
                "pulse_server": audio_watchdog["pulse_server"],  # type: ignore[index]
                "source": config["pulse_source"],
            }
        atomic_write_json(status_file, base_state)

        stopping = False
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
            "offline_streak": 0,
            "last_live": None,
        }

        def stop_child(signum: int, _frame: object) -> None:
            nonlocal stopping
            if stopping:
                return
            stopping = True
            monitor_state["stopping"] = True
            graceful_stop_ffmpeg(child, q_timeout_sec=stop_timeouts[0], sigint_timeout_sec=stop_timeouts[1])

        previous_term = signal.signal(signal.SIGTERM, stop_child)
        previous_int = signal.signal(signal.SIGINT, stop_child)
        monitor_thread: threading.Thread | None = None
        if (
            mode == "live"
            and reconnect is not None
            and reconnect["enabled"]
            and reconnect["twitch_channel"]
        ):
            monitor_thread = start_reconnect_monitor(
                reconnect, child, monitor_state, probe=twitch_probe, sleep=sleep
            )
        audio_thread: threading.Thread | None = None
        if watchdog_active:
            audio_thread = start_audio_capture_monitor(
                config, audio_watchdog, child, monitor_state, probe=audio_probe, sleep=sleep  # type: ignore[arg-type]
            )
        batch: list[str] = []
        latest: dict[str, object] = {}
        try:
            assert child.stdout is not None
            for line in child.stdout:
                batch.append(line)
                if line.startswith("progress="):
                    latest.update(parse_progress_lines(batch))
                    batch.clear()
                    metrics = metrics_sampler.sample(now_epoch=int(time.time()), now_monotonic=time.monotonic())
                    payload = {**base_state, **latest, "system_metrics": metrics, "updated_at": int(time.time())}
                    if "audio_capture" in monitor_state:
                        payload["audio_capture"] = monitor_state["audio_capture"]
                    atomic_write_json(status_file, payload)
            return_code = child.wait()
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
            if monitor_thread is not None:
                monitor_thread.join(timeout=5)
            if audio_thread is not None:
                audio_thread.join(timeout=5)

        if monitor_state.get("reconnect"):
            final_state = "reconnect"
            runner_exit_code = 0
        else:
            runner_exit_code, final_state = classify_ffmpeg_exit(return_code, stopping=stopping)
        final_payload = {
            **base_state,
            **latest,
            "system_metrics": metrics_sampler.latest,
            "running": False,
            "state": final_state,
            "exit_code": runner_exit_code,
            "ended_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        if stopping or monitor_state.get("reconnect"):
            final_payload["ffmpeg_exit_code"] = return_code
        if monitor_state.get("reconnect_reason"):
            final_payload["reconnect_reason"] = monitor_state["reconnect_reason"]
        if "audio_capture" in monitor_state:
            final_payload["audio_capture"] = monitor_state["audio_capture"]
        atomic_write_json(status_file, final_payload)
        return RunOutcome(
            exit_code=runner_exit_code,
            state=final_state,
            started_at=started_at,
            reconnect_reason=(
                str(monitor_state["reconnect_reason"]) if monitor_state.get("reconnect_reason") else None
            ),
            ffmpeg_exit_code=return_code,
        )


def record_reconnect(
    config: Mapping[str, object],
    *,
    reason: str,
    consecutive: int,
    offline_streak: int,
    backoff_sec: int,
    log: Callable[[str], None] | None = None,
) -> None:
    emit = log or (lambda text: print(text, file=sys.stderr))
    payload = {
        "reason": reason,
        "consecutive_restarts": consecutive,
        "offline_streak": offline_streak,
        "backoff_sec": backoff_sec,
        "reconnected_at": int(time.time()),
    }
    atomic_write_json(Path(str(config["state_dir"])) / RECONNECT_STATE_FILE, payload)
    emit(f"direct_stream: reconnect reason={reason} consecutive={consecutive} backoff={backoff_sec}s")


class EncoderLocks:
    """Both single-encoder locks, held until :meth:`close`.

    Order matches the legacy runner: the host-wide fixed lease first, then the
    ``state_dir`` lock. Either one being held elsewhere raises
    :class:`AlreadyRunningError` before anything is started. A missing or
    unsafe fixed-lock directory raises :class:`RuntimeCheckError` (fail-closed).
    """

    def __init__(self, state_dir: Path,
                 canonical_opener: Callable[[], object] = open_canonical_stream_encoder_lock) -> None:
        self._canonical = None
        self._legacy = None
        try:
            try:
                self._canonical = canonical_opener()
            except BlockingIOError as exc:
                raise AlreadyRunningError("direct stream is already running") from exc
            except OSError as exc:
                raise RuntimeCheckError("canonical stream encoder lock is unavailable") from exc
            self._legacy = (Path(state_dir) / LEGACY_LOCK_FILE).open("a+", encoding="utf-8")
            try:
                acquire_nonblocking_encoder_lock(self._legacy.fileno())
            except BlockingIOError as exc:
                raise AlreadyRunningError("direct stream is already running") from exc
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for held in (self._legacy, self._canonical):
            if held is not None:
                try:
                    held.close()
                except OSError:
                    pass
        self._legacy = self._canonical = None

    def __enter__(self) -> "EncoderLocks":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def run_supervised(
    config: Mapping[str, object],
    public_config: Mapping[str, object],
    command: Sequence[str],
    *,
    mode: str,
    captions_active: bool,
    reconnect: Mapping[str, object] | None,
    audio_watchdog: Mapping[str, object] | None,
    output_dir: Path | None = None,
    cwd: str | os.PathLike[str] | None = None,
    canonical_opener: Callable[[], object] = open_canonical_stream_encoder_lock,
    once: Callable[..., RunOutcome] = run_ffmpeg_once,
    reload: Callable[..., bool] = reload_relay,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    **once_kwargs: object,
) -> int:
    """Run (and in live mode keep reconnecting) the single encoder.

    The caller has already validated the host and built ``command``. The
    directories are created here, both locks are taken *before* any child
    starts and are released only after the last child has been reaped.
    """
    state_dir = Path(str(config["state_dir"]))
    ensure_private_directory(state_dir)
    ensure_private_directory(Path(str(config["log_file"])).parent)
    if output_dir is not None:
        ensure_private_directory(output_dir)

    with EncoderLocks(state_dir, canonical_opener):
        if mode != "live" or reconnect is None or not reconnect["enabled"]:
            return once(
                config, public_config, command, mode=mode, reconnect=None,
                captions_active=captions_active, audio_watchdog=audio_watchdog, cwd=cwd,
                sleep=sleep, **once_kwargs,  # type: ignore[arg-type]
            ).exit_code

        # Live supervise loop: reconnect on Twitch-side cuts and unexpected
        # FFmpeg exits, mirroring OBS's automatic reconnect behaviour.
        consecutive = 0
        stop_requested = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stop_requested
            stop_requested = True

        previous_term = signal.signal(signal.SIGTERM, request_stop)
        previous_int = signal.signal(signal.SIGINT, request_stop)
        try:
            while not stop_requested:
                outcome = once(
                    config, public_config, command, mode=mode, reconnect=reconnect,
                    captions_active=captions_active, audio_watchdog=audio_watchdog, cwd=cwd,
                    sleep=sleep, **once_kwargs,  # type: ignore[arg-type]
                )
                if stop_requested or outcome.state == "stopped":
                    return 0
                if outcome.state == "completed":
                    return outcome.exit_code

                reason = outcome.reconnect_reason or "ffmpeg_exit"
                consecutive += 1
                if consecutive > int(reconnect["max_consecutive_restarts"]):
                    record_reconnect(config, reason=reason, consecutive=consecutive,
                                     offline_streak=0, backoff_sec=0)
                    raise RuntimeCheckError(
                        f"direct stream reconnect limit reached ({reconnect['max_consecutive_restarts']})"
                    )
                backoff = int(reconnect["backoff_sec"]) * min(2 ** (consecutive - 1), 16)
                record_reconnect(config, reason=reason, consecutive=consecutive,
                                 offline_streak=0, backoff_sec=backoff)
                if reconnect["reload_relay"] and reason == "twitch_offline":
                    reload(cwd=None if cwd is None else str(cwd))
                deadline = monotonic() + backoff
                while not stop_requested and monotonic() < deadline:
                    sleep(0.5)
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)


def _default_read_cmdline(pid: int) -> bytes:
    return Path(f"/proc/{pid}/cmdline").read_bytes()


def is_owned_runner_cmdline(raw: bytes) -> bool:
    """True only for ``... -m docich.stream_supervisor [--root X] run``."""
    parts = [p for p in raw.split(b"\x00") if p]
    if OWNER_ENTRY_MODULE.encode() not in parts:
        return False
    after = parts[parts.index(OWNER_ENTRY_MODULE.encode()) + 1:]
    index = 0
    while index < len(after):
        if after[index] == b"--root":
            index += 2
            continue
        break
    return index < len(after) and after[index] == b"run"


def stop_runner(
    config: Mapping[str, object],
    *,
    read_cmdline: Callable[[int], bytes] = _default_read_cmdline,
    kill: Callable[[int, int], None] = os.kill,
    alive: Callable[[object], bool] = pid_alive,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    timeout_sec: float = 15.0,
) -> int:
    """Stop *this* supervisor's runner. Refuses any other process.

    The status file is shared with the legacy runner's state directory, so the
    recorded PID is only signalled when its command line proves it is a
    ``docich.stream_supervisor ... run`` process.
    """
    status = read_stream_status(_status_path(config), alive=alive)
    pid = status.get("pid")
    if not alive(pid):
        return 0
    pid_int = int(pid)  # type: ignore[arg-type]
    try:
        command = read_cmdline(pid_int)
    except OSError:
        return 0
    if not is_owned_runner_cmdline(command):
        raise RuntimeCheckError("refusing to stop a PID not owned by docich.stream_supervisor")
    kill(pid_int, signal.SIGTERM)
    deadline = monotonic() + timeout_sec
    while monotonic() < deadline:
        if not alive(pid_int):
            return 0
        sleep(0.2)
    raise RuntimeCheckError(f"direct stream did not stop within {int(timeout_sec)} seconds")
