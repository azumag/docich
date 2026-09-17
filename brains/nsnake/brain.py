#!/usr/bin/env python3
"""nsnake (Snake) 用の外部 brain コマンド (決定論・トークン不要)。

docich との境界は CommandBrain のまま変えない: stdin から Observation JSON を
1件読み、stdout には `{"actions": [...]}` だけを出力する。毎サイクル新規
プロセスとして起動されるステートレスなコマンドである。

方策: 盤面テキストを解析し、頭から餌への最短路 (BFS) の最初の一手を送る。
体・壁を避け、行き止まりは flood-fill で回避する。メニュー・Game Over など
プレイ画面でなければ空アクションを返す (遷移は wrapper が所有する)。

重みは `<repo>/run/brain/nsnake/weights.json` (無ければ既定値):
  tail_passable: しっぽのセルを通過可能とみなす
  min_free: 移動先から到達可能な空きマスの最低数 (行き止まり回避)

終了コード: 0 = 正常 / 2 = 入力 JSON 不正。
非0終了時、docich 側の CommandBrain は警告ログを出して空アクションで継続する。
"""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = REPO_ROOT / "run" / "brain" / "nsnake" / "weights.json"

DEFAULT_WEIGHTS = {
    "tail_passable": True,
    "min_free": 8,
}

DIRS = {
    "Up": (0, -1),
    "Down": (0, 1),
    "Left": (-1, 0),
    "Right": (1, 0),
}


def load_weights() -> dict:
    st = dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return st
    if isinstance(data, dict):
        st.update({k: v for k, v in data.items() if k in st})
    return st


def parse_arena(text: str) -> list[str] | None:
    """アリーナ行 (x a <76> a x) を抜き出す。プレイ画面でなければ None。"""
    rows = []
    for line in text.splitlines():
        if (
            len(line) >= 80
            and line[0] == "x"
            and line[1] == "a"
            and line[78] == "a"
            and line[79] == "x"
        ):
            rows.append(line[2:78])
    if len(rows) < 3:
        return None
    return rows


def find_cells(rows: list[str]) -> tuple | None:
    head = None
    body: list[tuple[int, int]] = []
    foods: list[tuple[int, int]] = []
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "@":
                if head is not None:
                    return None
                head = (x, y)
            elif ch == "o":
                body.append((x, y))
            elif ch == "$":
                foods.append((x, y))
    if head is None or not foods:
        return None
    return head, body, foods


def find_tail(head, body: list) -> tuple | None:
    """しっぽ (体1つのみに接する体セル。首は頭にも接するので除外) を探す。"""
    body_set = set(body)
    tails = []
    for bx, by in body:
        neighbours = [
            (bx + dx, by + dy) for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0))
        ]
        if head in neighbours:
            continue  # 首の可能性
        if sum(1 for n in neighbours if n in body_set) == 1:
            tails.append((bx, by))
    if len(tails) == 1:
        return tails[0]
    return None


def blocked_cells(rows, body: list, tail, tail_passable: bool) -> set:
    blocked = set()
    height = len(rows)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "a" or x >= len(row):
                blocked.add((x, y))
    for b in body:
        if tail_passable and tail is not None and b == tail:
            continue
        blocked.add(b)
    return blocked


def bfs(rows, blocked: set, start, goals: set) -> list | None:
    """start から最寄り goal への経路 (セルのリスト) を返す。無ければ None。"""
    width = max(len(r) for r in rows)
    height = len(rows)
    prev: dict = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur in goals:
            path = [cur]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nxt = (cur[0] + dx, cur[1] + dy)
            if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
                continue
            if nxt in prev or nxt in blocked:
                continue
            prev[nxt] = cur
            queue.append(nxt)
    return None


def flood_free(rows, blocked: set, start) -> int:
    """start から到達可能な空きセル数 (行き止まり検出用)。"""
    width = max(len(r) for r in rows)
    height = len(rows)
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nxt = (cur[0] + dx, cur[1] + dy)
            if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
                continue
            if nxt in seen or nxt in blocked:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return len(seen)


def decide(text: str, weights: dict) -> str | None:
    """送るべき方向キー (Up/Down/Left/Right) を返す。動くなけれは None。"""
    if "Game Over" in text or "Main Menu" in text:
        # 遷移画面は wrapper が所有する (開始・再開キーの二重送信を避ける)。
        return None
    rows = parse_arena(text)
    if rows is None:
        return None
    found = find_cells(rows)
    if found is None:
        return None
    head, body, foods = found
    tail = find_tail(head, body)
    blocked = blocked_cells(rows, body, tail, bool(weights.get("tail_passable", True)))
    width = max(len(r) for r in rows)
    height = len(rows)

    path = bfs(rows, blocked, head, set(foods))
    ordered: list[tuple[str, tuple[int, int]]] = []
    if path is not None and len(path) >= 2:
        nxt = path[1]
        dx, dy = nxt[0] - head[0], nxt[1] - head[1]
        for name, (ddx, ddy) in DIRS.items():
            if (dx, dy) == (ddx, ddy):
                ordered.append((name, nxt))
                break
    # 餌への路が無い/危険なら、安全な隣接セルを広さ順に試す。
    scored = []
    for name, (dx, dy) in DIRS.items():
        if any(n == name for n, _ in ordered):
            continue
        cell = (head[0] + dx, head[1] + dy)
        if not (0 <= cell[0] < width and 0 <= cell[1] < height):
            continue
        if cell in blocked:
            continue
        scored.append((flood_free(rows, blocked, cell), name, cell))
    scored.sort(reverse=True)
    ordered.extend((name, cell) for _, name, cell in scored)

    min_free = int(weights.get("min_free", DEFAULT_WEIGHTS["min_free"]))
    fallback = None
    for name, cell in ordered:
        space = flood_free(rows, blocked, cell)
        if fallback is None:
            fallback = name
        if space >= min_free:
            return name
    return fallback


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
    direction = decide(text, load_weights())
    if direction is None:
        print(json.dumps({"actions": []}, ensure_ascii=False), flush=True)
        return 0
    print(
        json.dumps(
            {"actions": [{"type": "key", "keys": [direction]}]}, ensure_ascii=False
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
