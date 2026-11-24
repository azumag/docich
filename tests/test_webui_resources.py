import hashlib
import http.client
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import config, webui
from docich.webui_resources_loader import LIMITS, ResourceError, ResourceLoader

# 実 HTTP fixture の共有ヘルパー (issue #2346)。応答後に書く handler の完了を
# 待ってから server を止める (tmp は pytest が消すので競合窓は狭いが揃える)。
from _webui_http_fixture import (  # noqa: E402
    shutdown_test_server,
    tracking_server_class,
)

PACKAGED = Path(webui.__file__).with_name("webui_resources")


def publish(root):
    manifest = {"schema": 1, "files": {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("index.html", "defaults.json")
    }}
    temp = root / "manifest.tmp"
    temp.write_text(json.dumps(manifest))
    temp.replace(root / "manifest.json")


@pytest.fixture
def resources(tmp_path):
    root = tmp_path / "resources"
    shutil.copytree(PACKAGED, root)
    return root


def loader(root):
    return ResourceLoader(root, webui.WEBUI_ALLOWLIST, webui._validate_value)


def _stop_resources_fixture(server, thread):
    """実 HTTP fixture の server を止め、応答後 writer の完了を待つ (issue #2346)。

    期限内に終わらない handler は黙って見逃さず assertion として報告する。
    """
    stuck = shutdown_test_server(server, thread)
    assert not stuck, (
        "in-flight handler did not finish before cleanup "
        f"(stuck: {[t.name for t in stuck]})"
    )
    return stuck


def test_packaged_extraction_preserves_defaults_and_html(resources):
    snap, status = loader(resources).refresh()
    assert status["status"] == "current"
    assert snap.html == webui.INDEX_HTML.encode()
    assert dict(snap.defaults) == dict(webui.DEFAULTS)
    for model in ("opencode-go:longcat-2.5-preview-free", "opencode-go:space-bunny-free"):
        assert model in snap.defaults["AI_COMMON_AGENTS"]
    with pytest.raises(TypeError):
        snap.defaults["AI_COMMON_AGENTS"] = "codex:changed"


def test_equal_size_preserved_mtime_update_is_detected(resources):
    live = loader(resources)
    old, _ = live.refresh()
    path = resources / "defaults.json"
    times = path.stat()
    # Same byte count, same mtime, both in-place and manifest atomic replacement.
    content = path.read_bytes().replace(b'"AI_AGENT_BACKOFF_SEC": "600"', b'"AI_AGENT_BACKOFF_SEC": "601"')
    path.write_bytes(content)
    os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    publish(resources)
    new, state = live.refresh()
    assert new.defaults["AI_AGENT_BACKOFF_SEC"] == "601"
    assert new.revision != old.revision
    assert state["error"] is None


def test_unchanged_content_does_not_reparse(resources):
    live = loader(resources)
    with mock.patch.object(live, "_validate", wraps=live._validate) as parse:
        for _ in range(5):
            live.refresh()
        parse.assert_not_called()


@pytest.mark.parametrize("bad,error", [
    (b'{', "invalid_json"),
    (b'{"AI_COMMON_AGENTS":"codex:x"}', "invalid_defaults_keys"),
    (b'{"x":1,"x":2}', "duplicate_json_key"),
])
def test_malformed_defaults_keep_last_good_and_recover(resources, bad, error):
    live = loader(resources)
    before, _ = live.refresh()
    path = resources / "defaults.json"
    original = path.read_bytes()
    path.write_bytes(bad)
    publish(resources)
    for _ in range(2):
        after, state = live.refresh()
        assert after is before
        assert state["status"] == "last_good"
        assert state["error"] == error
    path.write_bytes(original)
    publish(resources)
    after, state = live.refresh()
    assert dict(after.defaults) == dict(before.defaults)
    assert state["status"] == "current"


@pytest.mark.parametrize("value", ["codex:bad;command", ["codex:model"], 123])
def test_invalid_values_are_not_adopted(resources, value):
    live = loader(resources)
    before, _ = live.refresh()
    path = resources / "defaults.json"
    data = json.loads(path.read_text())
    data["AI_COMMON_AGENTS"] = value
    path.write_text(json.dumps(data))
    publish(resources)
    after, state = live.refresh()
    assert after is before
    assert state["error"] == "invalid_default_value"


def test_missing_unreadable_oversized_or_incomplete_release_preserves_snapshot(resources):
    live = loader(resources)
    before, _ = live.refresh()
    path = resources / "index.html"
    original = path.read_bytes()
    path.unlink()
    assert live.refresh()[1]["error"] == "read_failed"
    path.write_bytes(original.replace(b'<title>', b'<title>new '))
    assert live.refresh()[1]["error"] == "digest_mismatch"
    publish(resources)
    accepted, _ = live.refresh()
    assert accepted is not before
    with mock.patch("docich.webui_resources_loader._read", side_effect=PermissionError("private detail")):
        snap, state = live.refresh()
        assert snap is accepted
        assert state["error"] == "read_failed"
        assert "private detail" not in json.dumps(state)
    assert live.refresh()[1]["status"] == "current"
    path.write_bytes(b'x' * (LIMITS["index.html"] + 1))
    snap, state = live.refresh()
    assert snap is accepted
    assert state["error"] == "too_large"


def test_malformed_html_and_manifest_are_rejected(resources):
    live = loader(resources)
    before, _ = live.refresh()
    (resources / "index.html").write_bytes(b'<!doctype html><html><body>truncated')
    publish(resources)
    assert live.refresh()[1]["error"] == "invalid_html"
    (resources / "manifest.json").write_text('{"schema":true,"files":{}}')
    snap, state = live.refresh()
    assert snap is before
    assert state["error"] == "invalid_manifest"


def test_unclosed_script_is_rejected_even_with_document_end_marker(resources):
    live = loader(resources)
    before, _ = live.refresh()
    (resources / "index.html").write_text('<!doctype html><html><head></head><body><script>broken</body></html>')
    publish(resources)
    snap, state = live.refresh()
    assert snap is before
    assert state["error"] == "invalid_html"


def test_manifest_race_and_fifo_do_not_replace_snapshot(resources):
    live = loader(resources)
    before, _ = live.refresh()
    from docich import webui_resources_loader as module
    real_read = module._read
    calls = 0

    def race(path, limit):
        nonlocal calls
        data = real_read(path, limit)
        if path.name == "manifest.json":
            calls += 1
            if calls == 2:
                return data + b' '
        return data

    with mock.patch.object(module, "_read", side_effect=race):
        snap, state = live.refresh()
        assert snap is before
        assert state["error"] == "changed_during_read"
    path = resources / "defaults.json"
    path.unlink()
    os.mkfifo(path)
    assert live.refresh()[1]["error"] == "not_regular_file"


def test_initial_invalid_resources_fail_closed(resources):
    (resources / "manifest.json").unlink()
    with pytest.raises(ResourceError, match="read_failed"):
        loader(resources)


def test_same_server_refreshes_ui_and_candidates_without_mutating_saved_settings(resources, tmp_path):
    soren = tmp_path / "soren"
    (soren / "tmp/state").mkdir(parents=True)
    env = soren / ".env"
    saved = b'AI_COMMON_AGENTS=codex:saved-b,codex:saved-a\nAI_COMMON_AGENTS_PAUSED=1:codex:paused\nRADIO_AGENTS=codex:override\nPEAK_HOURS_WINDOWS=\n'
    env.write_bytes(saved)
    pause = soren / "tmp/state/radio_worker.paused"
    pause.write_text("user paused\n")
    g = config.load_global(tmp_path)
    g.webui.token = "fixture-token"

    class Handler(webui._Handler):
        pass

    Handler.g = g
    Handler.soren_root = soren
    Handler.read_only = False
    Handler.resources = loader(resources)
    Handler.csrf_secret = b'fixture' * 6
    server = tracking_server_class()(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, authenticated=True, method="GET", headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        hdrs = {"Authorization": "Bearer fixture-token"} if authenticated else {}
        hdrs.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, resp.read(), dict(resp.getheaders())
        finally:
            conn.close()

    try:
        csrf = json.loads(request("/api/csrf")[1])["csrf_token"]
        old_ui = request("/")[1]
        old_revision = json.loads(request("/api/config")[1])["resources"]["revision"]
        html = resources / "index.html"
        html.write_bytes(old_ui.replace(b'<title>', b'<title>updated '))
        defaults = resources / "defaults.json"
        data = json.loads(defaults.read_text())
        data["AI_COMMON_AGENTS"] += ",codex:new-candidate"
        data["MODEL_IMPROVE_LIST"] = "codex:new-improve"
        defaults.write_text(json.dumps(data))
        publish(resources)
        new_ui = request("/")
        assert b'<title>updated ' in new_ui[1]
        assert new_ui[2]["Cache-Control"] == "no-store"
        payload = json.loads(request("/api/config")[1])
        entries = {entry["key"]: entry for entry in payload["entries"]}
        assert entries["AI_COMMON_AGENTS"]["effective"] == "codex:saved-b,codex:saved-a"
        assert entries["AI_COMMON_AGENTS"]["value"] == "codex:saved-b,codex:saved-a"
        assert "codex:new-candidate" in entries["AI_COMMON_AGENTS"]["default"]
        assert entries["AI_COMMON_AGENTS_PAUSED"]["value"] == "1:codex:paused"
        assert entries["RADIO_AGENTS"]["effective"] == "codex:override"
        assert entries["MODEL_IMPROVE_LIST"]["effective"] == "codex:new-improve"
        assert entries["MODEL_IMPROVE_PEAK_LIST"]["effective"] == "codex:new-improve"
        assert entries["PEAK_HOURS_WINDOWS"]["effective"] == ""
        assert payload["resources"]["revision"] != old_revision
        # Same thread/server/PID, same CSRF secret and enforcement after refresh.
        assert thread.is_alive()
        assert request("/api/config", authenticated=False)[0] == 401
        guarded = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
        assert request("/api/config", method="PUT", headers={**guarded, "Host": "evil.example"}, body='{}')[0] == 400
        # The token obtained before refresh still passes CSRF; confirmation is
        # checked next, without writing .env or sending a worker reload signal.
        res = request("/api/config", method="PUT", headers=guarded, body='{}')
        assert json.loads(res[1])["error"] == "confirmation_required"
        assert request("/api/config", method="PUT", headers={"Origin": "https://evil.example", "Content-Type": "application/json", "X-CSRF-Token": csrf}, body='{}')[0] == 403
        assert request("/api/config", method="PUT", headers={"Content-Type": "application/json"}, body='{}')[0] == 403
        html.unlink()
        stale = json.loads(request("/api/health")[1])["resources"]
        assert stale["status"] == "last_good"
        assert stale["error"] == "read_failed"
        assert request("/")[1] == new_ui[1]
        assert json.loads(request("/api/config")[1])["resources"] == stale
        assert env.read_bytes() == saved
        assert pause.read_text() == "user paused\n"
    finally:
        _stop_resources_fixture(server, thread)


class _PostResponseWriterHandler(webui._Handler):
    """応答を返した後に書く handler (``_guard_mutation`` の audit event 相当)。

    応答送信後に ``post_response_started`` を set し、``release_post_response``
    が set されるまで保留する。解放後の書込み先は ``soren_root`` 配下なので、
    後片付けが先に tmp を消せば ``write_text`` が失敗して
    ``post_response_written`` が set されない (= full-suite flake の再現)。
    """

    POST_RESPONSE_PATH = "/test/resources-post-response-write"
    WRITE_RELATIVE = "tmp/debug/resources-post-response-audit.json"

    def do_GET(self):
        if self.path != self.POST_RESPONSE_PATH:
            return super().do_GET()
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.server.post_response_started.set()
        if not self.server.release_post_response.wait(timeout=5):
            return
        target = Path(self.soren_root) / self.WRITE_RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("post-response write completed", encoding="utf-8")
        self.server.post_response_written.set()


def test_fixture_teardown_waits_for_a_post_response_writer(resources, tmp_path):
    """応答後に書く handler が残っていても、後片付けが先に tmp を消さないこと。

    issue #2346 の決定的な回帰テスト。``_stop_resources_fixture`` から待ちを
    外す (``shutdown`` + ``server_close`` だけにする) と、このテストは
    ``assert not stopped.wait(...)`` で fail する。
    """
    soren = tmp_path / "soren"
    (soren / "tmp/state").mkdir(parents=True)
    g = config.load_global(tmp_path)
    g.webui.token = "fixture-token"

    class Handler(_PostResponseWriterHandler):
        pass

    Handler.g = g
    Handler.soren_root = soren
    Handler.read_only = False
    Handler.resources = loader(resources)
    Handler.csrf_secret = b'fixture' * 6
    server = tracking_server_class()(("127.0.0.1", 0), Handler)
    server.post_response_started = threading.Event()
    server.release_post_response = threading.Event()
    server.post_response_written = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    stopped = threading.Event()
    stop_result = {}

    def _stop_server():
        try:
            _stop_resources_fixture(server, thread)
        except Exception as exc:  # pragma: no cover - assertion で理由を出す
            stop_result["error"] = exc
        finally:
            stopped.set()

    stopper = threading.Thread(target=_stop_server, daemon=True)
    stopper_started = False
    try:
        conn.request("GET", _PostResponseWriterHandler.POST_RESPONSE_PATH)
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
        assert server.post_response_started.wait(timeout=5), (
            "handler did not reach the post-response write"
        )

        stopper.start()
        stopper_started = True
        # 応答後 writer は保留中なので、後片付けはここで完了してはならない。
        assert not stopped.wait(timeout=1.0), (
            "teardown removed the tmp dir without waiting for the post-response writer"
        )
        assert soren.exists(), "handler の完了前に tmp dir が消えた"
    finally:
        server.release_post_response.set()
        if stopper_started:
            stopper.join(timeout=5)
        else:
            shutdown_test_server(server, thread)
        conn.close()

    assert "error" not in stop_result, stop_result.get("error")
    assert server.post_response_written.is_set(), (
        "post-response write did not complete before cleanup"
    )
