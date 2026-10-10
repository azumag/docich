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
    horizon: int = 14  # 要素数 (先読み = step_s * horizon 秒)
    substeps: int = 2  # 1 要素あたりの積分分割
    candidates: int = 80  # 1 ティックで試す入力列の数
    delay_s: float = 0.08  # 入力が効くまでの遅れ
    persistence: float = 0.65  # ランダム列で直前と同じ入力を続ける確率
    mutate_p: float = 0.2  # 前回の最良列を崩す確率
    # コスト
    # "track": 経路上を速度 v_ref (曲がり角・家の手前で減速) で進む目標点を追う (既定)。
    # "progress": 経路の残り距離を減らす (角を斜めに突っ切りがちで、Lv8 の棘の柱に当たり続けた)。
    mode: str = "track"
    v_ref: float = 32.0
    w_track: float = 1.5  # 目標点との距離 (px)
    w_vel: float = 0.8  # 目標速度との差 (px/s)
    end_accel: float = 18.0  # 家の手前の減速
    foot: int = 6  # 中心から足元まで (地面との接触判定)
    ground_friction: float = 2.5  # 地面での横速度の減衰 (1/s)
    w_dist: float = 1.0  # 経路の残り距離 (px) を毎ステップ足す
    w_lat: float = 4.0  # 経路からのずれ (px)。高く登りすぎない・壁際を避ける
    w_final: float = 4.0  # 最終ステップの残り距離の追加重み
    brake_accel: float = 45.0  # 速度上限 sqrt(2*a*残距離) の a (px/s^2)
    v_floor: float = 12.0  # 残距離がほぼ 0 でも許す速度
    terrain_pen: float = 12.0  # 地形に重なる 1 ステップごと (地面は死なないので軽め)
    fatal_pen: float = 400.0  # 棘・画面外 (以降は死亡として打ち切り)
    max_speed: float = 90.0  # これを超える速度に罰則
    max_tilt: float = 0.8  # rad。これ以上傾くと立て直せない
    bound_margin: float = 4.0  # 画面端からこの距離を切ると死亡扱い
    hazard_margin: float = 9.0  # 体の半分の長さ (横倒しで約 7px) + 余裕。5 では Lv8 で x≈137–145 で棘に当たり続けた
    hazard_soft: float = 14.0  # 棘の(生の)矩形からこの距離以内に近づくと、近さに比例して罰則 (遠回りより近道を選んで突っ込むのを防ぐ)
    hazard_soft_w: float = 8.0
    corner_turn: float = 0.7  # rad。経路のこれ以上の曲がり角の手前では、角までの距離に応じた速度上限をかける
    corner_accel: float = 30.0
    corner_floor: float = 10.0
    home_slow_r: float = 50.0  # 家からこの距離以内では速度 <= home_v0 + home_v_per_px * 距離 を強く守る (通り過ぎ・滑り落ち対策)
    home_v0: float = 10.0
    home_v_per_px: float = 0.8
    w_home_v: float = 4.0
    w_omega: float = 3.0  # 毎ステップ w_omega * |角速度| (回して戻す列は実機では遅れで破綻しやすい)
    w_tilt: float = 5.0  # 毎ステップ w_tilt * 傾き^2 (まっすぐ上がる/降りるほうを少し優先)
    w_vaway: float = 0.8  # 経路から離れる向きの横速度 (px/s) への罰則 (経路を飛び出して家を通り過ぎるのを抑える)
    # 家の扉の上で低速になったら入力を止めて地面で静止する (実機: 扉付近に約 2 秒とどまるとクリア)
    settle_half_w: float = 9.0
    settle_above: float = 16.0  # 家の基準点 (扉の足元) からこの高さまでが静止ゾーン
    settle_speed: float = 45.0
    takeoff_s: float = 0.35  # 試行開始からこの秒数は両足で真上に上がる (開始直後は角速度が推定できず、片足で回りすぎる)
    seed: int = 1


class MpcController:
    """``JetController`` と同じ ``decide(state, target) -> (左, 右)`` を持つ。"""

    def __init__(self, physics: Physics | None = None, cfg: MpcConfig | None = None, *, width: int = 320, height: int = 180):
        self.p = physics or Physics()
        self.cfg = cfg or MpcConfig()
        self.width, self.height = width, height
        self.rng = random.Random(self.cfg.seed)
        self.grid: Grid | None = None
        self.solid: bytearray | None = None
        self.hazards: list[tuple[int, int, int, int]] = []
        self.home_bbox: tuple[int, int, int, int] | None = None
        self.path: list[tuple[float, float]] = []
        self._cum: list[float] = []
        self._corners: list[float] = []  # 曲がり角の経路上の位置 (始点からの弧長)
        self._samp: list[tuple[float, float, float, float, float]] = []  # 2px ごと (x, y, 接線x, 接線y, 速度上限)
        self._ref: list[tuple[float, float, float, float]] = []  # 各ステップの目標 (x, y, vx, vy)
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
            self._corners = self._find_corners()
            self._samp = self._sample_path()
        self.hazards = list(obs.hazards)
        self.home_bbox = obs.home_bbox
        self.width, self.height = obs.width, obs.height
        self.solid = obs.solid  # 画素単位の地形 (草/土)

    def _find_corners(self, m: int = 3) -> list[float]:
        pts, cum = self.path, self._cum
        out: list[float] = []
        for k in range(m, len(pts) - m):
            ax, ay = pts[k][0] - pts[k - m][0], pts[k][1] - pts[k - m][1]
            bx, by = pts[k + m][0] - pts[k][0], pts[k + m][1] - pts[k][1]
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            if la < 1e-6 or lb < 1e-6:
                continue
            turn = math.acos(max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb))))
            if turn >= self.cfg.corner_turn and (not out or cum[k] - out[-1] > 12.0):
                out.append(cum[k])
        return out

    def _sample_path(self, ds: float = 2.0) -> list[tuple[float, float, float, float, float]]:
        pts, cum, c = self.path, self._cum, self.cfg
        if len(pts) < 2:
            return []
        plen = cum[-1]
        out = []
        j = 0
        sv = 0.0
        while True:
            while j < len(pts) - 2 and cum[j + 1] < sv:
                j += 1
            seg = cum[j + 1] - cum[j]
            u = 0.0 if seg <= 1e-9 else (sv - cum[j]) / seg
            ax, ay = pts[j]
            bx, by = pts[j + 1]
            tx, ty = (bx - ax, by - ay)
            tl = math.hypot(tx, ty) or 1.0
            v = min(c.v_ref, math.sqrt(2.0 * c.end_accel * max(0.0, plen - sv)))
            for cs in self._corners:
                if cs >= sv:
                    v = min(v, math.sqrt(2.0 * c.corner_accel * (cs - sv)) + c.corner_floor)
                    break
            out.append((ax + u * (bx - ax), ay + u * (by - ay), tx / tl, ty / tl, v))
            if sv >= plen:
                break
            sv = min(plen, sv + ds)
        return out

    def _project(self, x: float, y: float) -> int:
        """現在位置に最も近い経路サンプルの番号。"""
        best, bi = 1e18, 0
        for i, (px, py, _, _, _) in enumerate(self._samp):
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best:
                best, bi = d, i
        return bi

    def _make_ref(self, x: float, y: float) -> None:
        c = self.cfg
        samp = self._samp
        self._ref = []
        if not samp:
            return
        sidx = float(self._project(x, y))
        last = len(samp) - 1
        dt = c.step_s / 2
        for _ in range(c.horizon):
            for _ in range(2):
                k = min(last, int(sidx))
                sidx = min(float(last), sidx + samp[k][4] * dt / 2.0)  # サンプル間隔 2px
            k = min(last, int(sidx))
            px, py, tx, ty, v = samp[k]
            self._ref.append((px, py, tx * v, ty * v))

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
        hraw = self.hazards
        hb = self.home_bbox
        W, H, bm = self.width, self.height, c.bound_margin
        total = 0.0
        path, cum = self.path, self._cum
        plen = cum[-1] if cum else 0.0
        segs = [(path[i][0], path[i][1], path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1], cum[i]) for i in range(len(path) - 1)]
        vb = 2.0 * c.brake_accel
        soft, soft_w = c.hazard_soft, c.hazard_soft_w
        corners = self._corners
        ref = self._ref
        home_pt = self.path[-1] if self.path else None
        dt_sub = c.step_s / c.substeps
        n_steps = len(seq)
        track = self.cfg.mode == "track" and len(ref) >= n_steps
        sin, cos, hypot = math.sin, math.cos, math.hypot
        solid = self.solid
        foot = c.foot

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
            if solid is not None:
                # 地面: 足元 (中心から foot px 下) が地形なら落ちずに止まり、横は摩擦で減速する (実機は地面を滑って止まる)
                xi, yi = int(x), int(y + foot)
                if 0 <= xi < W and 0 <= yi < H and solid[yi * W + xi]:
                    if vy > 0:
                        vy = 0.0
                        y = float(yi - foot)
                    vx -= vx * min(1.0, c.ground_friction * dt)

        # 入力が効くまでの遅れ: 直前に出した入力がまだ効いている間
        if first_dt > 0:
            advance(first_dt, first_action)

        for i in range(n_steps):
            a = seq[i]
            for _ in range(c.substeps):
                advance(dt_sub, a)
            if track:
                rx, ry, rvx, rvy = ref[i]
                d = hypot(x - rx, y - ry)
                total += c.w_track * d + c.w_vel * hypot(vx - rvx, vy - rvy)
            elif segs:
                best_lat, rem = 1e9, 0.0
                away = 0.0
                s_cur = 0.0
                for ax, ay, dx, dy, c0 in segs:
                    ll = dx * dx + dy * dy
                    u = 0.0 if ll <= 1e-9 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
                    lat = hypot(x - ax - u * dx, y - ay - u * dy)
                    if lat < best_lat:
                        best_lat, rem = lat, plen - (c0 + u * math.sqrt(ll))
                        s_cur = c0 + u * math.sqrt(ll)
                        if ll > 1e-9 and lat > 1.5:
                            inv = 1.0 / math.sqrt(ll)
                            side = ((x - ax) * dy - (y - ay) * dx) * inv  # 経路の左右どちら側か (符号付きずれ)
                            vperp = (vx * dy - vy * dx) * inv
                            away = vperp if side > 0 else -vperp
                        else:
                            away = 0.0
                d = rem if rem > 0.0 else 0.0
                total += c.w_dist * d + c.w_lat * best_lat
                if away > 0.0:
                    total += c.w_vaway * away
            else:
                d = hypot(x - tx, y - ty)
                total += c.w_dist * d
            if x < bm or x > W - bm or y < bm or y > H - bm:
                return total + c.fatal_pen * (n_steps - i)
            dmin = 1e9
            for hx0, hy0, hx1, hy1 in hz:
                if hx0 <= x <= hx1 and hy0 <= y <= hy1:
                    return total + c.fatal_pen * (n_steps - i)
            for hx0, hy0, hx1, hy1 in hraw:
                ddx = hx0 - x if x < hx0 else (x - hx1 if x > hx1 else 0.0)
                ddy = hy0 - y if y < hy0 else (y - hy1 if y > hy1 else 0.0)
                dd = hypot(ddx, ddy)
                if dd < dmin:
                    dmin = dd
            if dmin < soft:
                total += soft_w * (soft - dmin)  # 一番近い棘の生の矩形からの距離だけで (足し合わせない)
            if grid is not None:
                cc, rr = int(x // cell), int(y // cell)
                if 0 <= cc < cols and 0 <= rr < rows and blocked[rr * cols + cc]:
                    total += c.terrain_pen
            sp = hypot(vx, vy)
            if home_pt is not None:
                dh = hypot(x - home_pt[0], y - home_pt[1])
                if dh < c.home_slow_r:
                    lim = c.home_v0 + c.home_v_per_px * dh
                    if sp > lim:
                        total += c.w_home_v * (sp - lim)
            vcap = c.max_speed if track else min(c.max_speed, math.sqrt(vb * d) + c.v_floor)
            if segs and not track:
                for cs in corners:
                    if cs > s_cur:
                        vcap = min(vcap, math.sqrt(2.0 * c.corner_accel * (cs - s_cur)) + c.corner_floor)
                        break
            if sp > vcap:
                total += 3.0 * (sp - vcap)
            total += c.w_tilt * ang * ang + c.w_omega * abs(om)
            if abs(ang) > c.max_tilt:
                total += 60.0 * (abs(ang) - c.max_tilt)
            if hb is not None and hb[0] <= x <= hb[2] and hb[1] <= y <= hb[3]:
                total -= 30.0 * (n_steps - i)  # 家に入れる列は強く優先
                break
        total += c.w_final * (d if (segs or track) else hypot(x - tx, y - ty)) + 0.8 * abs(om)
        return total

    def _policy_seq(self, x, y, vx, vy, ang, om, theta: float) -> list[int]:
        """目標の傾き theta を PD で保ちながら両足で噴射する入力列を、モデルを進めて作る (閉ループ候補)。"""
        p, c = self.p, self.cfg
        dt = c.step_s / c.substeps
        seq: list[int] = []
        for _ in range(c.horizon):
            err = theta - ang - 0.45 * om
            a = 3 if abs(err) < 0.06 else (1 if (err > 0) == (p.torque_sign > 0) else 2)
            seq.append(a)
            left, right = a & 1, (a >> 1) & 1
            n = left + right
            thr = p.thrust if n == 2 else p.thrust * p.single_thrust_ratio
            for _ in range(c.substeps):
                om += (left - right) * p.torque_sign * p.spin * dt
                om -= om * min(1.0, p.angular_damping * dt)
                ang += om * dt
                vx += math.sin(ang) * thr * dt
                vy += (-math.cos(ang) * thr + p.gravity) * dt
                k = min(1.0, p.drag * dt)
                vx -= vx * k
                vy -= vy * k
                x += vx * dt
                y += vy * dt
        return seq

    def _track_policy_seq(self, x, y, vx, vy, ang, om, k_tilt: float, max_tilt: float) -> list[int]:
        """目標点の速度に合わせる閉ループ方策を、モデルを進めて入力列にする。
        上下は両足噴射の入/切 (PWM) で、左右は傾き (目標 = k_tilt * 横速度の不足) を片足の短い噴射で作る。"""
        p, c = self.p, self.cfg
        dt = c.step_s / c.substeps
        seq: list[int] = []
        for i in range(c.horizon):
            rx, ry, rvx, rvy = self._ref[i] if i < len(self._ref) else self._ref[-1]
            # 位置のずれも少し速度目標に足す
            want_vx = rvx + 0.8 * (rx - x)
            want_vy = rvy + 0.8 * (ry - y)
            theta = max(-max_tilt, min(max_tilt, k_tilt * (want_vx - vx)))
            err = theta - ang - 0.45 * om
            if abs(err) > 0.08:
                a = 1 if (err > 0) == (p.torque_sign > 0) else 2
            else:
                a = 3 if vy > want_vy else 0
            seq.append(a)
            left, right = a & 1, (a >> 1) & 1
            n = left + right
            thr = p.thrust if n == 2 else (p.thrust * p.single_thrust_ratio if n == 1 else 0.0)
            for _ in range(c.substeps):
                om += (left - right) * p.torque_sign * p.spin * dt
                om -= om * min(1.0, p.angular_damping * dt)
                ang += om * dt
                vx += math.sin(ang) * thr * dt
                vy += (-math.cos(ang) * thr + p.gravity) * dt
                k = min(1.0, p.drag * dt)
                vx -= vx * k
                vy -= vy * k
                x += vx * dt
                y += vy * dt
        return seq

    # --- 計画 -----------------------------------------------------------------------------------
    def decide(self, st: State, target: tuple[float, float]) -> tuple[bool, bool]:
        if st.x is None or st.angle is None:
            return False, False
        if st.attempt != self._attempt:
            self.reset()
            self._attempt = st.attempt
            self._t0 = st.t
        c = self.cfg
        x, y, vx, vy, ang, om = self._estimate(st)
        if st.t - getattr(self, "_t0", st.t) < c.takeoff_s and abs(ang) < 0.3:
            self._plan, self._last_action = [3] * c.horizon, 3
            return True, True
        if st.home is not None:
            hx, hy = st.home
            slow = math.hypot(vx, vy) < c.settle_speed
            in_door = abs(x - hx) <= c.settle_half_w and hy - c.settle_above <= y <= hy + 14
            # 実機: 家の前 (家の幅のどこでも) で地面にいれば、レベル番号の白い面が満ちていきクリアになる。
            # 横倒しで噴射すると地面を滑って家から出てしまうので、家の幅の中では何もしない。
            in_front = False
            if st.home_bbox is not None:
                x0, y0, x1, y1 = st.home_bbox
                in_front = x0 + 2 <= x <= x1 - 2 and y1 - 20 <= y <= y1 + 14
            if slow and (in_door or in_front):
                self._plan, self._last_action = None, 0
                return False, False
        tx, ty = target
        if c.mode == "track":
            self._make_ref(x, y)
        H = c.horizon
        cands: list[list[int]] = []
        if self._plan is not None:
            base = self._plan[1:] + [self._plan[-1]]
            cands.append(base)
            for _ in range(c.candidates // 3):
                cands.append([a if self.rng.random() > c.mutate_p else self.rng.randrange(4) for a in base])
        for a in range(4):
            cands.append([a] * H)
        for theta in (-0.5, -0.25, 0.0, 0.25, 0.5):
            cands.append(self._policy_seq(x, y, vx, vy, ang, om, theta))
        if self._ref:
            for k_tilt, mt in ((0.008, 0.2), (0.015, 0.3), (0.025, 0.4)):
                cands.append(self._track_policy_seq(x, y, vx, vy, ang, om, k_tilt, mt))
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
