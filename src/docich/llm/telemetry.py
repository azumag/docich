"""Secret-free, bounded telemetry for native dispatch attempts."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .contracts import AgentSpec


def record(
    directory: Path,
    *,
    event: str,
    label: str,
    spec: AgentSpec | None = None,
    returncode: int = 0,
    failure_kind: str = "",
    latency_ms: int = 0,
) -> None:
    """Write only fixed metadata; prompt, output, stderr and credentials never enter it."""

    if os.environ.get("DOCICH_LLM_TELEMETRY", "1") == "0":
        return
    if event not in {"attempt", "success", "failure", "skipped", "queue_giveup"}:
        return
    row = {
        "ts": int(time.time()),
        "event": event,
        "label": label,
        "agent": spec.raw if spec else "",
        "resolved_model": spec.resolved_model if spec else "",
        "rc": int(returncode),
        "failure_kind": failure_kind,
        "latency_ms": max(int(latency_ms), 0),
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{time.strftime('%Y%m%d')}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        # Metrics are diagnostic only; a read-only or full state directory must
        # never turn a successful provider response into a failed generation.
        return
