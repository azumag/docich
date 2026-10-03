"""Persistent, source-backed Discord conversations. No embeddings or model calls.

All methods run on the event-loop thread. A private POSIX directory and a
lifetime flock prevent sharing one store between independently running bots.
Only successfully delivered conversations can be recalled.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata

APPLICATION_ID = 0x44494348
RECENT_LIMIT = 6
RECALL_LIMIT = 4


class MemoryStoreError(RuntimeError):
    """Sanitized storage failure; do not attach SQLite errors or paths."""


def search_terms(text: str, limit: int = 256) -> tuple[str, ...]:
    """Word tokens and CJK bigrams work without optional SQLite extensions."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms: set[str] = set()
    for word in re.findall(r"[a-z0-9_]{2,}|[\u3040-\u30ff\u3400-\u9fff]+", normalized):
        if re.fullmatch(r"[a-z0-9_]+", word):
            terms.add(word)
        else:
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
    return tuple(sorted(terms)[:limit])


def _private_file(path: Path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise MemoryStoreError("memory storage requires private owned files")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(frozen=True)
class MemoryContext:
    messages: list[dict[str, str]]
    sources: tuple[int, ...]


class MemoryStore:
    def __init__(self, directory: Path | None):
        """None is an explicit, test-only in-memory store, never a fallback."""
        self._lock_fd: int | None = None
        self.db: sqlite3.Connection | None = None
        try:
            filename = ":memory:"
            if directory is not None:
                directory = Path(directory)
                if not directory.is_absolute() or directory.is_symlink():
                    raise MemoryStoreError("memory directory must be an absolute non-symlink path")
                directory.mkdir(mode=0o700, exist_ok=True)
                info = directory.stat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077):
                    raise MemoryStoreError("memory directory requires owner-only permissions")
                self._lock_fd = _private_file(directory / ".discord-chat.lock")
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                path = directory / "conversations.sqlite3"
                descriptor = _private_file(path)
                os.close(descriptor)
                filename = str(path)
            self.db = sqlite3.connect(filename, timeout=3)
            self.db.row_factory = sqlite3.Row
            app = self.db.execute("PRAGMA application_id").fetchone()[0]
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not ((app == APPLICATION_ID and version == 1) or (app == version == 0 and not tables)):
                raise MemoryStoreError("unsupported memory database")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.execute("PRAGMA temp_store=MEMORY")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    author_id TEXT NOT NULL, author_name TEXT NOT NULL,
                    content TEXT NOT NULL, reference_id TEXT, created_at REAL NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','failed','deleted')),
                    reply_id TEXT UNIQUE, reply TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS scope_recent ON conversations
                    (guild_id, channel_id, author_id, state, seq DESC);
                CREATE TABLE IF NOT EXISTS terms (
                    term TEXT NOT NULL, seq INTEGER NOT NULL REFERENCES conversations(seq),
                    PRIMARY KEY(term, seq)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS terms_source ON terms(seq);
            """)
            self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.db.execute("PRAGMA user_version=1")
            self.db.commit()
        except Exception:
            self.close()
            raise MemoryStoreError("memory storage unavailable") from None

    @contextmanager
    def _transaction(self):
        try:
            if self.db is None:
                raise MemoryStoreError("memory storage is closed")
            with self.db:
                yield self.db
        except Exception:
            raise MemoryStoreError("memory operation failed") from None

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def begin(self, event, created_at: float) -> int | None:
        with self._transaction() as db:
            # Conflict handling targets message_id only, not arbitrary DB errors.
            row = db.execute("""INSERT INTO conversations
                (message_id,guild_id,channel_id,author_id,author_name,content,reference_id,created_at,state)
                VALUES (?,?,?,?,?,?,?,?,'pending') ON CONFLICT(message_id) DO NOTHING RETURNING seq""",
                (str(event.id), str(event.guild_id), str(event.channel_id), str(event.author_id),
                 event.author_name[:80], event.content[:2000],
                 str(event.reference_id) if event.reference_id else None, created_at)).fetchone()
            return row[0] if row else None

    def active(self, seq: int) -> bool:
        with self._transaction() as db:
            row = db.execute("SELECT state FROM conversations WHERE seq=?", (seq,)).fetchone()
            return row is not None and row[0] == "pending"

    def sending(self, seq: int) -> bool:
        with self._transaction() as db:
            return bool(db.execute("UPDATE conversations SET state='sending' WHERE seq=? AND state='pending'",
                                   (seq,)).rowcount)

    def finish(self, seq: int, reply_id: int, reply: str) -> None:
        with self._transaction() as db:
            changed = db.execute("""UPDATE conversations SET state='sent',reply_id=?,reply=?
                WHERE seq=? AND state='sending'""", (str(reply_id), reply, seq)).rowcount
            if changed:
                content = db.execute("SELECT content FROM conversations WHERE seq=?", (seq,)).fetchone()[0]
                db.executemany("INSERT INTO terms(term,seq) VALUES (?,?)",
                               ((term, seq) for term in search_terms(content)))

    def fail(self, seq: int) -> None:
        with self._transaction() as db:
            db.execute("""UPDATE conversations SET state='failed',content='',author_name='',reply=''
                WHERE seq=? AND state IN ('pending','sending')""", (seq,))

    def context(self, event, before_seq: int) -> MemoryContext:
        """Recall only this speaker's completed conversations in this channel."""
        scope = (str(event.guild_id), str(event.channel_id), str(event.author_id), before_seq)
        with self._transaction() as db:
            recent = db.execute("""SELECT * FROM conversations WHERE guild_id=? AND channel_id=?
                AND author_id=? AND seq<? AND state='sent' ORDER BY seq DESC LIMIT ?""",
                (*scope, RECENT_LIMIT)).fetchall()
            cutoff = min((row["seq"] for row in recent), default=before_seq)
            tokens = search_terms(event.content, 48)
            recalled = []
            if tokens:
                placeholders = ",".join("?" for _ in tokens)
                recalled = db.execute(f"""SELECT c.* FROM conversations c JOIN terms t ON t.seq=c.seq
                    WHERE c.guild_id=? AND c.channel_id=? AND c.author_id=? AND c.seq<?
                    AND c.state='sent' AND t.term IN ({placeholders}) GROUP BY c.seq
                    ORDER BY COUNT(*) DESC,c.seq DESC LIMIT ?""",
                    (*scope[:3], cutoff, *tokens, RECALL_LIMIT)).fetchall()
            messages = []
            rows = sorted([*recent, *recalled], key=lambda item: item["seq"])
            for row in rows:
                messages.append({"role": "user", "content": json.dumps({
                    "source": "stored_conversation", "message_id": row["message_id"],
                    "author_id": row["author_id"], "name": row["author_name"],
                    "created_at": row["created_at"], "text": row["content"],
                }, ensure_ascii=False)})
                messages.append({"role": "assistant", "content": row["reply"]})
            return MemoryContext(messages, tuple(row["seq"] for row in rows))

    def valid_context(self, current_seq: int, context: MemoryContext) -> bool:
        if not self.active(current_seq):
            return False
        if not context.sources:
            return True
        placeholders = ",".join("?" for _ in context.sources)
        with self._transaction() as db:
            count = db.execute(f"SELECT COUNT(*) FROM conversations WHERE state='sent' AND seq IN ({placeholders})",
                               context.sources).fetchone()[0]
            return count == len(context.sources)

    def forget(self, guild_id: int, channel_id: int, *, message_ids: set[int] | None = None,
               author_id: int | None = None) -> None:
        """Scrub source+dependent reply; keep content-free IDs as replay tombstones."""
        if author_id is None and not message_ids:
            return
        with self._transaction() as db:
            where = "guild_id=? AND channel_id=?"
            params: list = [str(guild_id), str(channel_id)]
            if author_id is not None:
                where += " AND author_id=?"
                params.append(str(author_id))
            else:
                ids = tuple(str(item) for item in message_ids)
                placeholders = ",".join("?" for _ in ids)
                where += f" AND (message_id IN ({placeholders}) OR reply_id IN ({placeholders}))"
                params.extend((*ids, *ids))
            db.execute(f"DELETE FROM terms WHERE seq IN (SELECT seq FROM conversations WHERE {where})", params)
            db.execute(f"""UPDATE conversations SET content='',author_name='',reply='',reference_id=NULL,
                state='deleted' WHERE {where}""", params)
