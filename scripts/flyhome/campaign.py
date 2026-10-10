"""Fly Me To The Home! の連続プレイ (実機 Windows、リポジトリのルートで実行)。
クリア後はクリア画面を Enter で抜けてマップへ行き、BEST TIME が 99:99.999 (未クリア) のレベルまで → で進めて開始する。
使い方: py scripts/flyhome/campaign.py <表示用の最初のレベル> <最大レベル数> [秒/レベル]
環境変数 ON_LEVEL=1: 最初はすでにレベル中 / FLYHOME_STATE_FILE: 進捗を上書きするファイル (既定 run/game-state.txt)"""
import sys, time, os
sys.path.insert(0, "src")
from pathlib import Path
from docich.flyhome import settings as S, runner, vision
STATE = Path(os.environ.get("FLYHOME_STATE_FILE", "run/game-state.txt"))
LOG = Path("run/campaign_results.txt")
first = int(sys.argv[1]); count = int(sys.argv[2]); per = float(sys.argv[3]) if len(sys.argv) > 3 else 600.0
s = S.load(None)
done_lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
import re as _re
_cl = [int(m.group(1)) for l in done_lines for m in [_re.search(r"Lv(\d+): クリア", l)] if m]
if _cl:
    first = max(first, max(_cl) + 1)  # 再起動時は記録済みのクリアの次から数える
def save(note=""):
    lines = ["[campaign4] 製品版を自動プレイ中 (MPC, 実機 printwindow+postmessage)"] + done_lines
    if note: lines.append(note)
    STATE.write_text("\n".join(lines) + "\n", encoding="utf-8")
def is_map(b):
    img = vision.to_native(b.capture(), s)
    if vision._party_present(img):
        return False  # クリア画面 (こちらも暗い) はマップではない: Enter で抜ける
    r, g, bl = img.get(250, 60)  # 空の色。マップは暗転している
    return r + g + bl < 480
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
def to_next_level(b):
    time.sleep(3.0)
    for _ in range(6):
        if is_map(b):
            break
        b.tap("enter"); time.sleep(1.5)
    time.sleep(0.8)
    # マップの選択が次のレベルとは限らない (クリア済みのレベルのまま等): 未クリアのレベルまで → で進める
    for _ in range(50):
        if uncleared(b):
            break
        b.tap("right"); time.sleep(0.8)
    b.tap("enter"); time.sleep(2.0)
for i in range(count):
    lv = first + i
    b = runner.WindowsBackend(s, focus=False)
    try:
        if not (os.environ.get("ON_LEVEL") and i == 0):
            to_next_level(b)
        save("  いま: Lv%d を play 中..." % lv)
        out = runner.session_dir(s, "play")
        res = runner.play(b, s, attempts=0, max_seconds=per, log_root=out)
    finally:
        b.close()
    ok = bool(res) and res[-1]["outcome"] == "cleared"
    line = "  Lv%d: %s 試行%d回 %s (%s)" % (lv, "クリア" if ok else "未クリア(時間切れ)", len(res), ("%.1fs" % res[-1]["duration_s"]) if ok else "-", out)
    done_lines.append(line); LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("\n".join(done_lines) + "\n", encoding="utf-8")
    save()
    print("Lv%d" % lv, "cleared" if ok else "FAILED", "attempts", len(res), flush=True)
    if not ok:
        break
print("done")
