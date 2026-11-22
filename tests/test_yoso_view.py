"""Read-only 予想王 view and program-adapter contracts (no Twitch, no production)."""
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
import uuid

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import prediction_leaderboard as lb  # noqa: E402
from docich.adapters import make_coordinator_adapter  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.adapters import cli_game, program, yoso_program  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.yoso_view import (  # noqa: E402
    DEFAULT_PORT,
    HTML,
    VIEW_NAME,
    YosoViewError,
    board,
    display_name,
    handler_for,
    voice_state,
)
from test_prediction_leaderboard import predictor, with_top  # noqa: E402


def _ledger(tmp_path, rounds):
    ledger = lb.new_ledger()
    for remote in rounds:
        assert lb.record(ledger, remote, corner="hanjuku") is True
    lb.save(tmp_path / lb.FILE, ledger)
    return ledger


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
    state = tmp_path / "run"
    state.mkdir(parents=True)
    _ledger(state, [
        with_top("p1", {"o1": [predictor("u1", "alice"), predictor("u2", "bob", outcome="o2")]}),
        with_top("p2", {"o1": [predictor("u1", "alice"), predictor("u2", "bob", outcome="o2")]},
                 ended="2026-10-02T00:00:00Z"),
        with_top("p3", {"o1": [predictor("u1", "alice")]}, ended="2026-10-03T00:00:00Z"),
    ])
    lease = str(uuid.uuid4())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_for(state, "g4-view", generation=4, lease_id=lease),
    )
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        yield state, server.server_port, lease
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


def test_board_ranks_hits_and_keeps_one_shot_viewers_off_the_board(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    _ledger(state, [
        with_top("p1", {"o1": [predictor("u1", "alice"), predictor("u2", "bob", outcome="o2")]}),
        with_top("p2", {"o1": [predictor("u1", "alice")]}, ended="2026-10-02T00:00:00Z"),
        with_top("p3", {"o1": [predictor("u1", "alice"), predictor("u3", "carol", outcome="o2")]},
                 ended="2026-10-03T00:00:00Z"),
    ])

    data = board(state, clock=lambda: 1234.0)

    assert data["ok"] is True and data["updated_at"] == 1234
    assert data["rounds"] == 3 and data["viewers"] == 3
    assert data["min_rounds"] == lb.DEFAULT_MIN_ROUNDS
    assert [row["user_name"] for row in data["ranking"]] == ["Alice"]
    alice = data["ranking"][0]
    assert (alice["rank"], alice["rounds"], alice["hits"]) == (1, 3, 3)
    assert alice["hit_rate"] == 1.0 and alice["net"] == 300


def test_board_missing_ledger_is_an_empty_board_and_invalid_ledger_fails_closed(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    assert board(state)["ranking"] == []

    (state / lb.FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(lb.LeaderboardError):
        board(state)


def test_board_bounds_and_sanitizes_display_names(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    forged = with_top("p1", {"o1": [predictor("u1", "alice")]})
    forged["outcomes"][0]["top_predictors"][0]["user_name"] = "  \u202eAli\u0000ce  "
    _ledger(state, [forged])

    assert board(state)["ranking"] == []
    data = board(state, min_rounds=1)
    assert data["ranking"][0]["user_name"] == "Alice"
    with pytest.raises(YosoViewError):
        board(state, min_rounds=0)
    with pytest.raises(YosoViewError):
        board(state, limit=0)
    assert display_name("\u200b") == "（表示名なし）"
    assert len(display_name("x" * 200)) == 24


def test_view_serves_broadcast_html_and_a_runtime_bound_projection(served):
    _state, port, lease = served

    status, html = _get(port, "/broadcast")
    assert status == 200 and "予想王" in html and "上位予想者のみ集計" in html
    assert "GET /helix/predictions" in html
    # 日本語UIのブラウザ翻訳コントロール（日本語/English）を出さない。
    # 予想王は常に日本語表示で、翻訳した表示は集計の意味を変えてしまう。
    assert 'lang="ja" translate="no"' in html
    assert '<meta name="google" content="notranslate">' in html

    status, raw = _get(port, "/api/yosou")
    data = json.loads(raw)
    assert status == 200 and data["ok"] is True
    assert (data["runtime_id"], data["generation"], data["lease_id"]) == ("g4-view", 4, lease)
    # Bob appears in only 2 of the 3 recorded rounds, so min_rounds keeps him off.
    assert [row["user_name"] for row in data["ranking"]] == ["Alice"]
    assert data["rounds"] == 3 and data["min_rounds"] == lb.DEFAULT_MIN_ROUNDS

    status, _ = _get(port, "/nope")
    assert status == 404


def test_view_rotates_the_five_independent_boards_in_one_table(served):
    """#933 (a): 1枚の表を部門ごとに切り替え、5指標をすべて出せること。"""
    _state, port, _lease = served

    status, html = _get(port, "/broadcast")
    assert status == 200
    for label in ("予想的中率", "賭けたポイント数", "得たポイント数",
                  "失ったポイント数", "賭けた回数"):
        assert label in html
    assert "data.boards" in html and "BOARD_SECONDS" in html

    status, raw = _get(port, "/api/yosou")
    data = json.loads(raw)
    assert status == 200 and set(data["boards"]) == set(lb.METRICS)
    # 部門ごとに独立したランキングで、同じ行をそのまま使い回していない。
    assert data["boards"]["hit_rate"][0]["user_name"] == "Alice"


NODE = shutil.which("node")
VIEW_SCRIPT = HTML.split("<script>", 1)[1].split("</script>", 1)[0]

# The shipped inline script runs in node with DOM/fetch/timer doubles. The 18s
# board timer and the 2s poll timer are captured, not waited on, so the test can
# drive the exact sequence the reviewer reproduced on #2435.
TIMER_HARNESS = r"""
const out = {};
let fetches = 0;
let mode = 'ok';
const ROW = {rank: 1, user_name: 'Alice', hit_rate: 1, rounds: 3, hits: 3,
             used: 100, won: 300, lost: 0, refunded: 0, participations: 3, net: 200};
const DATA = {
  ok: true, rounds: 3, viewers: 1, min_rounds: 3, min_participations: 1,
  ranking: [ROW],
  boards: {hit_rate: [ROW], staked: [ROW], won: [ROW], lost: [ROW], plays: [ROW]},
};
globalThis.fetch = async () => {
  fetches += 1;
  if (mode === 'fail') throw new Error('network');
  return {ok: true, json: async () => DATA};
};
globalThis.AbortSignal = {timeout: () => null};
globalThis.addEventListener = () => {};
globalThis.innerWidth = 960;
globalThis.innerHeight = 540;
const timers = [];
globalThis.setInterval = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
function el() {
  return {textContent: '', hidden: false, style: {}, className: '',
          replaceChildren() {}, append() {}, appendChild() {}};
}
const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, el());
    return elements.get(id);
  },
  createElement() { return el(); },
};
const byId = (id) => document.getElementById(id);
// showUnavailable(true) sets hidden=false; the pause is visible only then.
const paused = () => byId('unavailable').hidden === false;
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));
const pollTimer = () => timers.find((timer) => timer.ms === 2000);
const boardTimer = () => timers.find((timer) => timer.ms === 18000);
(async () => {
__SCRIPT__
  await settle();
  out.after_success = {paused: paused(), fetches, state: byId('state').textContent};

  mode = 'fail';
  pollTimer().fn();
  await settle();
  out.after_failure = {paused: paused(), fetches, state: byId('state').textContent};

  const tag = byId('board-tag').textContent;
  boardTimer().fn(); boardTimer().fn(); boardTimer().fn();
  await settle();
  out.after_switches = {
    paused: paused(), fetches,
    tag_changed: byId('board-tag').textContent !== tag,
  };

  mode = 'ok';
  pollTimer().fn();
  await settle();
  out.after_recovery = {paused: paused(), fetches, state: byId('state').textContent};

  out.timer_ms = timers.map((timer) => timer.ms);
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node is required to execute the shipped view script")
def test_a_board_switch_never_clears_the_unavailable_pause():
    """#2435 P2: 休止表示は取得成功の render だけで解除する。

    ``draw()`` は18秒の部門切替タイマーからも呼ばれる。成功→失敗のあと部門を
    切り替えただけで休止が解けると、成功した再取得がないまま古い表が見えてしまう。
    """
    completed = subprocess.run(
        [NODE, "-"], input=TIMER_HARNESS.replace("__SCRIPT__", VIEW_SCRIPT),
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    out = json.loads(completed.stdout)

    assert out["timer_ms"] == [18000, 2000]
    assert out["after_success"] == {
        "paused": False, "fetches": 1, "state": "確定済みの予想を集計したランキング"}
    assert out["after_failure"] == {
        "paused": True, "fetches": 2, "state": "台帳を再確認中"}
    # 部門切替は表を描き直すが、取得も休止の解除もしない。
    assert out["after_switches"] == {
        "paused": True, "fetches": 2, "tag_changed": True}
    # 成功した再取得だけが休止を解く。
    assert out["after_recovery"] == {
        "paused": False, "fetches": 3, "state": "確定済みの予想を集計したランキング"}


def test_view_rejects_a_foreign_host_header(served):
    _state, port, _lease = served
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("GET", "/api/yosou", skip_host=True)
        connection.putheader("Host", f"example.invalid:{port}")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
    finally:
        connection.close()


def test_view_reports_unavailable_for_a_broken_ledger(served):
    state, port, _lease = served
    (state / lb.FILE).write_text("[]", encoding="utf-8")

    status, raw = _get(port, "/api/yosou")

    assert status == 503 and json.loads(raw)["ok"] is False


@pytest.mark.parametrize("generation", [True, 0, -1, "4"])
def test_view_handler_rejects_invalid_generation(tmp_path, generation):
    with pytest.raises(YosoViewError):
        handler_for(tmp_path, "g4-view", generation=generation)


def test_yoso_program_view_is_registered_and_owns_the_full_broadcast_frame(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, yoso_program.YosoProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    assert adapter._game_command() == [
        sys.executable, "-m", "docich.yoso_view",
        "--state-dir", str(g.state_dir), "--port", str(DEFAULT_PORT),
        "--runtime-id", spec.runtime_id, "--generation", str(spec.generation),
        "--lease-id", spec.lease_id,
    ]
    assert adapter._game_session_env()["PYTHONPATH"].endswith("src")
    # 予想王は weather と同じ配信ネイティブ情報画面なので、ディスプレイ全画面を所有する
    # （#2237。ステータス枠・通知枠はその間だけ覆われ、位置・サイズは変えない）。
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
    assert spec.runtime_id in " ".join(viewer)


def test_yoso_program_view_falls_back_to_the_game_viewport_without_a_display_size(tmp_path, monkeypatch):
    g = _global(tmp_path)
    g.display.width = 0
    g.display.height = 0
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    viewer = make_coordinator_adapter(g, _spec(tmp_path))._viewer_command()

    assert ("--x", "0") == (viewer[viewer.index("--x")], viewer[viewer.index("--x") + 1])
    assert ("--y", "90") == (viewer[viewer.index("--y")], viewer[viewer.index("--y") + 1])
    assert ("--width", "960") == (viewer[viewer.index("--width")], viewer[viewer.index("--width") + 1])
    assert ("--height", "540") == (viewer[viewer.index("--height")], viewer[viewer.index("--height") + 1])
    assert "--window-size=960,540" in viewer


def test_yoso_preflight_accepts_a_missing_ledger_and_rejects_a_broken_one(tmp_path, monkeypatch):
    g = _global(tmp_path)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    monkeypatch.setattr(yoso_program.ProgramViewAdapter, "preflight", lambda *_: None)

    adapter.preflight(time.monotonic() + 10, None)  # empty board is valid

    g.state_dir.mkdir(parents=True, exist_ok=True)
    (g.state_dir / lb.FILE).write_text("{broken", encoding="utf-8")
    with pytest.raises(AdapterError, match="予想台帳"):
        adapter.preflight(time.monotonic() + 10, None)


@pytest.mark.parametrize("identity", ["runtime_id", "generation", "lease_id"])
def test_yoso_readiness_rejects_another_runtime(identity, tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "ranking": [],
    }
    data[identity] = {
        "runtime_id": "g3-old",
        "generation": spec.generation - 1,
        "lease_id": str(uuid.uuid4()),
    }[identity]
    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(yoso_program, "urlopen", lambda *_args, **_kwargs: _response(data))
    now = time.monotonic()
    checks = iter((now, now + 5))
    monkeypatch.setattr(yoso_program, "monotonic", lambda: next(checks))

    with pytest.raises(ReadinessTimeoutError, match="not ready"):
        adapter.readiness(now + 5, None)


def test_yoso_readiness_accepts_only_the_exact_runtime_response(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "ranking": [],
    }
    requests = []

    def open_local(url, *, timeout):
        requests.append((url, timeout))
        return _response(data)

    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(yoso_program, "urlopen", open_local)

    adapter.readiness(time.monotonic() + 5, None)

    assert requests == [(f"http://127.0.0.1:{DEFAULT_PORT}/api/yosou", 2.0)]


# --- 読み上げ中の部門へ画面を連動させる（2026-11-07 オーナー指示） -------------------

def _marker(queue, source, suffix, *, name="1_2_3"):
    path = queue / f"comment_announce_{name}_{source}{suffix}"
    path.write_text("x", encoding="utf-8")
    return path


def test_voice_state_reports_the_department_being_read(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()

    # No queue configured / missing queue: not active, so the screen rotates itself.
    assert voice_state(None) == {"active": False, "speaking": None}
    assert voice_state(queue / "missing") == {"active": False, "speaking": None}
    assert voice_state(queue) == {"active": False, "speaking": None}

    # Queued but not yet claimed: the corner is reading, no department is on air.
    intro = _marker(queue, "yosou:intro", ".txt")
    queued = _marker(queue, "yosou:board:won", ".txt", name="4_5_6")
    assert voice_state(queue) == {"active": True, "speaking": None}

    # The worker claims an item by renaming it to .playing; that department is shown.
    intro.unlink()
    queued.rename(queue / queued.name.replace(".txt", ".playing"))
    assert voice_state(queue) == {"active": True, "speaking": "won"}

    # Other sources never count, and the intro is not a department.
    _marker(queue, "chat", ".playing", name="7_8_9")
    _marker(queue, "weather_corner", ".txt", name="7_8_9")
    (queue / queued.name.replace(".txt", ".playing")).unlink()
    _marker(queue, "yosou:intro", ".playing")
    assert voice_state(queue) == {"active": True, "speaking": None}


def test_voice_state_ignores_leftover_markers_from_a_dead_worker(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    _marker(queue, "yosou:board:lost", ".playing")
    _marker(queue, "yosou:board:plays", ".txt", name="4_5_6")

    now = time.time()
    assert voice_state(queue, clock=lambda: now)["speaking"] == "lost"
    # A leftover older than the bound must not pin the screen or suppress rotation.
    assert voice_state(queue, clock=lambda: now + 3600) == {"active": False, "speaking": None}


def test_the_api_projects_the_voice_state_read_only(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    queue = tmp_path / "queue"
    queue.mkdir()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_for(state, "g4-view", queue_dir=queue))
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        port = server.server_port
        assert json.loads(_get(port, "/api/yosou")[1])["voice"] == {
            "active": False, "speaking": None}
        marker = _marker(queue, "yosou:board:staked", ".playing")
        assert json.loads(_get(port, "/api/yosou")[1])["voice"] == {
            "active": True, "speaking": "staked"}
        # The view only lists the queue; it never consumes or removes a marker.
        assert marker.exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def test_the_api_without_a_queue_reports_an_inactive_voice(served):
    _state, port, _lease = served

    assert json.loads(_get(port, "/api/yosou")[1])["voice"] == {
        "active": False, "speaking": None}


VOICE_HARNESS = r"""
const out = {};
let voice = {active: false, speaking: null};
const ROW = {rank: 1, user_name: 'Alice', hit_rate: 1, rounds: 3, hits: 3,
             used: 100, won: 300, lost: 0, refunded: 0, participations: 3, net: 200};
const BOARDS_DATA = {hit_rate: [ROW], staked: [ROW], won: [ROW], lost: [ROW], plays: [ROW]};
globalThis.fetch = async () => ({ok: true, json: async () => ({
  ok: true, rounds: 3, viewers: 1, min_rounds: 3, min_participations: 1,
  ranking: [ROW], boards: BOARDS_DATA, voice})});
globalThis.AbortSignal = {timeout: () => null};
globalThis.addEventListener = () => {};
globalThis.innerWidth = 960;
globalThis.innerHeight = 540;
const timers = [];
globalThis.setInterval = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
function el() {
  return {textContent: '', hidden: false, style: {}, className: '',
          replaceChildren() {}, append() {}, appendChild() {}};
}
const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, el());
    return elements.get(id);
  },
  createElement() { return el(); },
};
const tag = () => document.getElementById('board-tag').textContent;
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));
const setVoice = async (next) => { voice = next; timers.find((t) => t.ms === 2000).fn(); await settle(); };
const tick = () => timers.find((t) => t.ms === 18000).fn();
(async () => {
__SCRIPT__
  await settle();
  const steps = [];
  const log = (name) => steps.push([name, tag()]);

  // 読み上げが無い間は従来どおり18秒ごとに回る。
  log('initial'); tick(); log('timer-rotates-without-voice');

  // 読み上げが始まった瞬間は先頭の部門に戻す（紹介の間は先頭を出す）。
  await setVoice({active: true, speaking: null}); log('intro');
  tick(); tick(); log('timer-frozen-during-voice');

  // 部門ごとの読み上げへ追従する。
  await setVoice({active: true, speaking: 'lost'}); log('lost');
  await setVoice({active: true, speaking: null}); log('gap-holds-previous');
  tick(); log('gap-timer-frozen');
  await setVoice({active: true, speaking: 'plays'}); log('plays');
  await setVoice({active: true, speaking: 'staked'}); log('staked');

  // 読み上げが終わっても、コーナーが終わるまで勝手に回さない。
  await setVoice({active: false, speaking: null}); log('finished');
  tick(); tick(); log('finished-timer-frozen');

  out.steps = steps;
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node is required to execute the shipped view script")
def test_the_screen_follows_the_department_being_read():
    """読み上げ中の部門 = 画面の部門。合間は直前の部門のまま、終わっても勝手に回さない。"""
    completed = subprocess.run(
        [NODE, "-"], input=VOICE_HARNESS.replace("__SCRIPT__", VIEW_SCRIPT),
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    steps = dict(json.loads(completed.stdout)["steps"])

    assert steps["initial"] == "部門 1/5 予想的中率"
    assert steps["timer-rotates-without-voice"] == "部門 2/5 賭けたポイント数"
    assert steps["intro"] == "部門 1/5 予想的中率"
    assert steps["timer-frozen-during-voice"] == "部門 1/5 予想的中率"
    assert steps["lost"] == "部門 4/5 失ったポイント数 ♪ 読み上げ中"
    # 合間（次の項目の合成待ち）は直前の部門を出したまま、読み上げ中の印だけ消す。
    assert steps["gap-holds-previous"] == "部門 4/5 失ったポイント数"
    assert steps["gap-timer-frozen"] == "部門 4/5 失ったポイント数"
    assert steps["plays"] == "部門 5/5 賭けた回数 ♪ 読み上げ中"
    assert steps["staked"] == "部門 2/5 賭けたポイント数 ♪ 読み上げ中"
    assert steps["finished"] == "部門 2/5 賭けたポイント数"
    assert steps["finished-timer-frozen"] == "部門 2/5 賭けたポイント数"


def test_the_program_adapter_hands_the_shared_audio_queue_to_the_view(tmp_path, monkeypatch):
    g = _global(tmp_path)
    queue = tmp_path / "soren-queue"
    g.webui = SimpleNamespace(soren_root=str(tmp_path / "soren"))
    monkeypatch.setenv("COMMENT_QUEUE_DIR", str(queue))
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    command = make_coordinator_adapter(g, _spec(tmp_path))._game_command()

    assert command[-2:] == ["--queue-dir", str(queue)]
    # Without a resolvable Soren root the view still starts and rotates by itself.
    assert "--queue-dir" not in make_coordinator_adapter(
        _global(tmp_path), _spec(tmp_path))._game_command()
