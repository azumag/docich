"""No real Codex/API calls. Process bounds use isolated local Python children."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

import pytest
from docich import reply_research as r
from docich import reply_research_bridge as bridge
from docich import reply_research_egress as egress


def source(tmp_path):
    root, dest = tmp_path / "approved", tmp_path / "copy"
    root.mkdir(); dest.mkdir()
    (root / "logic.py").write_text("answer = 42\n")
    manifest = {"repo": "azumag/docich", "revision": "a" * 40,
                "files": {"logic.py": hashlib.sha256(b"answer = 42\n").hexdigest()}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, dest, manifest


def transcript(sources, tools=(), notes="確認した根拠", finished=True):
    events = [{"type": "item.completed", "item": item} for item in tools]
    events.append({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"status": "ok", "notes": notes, "sources": sources})}})
    if finished:
        events.append({"type": "turn.completed"})
    return b"\n".join(json.dumps(e).encode() for e in events)


WEB = {"kind": "web", "ref": "https://example.org/source", "quote": "確認した資料"}
CODE = {"kind": "code", "ref": "logic.py", "line": 1, "quote": "answer = 42"}
# Official rust-v0.157.1 exec_events.rs / protocol models.rs shape.
# results are opaque JSON; even URL/snippet/body-like values do not establish
# a trusted page retrieval contract. Never use these as a success fixture.
SEARCH = {"id": "search-1", "type": "web_search", "query": "example",
          "action": {"type": "search", "query": "example"},
          "results": [{"url": "https://example.org/source", "snippet": "確認した資料"}]}
OPEN = {"id": "open-1", "type": "web_search", "query": "https://example.org/source",
        "action": {"type": "open_page", "url": "https://example.org/source"},
        "results": [{"url": "https://example.org/source", "content": "確認した資料"}]}
READ = {"type": "command_execution", "status": "completed", "command": "cat source/logic.py",
        "exit_code": 0, "aggregated_output": "answer = 42"}


def test_snapshot_copies_only_manifest_files(tmp_path):
    root, dest, manifest = source(tmp_path)
    (root / "NOT_APPROVED").write_text("secret")
    assert r.snapshot(root, dest, deadline=time.monotonic() + 10) == manifest
    assert list(dest.iterdir()) == [dest / "logic.py"]


@pytest.mark.parametrize("name", ["../logic.py", "/etc/passwd", ".env", "a/.codex/config.toml", "a//b", "a\\b", "AGENTS.md", "a/AGENTS.override.md"])
def test_manifest_path_cannot_expand_permissions(tmp_path, name):
    root, dest, manifest = source(tmp_path)
    manifest["files"] = {name: "a" * 64}
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        r.snapshot(root, dest, deadline=time.monotonic() + 10)


def test_source_hash_change_is_not_silently_read(tmp_path):
    root, dest, _ = source(tmp_path)
    (root / "logic.py").write_text("different")
    with pytest.raises(ValueError, match="mismatch"):
        r.snapshot(root, dest, deadline=time.monotonic() + 10)


@pytest.mark.parametrize("parent", [False, True])
def test_symlink_leaf_or_parent_is_rejected(tmp_path, parent):
    root, dest, _ = source(tmp_path)
    if parent:
        link = tmp_path / "link"; link.symlink_to(root, target_is_directory=True)
        root = link
    else:
        (root / "logic.py").unlink()
        (tmp_path / "elsewhere").write_text("answer = 42\n")
        (root / "logic.py").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="unsafe"):
        r.snapshot(root, dest, deadline=time.monotonic() + 10)


def test_snapshot_deadline(tmp_path):
    root, dest, _ = source(tmp_path)
    with pytest.raises(ValueError, match="timeout"):
        r.snapshot(root, dest, deadline=time.monotonic() - 1)


def test_source_root_cannot_traverse_parent_components(tmp_path):
    root, dest, _ = source(tmp_path)
    traversing = root.parent / "unused" / ".." / root.name
    with pytest.raises(ValueError, match="unsafe_source"):
        r.snapshot(traversing, dest, deadline=time.monotonic() + 10)


def test_web_needs_observed_search_not_model_claim(tmp_path):
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB]), "web", tmp_path, None)
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB], [SEARCH]), "web", tmp_path, None)
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB], [SEARCH, OPEN]), "web", tmp_path, None)


def test_web_quote_must_match_opened_content(tmp_path):
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([{**WEB, "quote": "モデルの自己申告"}], [SEARCH, OPEN]),
                         "web", tmp_path, None)


@pytest.mark.parametrize("results", [None, [], [{"url": WEB["ref"]}], [{"url": WEB["ref"], "snippet": WEB["quote"]}]])
def test_opaque_or_absent_search_results_are_not_page_evidence(tmp_path, results):
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB], [{**SEARCH, "results": results}, OPEN]), "web", tmp_path, None)


def test_code_requires_exact_snapshot_quote_and_successful_read(tmp_path):
    root, dest, manifest = source(tmp_path)
    r.snapshot(root, dest, deadline=time.monotonic() + 10)
    evidence = r.parse_evidence(transcript([CODE], [READ]), "code", dest, manifest)
    assert evidence.ok and evidence.sources[0].endswith("/logic.py#L1")
    for quote in ("invented", ""):
        with pytest.raises(ValueError):
            r.parse_evidence(transcript([{**CODE, "quote": quote}], [READ]), "code", dest, manifest)
    with pytest.raises(ValueError):
        r.parse_evidence(transcript([CODE], [{**READ, "exit_code": False}]), "code", dest, manifest)


@pytest.mark.parametrize("command", [
    "rg --pre=cat answer source/logic.py",
    "grep -n answer source/logic.py",
    "cat source/logic.py && id",
    "cat source/unapproved.py",
])
def test_unsupported_or_compound_commands_cannot_prove_a_code_read(command):
    assert not r._read_targets(command, {"logic.py"})
    assert r._read_targets("bash -lc 'cat source/logic.py'", {"logic.py"}) == {"logic.py"}


def test_mixed_scope_requires_both_sources(tmp_path):
    root, dest, manifest = source(tmp_path)
    r.snapshot(root, dest, deadline=time.monotonic() + 10)
    assert not r.parse_evidence(transcript([CODE], [READ]), "web_and_code", dest, manifest).ok
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB, CODE], [SEARCH, OPEN, READ]), "web_and_code", dest, manifest)


@pytest.mark.parametrize("ref", ["file:///etc/passwd", "https://user:password@example.org", "https://example.org/\nsecret", "http://example.org", "not-a-url"])
def test_invalid_reference(tmp_path, ref):
    with pytest.raises(ValueError):
        r.parse_evidence(transcript([{**WEB, "ref": ref}], [SEARCH, OPEN]), "web", tmp_path, None)


def test_unfinished_turn_and_empty_notes_not_success(tmp_path):
    assert not r.parse_evidence(transcript([WEB], [SEARCH, OPEN], finished=False), "web", tmp_path, None).ok
    with pytest.raises(ValueError):
        r.parse_evidence(transcript([WEB], [SEARCH, OPEN], notes=""), "web", tmp_path, None)


@pytest.mark.parametrize("raw", [b'{"type":"turn.completed","type":"turn.failed"}', b'NaN', b'not json'])
def test_invalid_json(tmp_path, raw):
    with pytest.raises((ValueError, TypeError)):
        r.parse_evidence(raw, "web", tmp_path, None)


def test_sandbox_has_no_host_home_repo_socket_or_credential_argv(tmp_path):
    argv = r.sandbox_argv(tmp_path, "synthetic-model", "/usr/bin/bwrap", "/usr/bin/codex",
                          bridge_script=tmp_path / "bridge.py", proxy_socket=tmp_path / "egress.sock")
    assert {"--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-net",
            "--unshare-uts", "--disable-userns", "--as-pid-1"} <= set(argv)
    assert "--share-net" not in argv and "--cap-drop" in argv and "--die-with-parent" in argv
    assert argv[argv.index("--cap-add") + 1] == "CAP_NET_ADMIN"
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert "--bind" not in argv and "--full-auto" not in argv and "--yolo" not in argv
    assert "CODEX_API_KEY" not in argv
    mounts = [(argv[i+1], argv[i+2]) for i, v in enumerate(argv) if v == "--ro-bind"]
    assert all(target not in {"/home", "/etc", "/var/run"} for _, target in mounts)
    assert (str(tmp_path), "/workspace/source") in mounts
    assert all(target != "/workspace" for _, target in mounts)
    assert (str(tmp_path / "bridge.py"), "/tmp/docich-research-bridge.py") in mounts


@pytest.mark.skipif(sys.platform != "linux", reason="bubblewrap isolation is Linux-only")
def test_bwrap_cannot_reach_host_loopback(tmp_path):
    bwrap = shutil.which("bwrap")
    if not bwrap:
        pytest.skip("bubblewrap is not installed")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bridge_script = Path(bridge.__file__)
    proxy_socket = tmp_path / "egress.sock"
    # The synthetic probe never uses the proxy. A bindable placeholder lets
    # this test exercise production bwrap/bridge setup without API credentials.
    proxy_socket.write_text("synthetic placeholder")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        argv = r.sandbox_argv(workspace, "synthetic-model", bwrap, "/usr/bin/codex",
                              bridge_script=bridge_script, proxy_socket=proxy_socket)
        boundary = argv.index("--")
        code = """import socket
import json
import sys

names = [name for _, name in socket.if_nameindex()]
if names != ["lo"]:
    print("sandbox-probe-ran interfaces=" + repr(names))
    raise SystemExit(3)

loop = socket.socket()
loop.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
loop.bind(("127.0.0.1", 0))
loop.listen(1)
client = socket.create_connection(loop.getsockname(), timeout=0.5)
peer, _ = loop.accept()
client.close()
peer.close()
loop.close()

s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    host_blocked = True
else:
    host_blocked = False
finally:
    s.close()

status = {}
with open("/proc/self/status", encoding="ascii") as f:
    for line in f:
        key, _, value = line.partition(":")
        if key in {"CapEff", "CapPrm", "CapInh", "NoNewPrivs"}:
            status[key] = value.strip()
caps_clear = all(int(status.get(key, "-1"), 16) == 0
                 for key in ("CapEff", "CapPrm", "CapInh"))
ok = host_blocked and caps_clear and status.get("NoNewPrivs") == "1"
print("sandbox-probe-ran " + json.dumps({"interfaces": names, "host_blocked": host_blocked,
      "capabilities": status}, sort_keys=True))
raise SystemExit(0 if ok else 7)
"""
        probe = argv[: boundary + 1] + ["/usr/bin/python3", "/tmp/docich-research-bridge.py",
                                        "/usr/bin/python3", "-c", code, str(port)]
        completed = subprocess.run(probe, capture_output=True, text=True, timeout=10)
        if "sandbox-probe-ran " in completed.stdout:
            assert completed.returncode == 0, completed.stdout + completed.stderr
            observed = json.loads(completed.stdout.split("sandbox-probe-ran ", 1)[1])
            assert observed["host_blocked"] is True
            assert observed["interfaces"] == ["lo"]
            for capability in ("CapEff", "CapPrm", "CapInh"):
                assert int(observed["capabilities"][capability], 16) == 0
            assert observed["capabilities"]["NoNewPrivs"] == "1"
        else:
            # Some Ubuntu/AppArmor hosts reject namespace setup before the child
            # runs. That remains fail-closed, but CI configures a targeted bwrap
            # profile and requires the probe child itself to run.
            if os.environ.get("DOCICH_REQUIRE_BWRAP_PROBE") == "1":
                pytest.fail(completed.stderr or "bubblewrap probe did not start")
            assert completed.returncode != 0, completed.stdout + completed.stderr
            known_fail_closed = (
                "loopback: Failed RTM_NEWADDR: Operation not permitted",
                "setting up uid map: Permission denied",
            )
            assert any(value in completed.stderr for value in known_fail_closed), completed.stderr
    finally:
        listener.close()


@pytest.mark.parametrize("env", [{}, {"DOCICH_REPLY_RESEARCH_ENABLED": "1"}])
def test_unconfigured_never_spawns(monkeypatch, env):
    monkeypatch.setattr(r, "_run", lambda *a: pytest.fail("spawned"))
    assert not r.research([], "web", env=env).ok


def test_spawn_gets_only_dedicated_credentials_and_workspace_is_removed(monkeypatch, tmp_path):
    root, _, _ = source(tmp_path)
    monkeypatch.setattr(r.sys, "platform", "linux")
    monkeypatch.setattr(r.shutil, "which", lambda name, **kw: "/usr/bin/" + name)
    calls = []
    def fake_run(argv, prompt, env, timeout):
        snapshot_dir = Path(argv[argv.index("/workspace/source") - 1])
        calls.append((argv, prompt, env, timeout, snapshot_dir))
        assert snapshot_dir.exists()
        return transcript([CODE], [READ])
    monkeypatch.setattr(r, "_run", fake_run)
    class FakeEgressProxy:
        def __init__(self, _):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(r, "EgressProxy", FakeEgressProxy)
    env = {"DOCICH_ALLOW_REAL_AI": "1", "DOCICH_REPLY_RESEARCH_ENABLED": "1", "DOCICH_REPLY_WEB_SEARCH_ENABLED": "1",
           "DOCICH_REPLY_CODEX_MODEL": "synthetic-model",
           "DOCICH_REPLY_CODEX_API_KEY": "SYNTHETIC_RESEARCH_ONLY", "DISCORD_TOKEN": "PRIVATE_DISCORD",
           "GITHUB_TOKEN": "PRIVATE_GITHUB", "AWS_ACCESS_KEY_ID": "PRIVATE_AWS",
           "OPENAI_API_KEY": "PRIVATE_OPENAI", "DOCKER_HOST": "PRIVATE_DOCKER",
           "HOME": "/private", "HTTPS_PROXY": "PRIVATE_PROXY",
           "DOCICH_REPLY_SOURCE_APPROVED": "1", "DOCICH_REPLY_SOURCE_DIR": str(root)}
    assert r.research([{"role": "user", "text": "この実装は"}], "code", env=env).ok
    argv, prompt, child_env, timeout, snapshot_dir = calls[0]
    assert set(child_env) == {"PATH", "LANG", "CODEX_API_KEY"}
    assert "PRIVATE" not in json.dumps((argv, child_env))
    assert "SYNTHETIC_RESEARCH_ONLY" not in json.dumps(argv)
    assert 0 < timeout <= 45 and not snapshot_dir.exists()


def test_absent_bwrap_never_uses_bare_codex(monkeypatch):
    monkeypatch.setattr(r.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(r, "_run", lambda *a: pytest.fail("bare spawn"))
    env = {"DOCICH_ALLOW_REAL_AI": "1", "DOCICH_REPLY_RESEARCH_ENABLED": "1", "DOCICH_REPLY_CODEX_MODEL": "synthetic-model", "DOCICH_REPLY_CODEX_API_KEY": "SYNTHETIC"}
    assert not r.research([], "web", env=env).ok


def test_bounded_local_process_success_and_nonzero():
    assert r._run([sys.executable, "-c", "print('ok')"], b"", {}, 2) == b"ok\n"
    with pytest.raises(ValueError, match="provider_failed"):
        r._run([sys.executable, "-c", "raise SystemExit(2)"], b"", {}, 2)


def test_bounded_local_process_timeout_and_output_limit():
    started = time.monotonic()
    with pytest.raises(ValueError, match="timeout"):
        r._run([sys.executable, "-c", "import time; time.sleep(10)"], b"", {}, .15)
    assert time.monotonic() - started < 3
    with pytest.raises(ValueError, match="output_limit"):
        r._run([sys.executable, "-c", f"print('x'*{r.LIMIT+1})"], b"", {}, 3)


def test_timeout_kills_descendant_process_group(tmp_path):
    pid_path = tmp_path / "child.pid"
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import pathlib,subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    with pytest.raises(ValueError, match="timeout"):
        r._run([sys.executable, "-c", parent_code], b"", {}, .25)
    child_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return
        proc_stat = Path(f"/proc/{child_pid}/stat")
        if proc_stat.is_file():
            try:
                raw = proc_stat.read_text()
            except (FileNotFoundError, ProcessLookupError):
                # The child can exit between the existence check and open.
                return
            state = raw.rsplit(")", 1)[1].split()[0]
        elif shutil.which("ps"):
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                   text=True, stdout=subprocess.PIPE).stdout.strip()
        else:
            pytest.fail("no portable process-state probe is available")
        if not state or state.startswith("Z"):
            return
        time.sleep(.05)
    pytest.fail("descendant process survived timeout cleanup")


def test_plain_progress_does_not_replace_final_evidence(tmp_path):
    root, dest, manifest = source(tmp_path)
    r.snapshot(root, dest, deadline=time.monotonic() + 10)
    progress = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "I will look up the sources."}}).encode()
    assert r.parse_evidence(progress + b"\n" + transcript([CODE], [READ]), "code", dest, manifest).ok
    argv = r.sandbox_argv(tmp_path, "synthetic", "/usr/bin/bwrap", "/usr/bin/codex",
                          bridge_script=tmp_path / "bridge.py", proxy_socket=tmp_path / "egress.sock")
    assert 'web_search="disabled"' in argv


@pytest.mark.parametrize("raw_request", [
    b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n",
    b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\nUser-Agent: codex\r\n\r\n",
])
def test_egress_bridges_allow_only_fixed_api_authority(raw_request):
    assert bridge._allowed(raw_request)
    assert egress.allowed_connect(raw_request)


@pytest.mark.parametrize("raw_request", [
    b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n",
    b"CONNECT api.openai.com:444 HTTP/1.1\r\nHost: api.openai.com:444\r\n\r\n",
    b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\nHost: api.openai.com:443\r\n\r\n",
    b"GET https://api.openai.com/ HTTP/1.1\r\nHost: api.openai.com\r\n\r\n",
])
def test_egress_bridges_reject_other_authorities_and_ambiguous_headers(raw_request):
    assert not bridge._allowed(raw_request)
    assert not egress.allowed_connect(raw_request)


def test_public_api_proxy_rejects_loopback_private_link_local_and_ipv6_internal(monkeypatch):
    candidates = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fc00::1", 443, 0, 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 443, 0, 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", 443, 0, 0)),
    ]
    monkeypatch.setattr(egress, "_resolve_api_addresses", lambda: candidates)
    monkeypatch.setattr(egress.socket, "socket", lambda *a, **kw: pytest.fail("private address dialed"))
    assert egress._public_api_socket() is None


def test_public_api_dial_uses_the_checked_ip_without_second_dns_lookup(monkeypatch):
    public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    lookups, dials = [], []
    monkeypatch.setattr(egress, "_resolve_api_addresses",
                        lambda: lookups.append((egress.ALLOWED_HOST, egress.ALLOWED_PORT)) or [public])

    class FakeSocket:
        def __init__(self, family, socktype, proto):
            self.family = family
        def settimeout(self, _):
            pass
        def connect(self, address):
            dials.append(address)
        def close(self):
            pass

    monkeypatch.setattr(egress.socket, "socket", FakeSocket)
    assert isinstance(egress._public_api_socket(), FakeSocket)
    assert lookups == [("api.openai.com", 443)]
    assert dials == [("93.184.216.34", 443)]


def test_egress_socket_is_private_unix_only_and_rejects_redirect_authorities():
    if not sys.platform.startswith("linux"):
        pytest.skip("Unix proxy listener acceptance runs in the Linux isolated test target")
    with tempfile.TemporaryDirectory(prefix="docich-egress-", dir="/tmp") as directory:
        path = Path(directory) / "egress.sock"
        with egress.EgressProxy(path) as proxy:
            assert proxy.server.socket.family == socket.AF_UNIX
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(path))  # Unix file permissions are the caller ACL.
                client.sendall(b"CONNECT redirected.example:443 HTTP/1.1\r\nHost: redirected.example:443\r\n\r\n")
                assert b"403 Forbidden" in client.recv(128)
            finally:
                client.close()
        assert not path.exists()


def test_egress_exit_closes_idle_upstream_after_client_half_close(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Unix proxy listener acceptance runs in the Linux isolated test target")
    upstream_peers = []

    def fake_upstream(state):
        upstream, peer = socket.socketpair()
        upstream_peers.append(peer)
        assert state.add(upstream)
        return upstream

    monkeypatch.setattr(egress, "_public_api_socket", fake_upstream)
    path = tmp_path / "idle-egress.sock"
    proxy = egress.EgressProxy(path)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    with proxy:
        client.connect(str(path))
        client.sendall(b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n")
        assert b"200 Connection Established" in client.recv(256)
        client.shutdown(socket.SHUT_WR)

        # The peer gets EOF when the relay propagates the half-close. It remains
        # idle waiting for a response until proxy context teardown cancels it.
        peer = upstream_peers[0]
        peer.settimeout(1.0)
        assert peer.recv(1) == b""
        assert proxy.server.active_connections

    try:
        assert proxy.server.active_connections == frozenset()
        assert all(not thread.is_alive() for thread in proxy.server._threads)
        peer.settimeout(1.0)
        assert peer.recv(1) == b""
    finally:
        client.close()
        for peer in upstream_peers:
            peer.close()
    assert not path.exists()


def test_bridge_drops_namespace_capabilities_before_launch_and_passes_no_parent_secrets(monkeypatch):
    order, captured = [], {}
    monkeypatch.setattr(bridge, "_bring_loopback_up", lambda: order.append("loopback"))
    monkeypatch.setattr(bridge, "_drop_capabilities", lambda: order.append("drop_caps"))

    class FakeServer:
        server_address = ("127.0.0.1", 45678)
        def __init__(self, *_):
            self.socket = type("Socket", (), {"family": socket.AF_INET})()
        def serve_forever(self):
            pass
        def shutdown(self):
            order.append("shutdown")
        def server_close(self):
            order.append("close")

    class FakeThread:
        def __init__(self, **_):
            pass
        def start(self):
            order.append("server_start")
        def join(self, **_):
            order.append("thread_join")

    monkeypatch.setattr(bridge, "_LoopbackServer", FakeServer)
    monkeypatch.setattr(bridge.threading, "Thread", FakeThread)
    monkeypatch.setattr(bridge.sys, "argv", ["bridge.py", "/usr/bin/codex"])
    monkeypatch.setenv("CODEX_API_KEY", "SYNTHETIC_ONLY_KEY")
    monkeypatch.setenv("DISCORD_TOKEN", "PRIVATE_DISCORD")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_OPENAI")
    monkeypatch.setenv("HOME", "/private")
    monkeypatch.setattr(bridge.subprocess, "call", lambda argv, env: captured.update(argv=argv, env=env) or order.append("codex") or 0)
    assert bridge.main() == 0
    assert order.index("loopback") < order.index("drop_caps") < order.index("codex")
    assert set(captured["env"]) == {"PATH", "LANG", "HOME", "CODEX_HOME", "CODEX_API_KEY",
                                    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                                    "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"}
    assert "PRIVATE" not in json.dumps(captured)


@pytest.mark.parametrize("scope", ["web", "web_and_code"])
def test_web_schema_blocker_holds_before_any_spawn(monkeypatch, scope):
    monkeypatch.setattr(r.sys, "platform", "linux")
    monkeypatch.setattr(r.shutil, "which", lambda *a, **kw: pytest.fail("binary discovery"))
    monkeypatch.setattr(r, "_run", lambda *a: pytest.fail("paid provider spawn"))
    env = {"DOCICH_ALLOW_REAL_AI": "1", "DOCICH_REPLY_RESEARCH_ENABLED": "1",
           "DOCICH_REPLY_WEB_SEARCH_ENABLED": "1",
           "DOCICH_REPLY_CODEX_MODEL": "synthetic-model", "DOCICH_REPLY_CODEX_API_KEY": "SYNTHETIC"}
    assert not r.research([], scope, env=env).ok


def test_invented_web_open_event_is_not_evidence(tmp_path):
    invented = {"type": "web_open", "status": "completed", "url": WEB["ref"], "content": WEB["quote"]}
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB], [SEARCH, invented]), "web", tmp_path, None)
