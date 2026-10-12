"""`docich nethack-corner` must reach the NetHack corner CLI (#1969).

recover_corner_rotation.sh runs ``bin/docich --config C nethack-corner
recover-failed-rotation``. The route was missing, so the real entry point died
with an argparse error that the operator reported as a refused recovery.
"""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*argv):
    return subprocess.run([sys.executable, "-m", "docich", *argv], cwd=ROOT, text=True,
                          capture_output=True, timeout=60,
                          env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})


@pytest.mark.parametrize("prefix", [(), ("--config", "x.toml"), ("--config=x.toml",)])
def test_nethack_corner_help_is_routed_for_every_config_spelling(prefix):
    result = run(*prefix, "nethack-corner", "--help")
    assert result.returncode == 0, result.stderr
    assert "docich nethack-corner" in result.stdout
    assert "recover-failed-rotation" in result.stdout
