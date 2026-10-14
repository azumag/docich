from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docich.nethack_candidate_eval import load_candidate_manifest
from docich.nethack_canary_container import (
    CANARY_ABI,
    NETHACK_SOURCE_SHA256,
    NETHACK_VERSION,
    CanaryContainerError,
    build_candidate_broker_argv,
    build_container_argv,
    run_container_worker,
)

IMAGE = "sha256:" + "a" * 64


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
        manifest_path = root / "candidate.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_id": "cand",
                    "version": "v1",
                    "command": ["python3", "brains/nethack_canary_hold.py"],
                    "timeout_s": 2.0,
                }
            ),
            encoding="utf-8",
        )
        manifest = load_candidate_manifest(manifest_path)
        controller = {
            "kind": "candidate_strategist",
            "manifest_path": str(manifest_path.resolve()),
            "candidate_id": manifest.candidate_id,
            "candidate_version": manifest.version,
            "candidate_fingerprint": manifest.fingerprint,
            "command_sha256": manifest.command_hash,
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
        "player_name": "canary_c_000" if candidate else "canary_b_000",
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
        "candidate_action_source": "baseline_p3b" if req["arm"] == "baseline" else "candidate_strategist_broker",
        "production_state_touched": False,
    }


def docker_info(*, runsc=True):
    return json.dumps(
        {
            "OSType": "linux",
            "Runtimes": {"runc": {}, **({"runsc": {}} if runsc else {})},
            "MemoryLimit": True,
            "PidsLimit": True,
            "CPUCfsQuota": True,
        }
    )


def image_inspect(*, abi=CANARY_ABI, version=NETHACK_VERSION, source=NETHACK_SOURCE_SHA256):
    return json.dumps(
        [{"Id": IMAGE, "Config": {"Volumes": None, "Labels": {
            "org.docich.nethack-canary.abi": abi,
            "org.docich.nethack.version": version,
            "org.docich.nethack.source-sha256": source,
        }}}]
    )


class DockerRunner:
    def __init__(self, req, *, runsc=True, inspect=None, mutate_result=None, timeout_run=False):
        self.req = req
        self.runsc = runsc
        self.inspect = inspect if inspect is not None else image_inspect()
        self.mutate_result = mutate_result
        self.timeout_run = timeout_run
        self.calls = []
        self.internal = None

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if len(argv) >= 2 and argv[1] == "info":
            return SimpleNamespace(returncode=0, stdout=docker_info(runsc=self.runsc), stderr="")
        if len(argv) >= 3 and argv[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=self.inspect, stderr="")
        if len(argv) >= 2 and argv[1] == "rm":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if len(argv) >= 2 and argv[1] == "run":
            if "--detach" in argv:
                return SimpleNamespace(returncode=0, stdout="broker-id\n", stderr="")
            if self.timeout_run:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))
            self.internal = json.loads(kwargs["input"])
            result = internal_result(self.internal)
            if self.mutate_result is not None:
                self.mutate_result(result)
            return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")
        raise AssertionError(f"unexpected docker call: {argv}")


def test_game_container_is_runsc_hardened_and_mounts_only_episode(tmp_path):
    req = request(tmp_path)
    host_arena = {key: Path(value) for key, value in req["arena"].items()}
    argv = build_container_argv(
        req, docker="/usr/bin/docker", image=IMAGE, name="game",
        host_arena=host_arena, manifest=None,
    )
    joined = " ".join(argv)
    for flag in (
        "-i", "--runtime=runsc", "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true", "--memory=1024m",
        "--memory-swap=1024m", "--pids-limit=256", "--ipc=none",
        "--log-driver=none", "--restart=no",
    ):
        assert flag in argv
    # `docker run --mount` rejects a `rw` key; read-write is the default.
    assert f"src={host_arena['episode_root']},dst=/canary/episode" in joined
    assert f"src={host_arena['episode_root']},dst=/canary/episode,readonly" not in joined
    assert "/canary/candidate.json" not in joined
    assert joined.count("type=bind") == 1


def test_extra_env_is_validated_and_rendered(tmp_path):
    req = request(tmp_path)
    host_arena = {key: Path(value) for key, value in req["arena"].items()}
    argv = build_container_argv(
        req,
        docker="/usr/bin/docker",
        image=IMAGE,
        name="game",
        host_arena=host_arena,
        manifest=None,
        extra_env={"DOCICH_CANARY_ACTION_TRACE": "1"},
    )
    assert "DOCICH_CANARY_ACTION_TRACE=1" in argv
    with pytest.raises(CanaryContainerError):
        build_container_argv(
            req,
            docker="/usr/bin/docker",
            image=IMAGE,
            name="game",
            host_arena=host_arena,
            manifest=None,
            extra_env={"HOME": "/tmp/elsewhere"},
        )
    with pytest.raises(CanaryContainerError):
        build_container_argv(
            req,
            docker="/usr/bin/docker",
            image=IMAGE,
            name="game",
            host_arena=host_arena,
            manifest=None,
            extra_env={"bad-name": "1"},
        )


def test_game_container_refuses_manifest_mount(tmp_path):
    req = request(tmp_path, candidate=True)
    host_arena = {key: Path(value) for key, value in req["arena"].items()}
    manifest = Path(req["controller"]["manifest_path"])
    with pytest.raises(CanaryContainerError, match="may not be mounted"):
        build_container_argv(
            req, docker="/usr/bin/docker", image=IMAGE, name="game",
            host_arena=host_arena, manifest=manifest,
        )


def test_candidate_broker_has_ipc_and_manifest_but_no_episode_mount(tmp_path):
    req = request(tmp_path, candidate=True)
    episode = Path(req["arena"]["episode_root"])
    ipc = episode / ".candidate-ipc"
    ipc.mkdir()
    manifest = Path(req["controller"]["manifest_path"])
    argv = build_candidate_broker_argv(
        docker="/usr/bin/docker", image=IMAGE, name="broker", ipc_dir=ipc, manifest=manifest
    )
    joined = " ".join(argv)
    assert "--detach" in argv
    assert f"src={ipc},dst=/canary/ipc" in joined
    assert f"src={ipc},dst=/canary/ipc,readonly" not in joined
    assert f"src={manifest},dst=/canary/candidate.json,readonly" in joined
    assert f"src={episode},dst=/canary/episode" not in joined
    assert "/canary/episode/playground" not in joined
    assert joined.count("type=bind") == 2


def test_podman_and_mutable_image_are_fail_closed(tmp_path):
    req = request(tmp_path)
    with pytest.raises(CanaryContainerError, match="Docker/runsc"):
        run_container_worker(json.dumps(req), docker="podman", image=IMAGE)
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        with pytest.raises(CanaryContainerError, match="immutable sha256"):
            run_container_worker(json.dumps(req), docker="docker", image="canary:latest")


def test_runsc_capability_is_required_before_container_start(tmp_path):
    req = request(tmp_path)
    runner = DockerRunner(req, runsc=False)
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        with pytest.raises(CanaryContainerError, match="runsc"):
            run_container_worker(json.dumps(req), docker="docker", image=IMAGE, runner=runner)
    assert not any(len(call) > 1 and call[1] == "run" for call in runner.calls)


def test_image_abi_and_source_hash_are_attested(tmp_path):
    req = request(tmp_path)
    runner = DockerRunner(req, inspect=image_inspect(abi="2"))
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        with pytest.raises(CanaryContainerError, match="ABI"):
            run_container_worker(json.dumps(req), docker="docker", image=IMAGE, runner=runner)


def test_candidate_run_starts_broker_then_game_without_manifest_in_game(tmp_path, monkeypatch):
    req = request(tmp_path, candidate=True)
    runner = DockerRunner(req)
    monkeypatch.setenv("DOCICH_CANARY_INNER_TIMEOUT_S", "120")
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        result = run_container_worker(
            json.dumps(req), docker="docker", image=IMAGE, runner=runner,
            wait_for_broker=lambda path, timeout: None,
        )
    run_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "run"]
    assert len(run_calls) == 2
    broker, game = run_calls
    assert "--detach" in broker
    assert "/canary/candidate.json" in " ".join(broker)
    assert f"src={Path(req['arena']['episode_root'])},dst=/canary/episode" not in " ".join(broker)
    assert "--detach" not in game
    assert "/canary/candidate.json" not in " ".join(game)
    assert runner.internal["controller"]["broker_socket"] == "/canary/episode/.candidate-ipc/candidate.sock"
    assert "manifest_path" not in runner.internal["controller"]
    assert result["candidate_action_source"] == "candidate_strategist"
    rm_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "rm"]
    assert len(rm_calls) == 2


def test_container_result_cannot_claim_another_arena(tmp_path):
    req = request(tmp_path)
    def mutate(result):
        result["arena"]["save_dir"] = "/var/games/nethack/save"
    runner = DockerRunner(req, mutate_result=mutate)
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        with pytest.raises(CanaryContainerError, match="arena attestation"):
            run_container_worker(json.dumps(req), docker="docker", image=IMAGE, runner=runner)


def test_host_arena_escape_is_rejected_before_docker(tmp_path):
    req = request(tmp_path)
    req["arena"]["save_dir"] = str((tmp_path / "outside").resolve())
    with patch("docich.nethack_canary_container.shutil.which") as which:
        with pytest.raises(CanaryContainerError, match="escapes"):
            run_container_worker(json.dumps(req), docker="docker", image=IMAGE)
    which.assert_not_called()


def test_invalid_isolation_requirement_is_rejected_before_game_run(tmp_path):
    req = request(tmp_path)
    req["requirements"]["isolation_mode"] = "vm"
    runner = DockerRunner(req)
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        with pytest.raises(CanaryContainerError, match="container isolation"):
            run_container_worker(json.dumps(req), docker="docker", image=IMAGE, runner=runner)
    assert not any(len(call) > 1 and call[1] == "run" for call in runner.calls)


def test_timeout_still_attempts_game_cleanup(tmp_path, monkeypatch):
    req = request(tmp_path)
    runner = DockerRunner(req, timeout_run=True)
    monkeypatch.setenv("DOCICH_CANARY_INNER_TIMEOUT_S", "10")
    with patch("docich.nethack_canary_container.shutil.which", return_value="/usr/bin/docker"):
        result = run_container_worker(json.dumps(req), docker="docker", image=IMAGE, runner=runner)
    assert result["worker_status"] == "timeout"
    run_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "run"]
    rm_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "rm"]
    assert len(run_calls) == 1
    assert len(rm_calls) == 1
