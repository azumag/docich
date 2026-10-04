"""Pinned real Codex sandbox probes; no model, credentials or external fetch.

These tests reproduce blockers, not successful research acceptance. The outer
production bwrap/bridge permissions are unchanged for every probe.
"""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pytest
from docich import reply_research as r
from docich import reply_research_bridge as bridge
from docich import reply_research_web as w


@pytest.mark.skipif(sys.platform != "linux", reason="actual Codex Linux sandbox requires Linux")
def test_pinned_codex_nested_sandbox_blockers(monkeypatch):
    codex = os.environ.get("DOCICH_TEST_CODEX_BINARY", "")
    bwrap = shutil.which("bwrap")
    required = os.environ.get("DOCICH_REQUIRE_CODEX_SANDBOX_PROBE") == "1"
    if not bwrap or not codex or not Path(codex).is_file():
        if required:
            pytest.fail("pinned Codex binary and bubblewrap are required")
        pytest.skip("pinned Codex Linux probe dependencies not supplied")
    assert Path(codex).is_absolute() and str(Path(codex)).startswith("/usr/")
    version = subprocess.run([codex, "--version"], capture_output=True, text=True,
                             env={"PATH": os.defpath}, timeout=5)
    assert version.returncode == 0 and version.stdout.strip() == "codex-cli 0.157.1"
    # Official PermissionProfile::read_only() serialization. The internal final
    # stage isolates seccomp behavior after the real outer bwrap filesystem was
    # established; it is not a proposed runtime invocation or a fallback.
    profile = {"type": "managed", "network": "restricted", "file_system": {
        "type": "restricted", "entries": [{"path": {"type": "special", "value": {
            "kind": "root"}}, "access": "read"}]}}
    url = "https://github.com/azumag/docich"
    probe = '''import errno,json,runpy,subprocess,sys
codex,profile,url=sys.argv[1:]
helper=["/usr/bin/python3","/tmp/docich-web-fetch.py","--client",url]
control=subprocess.run(helper,capture_output=True,text=True,timeout=5)
nested=subprocess.run([codex,"sandbox","linux","-c",'sandbox_mode="read-only"',
    "-c",'approval_policy="never"',"--","/usr/bin/python3","-c",
    'print("inner-tool-ran")'],capture_output=True,text=True,timeout=8)
code=''' + repr('''import json,runpy,sys
try:
    value=runpy.run_path("/tmp/docich-web-fetch.py")["client"](sys.argv[1])
except OSError as exc:
    print(json.dumps({"errno":exc.errno}));raise SystemExit(17)
print(json.dumps(value))
''') + '''
seccomp=subprocess.run(["codex-linux-sandbox","--sandbox-policy-cwd","/workspace",
    "--permission-profile",profile,"--apply-seccomp-then-exec","--",
    "/usr/bin/python3","-c",code,url],executable=codex,
    capture_output=True,text=True,timeout=5)
print(json.dumps({"control":{"returncode":control.returncode,"stdout":control.stdout},
    "nested":{"returncode":nested.returncode,"stdout":nested.stdout,"stderr":nested.stderr},
    "seccomp":{"returncode":seccomp.returncode,"stdout":seccomp.stdout,"stderr":seccomp.stderr}}))
'''
    with tempfile.TemporaryDirectory(prefix="docich-codex-probe-", dir="/tmp") as directory:
        root = Path(directory)
        workspace = root / "source"
        workspace.mkdir()
        (workspace / "public.txt").write_text("synthetic public fixture\n")
        proxy = root / "egress.sock"
        proxy.write_text("unused synthetic API socket placeholder")
        # No WebBroker worker starts: only the local synthetic receipt is used.
        receipt = w.Receipt(url, "a" * 32, "b" * 64, "c" * 64, "synthetic public fixture")
        monkeypatch.setattr(w.WebBroker, "fetch", lambda self, requested:
                            receipt if requested == url else None)
        with w.WebBroker(root / "web.sock", time.monotonic() + 25):
            argv = r.sandbox_argv(workspace, "unused-synthetic-model", bwrap, codex,
                bridge_script=Path(bridge.__file__).resolve(), proxy_socket=proxy,
                web_search_enabled=True, web_script=Path(w.__file__).resolve(),
                web_socket=root / "web.sock")
            boundary = argv.index("--")
            argv = argv[:boundary + 1] + ["/usr/bin/python3", "/tmp/docich-research-bridge.py",
                "/usr/bin/python3", "-c", probe, codex, json.dumps(profile), url]
            # Uses the production bounded process runner, including kill/reap.
            value = json.loads(r._run(argv, b"", {"PATH": os.defpath, "LANG": "C.UTF-8"}, 20))
    assert value["control"]["returncode"] == 0, value
    assert json.loads(value["control"]["stdout"]) == receipt.wire()
    assert value["nested"]["returncode"] != 0, value
    assert "inner-tool-ran" not in value["nested"]["stdout"], value
    assert "bwrap" in value["nested"]["stderr"], value
    assert any(text in value["nested"]["stderr"] for text in
               ("user namespace", "Operation not permitted", "Permission denied",
                "max_*_namespaces exceeded (ENOSPC)")), value
    assert value["seccomp"]["returncode"] == 17, value
    assert json.loads(value["seccomp"]["stdout"]) == {"errno": errno.EPERM}, value
    print("nested bwrap: " + value["nested"]["stderr"].strip())
    print("codex-0.157.1: outer helper succeeds; nested bwrap blocked; helper connect=EPERM")
