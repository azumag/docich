"""Synthetic /proc fixtures; no live runtime, signal or production write."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import selectors
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]


def tracked_script_repo(tmp_path, script):
    root = tmp_path / "tracked"
    repo = root / "games/soviet_now"
    repo.mkdir(parents=True)
    (repo / "start_all.sh").write_bytes(script)
    (repo / "update_stream_game.sh").write_bytes(b"# public updater\n")
    (repo / "lib").mkdir()
    (repo / "lib/stream_title_sync.py").write_bytes(b"# public title helper\n")
    (repo / "README.md").write_text("unreviewed synthetic source")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git = ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null"]
    subprocess.run(git + ["config", "user.name", "Test"], check=True)
    subprocess.run(git + ["config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["-c", "commit.gpgsign=false", "commit", "-qm", "reviewed script"], check=True)
    sha = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    return root, repo, sha


@pytest.fixture
def fixture(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "identity_collector", ROOT / "ops/vm_actions/collect_diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    soren = tmp_path / "runtime"
    (soren / "tmp/state").mkdir(parents=True)
    (soren / "tmp/state/start_all.pid").write_text("4242\n")
    script = b"# reviewed public script\n"
    (soren / "start_all.sh").write_bytes(script)
    proc = tmp_path / "proc"
    (proc / "4242/fd").mkdir(parents=True)
    (proc / "4242/exe").symlink_to("/usr/bin/bash")
    (proc / "4242/fd/255").symlink_to(soren / "start_all.sh")
    # A hostile comm includes delimiters. It must never reach output.
    fields = ["S"] + ["0"] * 18 + ["1000"] + ["0"] * 10
    (proc / "4242/stat").write_text("4242 (PRIVATE ) comm) " + " ".join(fields))
    (proc / "stat").write_text("cpu 1 2 3\nbtime 1000000\n")
    expected = hashlib.sha256(script).hexdigest()
    with mock.patch.object(module, "_supervisor_unit_pid", return_value=4242), \
            mock.patch.object(module, "_stream_title_sync_git_blob_sha256", return_value=expected), \
            mock.patch.object(module.os, "sysconf", return_value=100):
        yield module, soren, proc, expected


def collect(fixture):
    module, soren, proc, _ = fixture
    return module._collect_supervisor_identity(soren, "a" * 40, proc)


def test_matching_source_never_certifies_loaded_functions(fixture):
    result = collect(fixture)
    assert result["status"] == "observed"
    assert result["process_started_at"] == 1000010
    assert result["pidfile_matches_unit"] is True
    assert result["identity_stable"] is True
    assert result["deployed_matches_expected"] is True
    assert result["open_script_matches_expected"] is True
    assert result["loaded_functions_status"] == "unverified"
    text = json.dumps(result)
    for private in ["4242", "PRIVATE", str(fixture[1]), "/usr/bin/bash", "reviewed public script"]:
        assert private not in text
    assert len(text) < 1500


def test_expected_hash_and_comparisons_use_real_git_helper(fixture, tmp_path):
    _, soren, proc, expected = fixture
    # Fresh module: the existing synthetic fixture's blob helper is stubbed.
    spec = importlib.util.spec_from_file_location("git_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tracked, _, sha = tracked_script_repo(tmp_path, (soren / "start_all.sh").read_bytes())
    with mock.patch.object(module, "PROD_ROOT", tracked), \
            mock.patch.object(module, "_supervisor_unit_pid", return_value=4242):
        result = module._collect_supervisor_identity(soren, sha, proc)
    assert result["expected_script_sha256"] == expected
    assert result["deployed_matches_expected"] is True
    assert result["open_script_matches_expected"] is True
    assert result["loaded_functions_status"] == "unverified"


@pytest.mark.parametrize("relative_path", ["start_all.sh", "update_stream_game.sh", "lib/stream_title_sync.py"])
def test_real_blob_reader_permits_only_fixed_public_sources(tmp_path, relative_path):
    spec = importlib.util.spec_from_file_location("blob_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, repo, sha = tracked_script_repo(tmp_path, b"# reviewed script\n")
    assert module._stream_title_sync_git_blob_sha256(repo, sha, relative_path) == hashlib.sha256(
        (repo / relative_path).read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("relative_path", ["README.md", ".env", "../start_all.sh", "/start_all.sh",
                                          "tmp/state/start_all.pid", "lib/../start_all.sh"])
def test_real_blob_reader_rejects_other_paths_before_git(tmp_path, relative_path):
    spec = importlib.util.spec_from_file_location("blocked_blob_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, repo, sha = tracked_script_repo(tmp_path, b"# reviewed script\n")
    with mock.patch.object(module.subprocess, "run", side_effect=AssertionError("unapproved Git read")):
        assert module._stream_title_sync_git_blob_sha256(repo, sha, relative_path) is None


@pytest.mark.parametrize("case", ["missing", "oversized"])
def test_real_git_unknown_expected_source_keeps_comparisons_unknown(fixture, tmp_path, case):
    _, soren, proc, _ = fixture
    spec = importlib.util.spec_from_file_location("unknown_git_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    content = b"x" * (512 * 1024 + 1) if case == "oversized" else b"# reviewed script\n"
    tracked, repo, sha = tracked_script_repo(tmp_path, content)
    if case == "missing":
        git = ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null"]
        subprocess.run(git + ["rm", "-q", "start_all.sh"], check=True)
        subprocess.run(git + ["-c", "commit.gpgsign=false", "commit", "-qm", "source missing"], check=True)
        sha = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    with mock.patch.object(module, "PROD_ROOT", tracked), \
            mock.patch.object(module, "_supervisor_unit_pid", return_value=4242):
        result = module._collect_supervisor_identity(soren, sha, proc)
    assert result["expected_script_sha256"] is None
    assert result["deployed_matches_expected"] is None
    assert result["open_script_matches_expected"] is None
    assert result["loaded_functions_status"] == "unverified"


def test_deleted_old_script_is_distinct_from_current_disk(fixture, tmp_path):
    module, soren, proc, expected = fixture
    old = tmp_path / "old-script"
    old.write_bytes(b"# previous public script\n")
    descriptor = proc / "4242/fd/255"
    descriptor.unlink()
    descriptor.symlink_to(old)
    real_readlink = module.os.readlink
    def readlink(path, dir_fd=None):
        return (str(soren / "start_all.sh") + " (deleted)" if path == "255"
                else real_readlink(path, dir_fd=dir_fd))
    with mock.patch.object(module.os, "readlink", side_effect=readlink):
        result = collect(fixture)
    assert result["deployed_script_sha256"] == expected
    assert result["open_script_sha256"] == hashlib.sha256(old.read_bytes()).hexdigest()
    assert result["open_script_matches_expected"] is False
    assert result["reason"] == "open_script_differs_expected"
    assert result["loaded_functions_status"] == "unverified"


def test_in_place_update_leaves_already_defined_bash_function_old(fixture, tmp_path):
    _, soren, _, _ = fixture
    script = soren / "start_all.sh"
    reviewed = tmp_path / "reviewed-script"
    old = b"identity_probe() { printf '%s\\n' OLD; }\n"
    new = b"identity_probe() { printf '%s\\n' NEW; }\n"
    script.write_bytes(old)
    reviewed.write_bytes(new)
    proc = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; cp "$2" "$1"; identity_probe',
         "test", str(script), str(reviewed)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3, check=True,
    )
    assert proc.stdout == b"OLD\n"
    with mock.patch.object(fixture[0], "_stream_title_sync_git_blob_sha256", return_value=hashlib.sha256(new).hexdigest()):
        result = collect(fixture)
    assert result["deployed_matches_expected"] is True
    assert result["open_script_matches_expected"] is True
    assert result["loaded_functions_status"] == "unverified"


def test_same_pid_exec_cannot_be_identified_from_start_ticks_alone(fixture):
    result = collect(fixture)
    assert result["open_script_matches_expected"] is True
    assert result["loaded_functions_status"] == "unverified"


@pytest.mark.parametrize("change", ["ticks", "pidfile", "unit", "exit"])
def test_identity_change_discards_process_evidence(fixture, change):
    module, soren, proc, _ = fixture
    original = module._supervisor_process_start
    calls = 0
    def start(root, pid, process_fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "ticks":
                return 1001
            if change == "pidfile":
                (soren / "tmp/state/start_all.pid").write_text("4243\n")
            if change == "exit":
                raise FileNotFoundError("PRIVATE disappearance")
        return original(root, pid, process_fd)
    with mock.patch.object(module, "_supervisor_process_start", side_effect=start):
        if change == "unit":
            with mock.patch.object(module, "_supervisor_unit_pid", side_effect=[4242, 4243]):
                result = collect(fixture)
        else:
            result = collect(fixture)
    assert result["reason"] == "identity_changed"
    assert result["identity_stable"] is False
    assert result["open_script_sha256"] is None
    assert result["process_started_at"] is None


@pytest.mark.parametrize("unit", [None, 4243])
def test_unverified_unit_never_reads_process_fd(fixture, unit):
    module, _, _, _ = fixture
    with mock.patch.object(module, "_supervisor_unit_pid", return_value=unit), \
            mock.patch.object(module, "_supervisor_process_start") as process:
        result = collect(fixture)
    process.assert_not_called()
    assert result["reason"] in {"unit_unavailable", "unit_pid_mismatch"}


@pytest.mark.parametrize("case", ["missing", "malformed", "oversized", "symlink", "fifo"])
def test_bad_pidfile_is_bounded_without_unit_or_proc_read(fixture, tmp_path, case):
    module, soren, _, _ = fixture
    path = soren / "tmp/state/start_all.pid"
    path.unlink()
    if case == "malformed":
        path.write_text("PRIVATE secret")
    elif case == "oversized":
        path.write_text("1" * 1000)
    elif case == "symlink":
        secret = tmp_path / "private-file"
        secret.write_text("4242\nPRIVATE")
        path.symlink_to(secret)
    elif case == "fifo":
        os.mkfifo(path)
    with mock.patch.object(module, "_supervisor_unit_pid") as unit:
        result = collect(fixture)
    unit.assert_not_called()
    assert result["reason"] == "pidfile_unavailable"
    assert "PRIVATE" not in json.dumps(result)


def test_script_mutation_during_hash_is_rejected(fixture):
    module, soren, _, _ = fixture
    script = soren / "start_all.sh"
    read = module.os.read
    changed = False
    def mutate(fd, length):
        nonlocal changed
        data = read(fd, length)
        if data and not changed:
            changed = True
            script.write_bytes(b"changed public script\n")
        return data
    with mock.patch.object(module.os, "read", side_effect=mutate):
        with pytest.raises(ValueError):
            module._supervisor_script_fingerprint(script)


def test_source_growth_never_reads_past_fixed_limit_plus_sentinel(fixture):
    module, soren, _, _ = fixture
    script = soren / "start_all.sh"
    script.write_bytes(b"x" * (512 * 1024))
    read = module.os.read
    total = 0
    def grow(fd, length):
        nonlocal total
        data = read(fd, length)
        if total == 0:
            with script.open("ab") as target:
                target.write(b"x" * (512 * 1024))
        total += len(data)
        return data
    with mock.patch.object(module.os, "read", side_effect=grow):
        with pytest.raises(ValueError):
            module._supervisor_script_fingerprint(script)
    assert total <= 512 * 1024 + 1


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc; synthetic fixtures cover other hosts")
def test_proc_fd_hash_does_not_move_original_file_offset(fixture):
    module, soren, _, expected = fixture
    script = soren / "start_all.sh"
    fd = os.open(script, os.O_RDONLY)
    try:
        os.lseek(fd, 5, os.SEEK_SET)
        result, _, _ = module._supervisor_script_fingerprint(
            Path(f"/proc/self/fd/{fd}"), str(script)
        )
        assert result == expected
        assert os.lseek(fd, 0, os.SEEK_CUR) == 5
    finally:
        os.close(fd)


@pytest.mark.skipif(sys.platform != "linux", reason="requires a live Linux Bash /proc entry")
def test_live_linux_bash_retains_deleted_old_script(tmp_path):
    spec = importlib.util.spec_from_file_location("live_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    soren = tmp_path / "runtime"
    (soren / "tmp/state").mkdir(parents=True)
    script = soren / "start_all.sh"
    old = b"identity_probe() { printf '%s\\n' OLD; }\nprintf 'READY\\n'\nread -r token\nidentity_probe\n"
    new = old.replace(b"OLD", b"NEW")
    tracked, _, sha = tracked_script_repo(tmp_path, new)
    script.write_bytes(old)
    child = subprocess.Popen(["/bin/bash", str(script)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=3), "local Bash did not become ready"
        assert child.stdout.readline() == b"READY\n"
        (soren / "tmp/state/start_all.pid").write_text(str(child.pid))
        replacement = soren / "replacement"
        replacement.write_bytes(new)
        os.replace(replacement, script)
        with mock.patch.object(module, "_supervisor_unit_pid", return_value=child.pid), \
                mock.patch.object(module, "PROD_ROOT", tracked):
            result = module._collect_supervisor_identity(soren, sha)
        assert result["status"] == "observed"
        assert result["expected_script_sha256"] == hashlib.sha256(new).hexdigest()
        assert result["identity_stable"] is True
        assert result["process_started_at"] is not None
        assert result["deployed_matches_expected"] is True
        assert result["open_script_sha256"] == hashlib.sha256(old).hexdigest()
        assert result["reason"] == "open_script_differs_expected"
        assert result["loaded_functions_status"] == "unverified"
        output, _ = child.communicate(input=b"continue\n", timeout=3)
        assert output == b"OLD\n"
    finally:
        if child.poll() is None:
            child.terminate()  # only this test's local fixture process
            child.communicate(timeout=3)


def test_pid_reuse_never_redirects_fd_read_to_replacement_process(fixture):
    module, soren, proc, _ = fixture
    original_start = module._supervisor_process_start
    original_fingerprint = module._supervisor_script_fingerprint
    changed = False
    def start(root, pid, process_fd):
        nonlocal changed
        ticks = original_start(root, pid, process_fd)
        if not changed:
            changed = True
            (proc / "4242").rename(proc / "retired")
            (proc / "4242/fd").mkdir(parents=True)
            (proc / "4242/exe").symlink_to("/usr/bin/bash")
            data = (proc / "retired/stat").read_text().replace("1000", "2000")
            (proc / "4242/stat").write_text(data)
            (proc / "4242/fd/255").symlink_to(soren / "start_all.sh")
        return ticks
    def fingerprint(path, expected_fd_target=None, dir_fd=None):
        if path == "255":
            assert os.fstat(dir_fd).st_ino == (proc / "retired/fd").stat().st_ino
        return original_fingerprint(path, expected_fd_target, dir_fd)
    with mock.patch.object(module, "_supervisor_process_start", side_effect=start), \
            mock.patch.object(module, "_supervisor_script_fingerprint", side_effect=fingerprint):
        result = collect(fixture)
    assert result["reason"] == "identity_changed"
    assert result["open_script_sha256"] is None


@pytest.mark.parametrize("directory", ["tmp", "state", "process", "fd"])
def test_directory_symlinks_are_not_followed(fixture, directory):
    _, soren, proc, _ = fixture
    path = {"tmp": soren / "tmp", "state": soren / "tmp/state",
            "process": proc / "4242", "fd": proc / "4242/fd"}[directory]
    moved = path.with_name(path.name + "-moved")
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=True)
    result = collect(fixture)
    assert result["status"] == "unavailable"
    assert result["open_script_sha256"] is None


@pytest.mark.parametrize("case", ["missing", "foreign_target", "fifo", "oversized"])
def test_script_fd_allowlist_and_read_bound(fixture, tmp_path, case):
    module, soren, proc, _ = fixture
    descriptor = proc / "4242/fd/255"
    descriptor.unlink()
    if case == "foreign_target":
        secret = tmp_path / "private-file"
        secret.write_text("PRIVATE")
        descriptor.symlink_to(secret)
    elif case in {"fifo", "oversized"}:
        script = soren / "start_all.sh"
        script.unlink()
        if case == "fifo":
            os.mkfifo(script)
        else:
            script.write_bytes(b"PRIVATE" * 100000)
        descriptor.symlink_to(script)
    result = collect(fixture)
    assert result["open_script_sha256"] is None
    assert result["identity_stable"] is True
    assert result["reason"] == "script_fd_unavailable"
    assert result["loaded_functions_status"] == "unverified"
    assert "PRIVATE" not in json.dumps(result)


def test_reads_do_not_modify_runtime_or_send_signals(fixture):
    module, soren, proc, _ = fixture
    def snapshot():
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                for root in (soren, proc) for p in root.rglob("*")
                if p.is_file() and not p.is_symlink()}
    before = snapshot()
    with mock.patch.object(module.os, "kill", side_effect=AssertionError("signal forbidden")):
        collect(fixture)
    assert snapshot() == before


def test_unit_query_has_only_fixed_mainpid_property(fixture):
    # Temporarily override the fixture's unit stub to exercise the real reader.
    spec = importlib.util.spec_from_file_location("real_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    with mock.patch.object(real.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=b"4242\n")) as run:
        assert real._supervisor_unit_pid() == 4242
    assert run.call_args.args[0] == ["systemctl", "show", "soren-runtime.service", "--property=MainPID", "--value"]
    assert run.call_args.kwargs["timeout"] == 2


@pytest.mark.parametrize("failure", ["timeout", "missing", "failed", "zero", "garbage", "oversized"])
def test_fixed_unit_query_failures_return_unknown(failure):
    spec = importlib.util.spec_from_file_location("failed_identity", ROOT / "ops/vm_actions/collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = {"zero": b"0", "garbage": b"PRIVATE arguments", "oversized": b"1" * 1000}.get(failure, b"4242")
    error = (subprocess.TimeoutExpired("systemctl", 2) if failure == "timeout"
             else FileNotFoundError("PRIVATE") if failure == "missing" else None)
    with mock.patch.object(module.subprocess, "run", side_effect=error,
                           return_value=mock.Mock(returncode=1 if failure == "failed" else 0, stdout=output)):
        assert module._supervisor_unit_pid() is None


@pytest.mark.parametrize("case", ["zombie", "foreign_user", "not_bash", "bad_stat", "missing_boot"])
def test_process_metadata_uncertainty_stays_unverified(fixture, case):
    module, _, proc, _ = fixture
    if case == "zombie":
        path = proc / "4242/stat"
        path.write_text(path.read_text().replace(") S ", ") Z "))
    elif case == "bad_stat":
        (proc / "4242/stat").write_text("PRIVATE bad stat")
    elif case == "not_bash":
        (proc / "4242/exe").unlink()
        (proc / "4242/exe").symlink_to("/usr/bin/python")
    elif case == "missing_boot":
        (proc / "stat").unlink()
    if case == "foreign_user":
        with mock.patch.object(module.os, "getuid", return_value=-1):
            result = collect(fixture)
    else:
        result = collect(fixture)
    assert result["loaded_functions_status"] == "unverified"
    if case == "missing_boot":
        assert result["status"] == "observed"
        assert result["process_started_at"] is None
    else:
        assert result["open_script_sha256"] is None
    assert "PRIVATE" not in json.dumps(result)
