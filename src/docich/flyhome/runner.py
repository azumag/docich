"""実行ループ: 取得 → 認識 → 追跡 → (計画 → 制御 | 台本 | 人間 | 再生) → 入力 → ログ。

バックエンドは Windows 実機 (``WindowsBackend``) とシミュレータ (``SimBackend``) の 2 つ。
ループ本体は共通なので、Linux の CI でシミュレータを相手に一気通貫で検証できる。

安全策 (実機):
- ゲームが前面でない間は全キーを離し、入力を送らない (他アプリへの誤入力防止)。
- F12 (``Settings.abort_vk``) で即停止。終了時は例外でも必ず全キーを離す。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import planner, vision
from .control import JetController, Physics
from .image import Image, write_png
from .settings import Settings
from .timeline import Timeline
from .tracker import CLEARED, DEAD, PLAYING, State, Tracker


class Backend:
    def now(self) -> float:
        raise NotImplementedError

    def sleep_until(self, t: float) -> None:
        raise NotImplementedError

    def capture(self) -> Image:
        raise NotImplementedError

    def ready(self) -> bool:
        """入力を送ってよい状態か (実機ではゲームが前面か)。"""
        return True

    def aborted(self) -> bool:
        return False

    def set_jets(self, left: bool, right: bool) -> None:
        raise NotImplementedError

    def tap(self, key: str) -> None:
        raise NotImplementedError

    def human_jets(self) -> tuple[bool, bool]:
        return False, False

    def release_all(self) -> None:
        pass

    def close(self) -> None:
        self.release_all()


class WindowsBackend(Backend):  # pragma: no cover - Windows 実機のみ
    def __init__(self, settings: Settings, *, focus: bool = True):
        from . import win32

        self.w = win32
        self.s = settings
        win32.set_dpi_aware()
        win32.precise_timer(True)
        info = win32.find_window(settings.window_titles, settings.exe_names)
        if info is None:
            raise RuntimeError(f"game window not found (titles={settings.window_titles}); `windows` サブコマンドで一覧を確認")
        self.info = info
        self.hwnd = info.hwnd
        k = settings.keys
        self.scan = {"left": k.left, "right": k.right, "up": k.up, "down": k.down, "enter": k.enter, "escape": k.escape, "space": k.space}
        ext = {self.scan[n] for n in k.extended if n in self.scan}
        self.kb = win32.Keyboard(self.hwnd, ext, settings.input_mode)
        self.cap = win32.Capturer(self.hwnd, settings.native_width, settings.native_height, aspect=settings.aspect, mode=settings.capture_mode)
        if focus:
            win32.bring_to_front(self.hwnd)

    def now(self) -> float:
        return time.perf_counter()

    def sleep_until(self, t: float) -> None:
        d = t - time.perf_counter()
        if d > 0:
            time.sleep(d)

    def capture(self) -> Image:
        return self.cap.grab()

    def ready(self) -> bool:
        if self.s.input_mode == "postmessage":
            return True
        return self.w.foreground_hwnd() == self.hwnd

    def aborted(self) -> bool:
        return self.w.key_down(self.s.abort_vk)

    def set_jets(self, left: bool, right: bool) -> None:
        self.kb.set(self.scan["left"], left)
        self.kb.set(self.scan["right"], right)

    def tap(self, key: str) -> None:
        self.kb.tap(self.scan[key])

    def human_jets(self) -> tuple[bool, bool]:
        return self.w.key_down(self.w.VK_LEFT), self.w.key_down(self.w.VK_RIGHT)

    def release_all(self) -> None:
        self.kb.release_all()

    def close(self) -> None:
        try:
            self.release_all()
        finally:
            self.cap.close()
            self.w.precise_timer(False)


class SimBackend(Backend):
    """シミュレータ相手のバックエンド。時間は仮想 (sleep_until で物理を進める)。"""

    def __init__(self, world=None, physics: Physics | None = None, *, substeps_hz: float = 240.0):
        from . import sim

        self.sim = sim
        self.world = world or sim.World.basic()
        self.physics = physics or Physics()
        self.dt = 1.0 / substeps_hz
        self.t = 0.0
        self.body = sim.Body(*self.world.start)
        self.keys = (False, False)
        self.taps: list[str] = []

    def now(self) -> float:
        return self.t

    def sleep_until(self, t: float) -> None:
        while self.t + 1e-9 < t:
            self.sim.step(self.world, self.body, *self.keys, self.dt, self.physics)
            self.t += self.dt

    def capture(self) -> Image:
        return self.sim.render(self.world, self.body)

    def set_jets(self, left: bool, right: bool) -> None:
        self.keys = (left, right)
        self.body.left, self.body.right = left, right

    def tap(self, key: str) -> None:
        self.taps.append(key)
        if key == "enter" and self.body.outcome is not None:
            self.body = self.sim.Body(*self.world.start)

    def release_all(self) -> None:
        self.set_jets(False, False)


@dataclass
class SessionLog:
    root: Path | None
    snapshot_every_s: float = 5.0
    _frames: object = None
    _attempts: object = None
    _last_snap: float = -1e9
    results: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            self._frames = (self.root / "frames.jsonl").open("a", encoding="utf-8")
            self._attempts = (self.root / "attempts.jsonl").open("a", encoding="utf-8")

    def frame(self, st: State, obs: vision.Observation, keys: tuple[bool, bool], extra: dict | None = None) -> None:
        if self._frames is None:
            return
        row = st.as_dict()
        row.update({"left": keys[0], "right": keys[1], "hud": [obs.hud_left, obs.hud_right]})
        if extra:
            row.update(extra)
        self._frames.write(json.dumps(row, ensure_ascii=False) + "\n")

    def snapshot(self, t: float, native: Image, obs: vision.Observation, name: str | None = None, force: bool = False) -> None:
        if self.root is None or (not force and t - self._last_snap < self.snapshot_every_s):
            return
        self._last_snap = t
        write_png(self.root / "snaps" / (name or f"t{t:08.2f}.png"), vision.annotate(native, obs))

    def attempt(self, row: dict) -> None:
        self.results.append(row)
        if self._attempts is not None:
            self._attempts.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._attempts.flush()

    def close(self) -> None:
        for f in (self._frames, self._attempts):
            if f is not None:
                f.close()


def session_dir(settings: Settings, kind: str) -> Path:
    return settings.run_dir / "sessions" / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}"


class Loop:
    """1 ティック分の 取得→認識→追跡 を共通化したもの。"""

    def __init__(self, backend: Backend, settings: Settings, log: SessionLog):
        self.b = backend
        self.s = settings
        self.log = log
        self.tracker = Tracker()
        self.period = 1.0 / settings.tick_hz
        self.next_t = backend.now()
        self.native: Image | None = None
        self.obs: vision.Observation | None = None

    def tick(self) -> State:
        self.b.sleep_until(self.next_t)
        self.next_t = max(self.next_t + self.period, self.b.now())
        self.native = self.b.capture()
        self.obs = vision.analyze(self.native, self.s, prev_player=self.tracker.prev_pos, prev_angle=self.tracker.prev_angle)
        return self.tracker.update(self.obs, self.b.now())

    def wait(self, seconds: float) -> None:
        self.b.sleep_until(self.b.now() + seconds)
        self.next_t = self.b.now()


def _finish_attempt(loop: Loop, st: State, t_start: float | None, timeline: Timeline | None) -> dict:
    row = {
        "attempt": st.attempt,
        "outcome": st.phase,
        "duration_s": None if t_start is None else round(st.t - t_start, 3),
        "best_dist": None if st.best_dist is None else round(st.best_dist, 1),
        "level_sig": loop.obs.level_sig if loop.obs else None,
    }
    if timeline is not None:
        row["events"] = len(timeline.events)
    loop.log.attempt(row)
    if loop.native is not None and loop.obs is not None:
        loop.log.snapshot(st.t, loop.native, loop.obs, name=f"attempt{st.attempt:04d}-{st.phase}.png", force=True)
    return row


def play(
    backend: Backend,
    settings: Settings,
    *,
    physics: Physics | None = None,
    attempts: int = 1,
    max_seconds: float = 120.0,
    attempt_timeout_s: float = 30.0,
    retry: bool = True,
    log_root: Path | None = None,
    replan_every: int = 6,
) -> list[dict]:
    """制御器でプレイする。死亡したら Enter で再挑戦し、帰宅したら止まる。"""
    log = SessionLog(log_root)
    loop = Loop(backend, settings, log)
    ctl = JetController(physics or Physics.load(settings.physics_file))
    done = 0
    path = None
    k = 0
    t_begin = backend.now()
    t_start = None
    tl: Timeline | None = None
    try:
        while backend.now() - t_begin < max_seconds:
            if backend.aborted():
                break
            st = loop.tick()
            keys = (False, False)
            if st.phase == PLAYING and loop.obs is not None:
                if t_start is None:
                    t_start, tl, path = st.t, Timeline(meta={"source": "controller"}), None
                    ctl.reset()
                if path is None or k % replan_every == 0:
                    target = st.home
                    if target is not None:
                        grid = planner.build_grid(loop.tracker.last_obs or loop.obs)
                        path = planner.astar(grid, (st.x, st.y), target) or [(st.x, st.y), target]
                if path is not None:
                    keys = ctl.decide(st, planner.lookahead(path, (st.x, st.y)))
                if st.t - t_start > attempt_timeout_s:
                    keys = (False, False)  # 打ち切り: 落下させて死亡→再挑戦に回す
                tl.add(int((st.t - t_start) * 1000), *keys)
                k += 1
            if backend.ready():
                backend.set_jets(*keys)
            else:
                backend.release_all()
                keys = (False, False)
            log.frame(st, loop.obs, keys)
            if loop.native is not None:
                log.snapshot(st.t, loop.native, loop.obs)
            if st.phase in (DEAD, CLEARED) and t_start is not None:
                backend.release_all()
                row = _finish_attempt(loop, st, t_start, tl)
                if st.phase == CLEARED and tl is not None and log.root is not None:
                    tl.meta.update({"outcome": "cleared", "duration_s": row["duration_s"], "level_sig": row["level_sig"]})
                    tl.save(log.root / f"timeline-attempt{st.attempt:04d}.json")
                t_start, tl = None, None
                done += 1
                if st.phase == CLEARED or not retry or (attempts and done >= attempts):
                    break
                loop.wait(settings.retry_delay_s)
                if backend.ready():
                    backend.tap("enter")
    finally:
        backend.release_all()
        log.close()
    return log.results


# 物理推定用の既定パルス台本: (左, 右, 秒)。最初は真上に上がり、空中で回転と自由落下を測る。
DEFAULT_PROBE = [
    (True, True, 0.35),
    (False, False, 0.30),
    (True, False, 0.25),
    (False, False, 0.25),
    (False, True, 0.25),
    (True, True, 0.30),
    (False, False, 0.30),
    (False, True, 0.25),
    (False, False, 0.20),
    (True, False, 0.25),
    (True, True, 0.25),
    (False, False, 0.6),
]


def probe(
    backend: Backend,
    settings: Settings,
    *,
    script=DEFAULT_PROBE,
    log_root: Path | None = None,
    wait_player_s: float = 10.0,
) -> list[dict]:
    """台本どおりにジェットを操作し、その間の状態列を返す (calibrate.fit の入力)。"""
    log = SessionLog(log_root, snapshot_every_s=0.25)
    loop = Loop(backend, settings, log)
    rows: list[dict] = []
    try:
        t0 = backend.now()
        while backend.now() - t0 < wait_player_s:
            st = loop.tick()
            if st.phase == PLAYING:
                break
        else:
            raise RuntimeError("player not visible; レベルを開始した状態で実行する")
        plan = []
        t = backend.now()
        for left, right, dur in script:
            plan.append((t, left, right))
            t += dur
        t_end = t
        while backend.now() < t_end + 0.5:
            if backend.aborted():
                break
            st = loop.tick()
            keys = (False, False)
            for ts, left, right in plan:
                if st.t >= ts:
                    keys = (left, right)
            if st.t >= t_end or st.phase != PLAYING:
                keys = (False, False)
            if backend.ready():
                backend.set_jets(*keys)
            row = st.as_dict()
            row.update({"left": keys[0], "right": keys[1], "hud": [loop.obs.hud_left, loop.obs.hud_right]})
            if loop.obs.player is not None:
                row["flame"] = [loop.obs.player.flame_left, loop.obs.player.flame_right]
            rows.append(row)
            log.frame(st, loop.obs, keys)
            log.snapshot(st.t, loop.native, loop.obs)
            if st.phase in (DEAD, CLEARED):
                break
    finally:
        backend.release_all()
        log.close()
    return rows


def record(backend: Backend, settings: Settings, *, max_seconds: float = 600.0, log_root: Path | None = None) -> list[Timeline]:
    """人間のプレイを観測し、試行ごとの入力タイムラインを保存する (入力は送らない)。"""
    log = SessionLog(log_root)
    loop = Loop(backend, settings, log)
    out: list[Timeline] = []
    tl: Timeline | None = None
    t_start = None
    try:
        t0 = backend.now()
        while backend.now() - t0 < max_seconds and not backend.aborted():
            st = loop.tick()
            keys = backend.human_jets()
            if st.phase == PLAYING:
                if tl is None:
                    tl, t_start = Timeline(meta={"source": "human"}), None
                if t_start is None and (keys[0] or keys[1]):
                    t_start = st.t
                if t_start is not None:
                    tl.add(int((st.t - t_start) * 1000), *keys)
            log.frame(st, loop.obs, keys)
            if st.phase in (DEAD, CLEARED) and tl is not None:
                row = _finish_attempt(loop, st, t_start, tl)
                tl.meta.update({"outcome": st.phase, "duration_s": row["duration_s"], "level_sig": row["level_sig"]})
                if log.root is not None:
                    tl.save(log.root / f"timeline-attempt{st.attempt:04d}-{st.phase}.json")
                out.append(tl)
                tl = None
    finally:
        log.close()
    return out


def replay(backend: Backend, settings: Settings, tl: Timeline, *, log_root: Path | None = None, wait_player_s: float = 10.0) -> dict:
    """タイムラインを開ループで再生する。最初のイベント時刻 = 試行開始。"""
    log = SessionLog(log_root)
    loop = Loop(backend, settings, log)
    try:
        t0 = backend.now()
        st = loop.tick()
        while st.phase != PLAYING:
            if backend.now() - t0 > wait_player_s:
                raise RuntimeError("player not visible; レベルを開始した状態で実行する")
            st = loop.tick()
        t_start = backend.now()
        limit = tl.duration_ms / 1000 + 5.0
        while not backend.aborted():
            el = (backend.now() - t_start) * 1000
            keys = tl.state_at(el)
            if backend.ready():
                backend.set_jets(*keys)
            st = loop.tick()
            log.frame(st, loop.obs, keys)
            if st.phase in (DEAD, CLEARED) or el / 1000 > limit:
                break
        backend.release_all()
        return _finish_attempt(loop, st, t_start, tl)
    finally:
        backend.release_all()
        log.close()


def watch(backend: Backend, settings: Settings, *, seconds: float = 10.0, every_s: float = 0.5, printer=print) -> None:
    """入力を送らずに認識結果を表示する (色しきい値・HUD 位置の確認用)。"""
    loop = Loop(backend, settings, SessionLog(None))
    t0 = backend.now()
    last = -1e9
    while backend.now() - t0 < seconds and not backend.aborted():
        st = loop.tick()
        if st.t - last >= every_s:
            last = st.t
            printer(json.dumps({"state": st.as_dict(), "obs": loop.obs.summary()}, ensure_ascii=False))

