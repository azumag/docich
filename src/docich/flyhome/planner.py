"""地形・棘を避けて家まで行く経路 (A*) を作る。

ネイティブ 320x180 を ``cell`` px 角のセルに落とし、プレイヤーの半径ぶん障害物を膨らませる。
地形に触れても死なない可能性はあるが (要実機確認)、安全側で全て障害物として扱う。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .vision import Observation


@dataclass
class Grid:
    cols: int
    rows: int
    cell: int
    blocked: bytearray  # cols*rows, 0 = 通行可
    cost: bytearray  # 障害物近傍の追加コスト

    def idx(self, c: int, r: int) -> int:
        return r * self.cols + c

    def to_cell(self, x: float, y: float) -> tuple[int, int]:
        return (
            min(self.cols - 1, max(0, int(x // self.cell))),
            min(self.rows - 1, max(0, int(y // self.cell))),
        )

    def center(self, c: int, r: int) -> tuple[float, float]:
        return (c + 0.5) * self.cell, (r + 0.5) * self.cell

    def free(self, c: int, r: int) -> bool:
        return 0 <= c < self.cols and 0 <= r < self.rows and not self.blocked[self.idx(c, r)]


def _dilate(src: bytearray, cols: int, rows: int, radius: int) -> bytearray:
    if radius <= 0:
        return bytearray(src)
    out = bytearray(len(src))
    offs = [(dc, dr) for dr in range(-radius, radius + 1) for dc in range(-radius, radius + 1) if dc * dc + dr * dr <= radius * radius + radius]
    for r in range(rows):
        for c in range(cols):
            if src[r * cols + c]:
                for dc, dr in offs:
                    nc, nr = c + dc, r + dr
                    if 0 <= nc < cols and 0 <= nr < rows:
                        out[nr * cols + nc] = 1
    return out


def build_grid(
    obs: Observation,
    *,
    cell: int = 4,
    solid_margin: int = 2,
    hazard_margin: int = 3,
    soft_margin: int = 2,
) -> Grid:
    cols, rows = math.ceil(obs.width / cell), math.ceil(obs.height / cell)
    solid = bytearray(cols * rows)
    w = obs.width
    for i, v in enumerate(obs.solid):
        if v:
            x, y = i % w, i // w
            solid[(y // cell) * cols + x // cell] = 1
    # 画面外へ出ると見失う (実機で死亡扱いかは要確認) ので四辺を壁として扱う。
    for c in range(cols):
        solid[c] = solid[(rows - 1) * cols + c] = 1
    for r in range(rows):
        solid[r * cols] = solid[r * cols + cols - 1] = 1
    hazard = bytearray(cols * rows)
    for x0, y0, x1, y1 in obs.hazards:
        for r in range(y0 // cell, min(rows, (y1 - 1) // cell + 1)):
            for c in range(x0 // cell, min(cols, (x1 - 1) // cell + 1)):
                hazard[r * cols + c] = 1
    hard = _dilate(solid, cols, rows, solid_margin)
    hz = _dilate(hazard, cols, rows, hazard_margin)
    blocked = bytearray(a | b for a, b in zip(hard, hz))
    near = _dilate(blocked, cols, rows, soft_margin)
    cost = bytearray(4 if n and not b else 0 for n, b in zip(near, blocked))
    # 家の箱は目的地なので通行可にする
    if obs.home_bbox is not None:
        x0, y0, x1, y1 = obs.home_bbox
        for r in range(y0 // cell, min(rows, (y1 - 1) // cell + 1)):
            for c in range(x0 // cell, min(cols, (x1 - 1) // cell + 1)):
                blocked[r * cols + c] = 0
                cost[r * cols + c] = 0
    return Grid(cols, rows, cell, blocked, cost)


def _nearest_free(g: Grid, c: int, r: int, limit: int = 6) -> tuple[int, int] | None:
    if g.free(c, r):
        return c, r
    for rad in range(1, limit + 1):
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                if max(abs(dc), abs(dr)) == rad and g.free(c + dc, r + dr):
                    return c + dc, r + dr
    return None


def astar(g: Grid, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]] | None:
    s = _nearest_free(g, *g.to_cell(*start))
    t = _nearest_free(g, *g.to_cell(*goal))
    if s is None or t is None:
        return None
    openq = [(0.0, 0.0, s)]
    came: dict[tuple[int, int], tuple[int, int] | None] = {s: None}
    gcost = {s: 0.0}
    steps = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
    while openq:
        _, gc, cur = heapq.heappop(openq)
        if cur == t:
            break
        if gc > gcost.get(cur, math.inf):
            continue
        c, r = cur
        for dc, dr, w in steps:
            nc, nr = c + dc, r + dr
            if not g.free(nc, nr):
                continue
            if dc and dr and not (g.free(c + dc, r) and g.free(c, r + dr)):
                continue  # 角のすり抜け禁止
            ng = gc + w + g.cost[g.idx(nc, nr)]
            if ng < gcost.get((nc, nr), math.inf):
                gcost[(nc, nr)] = ng
                came[(nc, nr)] = cur
                h = math.hypot(t[0] - nc, t[1] - nr)
                heapq.heappush(openq, (ng + h, ng, (nc, nr)))
    if t not in came:
        return None
    cells = []
    cur: tuple[int, int] | None = t
    while cur is not None:
        cells.append(cur)
        cur = came[cur]
    cells.reverse()
    pts = [g.center(c, r) for c, r in cells]
    pts[-1] = goal
    return simplify(g, pts)


def line_free(g: Grid, a: tuple[float, float], b: tuple[float, float]) -> bool:
    n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (g.cell / 2)))
    for i in range(n + 1):
        x = a[0] + (b[0] - a[0]) * i / n
        y = a[1] + (b[1] - a[1]) * i / n
        c, r = g.to_cell(x, y)
        if g.blocked[g.idx(c, r)] or g.cost[g.idx(c, r)]:
            return False
    return True


def simplify(g: Grid, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """見通しの利く点を飛ばして折れ点だけ残す。"""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1 and not line_free(g, pts[i], pts[j]):
            j -= 1
        out.append(pts[j])
        i = j
    return out


def lookahead(path: list[tuple[float, float]], pos: tuple[float, float], reach: float = 10.0) -> tuple[float, float]:
    """経路上で次に向かう点。最寄りの折れ点より先で、reach より遠い最初の点を返す。"""
    near = min(range(len(path)), key=lambda i: (path[i][0] - pos[0]) ** 2 + (path[i][1] - pos[1]) ** 2)
    for p in path[near + 1 :]:
        if math.hypot(p[0] - pos[0], p[1] - pos[1]) > reach:
            return p
    return path[-1]
