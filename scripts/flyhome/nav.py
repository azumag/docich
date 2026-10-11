"""作業用: キー(enter/left/right/space)を順に1回ずつ押し、待ち秒・スクショ(shot:パス)も混ぜられる。
使い方: FLYCFG=設定.toml NOFOCUS=1 py nav.py enter 1.0 left 0.5 shot:out.png
NOFOCUS=1 なら前面化しない(postmessage 用)。※ escape はマップ画面で押すとゲームが終了するので使わない。"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, "src")
from docich.flyhome import settings as S, runner, vision
from docich.flyhome.image import write_png

s = S.load(Path(os.environ["FLYCFG"])) if os.environ.get("FLYCFG") else S.load(None)
b = runner.WindowsBackend(s, focus=not os.environ.get("NOFOCUS"))
time.sleep(0.3)
try:
    for a in sys.argv[1:]:
        if a.startswith("shot:"):
            native = b.capture()
            obs = vision.analyze(native, s)
            write_png(Path(a[5:]), vision.annotate(native, obs))
            print(obs.summary())
            continue
        try:
            time.sleep(float(a))
            continue
        except ValueError:
            pass
        if not b.ready():
            print("game not foreground; abort"); break
        b.tap(a)
        print("tapped", a)
finally:
    b.close()
