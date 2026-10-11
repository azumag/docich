#!/usr/bin/env python3
"""One bounded, passive DOM/RGBA observation window; no page/service mutation.

Only fixed exit categories leave the VM. No event is synthesized, acknowledged,
replayed, or consumed; the live page and its accumulated evidence are retained.
"""
from __future__ import annotations

from pathlib import Path
import os
import sys
import time


class WindowEvidence:
    def __init__(self) -> None:
        self.card_seen = False
        self.pixels_seen = False
        self.clipped_seen = False
        self.missing_since: float | None = None
        self.long_gap = False
        self.reliable_samples = 0

    def sample(self, now: float, visible: bool | None, frame_code: int) -> None:
        if visible is None:
            self.missing_since = None
            return
        self.reliable_samples += 1
        if not visible:
            self.missing_since = None
            return
        self.card_seen = True
        if frame_code in (0, 22):
            self.pixels_seen = True
            self.missing_since = None
        elif frame_code == 21:
            self.clipped_seen = True
            self.missing_since = None
        else:
            if self.missing_since is None:
                self.missing_since = now
            # Health is sampled once a second. Ignore the trailing record at
            # an ordinary hide boundary rather than diagnosing it as a gap.
            if now - self.missing_since >= 2.0:
                self.long_gap = True

    def result(self) -> int:
        if not self.reliable_samples:
            return 50
        if self.long_gap:
            return 24  # DOM visible but input absent/transparent/stale >= 2s.
        if self.clipped_seen and not self.pixels_seen:
            return 21
        if self.pixels_seen:
            return 0  # Input pixels observed, NOT final encoded output proof.
        if self.card_seen:
            return 25  # A short DOM-only window needs longer evidence.
        return 26  # No visible card arrived in this bounded observation.


def observe() -> int:
    # Reuse the original private-file, process identity and compositor checks.
    import probe_twica_runtime as probe
    directory = probe.ROOT / 'run/twica-common'
    policy = probe.read_record(directory / 'control.json')
    pipeline = probe.read_record(directory / 'pipeline.json')
    renderer = probe.read_record(directory / 'renderer.json')
    reason = probe.gate(policy, pipeline, renderer, time.monotonic_ns())
    if reason is not None:
        return 50
    runner = probe.read_record(probe.SOREN / 'tmp/state/direct_stream/status.json', private=False)
    if probe.native_gate(pipeline, runner) is not None:
        return 50
    frame = Path('/dev/shm') / ('docich-twica-' + str(os.geteuid())) / 'frame.rgba'
    baseline_attempts = renderer.get('attempts', 0)
    previous_cards = None
    evidence = WindowEvidence()
    deadline = time.monotonic() + 90
    ended_since = None
    while time.monotonic() < deadline:
        current = probe.read_record(directory / 'pipeline.json')
        current_renderer = probe.read_record(directory / 'renderer.json')
        current_policy = probe.read_record(directory / 'control.json')
        health = probe.read_record(directory / 'browser-health.json')
        # A producer may publish while these files are read. Compare against
        # the clock AFTER reading, not an earlier sample-start timestamp.
        # Actual future timestamps remain invalid in the existing fresh().
        now_ns = time.monotonic_ns()
        if current_policy != policy:
            return 51  # Ownership policy changed or became unreadable.
        if current.get('identity') != pipeline.get('identity'):
            return 52  # Pipeline process changed or record became unreadable.
        if current.get('encoder_pid') != pipeline.get('encoder_pid'):
            return 53  # Native encoder changed or record became incomplete.
        if current_renderer.get('identity') != renderer.get('identity'):
            return 54  # Renderer process changed or record became unreadable.
        if not probe.fresh(current, now_ns):
            return 55  # Pipeline heartbeat is stale, invalid, or process dead.
        attempts = current_renderer.get('attempts', 0)
        if type(attempts) is int and type(baseline_attempts) is int and attempts > baseline_attempts:
            return 27  # The service had to replace its renderer during sampling.
        valid = (probe.fresh(health, now_ns) and probe.fresh(current_renderer, now_ns)
                 and health.get('identity') == current_renderer.get('identity')
                 and health.get('pid') == current_renderer.get('pid')
                 and health.get('generation') == policy.get('generation')
                 and health.get('dom_state') == 'observed'
                 and type(health.get('card_visible')) is bool)
        visible = health['card_visible'] if valid else None
        cards = health.get('cards_observed') if valid else None
        if type(cards) is int:
            if previous_cards is not None and cards < previous_cards:
                return 27  # The in-process browser restarted (same service PID).
            previous_cards = cards
        try:
            packet = probe.read_file(frame, probe.HEADER.size + probe.MAX_PIXELS * 4)
            frame_code = probe.classify_frame(packet, pipeline.get('width'), pipeline.get('height'), time.monotonic_ns())
        except (OSError, ValueError):
            frame_code = 42
        now = time.monotonic()
        evidence.sample(now, visible, frame_code)
        if evidence.card_seen and visible is False:
            if ended_since is None:
                ended_since = now
            elif now - ended_since >= 2:
                break
        else:
            ended_since = None
        time.sleep(0.25)
    return evidence.result()


def main() -> int:
    if len(sys.argv) != 1:
        return 59
    try:
        return observe()
    except Exception:
        return 59


if __name__ == '__main__':
    raise SystemExit(main())
