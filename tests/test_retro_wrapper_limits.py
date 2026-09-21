"""Run tracked wrappers against a deterministic fake pane and game process."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import tmux as tmux_mod  # noqa: E402


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


def test_driver_helper_preserves_game_stdin_when_game_is_tracked_asynchronously(tmp_path):
    """A tracked interactive game must not inherit POSIX async /dev/null stdin."""

    output = tmp_path / "stdin.txt"
    script = tmp_path / "probe.sh"
    helper = ROOT / "games/cli-wrappers/_run_with_driver.sh"
    script.write_text(
        "#!/bin/sh\n"
        f". {helper!s}\n"
        # Keep the dummy driver's own descriptors out of subprocess capture;
        # this test isolates stdin inheritance of the tracked game process.
        "driver() { while :; do sleep 10; done; } >/dev/null 2>&1 &\n"
        "DRIVER=$!\n"
        "docich_wrapper_run_with_driver \"$DRIVER\" sh -c "
        "'IFS= read -r line || exit 7; printf \"%s\\n\" \"$line\" > \"$DOCICH_STDIN_PROBE\"'\n"
        "exit $?\n",
        encoding="utf-8",
    )
    script.chmod(0o700)

    result = subprocess.run(
        ["/bin/sh", str(script)],
        input="pane-input\n",
        env={**os.environ, "DOCICH_STDIN_PROBE": str(output)},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == "pane-input\n"


def test_tmux_pane_cleanup_lookup_stays_scoped_to_owned_target():
    """Retro CI must guard against accidentally enumerating all tmux panes."""

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="123\n", stderr="")
    with mock.patch("docich.tmux.procs.run", return_value=completed) as run:
        pids = tmux_mod.Tmux()._pane_pids("docich:game-g1")

    assert pids == [123]
    assert run.call_args.args[0] == [
        "tmux", "list-panes", "-t", "docich:game-g1", "-F", "#{pane_pid}"
    ]
    assert "-a" not in run.call_args.args[0]
