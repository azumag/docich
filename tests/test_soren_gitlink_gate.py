from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".github" / "scripts" / "soren_gitlink_gate.py"


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def commit(repo: Path, message: str, *, stage: bool = True) -> str:
    if stage:
        git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def init_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "ci@example.invalid")
    git(path, "config", "user.name", "CI Test")


def set_gitlink(root: Path, sha: str) -> None:
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{sha},games/soviet_now")


def run_gate(root: Path, before: str, after: str, *, root_paths: list[str], soren_paths: list[str],
             remote: Path) -> str:
    command = [
        sys.executable, str(GATE), "--before", before, "--after", after,
        *sum((["--root-path", p] for p in root_paths), []),
        *sum((["--soren-path", p] for p in soren_paths), []),
    ]
    env = dict(os.environ, SOREN_GITLINK_GATE_REMOTE=str(remote))
    return subprocess.check_output(command, cwd=root, env=env, text=True).strip()


def fixture(tmp_path: Path):
    soren = tmp_path / "soren"
    init_repo(soren)
    (soren / "README.md").write_text("one\n")
    first = commit(soren, "first")
    (soren / "README.md").write_text("two\n")
    unrelated = commit(soren, "unrelated")
    (soren / "lib").mkdir()
    (soren / "lib" / "weather_audio_consumer.py").write_text("print('ok')\n")
    relevant = commit(soren, "weather")

    root = tmp_path / "root"
    init_repo(root)
    (root / "games").mkdir()
    set_gitlink(root, first)
    before = commit(root, "old gitlink", stage=False)
    set_gitlink(root, unrelated)
    after_unrelated = commit(root, "unrelated soren update", stage=False)
    set_gitlink(root, relevant)
    after_relevant = commit(root, "relevant soren update", stage=False)
    return root, soren, before, after_unrelated, after_relevant


def test_gitlink_gate_skips_unrelated_soren_change(tmp_path):
    root, soren, before, after_unrelated, _ = fixture(tmp_path)
    assert run_gate(
        root, before, after_unrelated,
        root_paths=["src/docich/weather*.py"],
        soren_paths=["lib/weather_audio_consumer.py"],
        remote=soren,
    ) == "false"


def test_gitlink_gate_runs_for_relevant_soren_change(tmp_path):
    root, soren, _, after_unrelated, after_relevant = fixture(tmp_path)
    assert run_gate(
        root, after_unrelated, after_relevant,
        root_paths=["src/docich/weather*.py"],
        soren_paths=["lib/weather_audio_consumer.py"],
        remote=soren,
    ) == "true"


def test_gitlink_gate_runs_for_direct_root_change(tmp_path):
    root, soren, _, after_unrelated, _ = fixture(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "docich").mkdir()
    (root / "src" / "docich" / "weather.py").write_text("changed\n")
    direct = commit(root, "direct weather change")
    assert run_gate(
        root, after_unrelated, direct,
        root_paths=["src/docich/weather*.py"],
        soren_paths=["lib/weather_audio_consumer.py"],
        remote=soren,
    ) == "true"


def test_gitlink_gate_fails_open_for_unknown_before(tmp_path):
    root, soren, _, after_unrelated, _ = fixture(tmp_path)
    assert run_gate(
        root, "0" * 40, after_unrelated,
        root_paths=["src/docich/weather*.py"],
        soren_paths=["lib/weather_audio_consumer.py"],
        remote=soren,
    ) == "true"
