import os
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "games" / "cli-wrappers" / "ninvaders_docich.sh"
NINVADERS_CONFIG = ROOT / "config" / "games" / "ninvaders.toml"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_ninvaders_config_launches_deployed_tracked_wrapper() -> None:
    text = NINVADERS_CONFIG.read_text(encoding="utf-8")
    assert 'command = "/bin/sh games/cli-wrappers/ninvaders_docich.sh"' in text
    assert "/usr/local/bin/ninvaders_docich" not in text


def test_ninvaders_wrapper_drives_gameplay_not_only_title_start() -> None:
    with tempfile.TemporaryDirectory(prefix="ninvaders-wrapper-") as tmp:
        base = Path(tmp)
        fake_bin = base / "bin"
        fake_bin.mkdir()
        tmux_log = base / "tmux.log"
        tmux_state = base / "tmux.state"
        scorelog = base / "scores.jsonl"

        _write_executable(
            fake_bin / "tmux",
            """#!/bin/sh
set -eu
case "$1" in
  capture-pane)
    n=0
    if [ -f "$FAKE_TMUX_STATE" ]; then n="$(cat "$FAKE_TMUX_STATE")"; fi
    n=$((n + 1))
    printf '%s\n' "$n" > "$FAKE_TMUX_STATE"
    if [ "$n" -eq 1 ]; then
      printf 'Press SPACE to start\n'
    else
      printf 'Level: 1\nScore: 0000012\n'
    fi
    ;;
  send-keys)
    shift
    printf '%s\n' "$*" >> "$FAKE_TMUX_LOG"
    ;;
esac
""",
        )
        fake_game = base / "fake-ninvaders"
        _write_executable(
            fake_game,
            """#!/bin/sh
sleep 0.20
exit 0
""",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "TMUX_PANE": "%9",
                "FAKE_TMUX_LOG": str(tmux_log),
                "FAKE_TMUX_STATE": str(tmux_state),
                "NINVADERS_BIN": str(fake_game),
                "NINVADERS_DRIVER_INTERVAL": "0.02",
                "NINVADERS_SCORELOG": str(scorelog),
            }
        )
        proc = subprocess.run(
            ["/bin/sh", str(WRAPPER)],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        lines = tmux_log.read_text(encoding="utf-8").splitlines()
        assert "-t %9 Space" in lines, lines
        gameplay = [line for line in lines if (" Right " in f" {line} " or " Left " in f" {line} ")]
        assert gameplay, lines
        assert all("Space" in line for line in gameplay), gameplay
