"""実機ログ (probe/record/play の frames.jsonl) から物理パラメータを推定する。

入力 (左右ジェット) が一定の区間ごとに位置と角度を二次式で当てはめ、

- 両足 OFF で空中: 下向き加速度 → gravity
- 両足 ON: (加速度 - 重力) の頭方向成分 → thrust
- 片足 ON: 角速度 (rate) または角加速度 (accel) → spin, torque_sign、推進 → single_thrust_ratio

を中央値で集計する。入力から反映まで 1 フレーム程度遅れるので、区間の先頭は捨てる (実機の遅延は要確認)。
"""

from __future__ import annotations

import math
from statistics import median

from .control import Physics
from .tracker import wrap


def _solve3(a: list[list[float]], b: list[float]) -> list[float] | None:
    m = [row[:] + [v] for row, v in zip(a, b)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(3):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, 4):
                    m[r][c] -= f * m[col][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def quadfit(ts: list[float], vs: list[float]) -> tuple[float, float, float] | None:
    """v(t) = c0 + c1 t + 0.5 c2 t^2 の (c0, c1, c2)。"""
    t0 = ts[0]
    s = [[0.0] * 3 for _ in range(3)]
    rhs = [0.0] * 3
    for t, v in zip(ts, vs):
        t -= t0
        basis = (1.0, t, 0.5 * t * t)
        for i in range(3):
            rhs[i] += basis[i] * v
            for j in range(3):
                s[i][j] += basis[i] * basis[j]
    sol = _solve3(s, rhs)
    return None if sol is None else (sol[0], sol[1], sol[2])


def _slope(ts: list[float], vs: list[float]) -> float:
    n = len(ts)
    mt, mv = sum(ts) / n, sum(vs) / n
    den = sum((t - mt) ** 2 for t in ts)
    return 0.0 if den == 0 else sum((t - mt) * (v - mv) for t, v in zip(ts, vs)) / den


def segments(records: list[dict], *, skip: int = 1, min_len: int = 5) -> list[dict]:
    """入力が一定でプレイヤーが見えている連続区間。"""
    out = []
    cur: list[dict] = []
    key = None
    for r in records:
        if r.get("phase") != "playing" or r.get("x") is None or r.get("angle") is None:
            if len(cur) >= skip + min_len:
                out.append({"keys": key, "rows": cur[skip:]})
            cur, key = [], None
            continue
        k = (bool(r.get("left")), bool(r.get("right")))
        if k != key:
            if len(cur) >= skip + min_len:
                out.append({"keys": key, "rows": cur[skip:]})
            cur, key = [], k
        cur.append(r)
    if len(cur) >= skip + min_len:
        out.append({"keys": key, "rows": cur[skip:]})
    return out


def fit(records: list[dict], base: Physics | None = None) -> tuple[Physics, dict]:
    base = base or Physics()
    grav, thrust, single_thr = [], [], []
    spin_l, spin_r, alpha_l, alpha_r = [], [], [], []
    accel_ratio = []  # 片足区間の後半/前半の角速度比 (rate なら ~1、accel なら >1)
    moving_idle = 0
    for seg in segments(records):
        rows = seg["rows"]
        ts = [r["t"] for r in rows]
        fx = quadfit(ts, [r["x"] for r in rows])
        fy = quadfit(ts, [r["y"] for r in rows])
        if fx is None or fy is None:
            continue
        # 角度は区間内で連続になるよう展開してから当てはめる
        ang = [rows[0]["angle"]]
        for r in rows[1:]:
            ang.append(ang[-1] + wrap(r["angle"] - ang[-1]))
        fa = quadfit(ts, ang)
        ax, ay = fx[2], fy[2]
        span = ts[-1] - ts[0]
        moved = math.hypot(rows[-1]["x"] - rows[0]["x"], rows[-1]["y"] - rows[0]["y"])
        mean_angle = math.atan2(sum(math.sin(a) for a in ang), sum(math.cos(a) for a in ang))
        hx, hy = math.sin(mean_angle), -math.cos(mean_angle)
        keys = seg["keys"]
        if keys == (False, False):
            if moved > 2.0:  # 地面に立ったままの区間は除外
                grav.append(ay)
                moving_idle += 1
        elif keys == (True, True):
            thrust.append((ax * hx + ay * hy, mean_angle))
        elif fa is not None and span > 0:
            omega, alpha = fa[1] + fa[2] * span / 2, fa[2]
            (spin_l if keys[0] else spin_r).append(omega)
            (alpha_l if keys[0] else alpha_r).append(alpha)
            half = len(ts) // 2
            if half >= 3:
                w1 = _slope(ts[:half], ang[:half])
                w2 = _slope(ts[half:], ang[half:])
                if abs(w1) > 0.3:
                    accel_ratio.append(w2 / w1)
            single_thr.append((ax * hx + ay * hy, mean_angle))
    report: dict = {"segments": len(segments(records))}
    p = Physics(**{**base.__dict__})
    if grav:
        p.gravity = median(grav)
        report["gravity_samples"] = len(grav)
    if thrust:
        # a = T*h + (0, G) なので a·h = T + G*h_y = T - G*cos(angle)
        p.thrust = median(a + p.gravity * math.cos(m) for a, m in thrust)
        report["thrust_samples"] = len(thrust)
    if spin_l or spin_r:
        wl = median(spin_l) if spin_l else -median(spin_r)
        wr = median(spin_r) if spin_r else -wl
        p.torque_sign = 1 if wl - wr > 0 else -1
        p.spin = (abs(wl) + abs(wr)) / 2
        al = median(alpha_l) if alpha_l else 0.0
        ar = median(alpha_r) if alpha_r else 0.0
        # 角速度がほぼ一定 (角加速度が小さい) なら rate、そうでなければ accel
        # 角加速度の当てはめは画素量子化に弱いので、前半/後半の角速度比で判定する
        if accel_ratio and median(accel_ratio) > 1.6:
            p.spin_mode = "accel"
            p.spin = median(abs(a) for a in alpha_l + alpha_r)
        else:
            p.spin_mode = "rate"
        report["accel_ratio"] = median(accel_ratio) if accel_ratio else None
        report.update({"omega_left": wl, "omega_right": wr, "alpha_left": al, "alpha_right": ar})
    if single_thr and p.thrust > 0:
        p.single_thrust_ratio = max(0.0, min(1.0, median((a + p.gravity * math.cos(m)) / p.thrust for a, m in single_thr)))
    report["idle_segments"] = moving_idle
    return p, report
