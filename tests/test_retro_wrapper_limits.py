"""Run tracked wrappers against a deterministic fake pane and game process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("game", ["ninvaders", "nsnake"])
@pytest.mark.parametrize("limit", [1, 3])
@pytest.mark.parametrize("save_fails", [False, True])
def test_wrapper_records_zero_and_stops_at_match_limit(tmp_path, game, limit, save_fails):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_tmux = bin_dir / "tmux"
    fake_tmux.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
game = os.environ["FAKE_GAME"]
state = root / "captures"
if sys.argv[1] == "capture-pane":
    n = int(state.read_text()) if state.exists() else 0
    state.write_text(str(n + 1))
    if game == "ninvaders":
        print("Press SPACE to start" if n % 2 == 0 else "Level: 1\\nScore: 0000000")
    else:
        print("Main Menu" if n == 0 else ("Score 0\\nGame Over" if n % 2 == 0 else "Score 0\\nplaying"))
elif sys.argv[1] == "send-keys":
    with (root / "keys").open("a") as stream:
        stream.write(json.dumps(sys.argv[2:]) + "\\n")
''')
    fake_tmux.chmod(0o700)
    # Completion uses capture iterations, not an arbitrary sleep duration.
    # The driver stays alive but must send no more starts after the cap.
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o700)
    fake_game = bin_dir / "game"
    fake_game.write_text(f"#!{sys.executable}\n" + '''import os, pathlib, time
root = pathlib.Path(os.environ["FAKE_ROOT"])
# Driver caps must keep observing (without starting another match) so this
# handshake also detects an early driver exit with unflushed results.
deadline = time.monotonic() + 40  # generous: CPU-starved runners are slow
while time.monotonic() < deadline:
    try:
        if int((root / "captures").read_text()) >= 12:
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
    time.sleep(0.005)
raise SystemExit(1)
''')
    fake_game.chmod(0o700)
    if save_fails:
        (tmp_path / "scores.jsonl").mkdir()  # deterministic write failure
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "TMUX_PANE": "%9", "FAKE_ROOT": str(tmp_path), "FAKE_GAME": game,
           f"{game.upper()}_BIN": str(fake_game),
           f"{game.upper()}_MAX_MATCHES": str(limit),
           f"{game.upper()}_SCORELOG": str(tmp_path / "scores.jsonl")}
    result = subprocess.run(["/bin/sh", str(ROOT / "games/cli-wrappers" / f"{game}_docich.sh")],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    if not save_fails:
        scores = [json.loads(line) for line in (tmp_path / "scores.jsonl").read_text().splitlines()]
        assert len(scores) == limit
        assert [item["score"] for item in scores] == [0] * limit
    keys = [json.loads(line) for line in (tmp_path / "keys").read_text().splitlines()]
    start_key = "Space" if game == "ninvaders" else "Enter"
    assert keys.count(["-t", "%9", start_key]) == (1 if save_fails else limit)


@pytest.mark.parametrize("game", ["ninvaders", "nsnake"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "01"])
def test_wrapper_rejects_invalid_limits(game, value):
    env = {**os.environ, f"{game.upper()}_MAX_MATCHES": value}
    result = subprocess.run(["/bin/sh", str(ROOT / "games/cli-wrappers" / f"{game}_docich.sh")],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert "positive integer" in result.stderr
