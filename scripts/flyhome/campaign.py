"""Fly Me To The Home! の連続プレイ (実機 Windows、リポジトリのルートで実行)。
クリア後はクリア画面を Enter で抜けてマップへ行き、BEST TIME が 99:99.999 (未クリア) のレベルまで → で進めて開始する。
使い方: py scripts/flyhome/campaign.py <表示用の最初のレベル> <最大レベル数> [秒/レベル]
環境変数 ON_LEVEL=1: 最初はすでにレベル中 / FLYHOME_STATE_FILE: 進捗ファイル
FLYHOME_LEVEL_ORDER=sequential: クリア済みも番号順に再プレイ
FLYHOME_CAMPAIGN_LOG: 再プレイ用の独立した結果ファイル"""
import sys, time, os
sys.path.insert(0, "src")
from pathlib import Path
from docich.flyhome import settings as S, runner, vision
from docich.flyhome.image import write_png
from docich.flyhome.progress import StatePublisher
from docich.flyhome.map_navigation import level_number
from docich.flyhome.narration import SpeechJournal, LiveNarrator
STATE = Path(os.environ.get("FLYHOME_STATE_FILE", "run/game-state.txt"))
LOG = Path(os.environ.get("FLYHOME_CAMPAIGN_LOG", "run/campaign_results.txt"))
SEQUENTIAL = os.environ.get("FLYHOME_LEVEL_ORDER") == "sequential"
first = int(sys.argv[1]); count = int(sys.argv[2]); per = float(sys.argv[3]) if len(sys.argv) > 3 else 600.0
last = min(50, first + count - 1)
s = S.load(None)
done_lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
import re as _re
_cl = [int(m.group(1)) for l in done_lines for m in [_re.search(r"Lv(\d+): クリア", l)] if m]
if _cl:
    first = max(first, max(_cl) + 1)  # 再起動時は記録済みのクリアの次から数える
publisher = StatePublisher(STATE)
speech = SpeechJournal.from_env()

def save(note=""):
    heading = "[特別コーナー] Fly Me To The Home! %s面から自動プレイ" % sys.argv[1] if SEQUENTIAL else "[campaign4] 製品版を自動プレイ中 (MPC, 実機 printwindow+postmessage)"
    lines = [heading] + done_lines
    if note: lines.append(note)
    publisher.publish("\n".join(lines) + "\n")

def is_map(b):
    import zlib
    img = vision.to_native(b.capture(), s)
    if vision._party_present(img):
        return False
    # 実機マップ固有の BEST TIME 見出し。暗いLv48のプレイ/死亡画面は空の色では区別できない。
    bits = bytes(int(all(c > 200 for c in img.get(x, y)))
                 for y in range(1, 8) for x in range(267, 318))
    return sum(bits) == 129 and zlib.crc32(bits) == 3045283794
UNCLEARED_CRC = 3686374964  # 未クリアのマップの BEST TIME「99:99.999」の白画素パターン
def uncleared(b):
    """マップの右上 BEST TIME が 99:99.999 (未クリア) か。白い画素の並びで判定 (実機の未クリアのマップで常に 432 画素・同じ並び)。"""
    import zlib
    img = vision.to_native(b.capture(), s)
    bits = bytearray()
    for y in range(9, 22):
        for x in range(233, 318):
            r, g, bl = img.get(x, y)
            bits.append(1 if (r > 200 and g > 200 and bl > 200) else 0)
    return sum(bits) == 432 and zlib.crc32(bytes(bits)) == UNCLEARED_CRC
def to_next_level(b, level=None):
    save("  いま: マップ切替待ち")
    time.sleep(3.0)
    for _ in range(6):
        if is_map(b):
            break
        img = vision.to_native(b.capture(), s)
        b.tap("enter" if vision._party_present(img) else "escape")
        time.sleep(1.5)
    else:
        raise RuntimeError("map BEST TIME header was not observed")
    time.sleep(0.8)
    # マップの選択が次のレベルとは限らない (クリア済みのレベルのまま等): 未クリアのレベルまで → で進める
    for selection_step in range(50):
        save("  いま: マップで%sを選択 (%d/50)" % ("Lv%d" % level if SEQUENTIAL else "未クリアの面", selection_step + 1))
        if not is_map(b):
            raise RuntimeError("map lost during level selection")
        if SEQUENTIAL:
            if level_number(vision.to_native(b.capture(), s)) == level:
                break
        elif uncleared(b):
            break
        b.tap("right"); time.sleep(0.8)
    else:
        raise RuntimeError("requested level was not found on map")
    write_png(s.run_dir / "maps" / ("selected-%d.png" % time.time_ns()), b.capture())
    save("  いま: マップでLv%dを確認、Enterで開始" % level if SEQUENTIAL else "  いま: マップで未クリアを確認、Enterで開始")
    b.tap("enter"); time.sleep(2.0)
try:
    for i in range(max(0, last - first + 1)):
        lv = first + i
        b = runner.WindowsBackend(s, focus=False)
        try:
            initial_img = vision.to_native(b.capture(), s)
            rescan = s.run_dir / "request-map-rescan"
            if i == 0 and rescan.exists():
                rescan.unlink()
                b.tap("escape")  # 実プレイ画面からの明示的な選び直し要求。
                time.sleep(1.0)
                to_next_level(b, lv)
            elif not (i == 0 and os.environ.get("ON_LEVEL")) or vision._party_present(initial_img) or is_map(b):
                to_next_level(b, lv)
            save("  いま: Lv%d を play 中..." % lv)
            out = runner.session_dir(s, "play")
            narrator = LiveNarrator(speech, lv)
            def step(st, extra):
                narrator.step(st)
                pos = "位置不明" if st.x is None else "x=%.1f y=%.1f angle=%.2f" % (st.x, st.y, st.angle or 0.0)
                save("  いま: Lv%d 試行%d %s %s (%s)" % (lv, st.attempt, st.phase, pos, out))
                if extra is not None:
                    extra.update(publisher.metrics())
                    extra.update(speech.metrics())
            res = runner.play(b, s, attempts=0, max_seconds=per, log_root=out, on_step=step, on_restart=lambda: to_next_level(b, lv))
        finally:
            b.close()
        ok = bool(res) and res[-1]["outcome"] == "cleared"
        line = "  Lv%d: %s 試行%d回 %s (%s)" % (lv, "クリア" if ok else "未クリア(時間切れ)", len(res), ("%.1fs" % res[-1]["duration_s"]) if ok else "-", out)
        done_lines.append(line); LOG.parent.mkdir(exist_ok=True)
        LOG.write_text("\n".join(done_lines) + "\n", encoding="utf-8")
        save()
        print("Lv%d" % lv, "cleared" if ok else "FAILED", "attempts", len(res), flush=True)
        if not ok:
            raise SystemExit(1)
    print("done")
finally:
    try:
        speech.close()
    finally:
        publisher.close()
