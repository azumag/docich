"""No real Codex/API calls. Process bounds use isolated local Python children."""
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import pytest
from docich import reply_research as r


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


WEB = {"kind": "web", "ref": "https://example.org/source"}
CODE = {"kind": "code", "ref": "logic.py", "line": 1, "quote": "answer = 42"}
SEARCH = {"type": "web_search", "query": "example"}
READ = {"type": "command_execution", "exit_code": 0, "aggregated_output": "answer = 42"}


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


def test_web_needs_observed_search_not_model_claim(tmp_path):
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB]), "web", tmp_path, None)
    value = r.parse_evidence(transcript([WEB], [SEARCH]), "web", tmp_path, None)
    assert value.ok and value.sources == (WEB["ref"],)


def test_failed_search_is_not_evidence(tmp_path):
    with pytest.raises(ValueError, match="unverified"):
        r.parse_evidence(transcript([WEB], [{**SEARCH, "status": "failed"}]), "web", tmp_path, None)


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


def test_mixed_scope_requires_both_sources(tmp_path):
    root, dest, manifest = source(tmp_path)
    r.snapshot(root, dest, deadline=time.monotonic() + 10)
    assert not r.parse_evidence(transcript([WEB], [SEARCH]), "web_and_code", dest, manifest).ok
    assert r.parse_evidence(transcript([WEB, CODE], [SEARCH, READ]), "web_and_code", dest, manifest).ok


@pytest.mark.parametrize("ref", ["file:///etc/passwd", "https://user:password@example.org", "https://example.org/\nsecret", "http://example.org", "not-a-url"])
def test_invalid_reference(tmp_path, ref):
    with pytest.raises(ValueError):
        r.parse_evidence(transcript([{**WEB, "ref": ref}], [SEARCH]), "web", tmp_path, None)


def test_unfinished_turn_and_empty_notes_not_success(tmp_path):
    assert not r.parse_evidence(transcript([WEB], [SEARCH], finished=False), "web", tmp_path, None).ok
    with pytest.raises(ValueError):
        r.parse_evidence(transcript([WEB], [SEARCH], notes=""), "web", tmp_path, None)


@pytest.mark.parametrize("raw", [b'{"type":"turn.completed","type":"turn.failed"}', b'NaN', b'not json'])
def test_invalid_json(tmp_path, raw):
    with pytest.raises((ValueError, TypeError)):
        r.parse_evidence(raw, "web", tmp_path, None)


def test_sandbox_has_no_host_home_repo_socket_or_credential_argv(tmp_path):
    argv = r.sandbox_argv(tmp_path, "synthetic-model", "/usr/bin/bwrap", "/usr/bin/codex")
    assert "--unshare-all" in argv and "--cap-drop" in argv and "--die-with-parent" in argv
    assert "--share-net" not in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert "--bind" not in argv and "--full-auto" not in argv and "--yolo" not in argv
    assert "CODEX_API_KEY" not in argv
    mounts = [argv[i+1] for i, v in enumerate(argv) if v == "--ro-bind"]
    assert "/home" not in mounts and "/etc" not in mounts and "/var/run" not in mounts
    assert str(tmp_path) in mounts


@pytest.mark.skipif(sys.platform != "linux", reason="bubblewrap isolation is Linux-only")
def test_bwrap_cannot_reach_host_loopback(tmp_path):
    bwrap = shutil.which("bwrap")
    if not bwrap:
        pytest.skip("bubblewrap is not installed")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        argv = r.sandbox_argv(tmp_path, "synthetic-model", bwrap, "/usr/bin/codex")
        boundary = argv.index("--")
        code = (
            "import socket,sys; "
            "s=socket.socket(); s.settimeout(0.5); "
            "target=('127.0.0.1', int(sys.argv[1])); "
            "\\ntry: s.connect(target)\\n"
            "except OSError: print('blocked'); raise SystemExit(0)\\n"
            "print('reachable'); raise SystemExit(7)"
        )
        probe = argv[: boundary + 1] + ["/usr/bin/python3", "-c", code, str(port)]
        completed = subprocess.run(probe, capture_output=True, text=True, timeout=5)
        assert completed.stdout.strip() != "reachable"
        if completed.returncode == 0:
            assert completed.stdout.strip() == "blocked"
        else:
            # Ubuntu/AppArmor may reject creation/setup of the isolated network
            # namespace before the child runs. That is fail-closed: never treat
            # sandbox-unavailable as permission to reuse the host network.
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


def test_spawn_gets_only_dedicated_credentials_and_workspace_is_removed(monkeypatch):
    monkeypatch.setattr(r.sys, "platform", "linux")
    monkeypatch.setattr(r.shutil, "which", lambda name, **kw: "/usr/bin/" + name)
    calls = []
    def fake_run(argv, prompt, env, timeout):
        workspace = Path(argv[argv.index("/workspace") - 1])
        calls.append((argv, prompt, env, timeout, workspace))
        assert workspace.exists()
        return transcript([WEB], [SEARCH])
    monkeypatch.setattr(r, "_run", fake_run)
    env = {"DOCICH_ALLOW_REAL_AI": "1", "DOCICH_REPLY_RESEARCH_ENABLED": "1", "DOCICH_REPLY_CODEX_MODEL": "synthetic-model",
           "DOCICH_REPLY_CODEX_API_KEY": "SYNTHETIC_RESEARCH_ONLY", "DISCORD_TOKEN": "PRIVATE_DISCORD", "HOME": "/private", "HTTPS_PROXY": "PRIVATE_PROXY"}
    assert r.research([{"role": "user", "text": "XXとは"}], "web", env=env).ok
    argv, prompt, child_env, timeout, workspace = calls[0]
    assert set(child_env) == {"PATH", "LANG", "CODEX_API_KEY"}
    assert "PRIVATE" not in json.dumps((argv, child_env))
    assert "SYNTHETIC_RESEARCH_ONLY" not in json.dumps(argv)
    assert 0 < timeout <= 45 and not workspace.exists()


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


def test_plain_progress_does_not_replace_final_evidence(tmp_path):
    progress = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "I will look up the sources."}}).encode()
    assert r.parse_evidence(progress + b"\n" + transcript([WEB], [SEARCH]), "web", tmp_path, None).ok
    assert 'web_search="disabled"' in r.sandbox_argv(tmp_path, "synthetic", "/usr/bin/bwrap", "/usr/bin/codex", "code")
