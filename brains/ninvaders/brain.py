#!/usr/bin/env python3
r"""ninvaders (Space Invaders) 用の外部 brain コマンド (決定論・トークン不要)。

docich との境界は CommandBrain のまま変えない: stdin から Observation JSON を
1件読み、stdout には `{"actions": [...]}` だけを出力する。毎サイクル新規
プロセスとして起動されるステートレスなコマンドである。

実ゲーム (nInvaders 0.1.1, tmux capture-pane) の画面要素:
- 自機 `/-^-\` (下から2行目)。最下行はステータス行 (`Level: 01 Score: ... Lives:`)。
- `!` は自機自身の弾 (1フレームで数行上昇)。回避対象ではない。
- `:` は invader の爆弾 (ゆっくり下降)。これが回避対象。
- invader は `-O_` `/o\` `.-.` 等の3文字スプライト、`#` はバリア。

方策 (掃引+回避):
- 自機の上方の敵爆弾 `:` が至近 (左右±dodge_radius・上方dodge_height行以内) に
  あれば、爆弾の無い側へ回避する (回避が発射・寄せより優先)。
- そうでなければ最も近い invader 列へ寄せながら、毎手発射する。
- タイトル・Game Over などプレイ画面でなければ空アクションを返す (遷移は
  wrapper が所有する)。
- 新しい試合の直後は自機が描画されない (最初のキー入力で現れる)。ステータス行
  (`Level:`/`Score:`) があるのに自機が見えないときは、無入力で待たず移動+発射を
  送って再描画させる (実ゲームで自機不在のまま停止し続けた不具合の対策)。
- 移動と発射は1回の send-keys にまとめる (2回に分けるとその間に画面が進む)。

重みは `<repo>/run/brain/ninvaders/weights.json` (無ければ既定値):
  dodge_radius: 回避する敵爆弾の左右幅
  dodge_height: 回避する敵爆弾の上方行数

終了コード: 0 = 正常 / 2 = 入力 JSON 不正。
非0終了時、docich 側の CommandBrain は警告ログを出して空アクションで継続する。
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = Path(os.environ.get(
    "DOCICH_BRAIN_WEIGHTS",
    str(REPO_ROOT / "run" / "brain" / "ninvaders" / "weights.json"),
))

DEFAULT_WEIGHTS = {
    "dodge_radius": 2,
    "dodge_height": 6,
}

PLAYER_MARK = "/-^-\\"
BOMB = ":"
# invader スプライト (-O_ /o\ .-. ,^, 等) を構成する文字。バリアの `#`、UFO の
# `<=>`、自機弾 `!`、爆弾 `:` は含めない。
INVADER_CHARS = set("oO_-.,^/\\")


def load_weights() -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return weights
    if isinstance(data, dict):
        for key in weights:
            value = data.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                weights[key] = value
    return weights


def is_play_screen(text: str) -> bool:
    """対戦中の画面 (ステータス行あり。タイトル・Game Over は除く)。"""
    return (
        "Level:" in text
        and "Score:" in text
        and "Press SPACE to start" not in text
        and "game over" not in text.lower()
    )


def parse(text: str) -> dict | None:
    """自機・敵爆弾・invader列を抜き出す。自機が見えなければ None。"""
    lines = text.splitlines()
    player = None
    for y, line in enumerate(lines):
        idx = line.find(PLAYER_MARK)
        if idx >= 0:
            player = (idx + 2, y)  # '^' の位置
            break
    if player is None:
        return None
    bombs: list[tuple[int, int]] = []
    invader_cols: set[int] = set()
    # 自機より上だけを見る (自機行・ステータス行の `:` や `/-\` は対象外)。
    for y, line in enumerate(lines[: player[1]]):
        for x, ch in enumerate(line):
            if ch == BOMB:
                bombs.append((x, y))
            elif ch in INVADER_CHARS:
                invader_cols.add(x)
    return {"player": player, "bombs": bombs, "invader_cols": invader_cols}


def decide(text: str, weights: dict) -> list[str]:
    """送るキー列を返す。プレイ画面でなければ []。[移動?, "Space"] の順。"""
    if not is_play_screen(text):
        return []
    parsed = parse(text)
    if parsed is None:
        # 新しい試合の直後など、自機がまだ描画されていない。キーで再描画させる。
        return ["Right", "Space"]
    px, py = parsed["player"]
    radius = int(weights.get("dodge_radius", DEFAULT_WEIGHTS["dodge_radius"]))
    height = int(weights.get("dodge_height", DEFAULT_WEIGHTS["dodge_height"]))

    # 回避: 至近の敵爆弾があれば爆弾の無い側へ。
    threats = [
        (bx, by)
        for bx, by in parsed["bombs"]
        if abs(bx - px) <= radius and 0 < py - by <= height
    ]
    move = None
    if threats:
        left = sum(1 for bx, _ in threats if bx <= px)
        right = sum(1 for bx, _ in threats if bx > px)
        move = "Right" if left >= right else "Left"
    else:
        # 寄せ: 最も近い invader 列へ。
        cols = parsed["invader_cols"]
        if cols:
            target = min(cols, key=lambda c: (abs(c - px), c))
            if target < px - 1:
                move = "Left"
            elif target > px + 1:
                move = "Right"
    keys: list[str] = []
    if move is not None:
        keys.append(move)
    keys.append("Space")  # 毎手発射 (自機弾は1発ずつ。連打はゲーム側が吸収する)
    return keys


def main() -> int:
    try:
        obs = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print('{"actions": []}', flush=True)
        return 2
    text = obs.get("text") if isinstance(obs, dict) else None
    if not isinstance(text, str):
        print('{"actions": []}', flush=True)
        return 2
    keys = decide(text, load_weights())
    actions = [{"type": "key", "keys": keys}] if keys else []
    print(json.dumps({"actions": actions}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
