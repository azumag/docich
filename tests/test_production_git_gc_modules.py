"""Regression coverage for independently throttled submodule Git maintenance."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "vm_actions" / "production_git_gc.py"
spec = importlib.util.spec_from_file_location("production_git_gc", SCRIPT)
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)


def test_module_stores_use_independent_state_and_grace(tmp_path):
    root = tmp_path / "checkout"
    modules = root / ".git" / "modules"
    for name in ("alpha", "nested/beta"):
        store = modules / name
        (store / "objects").mkdir(parents=True)
        (store / "config").write_text("[core]\n")
    # Never traverse a symlink pointing outside the repository.
    outside = tmp_path / "outside"
    (outside / "objects").mkdir(parents=True)
    (outside / "config").write_text("[core]\n")
    (modules / "external").symlink_to(outside, target_is_directory=True)
    calls = []

    def fake_maintain(target, state, interval, warn, prune):
        calls.append((target, state, prune))
        return {"gc": "run", "canary": {"large": "ignored"}}

    state = tmp_path / "state" / "repo.json"
    with patch.object(gc, "maintain", side_effect=fake_maintain):
        result = gc.maintain_all(root, state, 86400, 100, "post_deploy")

    assert set(result["modules"]) == {"alpha", "nested/beta"}
    assert len(calls) == 3
    assert calls[0] == (root, state, "post_deploy")
    assert all(prune == "grace" for _, _, prune in calls[1:])
    assert len({str(item[1]) for item in calls}) == 3
    assert all("canary" not in value for value in result["modules"].values())


def test_missing_module_directory_is_safe(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    with patch.object(gc, "maintain", return_value={"gc": "skipped_interval"}):
        assert gc.maintain_all(root, tmp_path / "state.json", 10, 100, "grace")["modules"] == {}
