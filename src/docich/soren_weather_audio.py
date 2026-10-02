"""Narrow subprocess adapter for Soren's pinned weather-audio consumer.

The caller decides whether weather audio is enabled. This module talks only to
the existing shared comment queue and its receipt ledger; it never invokes a
TTS engine or starts a playback process itself.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Mapping

from .weather_audio import validate_weather_audio_request


class SharedWeatherAudioError(RuntimeError):
    """The existing shared consumer could not safely complete an operation."""


_ITEM_KEY_RE = re.compile(r"weather_corner:([0-9a-f-]{36}):(0[0-9]|1[0-2])\Z")
_TARGET_RE = re.compile(
    r"comment_announce_weather_audio_[0-9]{16,}_([0-9a-f-]{36})_"
    r"(0[0-9]|1[0-2])_weather_audio_item\.(txt|playing)\Z"
)


class SorenWeatherAudioPort:
    """Call only enqueue/get/interrupt operations on the source-pinned helper."""

    def __init__(
        self, soren_root: Path, state_dir: Path, *, queue_dir: Path | None = None,
        python: str | None = None, timeout_s: float = 5.0,
        interrupt_wait_s: float = 3.0, clock=time.time,
        monotonic=time.monotonic, sleep=time.sleep,
    ):
        if type(timeout_s) not in (int, float) or timeout_s <= 0:
            raise ValueError("weather consumer timeout must be positive")
        if type(interrupt_wait_s) not in (int, float) or interrupt_wait_s <= 0:
            raise ValueError("weather interrupt wait must be positive")
        self.soren_root = Path(soren_root)
        self.helper_path = self.soren_root / "lib" / "weather_audio_consumer.py"
        self.runtime_context_path = Path(state_dir) / "game_switch.json"
        self._queue_dir = Path(queue_dir) if queue_dir is not None else None
        self.python = python or sys.executable
        self.timeout_s = float(timeout_s)
        self.interrupt_wait_s = float(interrupt_wait_s)
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep

    def _shared_queue(self) -> Path:
        if self._queue_dir is None:
            # Use the same existing resolver as Soren's Web UI. It reads only
            # the queue path setting; no environment values are logged.
            from .webui import _comment_queue_dir

            self._queue_dir = _comment_queue_dir(self.soren_root)
        # Make relative paths independent of docich's working directory while
        # leaving symlinks visible to the consumer's fail-closed path checks.
        return Path(os.path.abspath(self._queue_dir))

    def _run_json(self, operation: str, *args: str):
        queue = self._shared_queue()
        if not self.helper_path.is_file():
            raise SharedWeatherAudioError("pinned shared weather consumer is unavailable")
        env = os.environ.copy()
        env["SOREN_ACTIVE_GAME_CONTEXT_FILE"] = str(self.runtime_context_path)
        command = [
            self.python, str(self.helper_path), operation,
            "--queue-dir", str(queue), *args,
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=self.timeout_s,
                check=False, env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SharedWeatherAudioError("shared weather consumer operation failed") from exc
        if result.returncode != 0:
            raise SharedWeatherAudioError("shared weather consumer rejected the operation")
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise SharedWeatherAudioError("shared weather consumer returned invalid JSON") from exc

    def enqueue_weather_audio(self, request: Mapping[str, object]) -> Mapping[str, object]:
        item = validate_weather_audio_request(request, now=self.clock())
        raw = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        result = self._run_json("enqueue", raw)
        if not isinstance(result, dict):
            raise SharedWeatherAudioError("shared weather consumer returned no receipt")
        return result

    def get_weather_audio_receipt(self, item_key: str) -> Mapping[str, object] | None:
        if not isinstance(item_key, str) or _ITEM_KEY_RE.fullmatch(item_key) is None:
            raise SharedWeatherAudioError("weather item key is invalid")
        result = self._run_json("get", item_key)
        if result is not None and not isinstance(result, dict):
            raise SharedWeatherAudioError("shared weather consumer returned invalid receipt")
        return result

    def _target_for_key(self, item_key: str) -> Path | None:
        match = _ITEM_KEY_RE.fullmatch(item_key)
        if match is None:
            raise SharedWeatherAudioError("weather item key is invalid")
        execution_id, index = match.groups()
        queue = self._shared_queue()
        pattern = f"comment_announce_weather_audio_*_{execution_id}_{index}_weather_audio_item.*"
        try:
            candidates = [path for path in queue.glob(pattern)
                          if _TARGET_RE.fullmatch(path.name)
                          and _TARGET_RE.fullmatch(path.name).groups()[:2] == (execution_id, index)]
        except OSError as exc:
            raise SharedWeatherAudioError("shared weather item cannot be located") from exc
        if not candidates:
            return None
        if len(candidates) != 1 or candidates[0].is_symlink() or not candidates[0].is_file():
            raise SharedWeatherAudioError("shared weather item location is ambiguous")
        return candidates[0]

    def _get_quiescence(self, item_key: str) -> dict[str, object]:
        result = self._run_json("quiescence", item_key)
        if (not isinstance(result, dict)
                or type(result.get("schema_version")) is not int
                or result.get("schema_version") != 1
                or result.get("item_key") != item_key
                or type(result.get("quiescent")) is not bool):
            raise SharedWeatherAudioError("shared consumer returned invalid quiescence state")
        receipt = result.get("receipt")
        if receipt is not None and (
            not isinstance(receipt, dict) or receipt.get("item_key") != item_key
        ):
            raise SharedWeatherAudioError("shared consumer quiescence receipt is invalid")
        return result

    def interrupt_weather_audio(self, item_key: str) -> Mapping[str, object] | None:
        """Cancel one item and require the consumer's durable player-stop acknowledgement."""
        receipt = self.get_weather_audio_receipt(item_key)
        if receipt is not None and receipt.get("status") == "queued":
            target = self._target_for_key(item_key)
            if target is None:
                receipt = self.get_weather_audio_receipt(item_key)
                if receipt is None or receipt.get("status") == "queued":
                    raise SharedWeatherAudioError("queued weather item has no cancellable consumer target")
            else:
                try:
                    result = self._run_json("interrupt", str(target))
                except SharedWeatherAudioError:
                    # The consumer may have persisted interruption before its
                    # response was lost. The durable quiescence query below is
                    # authoritative and is safe to repeat after owner restart.
                    pass
                else:
                    if not isinstance(result, dict) or result.get("item_key") != item_key:
                        raise SharedWeatherAudioError("shared weather consumer returned an invalid interrupt receipt")

        deadline = self.monotonic() + self.interrupt_wait_s
        while True:
            state = self._get_quiescence(item_key)
            current = state["receipt"]
            if state["quiescent"]:
                return current
            if current is not None and current.get("status") == "queued":
                target = self._target_for_key(item_key)
                if target is None:
                    raise SharedWeatherAudioError("queued weather item has no cancellable consumer target")
                try:
                    self._run_json("interrupt", str(target))
                except SharedWeatherAudioError:
                    pass
            if self.monotonic() >= deadline:
                raise SharedWeatherAudioError("consumer has not confirmed owned-player quiescence")
            self.sleep(min(0.05, max(0.0, deadline - self.monotonic())))
