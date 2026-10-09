"""Apply the readable schema-validation follow-up to an exact source checkout."""
import hashlib
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
source = Path(sys.argv[1]).resolve()
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() == '4a8091444a652fb72958b5799d2a1c4e5b95219f'
patch = (here / 'reviewed-patches/04-schema.patch').read_bytes()
assert hashlib.sha256(patch).hexdigest() == '28a21e8d7a9ad7ada2583bc58e89c95d35599f87d03943cbfa7c45fab459af01'
subprocess.run(['git', 'apply', '--check', '--index', '-'], cwd=source, input=patch, check=True)
subprocess.run(['git', 'apply', '--index', '-'], cwd=source, input=patch, check=True)
paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=source, text=True).splitlines()
assert paths == ['src/docich/game_switch.py', 'tests/test_soren_retirement_identity.py'], paths
subprocess.run(['git', 'diff', '--cached', '--check'], cwd=source, check=True)
