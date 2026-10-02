import json
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
    # The wrapper's policy player owns in-match input; a second command brain would compete.
    assert 'command = "/bin/sh games/cli-wrappers/ninvaders_docich.sh policy"' in text
    assert "enabled = false" in text
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
    elif [ "$n" -eq 2 ]; then
      printf 'Level: 1\nScore: 0000012\n'
    elif [ "$n" -eq 3 ]; then
      printf 'Press SPACE to start\n'
    elif [ "$n" -eq 4 ]; then
      printf 'Level: 1\nScore: 0000013\n'
    elif [ "$n" -eq 5 ]; then
      printf 'Press SPACE to start\n'
    elif [ "$n" -eq 6 ]; then
      printf 'Level: 1\nScore: 0000014\n'
    else
      printf 'Press SPACE to start\n'
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
n=0
while [ "$n" -lt 400 ]; do
  if [ -f "$FAKE_TMUX_LOG" ] && grep -q 'Right Space' "$FAKE_TMUX_LOG"; then
    exit 0
  fi
  sleep 0.02
  n=$((n + 1))
done
exit 1
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
            timeout=8,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        lines = tmux_log.read_text(encoding="utf-8").splitlines()
        assert "-t %9 Space" in lines, lines
        gameplay = [line for line in lines if (" Right " in f" {line} " or " Left " in f" {line} ")]
        assert gameplay, lines
        assert all("Space" in line for line in gameplay), gameplay


def test_ninvaders_wrapper_stops_after_three_matches() -> None:
    """3試合完走後はタイトルで再開しない (4試合目の Space 送信が無いこと)。"""
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
    printf '%s\\n' "$n" > "$FAKE_TMUX_STATE"
    if [ "$n" -eq 1 ]; then
      printf 'Press SPACE to start\\n'
    elif [ "$n" -eq 2 ]; then
      printf 'Level: 1\\nScore: 0000012\\n'
    elif [ "$n" -eq 3 ]; then
      printf 'Press SPACE to start\\n'
    elif [ "$n" -eq 4 ]; then
      printf 'Level: 1\\nScore: 0000013\\n'
    elif [ "$n" -eq 5 ]; then
      printf 'Press SPACE to start\\n'
    elif [ "$n" -eq 6 ]; then
      printf 'Level: 1\\nScore: 0000014\\n'
    else
      printf 'Press SPACE to start\\n'
    fi
    ;;
  send-keys)
    shift
    printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
    ;;
esac
""",
        )
        fake_game = base / "fake-ninvaders"
        _write_executable(
            fake_game,
            """#!/bin/sh
# 3試合ぶんスコアが記録されるまで前面で生き続ける (製品の record_score を
# 終了条件にすることで、キー送信回数依存のレースを避ける)。
n=0
while [ "$n" -lt 400 ]; do
  if [ -f "$NINVADERS_SCORELOG" ]; then
    recorded=$(grep -c . "$NINVADERS_SCORELOG" || true)
    if [ "$recorded" -ge 3 ]; then
      exit 0
    fi
  fi
  sleep 0.02
  n=$((n + 1))
done
exit 1
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
            timeout=10,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        lines = tmux_log.read_text(encoding="utf-8").splitlines()
        # タイトル開始Spaceは3回 (初回+2再開)。4試合目の開始Spaceは無い。
        starts = [line for line in lines if line == "-t %9 Space"]
        assert len(starts) == 3, lines
        scores = [json.loads(l) for l in scorelog.read_text(encoding="utf-8").splitlines()]
        assert [s["score"] for s in scores] == [12, 13, 14], scores
