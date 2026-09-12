"""Actual gVisor isolation tests, mandatory in the dedicated Linux CI job.

Without DOCICH_TEST_SANDBOX_IMAGE these skip; when it is set, unavailable
Docker/runsc, missing resource controls, or a bad image fail instead of skipping.
Only the configured gVisor runner executes the candidate strings below.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy.contract import Artifact, StrategyError  # noqa: E402
from docich.trading.free_strategy.sandbox import DockerSandbox, SandboxError  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOCICH_TEST_SANDBOX_IMAGE"),
    reason="Set DOCICH_TEST_SANDBOX_IMAGE to an immutable, built guest image on a gVisor host",
)


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=15, check=False)
    if check and result.returncode:
        pytest.fail(f"Docker control failed: {args[0]}", pytrace=False)
    return result


class RecordingSandbox(DockerSandbox):
    def __init__(self, image, *, state_dir):
        super().__init__(image, state_dir=state_dir)
        self.names = []
        self.inspections = []
        self.exit_states = []

    def _create_args(self, name):
        self.names.append(name)
        return super()._create_args(name)

    def _attach(self, name, payload):
        self.inspections.append(json.loads(docker("inspect", name).stdout)[0])
        return super()._attach(name, payload)

    def _remove(self, name):
        result = docker("inspect", name, check=False)
        if result.returncode == 0:
            self.exit_states.append(json.loads(result.stdout)[0]["State"])
        return super()._remove(name)


@pytest.fixture
def runner(tmp_path):
    sandbox = RecordingSandbox(os.environ["DOCICH_TEST_SANDBOX_IMAGE"], state_dir=tmp_path / "runner")
    assert sandbox.preflight()["runtime"] == "runsc"
    yield sandbox
    leaked = []
    for name in sandbox.names:
        found = docker("container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
        if found.stdout.strip():
            leaked.append(name)
            docker("rm", "--force", name)
    assert not leaked, "Every finished or failed decision must remove its whole container"
    assert not (sandbox.state_dir / "sandbox.active").exists()


def run_source(runner, source, **context):
    candidate = Artifact.create(source=source, image=runner.image, name="隔離試験",
                                family="isolation", thesis="CIで隔離を実測します", symbols=["BTC/JPY"])
    return runner.run(candidate, {"seed": 7, "state": {}, **context})


def test_real_gvisor_accepts_normal_python_and_keeps_only_explicit_json_state(runner):
    source = '''
import random
def decide(context):
    state = {"count": context["state"].get("count", 0) + 1, "sample": random.random()}
    return {"schema_version": 1, "target_positions": [{"symbol":"BTC/JPY","target_base_quantity":"0.001"}],
            "state": state, "reason": "独立した模擬口座への提案"}
'''
    first = run_source(runner, source)
    second = run_source(runner, source, state={"count": 1})
    assert first["state"]["count"] == 1 and second["state"]["count"] == 2
    assert first["state"]["sample"] == second["state"]["sample"]
    assert first["target_positions"][0]["target_base_quantity"] == "0.001"


def test_real_gvisor_host_files_env_network_and_guest_root_are_isolated(runner, tmp_path, monkeypatch):
    host_file = tmp_path / "host-only-sentinel"
    host_file.write_text("this is test data, not a credential")
    monkeypatch.setenv("DOCICH_TEST_HOST_SECRET", "must-not-enter-guest")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        result = run_source(runner, '''
import os, pathlib, socket
def decide(context):
    state = {"uid": os.getuid(), "host_file_visible": pathlib.Path(context["host_file"]).exists(),
             "host_env_visible": "DOCICH_TEST_HOST_SECRET" in os.environ,
             "docker_socket_visible": pathlib.Path("/var/run/docker.sock").exists()}
    try:
        pathlib.Path("/opt/docich/guest.py").write_text("changed")
        state["root_write_blocked"] = False
    except OSError:
        state["root_write_blocked"] = True
    connections = []
    for host, port in [("127.0.0.1", context["host_port"]), ("192.0.2.1", 80), ("169.254.169.254", 80)]:
        with socket.socket() as connection:
            connection.settimeout(0.2)
            try:
                connections.append(connection.connect_ex((host, port)) == 0)
            except OSError:
                connections.append(False)
    state["connected"] = connections
    pathlib.Path("/tmp/allowed-scratch").write_text("temporary")
    state["scratch_writable"] = True
    return {"schema_version":1,"target_positions":[],"state":state,"reason":"隔離検査"}
''', host_file=str(host_file), host_port=port)
    state = result["state"]
    assert state == {"uid": 65532, "host_file_visible": False, "host_env_visible": False,
                     "docker_socket_visible": False, "root_write_blocked": True,
                     "connected": [False, False, False], "scratch_writable": True}
    assert host_file.read_text() == "this is test data, not a credential"


def test_real_container_resource_configuration_and_scratch_are_bounded(runner):
    result = run_source(runner, '''
import errno, os, pathlib, resource
def decide(context):
    written = 0
    error = None
    for number in range(32):
        try:
            with open("/tmp/fill-" + str(number), "wb") as stream:
                written += stream.write(b"x" * (4 * 1024 * 1024))
        except OSError as failure:
            error = failure.errno
            break
    state = {"written": written, "error": error, "nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0]}
    return {"schema_version":1,"target_positions":[],"state":state,"reason":"資源検査"}
''')
    assert result["state"]["error"] == 28  # ENOSPC, not a simulated host exception.
    assert 0 < result["state"]["written"] <= 64 * 1024 * 1024
    assert result["state"]["nofile"] <= 64
    inspected = runner.inspections[-1]
    host = inspected["HostConfig"]
    assert host["Runtime"] == "runsc"
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True and host["Privileged"] is False
    assert host["Memory"] == 512 * 1024 * 1024
    assert host["MemorySwap"] == 512 * 1024 * 1024
    assert host["NanoCpus"] == 1_000_000_000
    assert 0 < host["PidsLimit"] <= 256
    assert host["PidMode"] != "host" and host["IpcMode"] != "host"
    assert "ALL" in host["CapDrop"]
    assert any(item.startswith("no-new-privileges") for item in host["SecurityOpt"])
    assert not any(mount["Type"] in {"bind", "volume"} for mount in inspected.get("Mounts", []))
    assert inspected["Config"]["User"] == "65532:65532"


def test_guest_files_never_persist_to_another_decision(runner):
    source = '''
import pathlib
def decide(context):
    file = pathlib.Path("/tmp/previous-decision")
    exists = file.exists()
    file.write_text("must disappear")
    return {"schema_version":1,"target_positions":[],"state":{"seen_previous_file":exists},"reason":"一時領域"}
'''
    assert run_source(runner, source)["state"]["seen_previous_file"] is False
    assert run_source(runner, source)["state"]["seen_previous_file"] is False


@pytest.mark.parametrize("source", [
    'import os\nos.write(1,b\'{"schema_version":1,"target_positions":[],"state":{},"state":{},"reason":""}\')\nos._exit(0)',
    'def decide(c): return {"schema_version":1,"target_positions":[],"state":{"bad":float("nan")},"reason":""}',
    'def decide(c): return {"schema_version":1,"target_positions":[],"state":{},"reason":"","approved":True}',
])
def test_real_guest_cannot_bypass_host_decision_validation(runner, source):
    with pytest.raises(StrategyError):
        run_source(runner, source)


@pytest.mark.parametrize("descriptor", [1, 2])
def test_real_guest_stdout_and_stderr_floods_are_stopped_and_cleaned(runner, descriptor):
    source = f"import os\nwhile True:\n    os.write({descriptor}, b'x' * 8192)"
    with pytest.raises(SandboxError, match="sandbox_output_limit"):
        run_source(runner, source)


def test_cpu_loop_and_uncooperative_descendant_are_killed_with_container(runner):
    source = '''
import os, signal, time
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)
while True:
    pass
'''
    started = time.monotonic()
    with pytest.raises(SandboxError, match="sandbox_timeout"):
        run_source(runner, source)
    assert time.monotonic() - started < 25
    for name in runner.names:
        assert not docker("container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}").stdout.strip()


def test_guest_process_count_is_actually_limited(runner):
    result = run_source(runner, '''
import os, time
def decide(context):
    children = []
    limited = False
    for number in range(64):
        try:
            child = os.fork()
        except OSError:
            limited = True
            break
        if child == 0:
            os.close(1)
            os.close(2)
            time.sleep(30)
            os._exit(0)
        children.append(child)
    return {"schema_version":1,"target_positions":[],"state":{"limited":limited,"children":len(children)},"reason":"子プロセス上限"}
''')
    assert result["state"]["limited"] is True
    assert result["state"]["children"] < 32


def test_guest_memory_exhaustion_is_contained_and_reaped(runner):
    source = '''
def decide(context):
    chunks = []
    try:
        for number in range(128):
            chunks.append(bytearray(8 * 1024 * 1024))
    except MemoryError:
        return {"schema_version":1,"target_positions":[],"state":{"memory_limited":True},"reason":"メモリ上限"}
    return {"schema_version":1,"target_positions":[],"state":{"memory_limited":False},"reason":"上限なし"}
'''
    try:
        result = run_source(runner, source)
    except SandboxError as error:
        assert str(error) == "strategy_execution_failed"
        assert any(state.get("OOMKilled") or state.get("ExitCode") == 137 for state in runner.exit_states)
    else:
        assert result["state"]["memory_limited"] is True
