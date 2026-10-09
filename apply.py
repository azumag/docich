"""Verify and apply four readable patches to an exact source checkout."""
import hashlib
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
source = Path(sys.argv[1]).resolve()
base = '14ad543977cebc935d0f28e0acec166aa0901175'
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() == base
expected = {
    '00-soren.patch': '9e85569b7d724df106f456497c7525e645391432b83ce696171994d1121dd37c',
    '01-coordinator.patch': '1d464ebf26a34ae50c19d7fd0828b5e145c2e9c9073d9f232f397f2fae444795',
    '02-existing-tests.patch': '55545c708899e929c64d7337a57ac81e6b1ffb41c12edad3de708e8283fba7eb',
    '03-new-tests.patch': '7031da14b451ba2c49b1b0cb213399d79d0f9a5486c51c2453b813e04a0a2e16',
}
parts = []
for name, digest in expected.items():
    data = (here / 'reviewed-patches' / name).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    print(name, actual, flush=True)
    assert actual == digest, (name, actual, digest)
    parts.append(data)
patch = b''.join(parts)
assert hashlib.sha256(patch).hexdigest() == 'be473d4c71d1e6c3d6eec86c380814c715c7a5ec0c38624510fe2d13246b88d7'
subprocess.run(['git', 'apply', '--check', '--index', '-'], cwd=source, input=patch, check=True)
subprocess.run(['git', 'apply', '--index', '-'], cwd=source, input=patch, check=True)
paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=source, text=True).splitlines()
assert paths == ['src/docich/adapters/soren.py', 'src/docich/game_switch.py', 'tests/test_adapter_stop_resume_safety.py', 'tests/test_soren_retirement_identity.py'], paths
subprocess.run(['git', 'diff', '--cached', '--check'], cwd=source, check=True)
