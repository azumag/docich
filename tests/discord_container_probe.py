"""Synthetic-only integration probe, never COPY into the runtime image."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS

from docich import discord_chat as chat
from docich.discord_memory import MemoryStore, MemoryStoreError

DIRECTORY = Path('/var/lib/docich-discord')


def message(mid=100, text='synthetic-conversation-canary', mentions=True, author=7, channel=10):
    async def reply(text, **kwargs):
        assert kwargs['mention_author'] is False
        assert kwargs['allowed_mentions'].to_dict() == {'parse': []}
        return NS(id=mid + 1000)
    return NS(id=mid, guild=NS(id=1), channel=NS(id=channel),
              author=NS(id=author, bot=False, display_name='synthetic'), webhook_id=None,
              is_system=lambda: False, reference=None, mentions=[NS(id=99)] if mentions else [],
              created_at=datetime.now(timezone.utc), content='<@99> ' + text, reply=reply)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert data['model'] == 'synthetic-model'
        assert data['messages'][0]['content'].startswith(chat.load_persona())
        if self.server.slow:
            (DIRECTORY / 'http-started').write_text('1')
            os.chmod(DIRECTORY / 'http-started', 0o600)
            time.sleep(3)
            (DIRECTORY / 'http-finished').write_text('1')
            os.chmod(DIRECTORY / 'http-finished', 0o600)
        if self.server.recall:
            assert 'synthetic-conversation-canary' in json.dumps(data['messages'][:-1])
        raw = json.dumps({'choices': [{'message': {'content': 'synthetic-reply'}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


async def probe(phase):
    os.umask(0o077)
    assert os.getuid() == os.getgid() == 65532
    assert sqlite3.sqlite_version_info >= (3, 35)
    status = Path('/proc/self/status').read_text()
    assert 'NoNewPrivs:\t1' in status and 'Seccomp:\t2' in status
    assert 'CapEff:\t0000000000000000' in status
    try:
        Path('/opt/docich/forbidden').write_text('x')
    except OSError:
        pass
    else:
        raise AssertionError('rootfs writable')
    if phase == 'lock':
        try:
            MemoryStore(DIRECTORY)
        except MemoryStoreError:
            print('double-start rejected', flush=True)
            return
        raise AssertionError('double-start accepted')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.slow = phase == 'shutdown'
    server.recall = phase == 'recall'
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    env = dict(os.environ)
    env.update(DOCICH_DISCORD_LLM_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
               DOCICH_DISCORD_LLM_MODEL='synthetic-model')
    settings = chat.Settings.from_env(env)  # Actually read Compose-mounted secret as nonroot.
    client = chat.make_client(settings)
    client._connection.user = NS(id=99)
    try:
        if phase in {'hold', 'shutdown'}:
            async def start(token):
                assert token == settings.token
                if phase == 'shutdown':
                    asyncio.create_task(client.on_message(message(300)))
                    while not (DIRECTORY / 'http-started').exists():
                        await asyncio.sleep(.01)
                    asyncio.create_task(client.on_message(message(301)))
                print('probe ready', flush=True)
                await asyncio.Event().wait()
            client.start = start
            await chat.run_client(client, settings.token)
            if phase == 'shutdown':
                assert (DIRECTORY / 'http-finished').exists()
            print('shutdown joined HTTP and closed SQLite', flush=True)
        elif phase == 'seed':
            await client.on_message(message(90, mentions=False))
            await client.on_message(message())
        elif phase == 'recall':
            await client.on_message(message(101, 'remember synthetic-conversation-canary'))
        elif phase == 'verify-shutdown':
            db = sqlite3.connect(DIRECTORY / 'conversations.sqlite3')
            assert db.execute("SELECT count(*) FROM conversations WHERE state IN ('pending','sending')").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM conversations WHERE message_id IN ('300','301') AND state='failed' AND content='' AND reply=''").fetchone()[0] == 2
            db.close()
        elif phase == 'delete':
            server.recall = False
            await client.on_message(message(102, '記憶を削除'))
        else:
            raise AssertionError('unknown phase')
    finally:
        await client.close()
        server.shutdown()
        server.server_close()
        worker.join()
    store = MemoryStore(DIRECTORY)
    try:
        count = store.db.execute("SELECT count(*) FROM conversations WHERE state='sent'").fetchone()[0]
        assert count == {'seed': 1, 'recall': 2, 'delete': 0}.get(phase, count)
        if phase in {'seed', 'recall'}:
            assert not store.context(chat.Incoming(999, 1, 10, 8, '', '', True), 999).messages
            assert not store.context(chat.Incoming(999, 1, 11, 7, '', '', True), 999).messages
    finally:
        store.close()
    for path in DIRECTORY.iterdir():
        assert path.stat().st_uid == 65532 and path.stat().st_mode & 0o077 == 0
    assert DIRECTORY.stat().st_mode & 0o777 == 0o700
    print('probe passed: ' + phase, flush=True)


asyncio.run(probe(sys.argv[1]))
