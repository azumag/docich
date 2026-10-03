"""Offline volume backup/restore. Run with network none and Bot stopped.

stdout is binary SQLite on backup. Never print database contents or paths.
"""
from pathlib import Path
import os
import shutil
import sqlite3
import sys
import tempfile

from docich.discord_memory import APPLICATION_ID, MemoryStore, MemoryStoreError


def main():
    directory = Path('/var/lib/docich-discord')
    os.umask(0o077)
    try:
        action = sys.argv[1]
        if action not in {'backup', 'restore'}:
            raise ValueError()
        # Restore is restricted to a fresh dedicated volume; never overwrite.
        if action == 'restore' and any(directory.iterdir()):
            raise ValueError()
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / 'snapshot.sqlite3'
            if action == 'backup':
                if not (directory / 'conversations.sqlite3').is_file():
                    raise ValueError()
                store = MemoryStore(directory)
                try:
                    with sqlite3.connect(snapshot) as target:
                        store.db.backup(target)
                finally:
                    store.close()
                with snapshot.open('rb') as source:
                    shutil.copyfileobj(source, sys.stdout.buffer)
            else:
                with snapshot.open('xb') as target:
                    shutil.copyfileobj(sys.stdin.buffer, target)
                with sqlite3.connect(f'file:{snapshot}?mode=ro', uri=True) as db:
                    if (db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                            or db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                            or db.execute('PRAGMA user_version').fetchone()[0] != 1):
                        raise ValueError()
                # Lock the fresh volume, then copy; no recursive ownership edits.
                store = MemoryStore(directory)
                try:
                    with sqlite3.connect(snapshot) as source:
                        source.backup(store.db)
                finally:
                    store.close()
                print('Discord memory restored; no content displayed.')
    except (OSError, sqlite3.Error, MemoryStoreError, ValueError, IndexError):
        print('Discord memory maintenance failed; inspect configuration privately.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
