"""Host contracts and transport limits; candidate Python is never executed here."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy.contract import (  # noqa: E402
    Artifact, MAX_INPUT, MAX_OUTPUT, MAX_SOURCE, StrategyError, decode, encode,
    read_source, validate_decision,
)
from docich.trading.free_strategy.sandbox import DockerSandbox, SandboxError  # noqa: E402

IMAGE = "sha256:" + "a" * 64
DECISION = {"schema_version": 1, "target_positions": [], "state": {}, "reason": "待機"}


def artifact(**overrides):
    values = dict(source="def decide(context): return {}", image=IMAGE, name="試験",
                  family="test", thesis="契約検証", symbols=["BTC/JPY", "ETH/JPY"])
    values.update(overrides)
    return Artifact.create(**values)


class FakeDaemon(DockerSandbox):
    """A stateful Docker control fake, deliberately incapable of running source."""

    def __init__(self, state_dir):
        super().__init__(IMAGE, state_dir=state_dir)
        self.info = {"OSType": "linux", "Runtimes": {"runsc": {}},
                     "MemoryLimit": True, "PidsLimit": True, "CPUCfsQuota": True}
        self.images = [{"Id": IMAGE, "Config": {
            "Labels": {"org.docich.paper-strategy.abi": "1"}, "Volumes": None,
        }}]
        self.calls = []
        self.containers = set()
        self.output = encode(DECISION)
        self.attach_error = None
        self.create_error = False
        self.remove_error = False
        self.envelopes = []

    def _command(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "info":
            return encode(self.info)
        if args[:2] == ("image", "inspect"):
            return encode(self.images)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            self.containers.add(name)
            if self.create_error:
                raise SandboxError("sandbox_control_failed")
            return b"b" * 64
        if args[:2] == ("container", "ls"):
            expected = args[args.index("--filter") + 1]
            name = expected.removeprefix("name=^/").removesuffix("$")
            return name.encode() if name in self.containers else b""
        if args[0] == "rm":
            if self.remove_error:
                raise SandboxError("sandbox_cleanup_failed")
            self.containers.discard(args[-1])
            return b""
        raise AssertionError(f"Unexpected trusted control call: {args[0]}")

    def _attach(self, name, payload):
        self.calls.append(("attach", name))
        self.envelopes.append(decode(payload, limit=MAX_INPUT))
        if self.attach_error is not None:
            raise self.attach_error
        return self.output


@pytest.mark.parametrize("raw", [
    b"", b"null trailing", b"\xff", b'{"state":{},"state":{}}',
    b'{"state":{"a":1,"a":2}}', b'{"v":NaN}', b'{"v":Infinity}',
    b'{"v":-Infinity}', b'{"v":1e9999}', b'{"v":"\\ud800"}',
])
def test_json_rejects_malformed_duplicate_nonfinite_and_invalid_unicode(raw):
    with pytest.raises(StrategyError):
        decode(raw)


def test_json_enforces_bytes_and_depth_limits_before_use():
    assert decode(b" " * (MAX_OUTPUT - 2) + b"{}") == {}
    with pytest.raises(StrategyError, match="json_size_limit"):
        decode(b" " * MAX_OUTPUT + b"{}")
    assert decode(b"[" * 16 + b"0" + b"]" * 16)
    with pytest.raises(StrategyError, match="json_depth_limit"):
        decode(b"[" * 17 + b"0" + b"]" * 17)
    with pytest.raises(StrategyError):
        encode({"secret": float("nan")})


@pytest.mark.parametrize("updates", [
    {"account_id": "other-account"}, {"mode": "live"}, {"approved": True},
    {"schema_version": True}, {"schema_version": 2}, {"state": []},
    {"reason": "x" * 401}, {"target_positions": {}},
    {"target_positions": [None]},
    {"target_positions": [{"symbol": "BTC/JPY", "target_base_quantity": "0", "order_id": "forged"}]},
    {"target_positions": [{"symbol": "USD/JPY", "target_base_quantity": "0"}]},
    {"target_positions": [{"symbol": "BTC/JPY", "target_base_quantity": "0"}] * 2},
])
def test_decision_rejects_host_authority_fields_and_invalid_shape(updates):
    with pytest.raises(StrategyError):
        validate_decision(encode({**DECISION, **updates}), ["BTC/JPY", "ETH/JPY"])


@pytest.mark.parametrize("quantity", [
    -1, 1, 1.5, True, None, "-1", "-0", "+1", "01", "1e2", "NaN",
    "Infinity", "1 ", " 1", "0.0000000000000000001", "1" * 31,
])
def test_decision_quantities_are_bounded_nonnegative_decimal_strings(quantity):
    value = {**DECISION, "target_positions": [
        {"symbol": "BTC/JPY", "target_base_quantity": quantity},
    ]}
    with pytest.raises(StrategyError, match="invalid_quantity"):
        validate_decision(encode(value), ["BTC/JPY"])


def test_omission_and_explicit_zero_remain_distinct_decisions():
    assert validate_decision(encode(DECISION), ["BTC/JPY"])["target_positions"] == []
    explicit = {**DECISION, "target_positions": [
        {"symbol": "BTC/JPY", "target_base_quantity": "0"},
    ], "state": {"observations": 3}}
    assert validate_decision(encode(explicit), ["BTC/JPY", "ETH/JPY"]) == explicit
    assert "ETH/JPY" not in encode(explicit).decode()


def test_artifact_registration_and_host_run_never_evaluate_candidate(tmp_path):
    marker = tmp_path / "host-must-not-be-written"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('candidate')"
    source_path = tmp_path / "candidate.py"
    source_path.write_text(source, encoding="utf-8")
    candidate = artifact(source=read_source(source_path))
    runner = FakeDaemon(tmp_path / "runner")
    assert runner.run(candidate, {"seed": 7}) == DECISION
    assert runner.envelopes[0]["source"] == source
    assert not marker.exists()


def test_artifact_hash_covers_source_parameters_initial_state_and_runtime():
    first = artifact(parameters={"threshold": 2}, initial_state={"observations": 0})
    same = artifact(parameters={"threshold": 2}, initial_state={"observations": 0}, symbols=["ETH/JPY", "BTC/JPY"])
    assert first.digest == same.digest
    assert len({first.digest, artifact(source="changed").digest,
                artifact(parameters={"threshold": 3}).digest,
                artifact(initial_state={"observations": 1}).digest,
                artifact(image="sha256:" + "b" * 64).digest}) == 5


@pytest.mark.parametrize("image", ["python:latest", "python:3.12-slim", "sha256:123", "a@sha256:" + "z" * 64])
def test_mutable_or_invalid_images_are_never_accepted(image, tmp_path):
    with pytest.raises(StrategyError, match="immutable_image_required"):
        artifact(image=image)
    with pytest.raises(SandboxError, match="immutable_image_required"):
        DockerSandbox(image, state_dir=tmp_path)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_source_reader_refuses_links_and_nonregular_files(tmp_path, kind):
    original = tmp_path / "original.py"
    original.write_text("def decide(c): return {}")
    path = tmp_path / "candidate.py"
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, path)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(StrategyError):
        read_source(path)


@pytest.mark.parametrize("source", ["", " \n", "x\x00y", "a" * (MAX_SOURCE + 1), "\ud800"])
def test_artifact_source_is_bounded_text(source):
    with pytest.raises(StrategyError):
        artifact(source=source)


@pytest.mark.parametrize("field,value", [
    ("OSType", "windows"), ("Runtimes", {"runc": {}}),
    ("MemoryLimit", False), ("PidsLimit", False), ("CPUCfsQuota", False),
])
def test_no_container_is_created_without_gvisor_and_resource_limits(tmp_path, field, value):
    runner = FakeDaemon(tmp_path)
    runner.info[field] = value
    with pytest.raises(SandboxError):
        runner.run(artifact(), {"seed": 1})
    assert not any(call[0] in {"create", "attach"} for call in runner.calls)
    assert not (tmp_path / "sandbox.active").exists()


@pytest.mark.parametrize("config", [
    {"Labels": {}}, {"Labels": {"org.docich.paper-strategy.abi": "2"}},
    {"Labels": {"org.docich.paper-strategy.abi": "1"}, "Volumes": {"/host": {}}},
])
def test_image_abi_and_no_volume_contract_is_required(tmp_path, config):
    runner = FakeDaemon(tmp_path)
    runner.images[0]["Config"] = config
    with pytest.raises(SandboxError, match="runtime_image_invalid"):
        runner.run(artifact(), {"seed": 1})
    assert not runner.containers


def test_source_and_context_travel_only_over_stdin_and_container_is_removed(tmp_path):
    runner = FakeDaemon(tmp_path)
    candidate = artifact(source="# candidate contents should not appear in commands")
    assert runner.run(candidate, {"seed": 42, "state": {"private": "example-only"}}) == DECISION
    create = next(call for call in runner.calls if call[0] == "create")
    assert create[create.index("--runtime") + 1] == "runsc"
    assert create[create.index("--network") + 1] == "none"
    assert "--read-only" in create and "--privileged" not in create
    assert all(flag not in create for flag in ("--mount", "--volume", "-v", "--env", "-e", "--env-file"))
    assert "candidate contents" not in str(runner.calls)
    assert "example-only" not in str(runner.calls)
    assert not runner.containers
    assert not (tmp_path / "sandbox.active").exists()


@pytest.mark.parametrize("failure", ["invalid_json", "timeout", "output", "create"])
def test_every_failure_cleans_container_before_return(tmp_path, failure):
    runner = FakeDaemon(tmp_path)
    if failure == "invalid_json":
        runner.output = b'{"not_a_decision":true}'
    elif failure == "create":
        runner.create_error = True
    else:
        runner.attach_error = SandboxError("sandbox_timeout" if failure == "timeout" else "sandbox_output_limit")
    with pytest.raises(StrategyError):
        runner.run(artifact(), {"seed": 1})
    assert not runner.containers
    assert not (tmp_path / "sandbox.active").exists()


def test_cleanup_failure_rejects_valid_decision_and_retry_reaps_exact_orphan(tmp_path):
    runner = FakeDaemon(tmp_path)
    runner.remove_error = True
    with pytest.raises(SandboxError, match="sandbox_cleanup_failed"):
        runner.run(artifact(), {"seed": 1})
    orphan = (tmp_path / "sandbox.active").read_text()
    assert runner.containers == {orphan}
    assert (tmp_path / "sandbox.active").stat().st_mode & 0o777 == 0o600
    runner.remove_error = False
    runner.calls.clear()
    runner.run(artifact(), {"seed": 2})
    remove_index = runner.calls.index(("rm", "--force", orphan))
    create_index = next(i for i, call in enumerate(runner.calls) if call[0] == "create")
    assert remove_index < create_index
    assert not runner.containers


def test_invalid_cleanup_receipt_and_concurrent_run_cannot_start_container(tmp_path):
    runner = FakeDaemon(tmp_path)
    (tmp_path / "sandbox.active").write_text("unrelated-production-container")
    with pytest.raises(SandboxError, match="invalid_cleanup_receipt"):
        runner.run(artifact(), {"seed": 1})
    assert not any(call[0] in {"rm", "create"} for call in runner.calls)
    (tmp_path / "sandbox.active").unlink()
    with runner._singleflight():
        with pytest.raises(SandboxError, match="sandbox_busy"):
            FakeDaemon(tmp_path).run(artifact(), {"seed": 1})


def test_mismatched_runtime_and_excessive_input_fail_before_docker(tmp_path):
    runner = FakeDaemon(tmp_path)
    with pytest.raises(SandboxError, match="runtime_image_mismatch"):
        runner.run(artifact(image="sha256:" + "b" * 64), {"seed": 1})
    with pytest.raises(SandboxError, match="sandbox_input_limit"):
        runner.run(artifact(), {"seed": 1, "padding": "x" * MAX_INPUT})
    assert runner.calls == []


@pytest.mark.parametrize("program,expected", [
    ("import os; os.write(1, b'x' * 131072)", "sandbox_output_limit"),
    ("import os; os.write(2, b'x' * 131072)", "sandbox_output_limit"),
    ("import time; time.sleep(30)", "sandbox_timeout"),
    ("import sys; sys.exit(1)", "strategy_execution_failed"),
])
def test_attach_enforces_pipe_and_wall_limits_and_reaps_control_client(tmp_path, monkeypatch, program, expected):
    # This is a trusted transport fixture, not candidate source or the guest.
    real_popen = subprocess.Popen
    clients = []

    def control_client(command, **kwargs):
        assert command[:4] == ["docker", "start", "--attach", "--interactive"]
        proc = real_popen([sys.executable, "-I", "-u", "-c", program], **kwargs)
        clients.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", control_client)
    runner = DockerSandbox(IMAGE, state_dir=tmp_path, wall_seconds=0.5)
    started = time.monotonic()
    with pytest.raises(SandboxError, match=expected):
        runner._attach("docich-paper-" + "0" * 32, b"{}")
    assert time.monotonic() - started < 3
    assert clients and all(proc.poll() is not None for proc in clients)
    assert all(stream.closed for proc in clients for stream in (proc.stdin, proc.stdout, proc.stderr))


def test_transport_errors_do_not_expose_docker_stderr(tmp_path, monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, b"", b"private diagnostic marker")

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(SandboxError) as error:
        DockerSandbox(IMAGE, state_dir=tmp_path).preflight()
    assert str(error.value) == "sandbox_control_failed"
    assert "private" not in str(error.value)
