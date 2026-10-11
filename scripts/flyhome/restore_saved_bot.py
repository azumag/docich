"""Restore saved real-game configuration without starting play or sending keys."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify files only; write nothing')
    parser.add_argument('--destination', type=Path, default=Path('run/flyhome'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest = root / 'docs/games/flyhome-20261011/manifest.json'
    entries = json.loads(manifest.read_text(encoding='utf-8'))['files']
    for entry in entries:
        path = root / entry['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise SystemExit(f'Saved file differs: {entry["path"]}')
    print(f'Archive verified: {len(entries)} files')
    if args.check:
        return
    source = root / 'config/windows/flyhome-saved'
    destination = args.destination.resolve()
    pairs = [(p, destination / p.relative_to(source))
             for p in source.rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and p.suffix != '.pyc']
    for original, target in pairs:
        if target.exists() and original.read_bytes() != target.read_bytes():
            raise SystemExit(f'Existing calibration differs; no files copied: {target}')
    for original, target in pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    print(f'Restored {len(pairs)} calibration/profile files to {destination}')
    print('No game input sent; campaign and supervisor were not started.')


if __name__ == '__main__':
    main()
