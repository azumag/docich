"""wrapper の policy モード: 政策プレイヤーの起動・競合キー無し・縮退・終了時の後始末。

fake の tmux / python3 / ゲームで wrapper を実際に走らせる (実ゲームでの通し実走は別途)。
"""
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "games" / "cli-wrappers" / "ninvaders_docich.sh"

FAKE_TMUX = """#!/bin/sh
set -eu
case "$1" in
  capture-pane)
    n=0
    if [ -f "$FAKE_TMUX_STATE" ]; then n="$(cat "$FAKE_TMUX_STATE")"; fi
    n=$((n + 1))
    printf '%s\\n' "$n" > "$FAKE_TMUX_STATE"
    if [ "$n" -eq 1 ]; then printf 'Press SPACE to start\\n'
    elif [ "$n" -le 12 ]; then printf 'Level: 1\\nScore: 0000012\\n'
    else printf 'Press SPACE to start\\n'
    fi ;;
  send-keys) shift; printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG" ;;
esac
"""

FAKE_PYTHON = """#!/bin/sh
{
  echo "ARGS: $*"
  echo "PYTHONPATH=$PYTHONPATH"
  echo "PID=$$"
} >> "$FAKE_PY_LOG"
echo "$$" > "$FAKE_PY_PID"
if [ "${FAKE_PY_EXIT:-0}" = "1" ]; then touch "$FAKE_PY_DONE"; exit 0; fi
stop_player() { kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; touch "$FAKE_PY_DONE"; exit 0; }
trap stop_player TERM INT HUP
sleep 30 &
child=$!
wait "$child"
"""

FAKE_PS = """#!/bin/sh
set -eu
case "$1" in
  -eo) exit 0 ;;
  -p)
    pid="$2"
    if [ -f "$FAKE_PY_PID" ] && [ "$(cat "$FAKE_PY_PID")" = "$pid" ] && [ -f "$FAKE_PY_DONE" ]; then
      echo Z
    else
      echo S
    fi
    ;;
  *) exit 1 ;;
esac
"""

FAKE_GAME = """#!/bin/sh
n=0
while [ "$n" -lt 500 ]; do
  if [ -f "$NINVADERS_SCORELOG" ] && [ "$(grep -c . "$NINVADERS_SCORELOG")" -ge 1 ]; then exit 0; fi
  sleep 0.02
  n=$((n + 1))
done
exit 1
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_wrapper(tmp_path, *args, player_exits=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "tmux", FAKE_TMUX)
    _exe(bin_dir / "python3", FAKE_PYTHON)
    _exe(bin_dir / "ps", FAKE_PS)
    game = tmp_path / "fake-ninvaders"
    _exe(game, FAKE_GAME)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TMUX_PANE": "%9",
           "FAKE_TMUX_LOG": str(tmp_path / "keys.log"), "FAKE_TMUX_STATE": str(tmp_path / "tmux.state"),
           "FAKE_PY_LOG": str(tmp_path / "py.log"), "FAKE_PY_EXIT": "1" if player_exits else "0",
           "FAKE_PY_PID": str(tmp_path / "py.pid"), "FAKE_PY_DONE": str(tmp_path / "py.done"),
           "NINVADERS_BIN": str(game), "NINVADERS_DRIVER_INTERVAL": "0.02", "NINVADERS_MAX_MATCHES": "1",
           "NINVADERS_SCORELOG": str(tmp_path / "scores.jsonl"),
           "NINVADERS_POLICY_DIR": str(tmp_path / "policy"), "NINVADERS_PLAYER_LOG": str(tmp_path / "player.log")}
    proc = subprocess.run(["/bin/sh", str(WRAPPER), *args], env=env, capture_output=True, text=True, timeout=15)
    keys = (tmp_path / "keys.log").read_text(encoding="utf-8").splitlines() if (tmp_path / "keys.log").exists() else []
    py = (tmp_path / "py.log").read_text(encoding="utf-8") if (tmp_path / "py.log").exists() else ""
    return proc, keys, py


def sweeps(keys):
    return [k for k in keys if k.startswith("-t %9 Right") or k.startswith("-t %9 Left")]


def test_policy_mode_launches_the_player_and_never_competes_with_it(tmp_path):
    proc, keys, py = run_wrapper(tmp_path, "policy")
    assert proc.returncode == 0, proc.stderr
    assert f"ARGS: -m docich.ninvaders.player --pane %9 --policy-dir {tmp_path / 'policy'}" in py
    assert f"PYTHONPATH={ROOT / 'src'}" in py
    assert "-t %9 Space" in keys       # the wrapper still starts the match
    assert sweeps(keys) == []          # ...but never sends in-play keys while the player is alive
    assert (tmp_path / "scores.jsonl").read_text(encoding="utf-8").count("\n") == 1  # and still records the score


def test_policy_player_is_stopped_when_the_game_exits(tmp_path):
    _, _, py = run_wrapper(tmp_path, "policy")
    pid = int([line for line in py.splitlines() if line.startswith("PID=")][0].split("=")[1])
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError("policy player outlived the wrapper")


def test_dead_policy_player_falls_back_to_the_sweep(tmp_path):
    proc, keys, py = run_wrapper(tmp_path, "policy", player_exits=True)
    assert proc.returncode == 0, proc.stderr
    assert "ARGS: -m docich.ninvaders.player" in py
    assert sweeps(keys), keys  # the cannon never idles because the player died


def test_brain_mode_starts_no_player_and_no_sweep(tmp_path):
    proc, keys, py = run_wrapper(tmp_path, "brain")
    assert proc.returncode == 0, proc.stderr
    assert py == "" and sweeps(keys) == []


def test_default_mode_is_the_old_sweep_and_starts_no_player(tmp_path):
    proc, keys, py = run_wrapper(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert py == "" and sweeps(keys)


def test_game_launch_passes_configured_state_dir_to_wrapper(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from docich.adapters.cli_game import _game_launch_command

    game = type("Game", (), {"name": "ninvaders"})()
    command = _game_launch_command(type("Config", (), {"state_dir": tmp_path})(), game,
                                   ["/bin/sh", "games/cli-wrappers/ninvaders_docich.sh", "policy"])
    assert command[1] == f"DOCICH_STATE_DIR={tmp_path.resolve()}"
    assert command[2:] == ["/bin/sh", "games/cli-wrappers/ninvaders_docich.sh", "policy"]
