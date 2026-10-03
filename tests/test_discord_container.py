"""Additional secret-file and shutdown contracts, run inside the test image."""
import asyncio
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from docich import discord_chat as chat
from docich.discord_memory import MemoryStore
from test_discord_chat import ENV, ConversationTests, event, settings


class SecretTests(unittest.TestCase):
    def test_file_secrets_and_optional_key(self):
        with TemporaryDirectory() as tmp:
            token = Path(tmp) / 'token'
            token.write_text('synthetic-file-token\n')
            token.chmod(0o400)
            env = {**ENV, 'DOCICH_DISCORD_TOKEN': '', 'DOCICH_DISCORD_LLM_API_KEY': '',
                   'DOCICH_DISCORD_TOKEN_FILE': str(token)}
            s = chat.Settings.from_env(env)
            self.assertEqual(s.token, 'synthetic-file-token')
            self.assertEqual(s.api_key, '')
            env['DOCICH_DISCORD_LLM_API_KEY_FILE'] = str(token)
            self.assertEqual(chat.Settings.from_env(env).api_key, 'synthetic-file-token')
            self.assertNotIn('synthetic-file-token', repr(chat.Settings.from_env(env)))
            for name in ('TOKEN', 'LLM_API_KEY'):
                with self.assertRaisesRegex(chat.ChatError, '^secret value and secret file are mutually exclusive$'):
                    chat.Settings.from_env({**env, 'DOCICH_DISCORD_' + name: 'synthetic-env-token'})

    def test_bad_secrets_fail_without_path_or_content(self):
        with TemporaryDirectory() as tmp:
            secret = Path(tmp) / 'synthetic-private-path'
            env = {**ENV, 'DOCICH_DISCORD_TOKEN': '', 'DOCICH_DISCORD_TOKEN_FILE': str(secret)}
            for data in (b'', b'\xff', b'one\ntwo', b'a b', b'x' * 4097, b'bad\x00token'):
                secret.unlink(missing_ok=True)
                secret.write_bytes(data)
                secret.chmod(0o400)
                with self.assertRaisesRegex(chat.ChatError, '^secret file unavailable or invalid$'):
                    chat.Settings.from_env(env)
            secret.unlink()
            with self.assertRaisesRegex(chat.ChatError, '^secret file unavailable or invalid$'):
                chat.Settings.from_env(env)
            secret.write_text('synthetic-token')
            secret.chmod(0o000)
            with self.assertRaises(chat.ChatError):
                chat.Settings.from_env(env)
            secret.chmod(0o444)
            with self.assertRaises(chat.ChatError):
                chat.Settings.from_env(env)
            secret.unlink()
            secret.symlink_to(Path(tmp) / 'missing')
            with self.assertRaises(chat.ChatError):
                chat.Settings.from_env(env)
            secret.unlink()
            os.mkfifo(secret, 0o600)
            with self.assertRaises(chat.ChatError):
                chat.Settings.from_env(env)

    def test_completion_endpoint_is_not_a_base_url(self):
        with self.assertRaises(chat.ChatError):
            chat.Settings.from_env({**ENV, 'DOCICH_DISCORD_LLM_BASE_URL': 'https://example.invalid/v1/chat/completions'})


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    setUp = ConversationTests.setUp
    blocked_backend = ConversationTests.blocked_backend
    async def test_shutdown_cancels_queue_joins_http_and_scrubs_inputs(self):
        started, release, calls = self.blocked_backend()
        first = asyncio.create_task(self.core.handle(event(), self.send))
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(self.core.handle(event(id=101), self.send))
        await asyncio.sleep(0)
        closing = asyncio.create_task(self.core.close())
        try:
            await asyncio.sleep(0.01)
            self.assertFalse(closing.done())
            self.assertEqual(len(calls), 1)
            self.assertEqual(await self.core.handle(event(id=102), self.send), 'ignored')
        finally:
            release.set()
        await closing
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())
        self.send.assert_not_awaited()
        self.assertEqual([tuple(row) for row in self.memory.db.execute(
            'SELECT state,content,reply FROM conversations')], [('failed', '', ''), ('failed', '', '')])

    async def test_crash_reopen_scrubs_unfinished_records_preserves_ids(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory'
            store = MemoryStore(path)
            store.begin(event(), 0)
            seq = store.begin(event(id=101), 0)
            store.sending(seq)
            store.close()
            store = MemoryStore(path)
            try:
                self.assertEqual([tuple(row) for row in store.db.execute(
                    'SELECT state,content,reply FROM conversations')], [('failed', '', ''), ('failed', '', '')])
                self.assertIsNone(store.begin(event(), 0))
            finally:
                store.close()


class RuntimeTests(unittest.TestCase):
    def test_linux_sqlite_sdk_and_flock_available(self):
        import discord
        import fcntl
        self.assertGreaterEqual(sqlite3.sqlite_version_info, (3, 35))
        self.assertEqual(discord.__version__, '2.7.1')
        self.assertEqual(os.getuid(), 65532)
        self.assertEqual(os.getgid(), 65532)
        self.assertTrue(callable(fcntl.flock))
