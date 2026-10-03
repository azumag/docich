"""``python -m docich.flyhome <command>`` (リポジトリ直下で ``set PYTHONPATH=src`` してから実行)。

実機 (Windows) で使う順番:

1. ``doctor``    環境確認 (OS / Steam / ゲーム導入 / ウィンドウ検出)
2. ``launch``    Steam 経由で起動 (``--demo`` で体験版)
3. ``windows``   ウィンドウ一覧 (タイトルが既定と違えば設定で上書き)
4. ``shot``      ゲーム画面を PNG 保存 (等倍 + 320x180 + 解析注釈)
5. ``watch``     入力なしで認識結果を表示 (レベル内で実行)
6. ``keytest``   左右キーを順に押し、右下の矢印 HUD が光るか確認 (入力経路の確認)
7. ``probe``     台本パルスで物理を測る → ``fit`` で physics.json を作る
8. ``play``      制御器でプレイ (死亡→Enter で再挑戦、帰宅で停止)
9. ``record`` / ``replay``  人間の操作を記録・再生

``analyze`` と ``sim`` はオフライン (Linux でも可)。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from . import calibrate, runner, settings as settings_mod, steam, vision
from .control import Physics
from .image import read_png, write_png
from .timeline import Timeline


def _settings(args) -> settings_mod.Settings:
    s = settings_mod.load(Path(args.config) if args.config else None)
    if getattr(args, "demo", False):
        from dataclasses import replace

        s = replace(s, use_demo=True)
    return s


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_doctor(args) -> int:
    s = _settings(args)
    report: dict = {"python": sys.version.split()[0], "platform": platform.platform(), "windows": sys.platform == "win32"}
    root = steam.steam_root()
    report["steam_root"] = str(root) if root else None
    for label, app in (("full", s.app_id), ("demo", s.demo_app_id)):
        inst = steam.find_app(app, root) if root else None
        report[label] = None if inst is None else {
            "app_id": app,
            "name": inst.name,
            "dir": str(inst.install_dir),
            "installed": inst.fully_installed,
            "build": inst.build_id,
            "exes": [p.name for p in inst.exes],
        }
    if sys.platform == "win32":  # pragma: no cover
        from . import win32

        win32.set_dpi_aware()
        w = win32.find_window(s.window_titles, s.exe_names)
        report["window"] = None if w is None else {"title": w.title, "exe": w.exe, "client_rect": w.rect}
    report["physics_file"] = str(s.physics_file) + (" (exists)" if s.physics_file.is_file() else " (missing: 仮説値を使用)")
    _print(report)
    return 0


def cmd_launch(args) -> int:
    s = _settings(args)
    if args.install:
        steam.install(s.active_app_id)
    else:
        steam.launch(s.active_app_id)
    print(f"requested steam app {s.active_app_id}")
    return 0


def cmd_windows(args) -> int:  # pragma: no cover - Windows のみ
    from . import win32

    win32.set_dpi_aware()
    needle = (args.filter or "").lower()
    for w in win32.list_windows():
        if needle and needle not in w.title.lower() and needle not in w.exe.lower():
            continue
        print(f"{w.hwnd:>10}  {w.rect}  {w.title!r}  [{w.cls}]  {w.exe}")
    return 0


def _analyze_and_save(native, out: Path, s) -> dict:
    obs = vision.analyze(native, s)
    write_png(out.with_suffix(".native.png"), native)
    write_png(out.with_suffix(".annot.png"), vision.annotate(native, obs))
    write_png(out.with_suffix(".labels.png"), vision.annotate(native, obs, labels_view=True))
    return obs.summary()


def cmd_shot(args) -> int:  # pragma: no cover - Windows のみ
    from . import win32

    s = _settings(args)
    win32.set_dpi_aware()
    w = win32.find_window(s.window_titles, s.exe_names)
    if w is None:
        print("game window not found", file=sys.stderr)
        return 2
    out = Path(args.out or (s.run_dir / "shots" / "shot.png"))
    full = win32.grab_full(w.hwnd)
    write_png(out, full)
    native = vision.to_native(full, s)
    _print({"full": [full.width, full.height], "saved": str(out), "obs": _analyze_and_save(native, out, s)})
    return 0


def cmd_analyze(args) -> int:
    s = _settings(args)
    img = read_png(args.png)
    native = vision.to_native(img, s)
    out = Path(args.out) if args.out else Path(args.png)
    _print(_analyze_and_save(native, out, s))
    return 0


def _backend(args, s):
    if getattr(args, "sim", False):
        return runner.SimBackend(physics=Physics.load(s.physics_file))
    return runner.WindowsBackend(s)


def cmd_watch(args) -> int:
    s = _settings(args)
    b = _backend(args, s)
    try:
        runner.watch(b, s, seconds=args.seconds)
    finally:
        b.close()
    return 0


def cmd_keytest(args) -> int:  # pragma: no cover - Windows のみ
    """左 → 右 → 両方 を押し、HUD の点灯で入力が届いているかを確かめる。"""
    s = _settings(args)
    b = runner.WindowsBackend(s)
    loop = runner.Loop(b, s, runner.SessionLog(None))
    results = {}
    try:
        for name, keys in (("none", (False, False)), ("left", (True, False)), ("right", (False, True)), ("both", (True, True))):
            if not b.ready():
                print("game is not foreground; aborting", file=sys.stderr)
                return 2
            b.set_jets(*keys)
            for _ in range(int(s.tick_hz * args.hold)):
                loop.tick()
            results[name] = {"sent": list(keys), "hud": [loop.obs.hud_left, loop.obs.hud_right]}
            b.release_all()
            loop.wait(0.3)
    finally:
        b.close()
    ok = all(r["sent"] == r["hud"] for r in results.values())
    _print({"ok": ok, "results": results})
    return 0 if ok else 1


def cmd_probe(args) -> int:
    s = _settings(args)
    b = _backend(args, s)
    out = runner.session_dir(s, "probe")
    try:
        rows = runner.probe(b, s, log_root=out)
    finally:
        b.close()
    phys, report = calibrate.fit(rows, Physics.load(s.physics_file))
    (out / "fit.json").write_text(json.dumps({"physics": phys.__dict__, "report": report}, indent=2), encoding="utf-8")
    if args.save:
        phys.save(s.physics_file)
    _print({"log": str(out), "physics": phys.__dict__, "report": report, "saved": bool(args.save)})
    return 0


def cmd_fit(args) -> int:
    s = _settings(args)
    rows = []
    for f in args.frames:
        rows += [json.loads(line) for line in Path(f).read_text(encoding="utf-8").splitlines() if line.strip()]
    phys, report = calibrate.fit(rows, Physics.load(s.physics_file))
    if args.save:
        phys.save(s.physics_file)
    _print({"physics": phys.__dict__, "report": report, "saved": bool(args.save)})
    return 0


def cmd_play(args) -> int:
    s = _settings(args)
    b = _backend(args, s)
    out = runner.session_dir(s, "sim" if args.sim else "play")
    try:
        results = runner.play(b, s, attempts=args.attempts, max_seconds=args.seconds, log_root=out, retry=not args.no_retry)
    finally:
        b.close()
    _print({"log": str(out), "attempts": results})
    return 0 if results and results[-1]["outcome"] == "cleared" else 1


def cmd_record(args) -> int:  # pragma: no cover - Windows のみ
    s = _settings(args)
    b = runner.WindowsBackend(s, focus=False)
    out = runner.session_dir(s, "record")
    try:
        tls = runner.record(b, s, max_seconds=args.seconds, log_root=out)
    finally:
        b.close()
    _print({"log": str(out), "attempts": [t.meta for t in tls]})
    return 0


def cmd_replay(args) -> int:
    s = _settings(args)
    tl = Timeline.load(Path(args.timeline))
    b = _backend(args, s)
    try:
        row = runner.replay(b, s, tl, log_root=runner.session_dir(s, "replay"))
    finally:
        b.close()
    _print(row)
    return 0 if row["outcome"] == "cleared" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m docich.flyhome", description="Fly Me To The Home! (Steam) runner for Windows")
    ap.add_argument("--config", help="TOML (既定: config/windows/fly-me-to-the-home.toml)")
    ap.add_argument("--demo", action="store_true", help="体験版 (app 2919910) を対象にする")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("launch")
    p.add_argument("--install", action="store_true", help="起動ではなくインストール画面を開く")
    p.set_defaults(fn=cmd_launch)
    p = sub.add_parser("windows")
    p.add_argument("filter", nargs="?")
    p.set_defaults(fn=cmd_windows)
    p = sub.add_parser("shot")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_shot)
    p = sub.add_parser("analyze")
    p.add_argument("png")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_analyze)
    for name, fn in (("watch", cmd_watch), ("probe", cmd_probe), ("play", cmd_play), ("replay", cmd_replay)):
        p = sub.add_parser(name)
        p.add_argument("--sim", action="store_true", help="実機ではなくシミュレータで実行")
        p.set_defaults(fn=fn)
        if name == "watch":
            p.add_argument("--seconds", type=float, default=10.0)
        if name == "probe":
            p.add_argument("--save", action="store_true", help="推定値を physics.json に保存")
        if name == "play":
            p.add_argument("--attempts", type=int, default=5)
            p.add_argument("--seconds", type=float, default=120.0)
            p.add_argument("--no-retry", action="store_true")
        if name == "replay":
            p.add_argument("timeline")
    p = sub.add_parser("keytest")
    p.add_argument("--hold", type=float, default=0.4)
    p.set_defaults(fn=cmd_keytest)
    p = sub.add_parser("fit")
    p.add_argument("frames", nargs="+", help="frames.jsonl (probe/record/play のログ)")
    p.add_argument("--save", action="store_true")
    p.set_defaults(fn=cmd_fit)
    p = sub.add_parser("record")
    p.add_argument("--seconds", type=float, default=600.0)
    p.set_defaults(fn=cmd_record)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
