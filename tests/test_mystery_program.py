"""Program-view contract checks for the AI探偵ミステリー board (no live display).

The board is a broadcast-native information screen (weather / 予想王 precedent):
when the full X11 display size is known it owns the complete broadcast frame
``(0, 0, 1280x720)``, and it falls back to the shared game-area viewport
``(0, 90, 960x540)`` when the display size is unknown.  These checks pin that
contract, the aspect-preserving 960x540 HTML stage, and the isolation of the
other corners' frames.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter, program  # noqa: E402
from docich.adapters.mystery_program import MysteryProgramViewAdapter  # noqa: E402
from docich.config import load_global  # noqa: E402
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.mystery_view import DEFAULT_PORT, HTML, VIEW_NAME  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        display=SimpleNamespace(
            name=":99", width=1280, height=720,
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


def _arg(viewer, flag):
    return viewer[viewer.index(flag) + 1]


def test_mystery_view_is_registered_and_owns_the_full_broadcast_frame(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, MysteryProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    viewer = adapter._viewer_command()
    assert viewer[0] == sys.executable
    # 2026-10-21 owner request: the board is a broadcast-native information
    # screen and owns the whole 1280x720 frame instead of the game viewport.
    assert (_arg(viewer, "--x"), _arg(viewer, "--y")) == ("0", "0")
    assert (_arg(viewer, "--width"), _arg(viewer, "--height")) == ("1280", "720")
    assert f"--app=http://127.0.0.1:{DEFAULT_PORT}/broadcast" in viewer
    assert "--window-size=1280,720" in viewer
    assert "--lang=ja-JP" in viewer
    assert "--disable-translate" in viewer
    assert spec.runtime_id in " ".join(viewer)


def test_mystery_view_falls_back_to_the_game_viewport_without_a_display_size(
        tmp_path, monkeypatch):
    g = _global(tmp_path)
    g.display.width = 0
    g.display.height = 0
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    viewer = make_coordinator_adapter(g, _spec(tmp_path))._viewer_command()

    assert (_arg(viewer, "--x"), _arg(viewer, "--y")) == ("0", "90")
    assert (_arg(viewer, "--width"), _arg(viewer, "--height")) == ("960", "540")
    assert "--window-size=960,540" in viewer


def test_production_display_config_keeps_the_full_frame_known():
    """検証ポイント (a): 本番 display.width/height が 1280x720 であること。

    本番 config は `config/docich.soren-live.toml`（配信用）。ここが
    1280x720 でなければ全画面提示は成立しないため、設定値そのものを
    固定する（env や作業ツリーに依らず、リポジトリ内の設定を読む）。
    """
    production = load_global(ROOT, ROOT / "config" / "docich.soren-live.toml")

    assert production.display.width == 1280
    assert production.display.height == 720
    # ゲーム領域の viewport は共有の (0, 90, 960x540) のまま。フルスクリーン
    # 提示は表示寸法だけを使い、枠の座標・サイズそのものは動かさない。
    assert (production.display.viewport_x, production.display.viewport_y) == (0, 90)
    assert (production.display.viewport_width, production.display.viewport_height) == (960, 540)


def test_mystery_fullscreen_does_not_move_the_shared_status_rails(tmp_path, monkeypatch):
    """検証ポイント (d): 枠の位置・サイズを変えないこと。

    全画面提示は (0,0,1280,720) を presenter に渡すだけで、共有の
    viewport (0,90,960,540) は設定上そのまま残る。他のコーナー・ゲームが
    使う枠の矩形は設定値由来なので、ここでは「他コーナーの adapter は
    viewport のまま」であればあることを固定する。
    """
    from docich.adapters import doushi_program  # noqa: E402

    g = _global(tmp_path)
    names = runtime_names(4)
    spec = RuntimeSpec(
        game=doushi_program.VIEW_NAME, adapter="program", generation=4,
        runtime_id="g4-rails0001", lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / "g4-rails0001",
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )

    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")
    other = doushi_program.make_doushi_view_adapter(g, spec)
    viewer = other._viewer_command()

    assert (_arg(viewer, "--x"), _arg(viewer, "--y")) == ("0", "90")
    assert (_arg(viewer, "--width"), _arg(viewer, "--height")) == ("960", "540")
    # 提示矩形は display 全体のまま。
    assert (g.display.width, g.display.height) == (1280, 720)


def test_mystery_view_html_stage_keeps_the_960x540_aspect_ratio():
    """検証ポイント (b): view HTML が 960x540 基準で縦横比維持して拡大されるか。

    天気予報・予想王と同じ `Math.min(innerWidth/960, innerHeight/540)` の
    contain フィットであること（切り抜き・引き伸ばしをしない）を固定する。
    """
    match = re.search(r"Math\.min\(innerWidth/960,innerHeight/540\)", HTML)
    assert match is not None, "the board must scale with a contain fit"
    assert "#stage{position:absolute;width:960px;height:540px;" in HTML
    # 縮尺は transform の scale だけで、width/height の直接書き換えはしない。
    assert "el.style.transform=`scale(${s})`" in HTML



#: The shipped inline script is executed with DOM/fetch/timer doubles so the
#: real ``fit()`` runs.  The 2s poll timer is captured, not waited on.
NODE = shutil.which("node")
VIEW_SCRIPT = HTML.split("<script>", 1)[1].split("</script>", 1)[0]

FIT_HARNESS = r"""
const out = {};
const timers = [];
globalThis.addEventListener = () => {};
globalThis.setInterval = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
globalThis.fetch = async () => { throw new Error('network'); };
globalThis.AbortSignal = {timeout: () => null};
// NOTE: the shipped mystery script defines its own global `el(id)` helper, so
// this harness must not use that name (it would shadow it and recurse).
function mkEl() {
  return {textContent: '', hidden: false, style: {}, className: '',
          replaceChildren() {}, append() {}, appendChild() {}};
}
const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, mkEl());
    return elements.get(id);
  },
  createElement() { return mkEl(); },
};
globalThis.innerWidth = 1280;
globalThis.innerHeight = 720;
__SCRIPT__
const stage = document.getElementById('stage');
out.scale = stage.style.transform;
out.left = stage.style.left;
out.top = stage.style.top;
process.stdout.write(JSON.stringify(out));
"""


def _scale_of(transform):
    """Parse the ``scale(s)`` transform the shipped view sets on the stage."""
    match = re.fullmatch(r"scale\(([0-9.eE+-]+)\)", transform)
    assert match is not None, transform
    return float(match.group(1))


@pytest.mark.skipif(NODE is None, reason="node is required to execute the shipped view script")
def test_mystery_html_scales_the_960x540_stage_up_to_1280x720_without_distortion():
    """検証ポイント (b): 1280x720 の提示枠で 960x540 のステージが
    縦横比を保ったまま拡大されること（横潰れ・引き伸ばしをしない）。

    1280/960 = 4/3、720/540 = 4/3 なので contain フィットの縮尺は 4/3。
    中心配置になるよう left/top も左右・上下の余白を等分すること。
    """
    completed = subprocess.run(
        [NODE, "-"], input=FIT_HARNESS.replace("__SCRIPT__", VIEW_SCRIPT),
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    out = json.loads(completed.stdout)

    # 1280/960 == 720/540 == 4/3 なので、960x540 のステージは 1280x720 に
    # ちょうど収まり、余白は生じない（引き伸ばしではなく等倍拡大）。
    assert _scale_of(out["scale"]) == pytest.approx(4 / 3, rel=1e-9)
    assert out["left"] == "0px"
    assert out["top"] == "0px"


@pytest.mark.skipif(NODE is None, reason="node is required to execute the shipped view script")
def test_mystery_html_keeps_the_aspect_ratio_on_a_non_16_9_frame():
    """提示枠の縦横比が 16:9 でなくても、960x540 の前提比は保たれる。

    4:3 (1280x960) では高さで頭打ちになり、左右に黒帯}$; 引き伸ばしはしない。
    """
    harness = FIT_HARNESS.replace("globalThis.innerWidth = 1280;",
                                  "globalThis.innerWidth = 1280;").replace(
        "globalThis.innerHeight = 720;", "globalThis.innerHeight = 960;")
    completed = subprocess.run(
        [NODE, "-"], input=harness.replace("__SCRIPT__", VIEW_SCRIPT),
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    out = json.loads(completed.stdout)

    # 960/540 = 16/9、1280/960 = 4/3 → min は 4/3、高さは 540*4/3 = 720 に収まる。
    assert _scale_of(out["scale"]) == pytest.approx(4 / 3, rel=1e-9)
    # 上下に (960 - 720) / 2 = 120px の余白。左右はちょうど埋まる。
    assert out["top"] == "120px"
    assert out["left"] == "0px"


def test_mystery_fullscreen_preflight_requires_the_presenter_binaries(
        tmp_path, monkeypatch):
    """全画面提示も presenter（Xvfb/ffplay/xdotool）を必要とする。

    ``ProgramViewAdapter.preflight`` は viewport が設定されているときだけ
    これらのバイナリを確認する。全画面提示は viewport が未設定でも
    presentation.py を使うため、同じ確認を全画面経路にも適用する。
    """
    import docich.adapters.program as program_module

    g = _global(tmp_path)
    # 本番と同じ「全画面提示だが viewport 未設定」の構成。
    g.display.viewport_x = 0
    g.display.viewport_y = 0
    g.display.viewport_width = 0
    g.display.viewport_height = 0
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    missing = {"Xvfb", "ffplay", "xdotool"}
    monkeypatch.setattr(
        program_module.shutil, "which",
        lambda name: None if name in missing else f"/usr/bin/{name}")

    with pytest.raises(Exception):
        adapter.preflight(2 ** 40, None)


def test_mystery_restore_path_is_the_shared_game_switch_route():
    """検証ポイント (c): コーナー終了は GameSwitch の既存復帰経路を使う。

    全画面提示のための専用復帰経路（枠の隠す/戻す専用処理や、専用の
    表示切替）は追加しない。コーナーの復帰は共有の GameSwitch へ
    前のゲームへ戻る switch として集約されている。
    """
    source = (ROOT / "src" / "docich" / "mystery_corner.py").read_text(encoding="utf-8")

    # 復帰は共有 coordinator の switch/stop で行う。
    assert "self.coordinator.switch(" in source
    assert "self.coordinator.stop(" in source
    # 枠の復帰 obligation は毎回の退出経路で解放される。
    assert source.count("self._release_status_overlays(state)") >= 3
    # 全画面提示のための専用 Presentation 経路は無い（提示は adapter 側）。
    assert "presentation.py" not in source


def test_mystery_status_rails_are_hidden_only_for_this_corner():
    """検証ポイント (c): コーナー中のみステータス枠が隠れる。

    隠す/戻すのは OBS のステータス枠ソース（statsOverlay / opsOverlay）
    だけで、通知オーバーレイや他コーナーの表示は触らない。
    """
    source = (ROOT / "src" / "docich" / "mystery_corner.py").read_text(encoding="utf-8")

    assert '"statsOverlay"' in source
    assert '"opsOverlay"' in source
    # 通知オーバーレイは対象外。
    assert "notice_overlay" not in source
