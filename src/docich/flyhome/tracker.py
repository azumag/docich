"""フレーム列からプレイヤーの運動状態と試行の結末 (死亡/帰宅) を推定する。

死亡時は爆発してプレイヤーが消え、帰宅時は家に入って消える。どちらも左上に案内看板が
出るが、背景色と紛らわしいので「消えた直前の位置が家の近くか」で判定する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .vision import Observation

MENU, PLAYING, DEAD, CLEARED = "menu", "playing", "dead", "cleared"


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class State:
    t: float
    phase: str
    attempt: int
    x: float | None = None
    y: float | None = None
    vx: float = 0.0
    vy: float = 0.0
    angle: float | None = None
    omega: float = 0.0
    home: tuple[float, float] | None = None
    home_bbox: tuple[int, int, int, int] | None = None
    best_dist: float | None = None  # この試行で家に最も近づいた距離 (改善の評価値)

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        return {
            "t": round(self.t, 4),
            "phase": self.phase,
            "attempt": self.attempt,
            "x": r(self.x),
            "y": r(self.y),
            "vx": r(self.vx),
            "vy": r(self.vy),
            "angle": r(self.angle),
            "omega": r(self.omega),
            "best_dist": r(self.best_dist),
        }


class Tracker:
    def __init__(self, *, vel_alpha: float = 0.6, lost_frames: int = 3, home_margin: float = 8.0):
        self.vel_alpha = vel_alpha
        self.lost_frames = lost_frames
        self.home_margin = home_margin
        self.phase = MENU
        self.attempt = 0
        self._last: State | None = None
        self._missing = 0
        self._home = None
        self._home_bbox = None
        self.last_obs: Observation | None = None  # プレイヤーが写っていた最新の観測 (地図用)

    @property
    def prev_pos(self):
        if self._last is None or self._last.x is None:
            return None
        return self._last.x, self._last.y

    @property
    def prev_angle(self):
        return None if self._last is None else self._last.angle

    def _near_home(self, x: float, y: float) -> bool:
        if self._home_bbox is None:
            return False
        x0, y0, x1, y1 = self._home_bbox
        m = self.home_margin
        return x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m

    def update(self, obs: Observation, t: float) -> State:
        if obs.home_bbox is not None:
            self._home, self._home_bbox = obs.home, obs.home_bbox
        p = obs.player
        last = self._last
        if p is None:
            self._missing += 1
            if self.phase == PLAYING and self._missing >= self.lost_frames and last is not None and last.x is not None:
                self.phase = CLEARED if self._near_home(last.x, last.y) else DEAD
            st = State(t, self.phase, self.attempt, home=self._home, home_bbox=self._home_bbox)
            if last is not None:
                st.x, st.y, st.angle, st.best_dist = last.x, last.y, last.angle, last.best_dist
            # 欠落フレームでは速度を持ち越さない (復帰時に跳ねた値を作らない)
            self._last = st if self.phase != PLAYING else last
            return st

        self._missing = 0
        self.last_obs = obs
        if self.phase != PLAYING:
            self.attempt += 1
            self.phase = PLAYING
            last = None
        vx = vy = omega = 0.0
        if last is not None and last.x is not None and t > last.t:
            dt = t - last.t
            a = self.vel_alpha
            vx = a * (p.x - last.x) / dt + (1 - a) * last.vx
            vy = a * (p.y - last.y) / dt + (1 - a) * last.vy
            if p.angle is not None and last.angle is not None:
                omega = a * wrap(p.angle - last.angle) / dt + (1 - a) * last.omega
        best = last.best_dist if last is not None else None
        if self._home is not None:
            d = math.hypot(p.x - self._home[0], p.y - self._home[1])
            best = d if best is None else min(best, d)
        st = State(t, PLAYING, self.attempt, p.x, p.y, vx, vy, p.angle, omega, self._home, self._home_bbox, best)
        self._last = st
        return st
