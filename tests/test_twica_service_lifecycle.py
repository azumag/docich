"""Lifecycle regressions independent of the optional browser/network fixture."""
import asyncio
from dataclasses import replace
import time
from uuid import uuid4
from docich.twica_config import load_common_config
from docich.twica_overlay import private_directory
from docich.twica_state import heartbeat, identity, owner, set_owner, write_json
import docich.twica_renderer_service as service


def test_pending_new_game_ack_does_not_restart_active_common_page(tmp_path, monkeypatch):
    monkeypatch.setenv('SOREN_DIRECT_TWICA_OVERLAY_URL', 'https://example.test/overlay/fixture')
    cfg = load_common_config(tmp_path, {'DOCICH_TWICA_COMMON_ENABLED': '1',
        'DOCICH_TWICA_FRAME_DIR': str(tmp_path / 'frames')})
    cfg = replace(cfg, proxy_ports=())
    calls = []
    async def preflight(*args):
        pass
    async def renderer(*args, stop, **kwargs):
        calls.append('open')
        await stop.wait()
        calls.append('close')
    monkeypatch.setattr(service, 'preflight', preflight)
    monkeypatch.setattr(service, 'run_renderer', renderer)
    monkeypatch.setattr(service, 'proxy_inventory', lambda ports: True)
    async def scenario():
        stop = asyncio.Event()
        heartbeat(cfg.state, 'compositor', state='running', frames_sent=10)
        set_owner(cfg.state, 'common')
        task = asyncio.create_task(service.serve(cfg, width=120, height=72, stop=stop))
        try:
            for _ in range(20):
                if calls:
                    break
                await asyncio.sleep(.05)
            assert calls == ['open']
            path = private_directory(cfg.state / 'consumers') / f'{uuid4().hex}.json'
            # New guarded game registers before its first browser ACK. That is
            # not evidence of a live old subscriber and must not reset the queue.
            write_json(path, {'protocol': 1, **identity(), 'updated_ns': time.monotonic_ns(),
                'generation': '0' * 32, 'state': 'pending', 'frames': 0})
            await asyncio.sleep(.6)
            assert calls == ['open']
            write_json(path, {'protocol': 1, **identity(), 'updated_ns': time.monotonic_ns(),
                'generation': owner(cfg.state)['generation'], 'state': 'retired', 'frames': 0})
            await asyncio.sleep(.3)
            assert calls == ['open']
        finally:
            stop.set()
            await asyncio.wait_for(task, 3)
        assert calls == ['open', 'close']
    asyncio.run(scenario())
