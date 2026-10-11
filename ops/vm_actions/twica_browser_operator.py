#!/usr/bin/env python3
"""Fixed renderer-only refresh and passive browser evidence categories.

The workflow pins deployed reviewed main. No URL, event body, checkpoint,
credential or raw browser data is printed. No encoder/game service is touched.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def classify(record: dict, mode: str) -> int:
    if mode == 'errors':
        count = record.get('script_errors')
        return 40 if type(count) is int and count > 0 else 0 if count == 0 else 59
    if mode == 'dom':
        if record.get('dom_state') != 'observed':
            return 30
        if record.get('card_visible') is True:
            return 0
        if record.get('card_present') is True:
            return 31
        count = record.get('cards_observed')
        return 32 if type(count) is int and count > 0 else 33
    if mode != 'transport':
        return 59
    if record.get('page_state') != 'overlay':
        return 10
    bad = {'denied': 11, 'rate_limited': 12, 'server_error': 13,
           'http_error': 14, 'network_error': 15}
    events = record.get('events_state')
    if events in bad:
        return bad[events]
    socket = record.get('socket_state')
    if events == 'ok':
        return 0 if socket == 'receiving' else 20
    if socket == 'receiving':
        return 21
    config = record.get('config_state')
    if config in bad:
        return bad[config]
    return 22 if config == 'ok' else 23


def evidence(mode: str) -> int:
    from docich.twica_state import control, fresh, read_json, state_directory
    directory = state_directory()
    owner = control(directory)
    renderer = read_json(directory / 'renderer.json')
    record = read_json(directory / 'browser-health.json')
    if (owner.get('owner') != 'common' or owner.get('invalid')
            or not fresh(renderer) or not fresh(record)
            or record.get('pid') != renderer.get('pid')
            or record.get('identity') != renderer.get('identity')
            or record.get('generation') != owner.get('generation')):
        return 50
    if control(directory) != owner:
        return 51
    return classify(record, mode)


def refresh() -> int:
    from docich.twica_state import control, fresh, read_json, state_directory
    directory = state_directory()
    owner = control(directory)
    before = read_json(directory / 'pipeline.json')
    if (owner.get('owner') != 'common' or owner.get('invalid')
            or not fresh(before) or before.get('ready') is not True
            or type(before.get('encoder_pid')) is not int or before['encoder_pid'] < 2):
        return 52
    spec = importlib.util.spec_from_file_location('twica_ops', ROOT / 'ops/vm_actions/twica_common.py')
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    # This existing operation only restarts docich-twica-common.service; it
    # never invokes arm/activate/enable and never writes the ownership policy.
    try:
        ops.user_bus_environment()
        ops.checked(['systemctl', '--user', 'restart', ops.UNIT])
        ops.wait_renderer_ready()
    except Exception:
        return 53
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        after = read_json(directory / 'pipeline.json')
        if (control(directory) != owner or after.get('identity') != before.get('identity')
                or after.get('encoder_pid') != before.get('encoder_pid')):
            return 54
        if not fresh(after) or after.get('ready') is not True:
            return 55
        if evidence('transport') not in (50, 51, 59):
            # Allow the app's initial config/poll/WS startup to settle without
            # classifying the first blank screenshot as successful delivery.
            time.sleep(10)
            after = read_json(directory / 'pipeline.json')
            if (control(directory) != owner or not fresh(after)
                    or after.get('encoder_pid') != before.get('encoder_pid')
                    or after.get('identity') != before.get('identity')):
                return 54
            return 0
        time.sleep(0.25)
    return 56


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in {'refresh', 'transport', 'dom', 'errors'}:
        return 59
    sys.path.insert(0, str(ROOT / 'src'))
    try:
        return refresh() if args[0] == 'refresh' else evidence(args[0])
    except Exception:
        return 59


if __name__ == '__main__':
    raise SystemExit(main())
