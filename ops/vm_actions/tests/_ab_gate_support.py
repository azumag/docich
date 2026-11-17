"""Test-only fixtures for the #4412 A/B gate (never used in production).

The fake gate reads ``tmp/state/fake_gate_rc`` from the projection root so a
test can simulate a clear (0), active (1) or indeterminate (2) verdict without
touching real A/B state.
"""
from __future__ import annotations

from pathlib import Path

FAKE_GATE = """#!/usr/bin/env bash
set -u
rc=0
f="tmp/state/fake_gate_rc"
if [ -r "$f" ]; then
  rc=$(cat "$f" 2>/dev/null || echo 2)
fi
case "$rc" in
  0|1|2) exit "$rc" ;;
  *) exit 2 ;;
esac
"""


def install_clear_gate(root):
    """Install an executable gate script that reports 'clear' by default."""
    root = Path(root)
    script = root / "strategy" / "ab_gate_on_merge.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(FAKE_GATE, encoding="utf-8")
    script.chmod(0o755)
    return script


def set_gate_rc(root, rc):
    root = Path(root)
    state = root / "tmp" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "fake_gate_rc").write_text(str(rc), encoding="utf-8")


def install_counting_gate(root, first_rc=0, later_rc=1):
    """Gate whose verdict changes after the first call (TOCTOU simulation)."""
    root = Path(root)
    script = root / "strategy" / "ab_gate_on_merge.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    (root / "tmp" / "state").mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        'c="tmp/state/fake_gate_count"\n'
        "n=0\n"
        '[ -r "$c" ] && n=$(cat "$c")\n'
        "n=$((n+1))\n"
        'echo "$n" > "$c"\n'
        f'if [ "$n" -le 1 ]; then exit {first_rc}; fi\n'
        f"exit {later_rc}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_raw_state(root, text):
    """Write a raw ab_state.json to exercise independent state validation."""
    root = Path(root)
    state = root / "tmp" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "ab_state.json").write_text(text, encoding="utf-8")
