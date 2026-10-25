"""Read-only 同志カード view and program-adapter contracts (no Twitch, no network)."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
import uuid

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.adapters import cli_game, doushi_program  # noqa: E402
from docich.adapters import program as program_adapter  # noqa: E402
from docich.doushi_ranking import DoushiRankingError, derive_endpoint  # noqa: E402
from docich.doushi_view import (  # noqa: E402
    DEFAULT_PORT,
    HTML,
    PAGE_COUNT,
    PAGE_SECONDS,
    VIEW_NAME,
    DoushiViewError,
    handler_for,
    load_payload,
)
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from doushi_payload_fixtures import api_body, make_payload, stored_body  # noqa: E402

STREAMER = "11111111-2222-4b33-8444-555555555555"
OVERLAY_URL = f"https://twica.example/overlay/{STREAMER}?token=SECRET-QUERY"



def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        display=SimpleNamespace(
            name=":98", width=1280, height=720,
            viewport_x=0, viewport_y=90,
            viewport_width=960, viewport_height=540,
        ),
    )


def _spec(tmp_path, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game=VIEW_NAME, adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _response(data, status=200):
    class Response:
        def __init__(self):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _limit):
            return json.dumps(data).encode("utf-8")

    return Response()


@pytest.fixture
def served(tmp_path):
    """One loopback view server bound to an ephemeral port."""
    path = tmp_path / "doushi_ranking.json"
    path.write_bytes(stored_body())
    payload = load_payload(path)
    lease = str(uuid.uuid4())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_for(payload, "g4-view", generation=4, lease_id=lease),
    )
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        yield payload, server.server_port, lease
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def _get(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_load_payload_revalidates_the_adapter_written_file(tmp_path):
    path = tmp_path / "doushi_ranking.json"
    path.write_bytes(stored_body())

    data = load_payload(path)

    assert data["ok"] is True and data["period"] == "all" and data["limit"] == 10
    assert [row["rank"] for row in data["by_count"]] == [1, 2, 3]
    assert data["card_type_total"] == 128
    assert set(data) == {
        "ok", "period", "generated_at", "metric_labels", "note", "card_type_total",
        "by_count", "by_points", "by_unique", "popular_cards", "limit",
    }


def test_browser_renderer_preserves_full_names_counts_and_units_on_all_pages():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the view renderer")
    payload = make_payload()
    for key in ("by_count", "by_points", "by_unique", "popular_cards"):
        row = payload[key][0]
        payload[key] = [dict(row, rank=i + 1) for i in range(10)]
    payload["by_count"][0]["username"] = "<b>同志テスト</b>"
    script = HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    # Run the shipped JavaScript against a minimal DOM sink, rather than
    # reproducing its formatting logic in Python. textContent must keep the
    # whole value (and literal markup) on all four pages, including rank 10.
    harness = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function element() {
  return {style:{}, children:[], textContent:'',
    append(child) { this.children.push(child); },
    replaceChildren() { this.children = []; }};
}
const nodes = new Map();
const document = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  },
  createElement: element,
};
const context = vm.createContext({document, innerWidth:960, innerHeight:540,
  addEventListener(){}, fetch:()=>new Promise(()=>{}),
  AbortSignal:{timeout(){return null;}}, fixture:input.payload});
vm.runInContext(input.script, context);
const pages = [];
for(let i=0; i<4; i++) {
  vm.runInContext(`render(fixture, ${i})`, context);
  pages.push(nodes.get('rows').children.map(row=>row.children.map(cell=>cell.textContent)));
}
process.stdout.write(JSON.stringify(pages));
'''
    result = subprocess.run(
        [node, "-e", harness], input=json.dumps({"script": script, "payload": payload}),
        text=True, capture_output=True, timeout=5, check=True,
    )
    pages = json.loads(result.stdout)
    assert pages[0][0] == ["1", "<b>同志テスト</b>", "50 枚"]
    assert pages[1][0] == ["1", "alice0", "5000 pt"]
    assert pages[2][0] == ["1", "alice0", "40 種類"]
    assert pages[3][0] == ["1", "カード0", "30 枚（20人が獲得）"]
    assert all(len(page) == 10 and page[-1][0] == "10" for page in pages)


@pytest.mark.parametrize("body", [
    b"",
    b"{broken",
    b"[]",
    b'{"period": "all", "limit": 10}',
    b'{"period": "all", "limit": "10", "payload": {}}',
])
def test_load_payload_fails_closed_on_missing_unreadable_or_tampered_files(tmp_path, body):
    path = tmp_path / "doushi_ranking.json"
    path.write_bytes(body)

    with pytest.raises(DoushiRankingError):
        load_payload(path)


@pytest.mark.parametrize("field, value, reason", [
    ("period", "30d", "schema"),
    ("limit", 0, "invalid-limit"),
], ids=["period-mismatch", "zero-limit"])
def test_load_payload_rejects_tampered_contract_with_fresh_payload(tmp_path, field, value, reason):
    # Build the valid, fresh payload during the test; stable IDs keep xdist
    # collection deterministic and each case changes only its intended field.
    stored = json.loads(stored_body())
    stored[field] = value
    path = tmp_path / "doushi_ranking.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(DoushiRankingError, match=reason):
        load_payload(path)


def test_load_payload_rejects_a_missing_file_without_leaking_the_path(tmp_path):
    with pytest.raises(DoushiRankingError) as raised:
        load_payload(tmp_path / "doushi_ranking.json")

    assert "doushi ranking payload missing" in str(raised.value)
    assert str(tmp_path) not in str(raised.value)


def test_view_serves_broadcast_html_and_the_runtime_bound_projection(served):
    _payload, port, lease = served

    status, html = _get(port, "/broadcast")
    assert status == 200
    assert "同志カード大賞" in html and "配信者本人とBOTを除く" in html
    assert html.count("title:'") == PAGE_COUNT
    assert "id=\"unavailable\" hidden" in html

    status, raw = _get(port, "/api/doushi")
    data = json.loads(raw)
    assert status == 200 and data["ok"] is True
    assert (data["runtime_id"], data["generation"], data["lease_id"]) == ("g4-view", 4, lease)
    assert data["page_seconds"] == PAGE_SECONDS
    assert [row["username"] for row in data["by_count"]] == ["alice0", "alice1", "alice2"]
    assert [row["card_name"] for row in data["popular_cards"]] == ["カード0", "カード1", "カード2"]

    status, _ = _get(port, "/nope")
    assert status == 404


def test_view_rejects_a_foreign_host_header(served):
    _payload, port, _lease = served
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("GET", "/api/doushi", skip_host=True)
        connection.putheader("Host", f"example.invalid:{port}")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
    finally:
        connection.close()


@pytest.mark.parametrize("generation", [True, 0, -1, "4"])
def test_view_handler_rejects_invalid_generation(tmp_path, generation):
    with pytest.raises(DoushiViewError):
        handler_for(make_payload(), "g4-view", generation=generation)


@pytest.mark.parametrize("lease", ["", "not-a-uuid", 7])
def test_view_handler_rejects_invalid_lease_ids(lease):
    with pytest.raises(DoushiViewError):
        handler_for(make_payload(), "g4-view", lease_id=lease)


def test_view_handler_rejects_unsafe_runtime_ids_and_page_seconds():
    for bad in ("", "with space", "../escape", "x" * 200):
        with pytest.raises(DoushiViewError):
            handler_for(make_payload(), bad)
    for bad in (0, 301, True, 1.5):
        with pytest.raises(DoushiViewError):
            handler_for(make_payload(), "g4-view", page_seconds=bad)


def test_doushi_program_view_is_registered_and_owns_the_fullscreen_display(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    # 翻訳抑止と日本語UIの固定は共通の broadcast_browser_command が組み立てるため、
    # ブラウザ探索は共有モジュール側を差し替える（他の view テストと同じ形）。
    monkeypatch.setattr(program_adapter, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, doushi_program.DoushiProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    assert adapter.dashboard_port == DEFAULT_PORT
    assert adapter.payload_path == spec.runtime_dir / "doushi_ranking.json"
    assert adapter._game_command() == [
        sys.executable, "-m", "docich.doushi_view",
        "--payload-file", str(adapter.payload_path),
        "--port", str(DEFAULT_PORT),
        "--runtime-id", spec.runtime_id, "--generation", str(spec.generation),
        "--lease-id", spec.lease_id,
    ]
    assert adapter._game_session_env()["PYTHONPATH"].endswith("src")
    viewer = adapter._viewer_command()
    assert viewer[0] == sys.executable
    assert ("--x", "0") == (viewer[viewer.index("--x")], viewer[viewer.index("--x") + 1])
    assert ("--y", "0") == (viewer[viewer.index("--y")], viewer[viewer.index("--y") + 1])
    assert ("--width", "1280") == (viewer[viewer.index("--width")], viewer[viewer.index("--width") + 1])
    assert ("--height", "720") == (viewer[viewer.index("--height")], viewer[viewer.index("--height") + 1])
    assert f"--app=http://127.0.0.1:{DEFAULT_PORT}/broadcast" in viewer
    assert "--window-size=1280,720" in viewer
    assert "--lang=ja-JP" in viewer
    assert "--disable-translate" in viewer
    assert "--disable-features=Translate,TranslateUI,BackForwardCache" in viewer


def test_doushi_preflight_fetches_once_and_stores_the_transient_payload(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    monkeypatch.setattr(doushi_program.ProgramViewAdapter, "preflight", lambda *_: None)
    monkeypatch.setenv("SOREN_DIRECT_TWICA_OVERLAY_URL", OVERLAY_URL)
    seen = []

    def http_get(url, timeout):
        seen.append((url, timeout))
        return api_body()

    monkeypatch.setattr(doushi_program, "http_get", http_get)

    payload = adapter.preflight(time.monotonic() + 10, None)

    assert payload is None  # preflight returns nothing; the file is the hand-off
    assert seen == [(derive_endpoint(OVERLAY_URL) + "?period=all&limit=10", 10.0)]
    stored = json.loads(adapter.payload_path.read_text(encoding="utf-8"))
    assert (stored["period"], stored["limit"]) == ("all", 10)
    assert stored["payload"]["card_type_total"] == 128
    assert "SECRET-QUERY" not in json.dumps(stored)
    assert load_payload(adapter.payload_path)["ok"] is True


def test_doushi_preflight_reads_only_overlay_setting_from_soren_env_when_timer_has_no_env(
        tmp_path, monkeypatch):
    g = _global(tmp_path)
    root = tmp_path / "soren"
    root.mkdir()
    g.webui = SimpleNamespace(soren_root=str(root))
    (root / ".env").write_text(
        f'SOREN_DIRECT_TWICA_OVERLAY_URL="{OVERLAY_URL}"\n'
        'UNRELATED_SECRET=must-not-export\n', encoding="utf-8")
    monkeypatch.delenv("SOREN_DIRECT_TWICA_OVERLAY_URL", raising=False)
    monkeypatch.delenv("UNRELATED_SECRET", raising=False)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    monkeypatch.setattr(doushi_program.ProgramViewAdapter, "preflight", lambda *_: None)
    seen = []
    def http_get(url, timeout):
        seen.append(url)
        return api_body()
    monkeypatch.setattr(doushi_program, "http_get", http_get)

    adapter.preflight(time.monotonic() + 10, None)

    assert seen == [derive_endpoint(OVERLAY_URL) + "?period=all&limit=10"]
    assert "SECRET-QUERY" not in adapter.payload_path.read_text()
    assert "UNRELATED_SECRET" not in doushi_program.os.environ
    assert "SOREN_DIRECT_TWICA_OVERLAY_URL" not in doushi_program.os.environ
    assert "SOREN_DIRECT_TWICA_OVERLAY_URL" not in adapter._game_session_env()


@pytest.mark.parametrize("explicit", [OVERLAY_URL, "", "not-a-url"])
def test_doushi_explicit_environment_does_not_fall_back_to_another_channel(
        tmp_path, monkeypatch, explicit):
    g = _global(tmp_path)
    root = tmp_path / "soren"
    root.mkdir()
    g.webui = SimpleNamespace(soren_root=str(root))
    (root / ".env").write_text('SOREN_DIRECT_TWICA_OVERLAY_URL="https://other.example/overlay/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"\n')
    monkeypatch.setenv("SOREN_DIRECT_TWICA_OVERLAY_URL", explicit)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    seen = []
    def http_get(url, timeout):
        seen.append(url)
        return api_body()
    monkeypatch.setattr(doushi_program, "http_get", http_get)
    if explicit == OVERLAY_URL:
        adapter.fetch_ranking_payload()
        assert seen == [derive_endpoint(OVERLAY_URL) + "?period=all&limit=10"]
    else:
        with pytest.raises(AdapterError):
            adapter.fetch_ranking_payload()
        assert seen == []


@pytest.mark.parametrize("missing", [True, False])
def test_doushi_preflight_fails_closed_without_the_overlay_url_or_on_a_broken_body(
        tmp_path, monkeypatch, missing):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    monkeypatch.setattr(doushi_program.ProgramViewAdapter, "preflight", lambda *_: None)
    if missing:
        monkeypatch.delenv("SOREN_DIRECT_TWICA_OVERLAY_URL", raising=False)
        monkeypatch.setattr(doushi_program, "http_get", lambda *_: stored_body())
    else:
        monkeypatch.setenv("SOREN_DIRECT_TWICA_OVERLAY_URL", OVERLAY_URL)
        monkeypatch.setattr(doushi_program, "http_get", lambda *_: b"{broken")

    with pytest.raises(AdapterError) as raised:
        adapter.preflight(time.monotonic() + 10, None)

    message = str(raised.value)
    assert message == "同志カードランキングを取得できないため開始できません"
    for secret in ("SECRET", STREAMER, "twica.example", "period="):
        assert secret not in message
    assert not adapter.payload_path.exists()


def test_doushi_preflight_skips_when_twica_has_not_opted_the_channel_in(
        tmp_path, monkeypatch):
    """TwiCa 側の公開オプトインが無いチャンネルは 404 を返す（設計 §6）。

    docich は前回値を出さず当該コーナーを開始しない。404 という事実も URL も
    例外文言へ運ばず、固定文言だけで skip することを固定する。
    """
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    monkeypatch.setattr(doushi_program.ProgramViewAdapter, "preflight", lambda *_: None)
    monkeypatch.setenv("SOREN_DIRECT_TWICA_OVERLAY_URL", OVERLAY_URL)

    def not_published(url, timeout):
        # urllib は非 2xx で HTTPError を送出する（実経路と同じ形）。
        raise HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(doushi_program, "http_get", not_published)

    with pytest.raises(AdapterError) as raised:
        adapter.preflight(time.monotonic() + 10, None)

    message = str(raised.value)
    assert message == "同志カードランキングを取得できないため開始できません"
    for secret in ("SECRET", STREAMER, "twica.example", "404", "Not Found", "period="):
        assert secret not in message
    assert not adapter.payload_path.exists()


@pytest.mark.parametrize("identity", ["runtime_id", "generation", "lease_id"])
def test_doushi_readiness_rejects_another_runtime(identity, tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "by_count": [], "popular_cards": [],
    }
    data[identity] = {
        "runtime_id": "g3-old",
        "generation": spec.generation - 1,
        "lease_id": str(uuid.uuid4()),
    }[identity]
    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(doushi_program, "urlopen", lambda *_args, **_kwargs: _response(data))
    now = time.monotonic()
    checks = iter((now, now + 5))
    monkeypatch.setattr(doushi_program, "monotonic", lambda: next(checks))

    with pytest.raises(ReadinessTimeoutError, match="not ready"):
        adapter.readiness(now + 5, None)


def test_doushi_readiness_accepts_only_the_exact_runtime_response(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "by_count": [], "popular_cards": [],
    }
    requests = []

    def open_local(url, *, timeout):
        requests.append((url, timeout))
        return _response(data)

    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(doushi_program, "urlopen", open_local)

    adapter.readiness(time.monotonic() + 5, None)

    assert requests == [(f"http://127.0.0.1:{DEFAULT_PORT}/api/doushi", 2.0)]
