"""左右ジェットの ON/OFF を決める制御器。

仮説モデル (スクリーンショットと「左右ジェットの ON/OFF だけ」という説明から):

- 両足噴射: 体の頭方向へ推進する (両方 ON で真っすぐ飛ぶ)。
- 片足噴射: 体が回転する (+ 弱い推進)。どちらの足でどちら向きに回るかは ``torque_sign``。
- 噴射しなければ重力で落ちる。

実機の値は ``probe`` → ``calibrate`` で推定して ``physics.json`` に保存する。
制御は月着陸船型: 目標点への PD で必要加速度を出し、重力を打ち消す推力方向へ体を傾け、
向きが合っていれば両足、ずれていれば片足で回す。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .tracker import State, wrap


@dataclass
class Physics:
    gravity: float = 260.0  # px/s^2 (下向き)
    thrust: float = 520.0  # 両足噴射の加速度 px/s^2
    single_thrust_ratio: float = 0.5  # 片足噴射時の推進 (両足比)
    spin: float = 5.0  # 片足噴射の回転: rate なら rad/s、accel なら rad/s^2
    spin_mode: str = "rate"  # "rate" | "accel"
    angular_damping: float = 6.0  # accel モード時の角速度減衰 1/s
    torque_sign: int = 1  # +1: 左足噴射で時計回り (angle 増加)
    drag: float = 0.4  # 速度減衰 1/s

    @classmethod
    def load(cls, path: Path) -> "Physics":
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")


@dataclass
class Gains:
    # 位置誤差 → 目標速度 (上限 max_speed) → 速度誤差 → 必要加速度、の 2 段。
    # 速度上限で行き過ぎを抑える (タイムより確実な帰宅を優先)。
    k_pos: float = 2.5
    k_vel: float = 5.0
    max_speed: float = 70.0  # px/s
    max_accel_ratio: float = 0.7  # 要求加速度の上限 (推力比)
    max_tilt: float = math.radians(70)
    angle_tol: float = math.radians(12)
    # 推力が要らない (惰性/落下) 間は、片足噴射の推進で浮いてしまわないよう大きくずれた時だけ回す。
    coast_angle_tol: float = math.radians(35)
    lead: float = 0.12  # 角速度の先読み秒
    thrust_on: float = 0.35  # 推力要求 (推力比) がこれを超えたら噴射
    thrust_off: float = 0.2
    # 試行開始直後は地面に立っているので、まず両足で真上に浮く (片足で回ると地面に擦る)。
    takeoff_s: float = 0.15


class JetController:
    def __init__(self, physics: Physics | None = None, gains: Gains | None = None):
        self.p = physics or Physics()
        self.g = gains or Gains()
        self._thrusting = False
        self._attempt: int | None = None
        self._t0 = 0.0

    def reset(self) -> None:
        self._thrusting = False
        self._attempt = None

    def decide(self, st: State, target: tuple[float, float]) -> tuple[bool, bool]:
        if st.x is None or st.angle is None:
            return False, False
        p, g = self.p, self.g
        if st.attempt != self._attempt:
            self._attempt, self._t0 = st.attempt, st.t
            self._thrusting = False
        if st.t - self._t0 < g.takeoff_s:
            return True, True
        amax = p.thrust * g.max_accel_ratio
        vx_des = g.k_pos * (target[0] - st.x)
        vy_des = g.k_pos * (target[1] - st.y)
        sp = math.hypot(vx_des, vy_des)
        if sp > g.max_speed:
            vx_des, vy_des = vx_des * g.max_speed / sp, vy_des * g.max_speed / sp
        ax = g.k_vel * (vx_des - st.vx)
        ay = g.k_vel * (vy_des - st.vy)
        mag = math.hypot(ax, ay)
        if mag > amax:
            ax, ay = ax * amax / mag, ay * amax / mag
        # 必要推力 f = a - gravity (画面座標は y 下向き)
        fx, fy = ax, ay - p.gravity
        need = math.hypot(fx, fy) / p.thrust
        if fy >= 0:  # 重力より速く落ちたい → 推力不要、姿勢は直立へ
            desired = 0.0
            need = 0.0
        else:
            # 傾けすぎると縦の推力が重力を支えられない: T cos(tilt) >= 1.1 G
            tilt_cap = min(g.max_tilt, math.acos(min(1.0, 1.1 * p.gravity / p.thrust)))
            desired = max(-tilt_cap, min(tilt_cap, math.atan2(fx, -fy)))
        # rate モード (離すと回転が止まる) では先読みすると逆噴射を往復するので accel のみ先読みする
        lead = g.lead if p.spin_mode == "accel" else 0.0
        err = wrap(desired - (st.angle + st.omega * lead))
        if self._thrusting:
            self._thrusting = need > g.thrust_off
        else:
            self._thrusting = need > g.thrust_on
        tol = g.angle_tol if self._thrusting else g.coast_angle_tol
        if abs(err) > tol:
            cw = err > 0  # 時計回りに回したい
            left = cw == (p.torque_sign > 0)
            return (True, False) if left else (False, True)
        return self._thrusting, self._thrusting
