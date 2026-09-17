#!/usr/bin/env python3
r"""ninvaders (Space Invaders) 用の外部 brain コマンド (決定論・トークン不要)。

docich との境界は CommandBrain のまま変えない: stdin から Observation JSON を
1件読み、stdout には `{"actions": [...]}` だけを出力する。毎サイクル新規
プロセスとして起動されるステートレスなコマンドである。

方策 (掃引+回避):
- 自機 (`/-^-\`) の真上にある最も低い invader 列へ寄せながら、毎手発射する。
- 敵弾 `!` が自機の至近 (左右±dodge_radius・上方dodge_height行以内) にあれば、
  弾の無い側へ回避する (回避が発射・寄せより優先)。
- タイトル・Game Over などプレイ画面でなければ空アクションを返す (遷移は
  wrapper が所有する)。

重みは `<repo>/run/brain/ninvaders/weights.json` (無ければ既定値):
  dodge_radius: 回避する敵弾の左右幅
  dodge_height: 回避する敵弾の上方行数

終了コード: 0 = 正常 / 2 = 入力 JSON 不正。
非0終了時、docich 側の CommandBrain は警告ログを出して空アクションで継続する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = REPO_ROOT / "run" / "brain" / "ninvaders" / "weights.json"

DEFAULT_WEIGHTS = {
    "dodge_radius": 2,
    "dodge_height": 5,
}

PLAYER_MARK = "/-^-\\"
INVADER_CHARS = set("oO.,^_-/")


def load_weights() -> dict:
    st = dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return st
    if isinstance(data, dict):
        st.update({k: v for k, v in data.items() if k in st})
    return st


def parse(text: str) -> dict | None:
    """自機・敵弾・invader列を抜き出す。プレイ画面でなければ None。"""
    if "Press SPACE to start" in text:
        return None
    player = None
    bullets: list[tuple[int, int]] = []
    invader_cols: set[int] = set()
    lines = text.splitlines()
    for y, line in enumerate(lines):
        idx = line.find(PLAYER_MARK)
        if idx >= 0 and player is None:
            player = (idx + 2, y)  # '^' の位置
        for x, ch in enumerate(line):
            if ch == "!":
                bullets.append((x, y))
    if player is None:
        return None
    # invader列: 自機より上にある invader 風の文字の列
    for y, line in enumerate(lines):
        if y >= player[1]:
            continue
        for x, ch in enumerate(line):
            if ch in INVADER_CHARS:
                invader_cols.add(x)
    return {"player": player, "bullets": bullets, "invader_cols": invader_cols}


def decide(text: str, weights: dict) -> list[str]:
    """送るキー列を返す。動くなけれは [] (第1要素=移動、第2要素=発射)。"""
    parsed = parse(text)
    if parsed is None:
        return []
    px, py = parsed["player"]
    radius = int(weights.get("dodge_radius", DEFAULT_WEIGHTS["dodge_radius"]))
    height = int(weights.get("dodge_height", DEFAULT_WEIGHTS["dodge_height"]))

    # 回避: 至近の敵弾があれば弾の無い側へ。
    threats = [
        (bx, by)
        for bx, by in parsed["bullets"]
        if abs(bx - px) <= radius and 0 < py - by <= height
    ]
    move = None
    if threats:
        left = sum(1 for bx, _ in threats if bx <= px)
        right = sum(1 for bx, _ in threats if bx > px)
        move = "Right" if left >= right else "Left"
    else:
        # 寄せ: 自機より上にいる最も低い invader…ではなく、最も近い列へ。
        cols = parsed["invader_cols"]
        if cols:
            target = min(cols, key=lambda c: (abs(c - px), c))
            if target < px - 1:
                move = "Left"
            elif target > px + 1:
                move = "Right"
    actions: list[str] = []
    if move is not None:
        actions.append(move)
    actions.append("Space")  # 毎手発射 (弾数制限はゲーム側が吸収する)
    return actions


def main() -> int:
    try:
        obs = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("[]", flush=True)
        return 2
    text = obs.get("text") if isinstance(obs, dict) else None
    if not isinstance(text, str):
        print("[]", flush=True)
        return 2
    keys = decide(text, load_weights())
    print(
        json.dumps(
            {"actions": [{"type": "key", "keys": [k]} for k in keys]},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
