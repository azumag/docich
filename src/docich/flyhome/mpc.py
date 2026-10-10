"""物理モデルで先の動きを何通りも試して、一番よい入力を選ぶ制御器 (モデル予測制御・ランダムシューティング)。

実機の物理 (2026-10 に製品版で計測) は ``control.JetController`` の仮説と違った:

- 片足だけ噴射してもほぼ満額の推力が出る (``single_thrust_ratio`` ≈ 0.9)。回すだけの片足噴射は無い。
- 回転は「押している間だけ角加速度」で、離しても角速度がほとんど減衰しない (``spin_mode="accel"``)。
- キー入力から動きに出るまで約 0.08 秒の遅れがある。

こうなると「傾けたい角度を決めて PD で追う」方式は噴射のたびに上へ飛んでしまうので、
``Physics`` のモデルで 1 秒ほど先までをシミュレートし、経路の目標点へ近づき、
地形・棘・画面外を避ける入力列を選ぶ。最初の 1 手だけ使い、毎ティック計画し直す。
標準ライブラリだけで動く (1 ティックあたり数十 ms)。
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

from .control import Physics
from .planner import Grid
from .tracker import State, wrap
from .vision import Observation

# (左, 右) の 4 通り。インデックス = 左*1 + 右*2
ACTIONS: tuple[tuple[bool, bool], ...] = ((False, False), (True, False), (False, True), (True, True))


@dataclass
class MpcConfig:
    step_s: float = 0.1  # 入力列 1 要素の長さ
    horizon: int = 10  # 要素数 (先読み = step_s * horizon 秒)
    substeps: int = 2  # 1 要素あたりの積分分割
    candidates: int = 70  # 1 ティックで試す入力列の数
    delay_s: float = 0.08  # 入力が効くまでの遅れ
    persistence: float = 0.65  # ランダム列で直前と同じ入力を続ける確率
    mutate_p: float = 0.2  # 前回の最良列を崩す確率
    # コスト
    w_dist: float = 1.0  # 経路の残り距離 (px) を毎ステップ足す
    w_lat: float = 1.5  # 経路からのずれ (px)。高く登りすぎない・壁際を避ける
    w_final: float = 4.0  # 最終ステップの残り距離の追加重み
    brake_accel: float = 45.0  # 速度上限 sqrt(2*a*残距離) の a (px/s^2)
    v_floor: float = 12.0  # 残距離がほぼ 0 でも許す速度
    terrain_pen: float = 12.0  # 地形に重なる 1 ステップごと (地面は死なないので軽め)
    fatal_pen: float = 400.0  # 棘・画面外 (以降は死亡として打ち切り)
    max_speed: float = 90.0  # これを超える速度に罰則
    max_tilt: float = 1.2  # rad。これ以上傾くと立て直せない
    bound_margin: float = 4.0  # 画面端からこの距離を切ると死亡扱い
    hazard_margin: float = 5.0
    # 家の扉の上で低速になったら入力を止めて地面で静止する (実機: 扉付近に約 2 秒とどまるとクリア)
    settle_half_w: float = 9.0
    settle_above: float = 16.0  # 家の基準点 (扉の足元) からこの高さまでが静止ゾーン
    settle_speed: float = 45.0
    seed: int = 1


class MpcController:
    """``JetController`` と同じ ``decide(state, target) -> (左, 右)`` を持つ。"""

    def __init__(self, physics: Physics | None = None, cfg: MpcConfig | None = None, *, width: int = 320, height: int = 180):
        self.p = physics or Physics()
        self.cfg = cfg or MpcConfig()
        self.width, self.height = width, height
        self.rng = random.Random(self.cfg.seed)
        self.grid: Grid | None = None
        self.hazards: list[tuple[int, int, int, int]] = []
        self.home_bbox: tuple[int, int, int, int] | None = None
        self.path: list[tuple[float, float]] = []
        self._cum: list[float] = []
        self.reset()

    # --- 外部から ---------------------------------------------------------------------------
    def reset(self) -> None:
        self._plan: list[int] | None = None
        self._last_action = 0
        self._hist: deque[tuple[float, float, float, float]] = deque(maxlen=6)  # (t, x, y, angle 連続値)
        self._attempt: int | None = None
        self._unwrapped = 0.0
        self._last_angle: float | None = None

    def set_world(self, grid: Grid, obs: Observation, path: list[tuple[float, float]] | None = None) -> None:
        self.grid = grid
        if path:
            self.path = list(path)
            self._cum = [0.0]
            for a, b in zip(self.path, self.path[1:]):
                self._cum.append(self._cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.hazards = list(obs.hazards)
        self.home_bbox = obs.home_bbox
        self.width, self.height = obs.width, obs.height

    # --- 状態推定 -----------------------------------------------------------------------------
    def _estimate(self, st: State) -> tuple[float, float, float, float, float, float]:
        """位置は 1 px 量子化なので、数フレーム前との差で速度・角速度を出す (tracker の速度は雑音が大きい)。"""
        if self._last_angle is None:
            self._unwrapped = st.angle
        else:
            self._unwrapped += wrap(st.angle - self._last_angle)
        self._last_angle = st.angle
        self._hist.append((st.t, st.x, st.y, self._unwrapped))
        t0, x0, y0, a0 = self._hist[0]
        dt = st.t - t0
        if len(self._hist) < 3 or dt < 0.05:
            return st.x, st.y, 0.0, 0.0, st.angle, 0.0
        return st.x, st.y, (st.x - x0) / dt, (st.y - y0) / dt, st.angle, (self._unwrapped - a0) / dt

    # --- シミュレーション -----------------------------------------------------------------
    def _cost(self, x, y, vx, vy, ang, om, seq, tx, ty, first_dt, first_action) -> float:
        p, c = self.p, self.cfg
        g, T, ratio, spin, damp, drag, sign = p.gravity, p.thrust, p.single_thrust_ratio, p.spin, p.angular_damping, p.drag, p.torque_sign
        grid = self.grid
        cell = grid.cell if grid else 4
        cols = grid.cols if grid else 0
        blocked = grid.blocked if grid else b""
        rows = grid.rows if grid else 0
        hz = [(x0 - c.hazard_margin, y0 - c.hazard_margin, x1 + c.hazard_margin, y1 + c.hazard_margin) for x0, y0, x1, y1 in self.hazards]
        hb = self.home_bbox
        W, H, bm = self.width, self.height, c.bound_margin
        total = 0.0
        path, cum = self.path, self._cum
        plen = cum[-1] if cum else 0.0
        segs = [(path[i][0], path[i][1], path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1], cum[i]) for i in range(len(path) - 1)]
        vb = 2.0 * c.brake_accel
        dt_sub = c.step_s / c.substeps
        n_steps = len(seq)
        sin, cos, hypot = math.sin, math.cos, math.hypot

        def advance(dt: float, a: int) -> None:
            nonlocal x, y, vx, vy, ang, om
            left, right = a & 1, (a >> 1) & 1
            n = left + right
            thr = T if n == 2 else (T * ratio if n == 1 else 0.0)
            om += (left - right) * sign * spin * dt
            om -= om * min(1.0, damp * dt)
            ang += om * dt
            vx += sin(ang) * thr * dt
            vy += (-cos(ang) * thr + g) * dt
            k = min(1.0, drag * dt)
            vx -= vx * k
            vy -= vy * k
            x += vx * dt
            y += vy * dt

        # 入力が効くまでの遅れ: 直前に出した入力がまだ効いている間
        if first_dt > 0:
            advance(first_dt, first_action)

        for i in range(n_steps):
            a = seq[i]
            for _ in range(c.substeps):
                advance(dt_sub, a)
            if segs:
                best_lat, rem = 1e9, 0.0
                for ax, ay, dx, dy, c0 in segs:
                    ll = dx * dx + dy * dy
                    u = 0.0 if ll <= 1e-9 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
                    lat = hypot(x - ax - u * dx, y - ay - u * dy)
                    if lat < best_lat:
                        best_lat, rem = lat, plen - (c0 + u * math.sqrt(ll))
                d = rem if rem > 0.0 else 0.0
                total += c.w_dist * d + c.w_lat * best_lat
            else:
                d = hypot(x - tx, y - ty)
                total += c.w_dist * d
            if x < bm or x > W - bm or y < bm or y > H - bm:
                return total + c.fatal_pen * (n_steps - i)
            for hx0, hy0, hx1, hy1 in hz:
                if hx0 <= x <= hx1 and hy0 <= y <= hy1:
                    return total + c.fatal_pen * (n_steps - i)
            if grid is not None:
                cc, rr = int(x // cell), int(y // cell)
                if 0 <= cc < cols and 0 <= rr < rows and blocked[rr * cols + cc]:
                    total += c.terrain_pen
            sp = hypot(vx, vy)
            vcap = min(c.max_speed, math.sqrt(vb * d) + c.v_floor)
            if sp > vcap:
                total += 3.0 * (sp - vcap)
            if abs(ang) > c.max_tilt:
                total += 25.0 * (abs(ang) - c.max_tilt)
            if hb is not None and hb[0] <= x <= hb[2] and hb[1] <= y <= hb[3]:
                total -= 30.0 * (n_steps - i)  # 家に入れる列は強く優先
                break
        total += c.w_final * (d if segs else hypot(x - tx, y - ty)) + 0.8 * abs(om)
        return total

    # --- 計画 -----------------------------------------------------------------------------------
    def decide(self, st: State, target: tuple[float, float]) -> tuple[bool, bool]:
        if st.x is None or st.angle is None:
            return False, False
        if st.attempt != self._attempt:
            self.reset()
            self._attempt = st.attempt
        c = self.cfg
        x, y, vx, vy, ang, om = self._estimate(st)
        if st.home is not None:
            hx, hy = st.home
            if abs(x - hx) <= c.settle_half_w and hy - c.settle_above <= y <= hy + 14 and math.hypot(vx, vy) < c.settle_speed:
                self._plan, self._last_action = None, 0
                return False, False
        tx, ty = target
        H = c.horizon
        cands: list[list[int]] = []
        if self._plan is not None:
            base = self._plan[1:] + [self._plan[-1]]
            cands.append(base)
            for _ in range(c.candidates // 3):
                cands.append([a if self.rng.random() > c.mutate_p else self.rng.randrange(4) for a in base])
        for a in range(4):
            cands.append([a] * H)
        while len(cands) < c.candidates:
            seq = [self.rng.randrange(4)]
            for _ in range(H - 1):
                seq.append(seq[-1] if self.rng.random() < c.persistence else self.rng.randrange(4))
            cands.append(seq)
        best_seq, best_cost = cands[0], math.inf
        for seq in cands:
            cost = self._cost(x, y, vx, vy, ang, om, seq, tx, ty, c.delay_s, self._last_action)
            if cost < best_cost:
                best_seq, best_cost = seq, cost
        self._plan = best_seq
        self._last_action = best_seq[0]
        left, right = ACTIONS[best_seq[0]]
        return left, right
