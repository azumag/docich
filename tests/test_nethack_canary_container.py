from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docich.nethack_canary_container import (
    CanaryContainerError,
    build_container_argv,
    run_container_worker,
)


def arena(root: Path):
    episode = root / "episode"
    playground = episode / "playground"
    save = playground / "save"
    dumps = playground / "dumps"
    save.mkdir(parents=True)
    dumps.mkdir(parents=True)
    return {
        "episode_root": str(episode.resolve()),
        "playground_dir": str(playground.resolve()),
        "save_dir": str(save.resolve()),
        "xlogfile": str((playground / "xlogfile").resolve()),
        "dump_dir": str(dumps.resolve()),
    }


def request(root: Path, *, candidate=False):
    paths = arena(root)
    if candidate:
        manifest = root / "candidate.json"
        manifest.write_text("{}", encoding="utf-8")
        controller = {
            "kind": "candidate_strategist",
            "manifest_path": str(manifest.resolve()),
            "candidate_id": "cand",
            "candidate_version": "v1",
            "candidate_fingerprint": "f" * 64,
            "command_sha256": "c" * 64,
        }
        arm = "candidate"
    else:
        controller = {"kind": "baseline_p3b"}
        arm = "baseline"
    return {
        "schema_version": 1,
        "experiment_id": "exp",
        "episode_id": "000",
        "arm": arm,
        "arena": paths,
        "player_name": "canary_b_000",
        "max_turns": 1000,
        "seed": None,
        "controller": controller,
        "requirements": {
            "isolation_mode": "container",
            "production_state_must_remain_untouched": True,
            "wizard_mode": False,
            "explore_mode": False,
        },
    }


def internal_result(req):
    return {
        "schema_version": 1,
        "worker_status": "completed",
        "episode_id": req["episode_id"],
        "arm": req["arm"],
        "isolation_mode": "container",
        "arena": {
            "episode_root": "/canary/episode",
            "playground_dir": "/canary/episode/playground",
            "save_dir": "/canary/episode/playground/save",
            "xlogfile": "/canary/episode/playground/xlogfile",
            "dump_dir": "/canary/episode/playground/dumps",
        },
        "player_name": req["player_name"],
        "seed": None,
        "seed_applied": False,
        "controller_kind": "baseline_p3b" if req["arm"] == "baseline" else "candidate_strategist",
        "terminal_status": "dead",
        "score": 12,
        "turns": 20,
        "max_depth": 2,
        "death_reason": "killed by a grid bug",
        "got_amulet": False,
        "exit_reason": "terminal_xlog",
        "candidate_action_source": "baseline_p3b" if req["arm"] == "baseline" else "candidate_strategist",
        "production_state_touched": False,
    }


def test_podman_argv_has_hardening_and_only_episode_mount(tmp_path):
    req = request(tmp_path)
    host_arena = {key: Path(value) for key, value in req["arena"].items()}
    argv = build_container_argv(
        req,
        runtime="podman",
        image="image:test",
        host_arena=host_arena,
        manifest=None,
    )
    joined = " ".join(argv)
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--userns=keep-id" in argv
    assert "--pids-limit=256" in argv
    assert "--memory=1024m" in argv
    assert "--cpus=1.0" in argv
    assert f"src={host_arena['episode_root']},dst=/canary/episode,rw" in joined
    assert "/canary/candidate.json" not in joined
    assert argv[-1] == "image:test"


def test_candidate_mount_is_one_readonly_manifest(tmp_path):
    req = request(tmp_path, candidate=True)
    host_arena = {key: Path(value) for key, value in req["arena"].items()}
    manifest = Path(req["controller"]["manifest_path"])
    argv = build_container_argv(
        req,
        runtime="docker",
        image="image:test",
        host_arena=host_arena,
        manifest=manifest,
    )
    joined = " ".join(argv)
    assert "--security-opt=no-new-privileges:true" in argv
    assert "--user" in argv
    assert f"src={manifest},dst=/canary/candidate.json,readonly" in joined
    assert joined.count("type=bind") == 2


def test_run_rewrites_request_and_result_without_raw_commands(tmp_path):
    req = request(tmp_path, candidate=True)
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = json.loads(kwargs["input"])
        result = internal_result(seen["input"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/podman"):
        result = run_container_worker(
            json.dumps(req),
            runtime="podman",
            image="image:test",
            runner=runner,
        )

    assert seen["input"]["arena"]["episode_root"] == "/canary/episode"
    assert seen["input"]["controller"]["manifest_path"] == "/canary/candidate.json"
    assert result["arena"] == req["arena"]
    encoded = json.dumps(result)
    assert "image:test" not in encoded
    assert req["controller"]["manifest_path"] not in encoded


def test_container_result_cannot_claim_another_arena(tmp_path):
    req = request(tmp_path)

    def runner(argv, **kwargs):
        internal = json.loads(kwargs["input"])
        result = internal_result(internal)
        result["arena"]["save_dir"] = "/var/games/nethack/save"
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/podman"):
        with pytest.raises(CanaryContainerError):
            run_container_worker(json.dumps(req), runtime="podman", runner=runner)


def test_host_arena_escape_is_rejected_before_runtime(tmp_path):
    req = request(tmp_path)
    req["arena"]["save_dir"] = str((tmp_path / "outside").resolve())
    with patch("docich.nethack_canary_container.shutil.which") as which:
        with pytest.raises(CanaryContainerError):
            run_container_worker(json.dumps(req), runtime="podman")
    which.assert_not_called()


def test_invalid_isolation_requirement_is_rejected(tmp_path):
    req = request(tmp_path)
    req["requirements"]["isolation_mode"] = "vm"
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/podman"):
        with pytest.raises(CanaryContainerError):
            run_container_worker(json.dumps(req), runtime="podman")
