"""制御器と認識をオフラインで検証するための簡易シミュレータ。

実機の物理は未計測なので ``control.Physics`` の仮説モデルで動かす。描画はゲームと同じ色で
320x180 に行い、``vision.analyze`` がそのまま読めるようにする (認識→追跡→計画→制御の
一気通貫テストを Linux CI で回すため)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .control import Physics
from .image import Image

SKY = (110, 145, 213)
GRASS = (32, 129, 0)
DIRT = (128, 64, 0)
HAZARD_EDGE = (217, 0, 0)
ROOF = (128, 0, 0)
WALL = (16, 32, 96)
ORANGE = (255, 127, 0)
WHITE = (255, 255, 255)
FLAME_RED = (251, 0, 2)
FLAME_YELLOW = (249, 255, 0)

Box = tuple[int, int, int, int]


@dataclass
class World:
    width: int = 320
    height: int = 180
    solids: list[Box] = field(default_factory=list)
    hazards: list[Box] = field(default_factory=list)
    home: Box = (240, 40, 272, 64)  # 家全体 (上 40% が屋根)
    start: tuple[float, float] = (60.0, 140.0)
    radius: float = 5.0

    @classmethod
    def basic(cls) -> "World":
        return cls(solids=[(30, 147, 100, 180), (225, 64, 290, 180)], home=(236, 40, 268, 64), start=(60.0, 140.0))


@dataclass
class Body:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    angle: float = 0.0
    omega: float = 0.0
    started: bool = False
    outcome: str | None = None  # None | "dead" | "cleared"
    left: bool = False
    right: bool = False


def _hit(box: Box, x: float, y: float, r: float) -> bool:
    x0, y0, x1, y1 = box
    cx = min(max(x, x0), x1)
    cy = min(max(y, y0), y1)
    return (x - cx) ** 2 + (y - cy) ** 2 < r * r


def step(world: World, b: Body, left: bool, right: bool, dt: float, p: Physics) -> None:
    b.left, b.right = left, right
    if b.outcome is not None:
        return
    if not b.started:
        if not (left or right):
            return
        b.started = True
    n = int(left) + int(right)
    thrust = 0.0
    if n == 2:
        thrust = p.thrust
    elif n == 1:
        thrust = p.thrust * p.single_thrust_ratio
    turn = (int(left) - int(right)) * p.torque_sign
    if p.spin_mode == "accel":
        b.omega += turn * p.spin * dt
        b.omega -= b.omega * min(1.0, p.angular_damping * dt)
    else:
        b.omega = turn * p.spin
    b.angle = (b.angle + b.omega * dt + math.pi) % (2 * math.pi) - math.pi
    hx, hy = math.sin(b.angle), -math.cos(b.angle)
    b.vx += (hx * thrust) * dt
    b.vy += (hy * thrust + p.gravity) * dt
    b.vx -= b.vx * min(1.0, p.drag * dt)
    b.vy -= b.vy * min(1.0, p.drag * dt)
    b.x += b.vx * dt
    b.y += b.vy * dt
    hx0, hy0, hx1, hy1 = world.home
    if hx0 <= b.x <= hx1 and hy0 <= b.y <= hy1:
        b.outcome = "cleared"
        return
    if not (-10 <= b.x <= world.width + 10 and -40 <= b.y <= world.height + 10):
        b.outcome = "dead"
        return
    for box in world.solids + world.hazards:
        if _hit(box, b.x, b.y, world.radius):
            b.outcome = "dead"
            return


def render(world: World, b: Body) -> Image:
    img = Image.blank(world.width, world.height, SKY)
    for x0, y0, x1, y1 in world.solids:
        img.fill_rect(x0, y0, x1, y1, DIRT)
        img.fill_rect(x0, y0, x1, min(y1, y0 + 3), GRASS)
    for x0, y0, x1, y1 in world.hazards:
        img.fill_rect(x0, y0, x1, y1, HAZARD_EDGE)
        img.fill_rect(x0 + 2, y0 + 2, x1 - 2, y1 - 2, (0, 0, 0))
    hx0, hy0, hx1, hy1 = world.home
    roof_h = max(3, int((hy1 - hy0) * 0.4))
    img.fill_rect(hx0, hy0 + roof_h, hx1, hy1, WALL)
    img.fill_rect(hx0, hy0, hx1, hy0 + roof_h, ROOF)
    # 矢印 HUD (押下中はオレンジで塗る)
    for i, on in enumerate((b.left, b.right)):
        x0 = 278 + i * 21
        img.fill_rect(x0, 160, x0 + 16, 174, WHITE)
        img.fill_rect(x0 + 1, 161, x0 + 15, 173, ORANGE if on else SKY)
    if b.outcome is None:
        _draw_player(img, b)
    return img


def _draw_player(img: Image, b: Body) -> None:
    hx, hy = math.sin(b.angle), -math.cos(b.angle)  # 頭方向
    rx, ry = math.cos(b.angle), math.sin(b.angle)  # 体の右
    for py in range(int(b.y) - 13, int(b.y) + 14):
        for px in range(int(b.x) - 13, int(b.x) + 14):
            dx, dy = px + 0.5 - b.x, py + 0.5 - b.y
            u = dx * rx + dy * ry
            v = dx * hx + dy * hy
            if abs(u) <= 4 and -7 <= v <= 7:
                if v < -5 and abs(u) < 1:
                    continue
                face = 1.5 <= v <= 5 and 0.5 <= u <= 3.5 and (int(u * 2) + int(v * 2)) % 2 == 0
                img.put(px, py, WHITE if face else ORANGE)
            for on, side in ((b.left, -1), (b.right, 1)):
                if on and -11.5 <= v <= -7.5 and 0.5 <= u * side <= 3.5:
                    img.put(px, py, FLAME_YELLOW if v > -9 else FLAME_RED)


def run_controller(world: World, decide, *, physics: Physics, tick_hz: float = 30.0, seconds: float = 20.0, substeps: int = 4):
    """decide(body) -> (left, right) を tick_hz で呼び、結末と軌跡を返す (vision を通さない版)。"""
    b = Body(*world.start)
    trace = []
    dt = 1.0 / tick_hz / substeps
    for k in range(int(seconds * tick_hz)):
        left, right = decide(b)
        for _ in range(substeps):
            step(world, b, left, right, dt, physics)
        trace.append((k / tick_hz, b.x, b.y, b.angle, left, right))
        if b.outcome:
            break
    return b.outcome, trace
