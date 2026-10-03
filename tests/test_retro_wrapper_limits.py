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


@pytest.mark.parametrize("game", ["ninvaders", "nsnake"])
@pytest.mark.parametrize("limit", [1, 3])
@pytest.mark.parametrize("save_fails", [False, True])
def test_wrapper_records_zero_and_stops_at_match_limit(tmp_path, game, limit, save_fails):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_tmux = bin_dir / "tmux"
    fake_tmux.write_text(f"#!{sys.executable} -S\n" + '''import json, os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
game = os.environ["FAKE_GAME"]
state = root / "captures"
if sys.argv[1] == "capture-pane":
    n = int(state.read_text()) if state.exists() else 0
    state.write_text(str(n + 1))
    if game == "ninvaders":
        print("Press SPACE to start" if n % 2 == 0 else "Level: 1\\nScore: 0000000")
    else:
        setup = ["Main Menu", "Game Settings\\nStarting Speed [1]",
                 "Game Settings\\nStarting Speed <1>", "Game Settings\\nStarting Speed <3>",
                 "Main Menu"]
        print(setup[n] if n < len(setup) else ("Score 0\\nGame Over" if n % 2 == 0 else "Score 0\\nplaying"))
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
    fake_game.write_text(f"#!{sys.executable} -S\n" + '''import os, pathlib, time
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
           "TMUX_PANE": "%9", "FAKE_ROOT": str(tmp_path), "FAKE_GAME": game, "NSNAKE_SPEED": "3",
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
    menu_enters = 2 if game == "nsnake" else 0  # open settings, return via Back
    assert keys.count(["-t", "%9", start_key]) == menu_enters + (1 if save_fails else limit)
    if game == "nsnake":
        speed_key = ["-t", "%9", "3"]
        assert keys.count(speed_key) == 1
        enters = [i for i, key in enumerate(keys) if key == ["-t", "%9", "Enter"]]
        assert enters[0] < keys.index(speed_key) < enters[1] < enters[2]


def _run_nsnake_menu(tmp_path, speed=None, *, fault="", limit=1, save_fails=False):
    """Model nSnake 3's real menu semantics, not just the emitted key list.

    Digits on Arcade Mode do nothing. Only the focused Starting Speed
    numberbox accepts them; Enter on that numberbox resets the old value.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_file = tmp_path / "state.json"
    initial_screen = fault if fault in {"playing", "over", "unknown"} else "main"
    state_file.write_text(json.dumps({
        "screen": initial_screen, "item": 0, "speed": 8,
        "starts": [], "captures": 0, "play_frames": 0, "keys": [],
    }))
    fake_tmux = bin_dir / "tmux"
    fake_tmux.write_text(f"#!{sys.executable} -S\n" + r'''import json, os, pathlib, sys
path = pathlib.Path(os.environ["FAKE_STATE"])
s = json.loads(path.read_text())
fault = os.environ["FAKE_FAULT"]
rc = 0
if sys.argv[1] == "capture-pane":
    s["captures"] += 1
    if fault == "capture":
        print("Main Menu\nArcade Mode\nGame Settings")
        rc = 1
    elif s["screen"] == "main":
        print("Main Menu\nArcade Mode\nLevel Select\nGame Settings")
    elif s["screen"] == "settings":
        value = ("<%s>" if s["item"] == 1 else "[%s]") % s["speed"]
        print("Game Settings\nBack\nStarting Speed " + value)
    elif s["screen"] == "playing":
        print("Arcade Mode\nScore 0\nSpeed " + str(s["speed"]))
        s["play_frames"] += 1
        if s["play_frames"] >= 2 and fault != "playing":
            s["screen"] = "over"
    elif s["screen"] == "over":
        print("Score 0\nGame Over")
    else:
        print("Unknown screen")
elif sys.argv[1] == "send-keys":
    assert sys.argv[2:4] == ["-t", "%9"], sys.argv
    key = sys.argv[4]
    s["keys"].append([s["screen"], s["item"], key])
    if fault == "send" and key == "4":
        rc = 1
    elif s["screen"] == "main":
        if key == "Home":
            s["item"] = 0
        elif key == "Down":
            s["item"] += 1
        elif key == "Enter" and s["item"] == 2 and fault != "stale_main":
            s["screen"], s["item"] = "settings", 0
        elif key == "Enter" and s["item"] == 0:
            s["screen"] = "playing"
            s["starts"].append(s["speed"])
    elif s["screen"] == "settings":
        if key == "Home":
            s["item"] = 0
        elif key == "Down" and fault != "focus":
            s["item"] = 1
        elif key.isdigit() and s["item"] == 1 and fault != "value":
            s["speed"] = int(key)
        elif key == "Enter" and s["item"] == 1:
            s["speed"] = 8  # Enter resets, it does not commit a numberbox!
        elif key == "Enter" and s["item"] == 0:
            s["screen"], s["item"] = "main", 2
    elif s["screen"] == "over" and key == "Enter":
        s["screen"], s["play_frames"] = "playing", 0
        s["starts"].append(s["speed"])
# Atomic observation for the fake game process's completion handshake.
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(s))
tmp.replace(path)
sys.exit(rc)
''')
    fake_tmux.chmod(0o700)
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o700)
    fake_game = bin_dir / "game"
    fake_game.write_text(f"#!{sys.executable} -S\n" + r'''import json, os, pathlib, time
path = pathlib.Path(os.environ["FAKE_STATE"])
deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    if json.loads(path.read_text())["captures"] >= 24:
        raise SystemExit(0)
    time.sleep(0.005)
raise SystemExit(1)
''')
    fake_game.chmod(0o700)
    scores = tmp_path / "scores.jsonl"
    if save_fails:
        scores.mkdir()
    env = {
        **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TMUX_PANE": "%9", "FAKE_STATE": str(state_file), "FAKE_FAULT": fault,
        "NSNAKE_BIN": str(fake_game), "NSNAKE_MAX_MATCHES": str(limit),
        "NSNAKE_DRIVER_INTERVAL": "0.02", "NSNAKE_SCORELOG": str(scores),
    }
    env.pop("NSNAKE_SPEED", None)
    if speed is not None:
        env["NSNAKE_SPEED"] = speed
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "games/cli-wrappers/nsnake_docich.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(state_file.read_text())
    recorded = [json.loads(line) for line in scores.read_text().splitlines()] if scores.is_file() else []
    return state, recorded


@pytest.mark.parametrize("speed", [None, "", *map(str, range(1, 10))])
def test_nsnake_wrapper_uses_speed_override_before_start(tmp_path, speed):
    state, recorded = _run_nsnake_menu(tmp_path, speed)
    expected = int(speed or "4")
    assert state["starts"] == [expected]
    assert [row["score"] for row in recorded] == [0]
    digits = [entry for entry in state["keys"] if entry[2].isdigit()]
    assert digits == [["settings", 1, str(expected)]]
    assert ["settings", 1, "Enter"] not in state["keys"]


@pytest.mark.parametrize("fault", ["focus", "value", "send", "stale_main", "capture"])
def test_nsnake_wrapper_does_not_start_without_verified_speed(tmp_path, fault):
    state, recorded = _run_nsnake_menu(tmp_path, fault=fault)
    assert state["starts"] == []
    assert recorded == []
    digits = [entry for entry in state["keys"] if entry[2].isdigit()]
    assert digits == ([["settings", 1, "4"]] if fault in {"value", "send"} else [])


@pytest.mark.parametrize("screen", ["playing", "over", "unknown"])
def test_nsnake_wrapper_never_sends_speed_on_other_screens(tmp_path, screen):
    state, _ = _run_nsnake_menu(tmp_path, fault=screen)
    assert not any(entry[2].isdigit() for entry in state["keys"])


@pytest.mark.parametrize("limit", [1, 3])
@pytest.mark.parametrize("save_fails", [False, True])
def test_nsnake_speed_setup_preserves_retries_and_score_limits(tmp_path, limit, save_fails):
    state, recorded = _run_nsnake_menu(tmp_path, "4", limit=limit, save_fails=save_fails)
    assert state["starts"] == [4] * (1 if save_fails else limit)
    assert [row["score"] for row in recorded] == ([] if save_fails else [0] * limit)
    assert sum(entry[2].isdigit() for entry in state["keys"]) == 1


@pytest.mark.parametrize("game", ["ninvaders", "nsnake"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "01"])
def test_wrapper_rejects_invalid_limits(game, value):
    env = {**os.environ, f"{game.upper()}_MAX_MATCHES": value}
    result = subprocess.run(["/bin/sh", str(ROOT / "games/cli-wrappers" / f"{game}_docich.sh")],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert "positive integer" in result.stderr


@pytest.mark.parametrize("value", ["0", "10", "-1", "abc", "01", " 3", "3 ", "3.0", "3\n", "３"])
def test_nsnake_wrapper_rejects_invalid_speed(value):
    env = {**os.environ, "NSNAKE_SPEED": value}
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "games/cli-wrappers/nsnake_docich.sh")],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 2
    assert "NSNAKE_SPEED must be an integer from 1 to 9" in result.stderr


@pytest.mark.parametrize("env_prefix,script_name", [
    ("BASTET", "bastet_docich.sh"),
    ("MOONBUGGY", "moon-buggy_docich.sh"),
    ("PACMAN", "pacman4console_docich.sh"),
])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "01"])
def test_other_wrapper_rejects_invalid_limits(env_prefix, script_name, value):
    env = {**os.environ, f"{env_prefix}_MAX_MATCHES": value}
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "games/cli-wrappers" / script_name)],
        env=env, capture_output=True, text=True, timeout=5,
    )
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert output.read_text(encoding="utf-8") == "pane-input\n"


def test_tmux_pane_cleanup_lookup_stays_scoped_to_owned_target():
    """Retro CI must guard against accidentally enumerating all tmux panes."""

    from docich import tmux as tmux_mod

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="123\n", stderr="")
    with mock.patch("docich.tmux.procs.run", return_value=completed) as run:
        pids = tmux_mod.Tmux()._pane_pids("docich:game-g1")

    assert pids == [123]
    assert run.call_args.args[0] == [
        "tmux", "list-panes", "-t", "docich:game-g1", "-F", "#{pane_pid}"
    ]
    assert "-a" not in run.call_args.args[0]
