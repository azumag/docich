"""実機の観測/入力を待たせず、静的な地形の経路を探索する1スレッド。"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from . import planner
from .vision import Observation

@dataclass
class RouteResult:
    path: list[tuple[float, float]]
    grid: planner.Grid
    spec: tuple[int, int, int]
    observation: Observation
    detail: dict | None
    duration_s: float
    attempt: int

class RouteWorker:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flyhome-route")
        self._pending = None
        self._cache = {}
        self._cache_attempt = None

    @property
    def busy(self):
        return self._pending is not None

    def request(self, attempt, world_obs, planning_obs, pos, target, path, grid_spec):
        if self._pending is not None:
            return False
        self._pending = self._executor.submit(self._solve, attempt, world_obs, planning_obs, pos, target,
                                               list(path) if path else None, grid_spec)
        return True

    def poll(self, attempt):
        if self._pending is None or not self._pending.done():
            return None
        future, self._pending = self._pending, None
        result = future.result()
        # 死亡/やり直し後に、前の試行の位置から作った経路を採用しない。
        return result if result.attempt == attempt else None

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
        if self._pending is not None and not self._pending.cancelled():
            self._pending.result()
        self._pending = None

    def _solve(self, attempt, world_obs, planning_obs, pos, target, path, grid_spec):
        started = time.perf_counter()
        if self._cache_attempt != attempt:
            self._cache.clear()
            self._cache_attempt = attempt
        new_path, kept_path, detail = None, False, None
        world_key = (hash(bytes(planning_obs.solid)), tuple(sorted(world_obs.hazards)), world_obs.home_bbox)
        def get_grid(cell, sm, hm):
            cache_key = (world_key, cell, sm, hm)
            if cache_key not in self._cache:
                if len(self._cache) > 128:
                    self._cache.clear()
                self._cache[cache_key] = planner.build_grid(planning_obs, cell=cell, solid_margin=sm, hazard_margin=hm, soft_margin=2 if cell == 4 else 4, top_extra=2 if cell == 4 else 5)
            return self._cache[cache_key]
        if path and grid_spec and planner.path_distance(path, (pos[0], pos[1])) <= 12.0:
            cell, sm, hm = grid_spec
            grid = get_grid(cell, sm, hm)
            if planner.forward_path_clear(grid, path, (pos[0], pos[1])):
                new_path, kept_path = path, True
        if new_path is None:
            detail = {"cached_spec": grid_spec, "distance": planner.path_distance(path, (pos[0], pos[1])) if path else None,
                "hazards": world_obs.hazards, "solid_pixels": sum(world_obs.solid)}
            if path and grid_spec:
                grid = get_grid(*grid_spec)
                rest_check = planner.remaining_path(path, (pos[0], pos[1]))[1:]
                detail["blocked_vertices"] = [pt for pt in rest_check[:-1] if not grid.free(*grid.to_cell(*pt))][:5]
            specs = ((4, 2, 3), (4, 2, 2), (4, 2, 1), (2, 3, 5), (2, 3, 4), (4, 1, 3), (4, 1, 2), (4, 1, 1))
            if grid_spec is not None:
                specs = (grid_spec,) + tuple(spec for spec in specs if spec != grid_spec)
            for cell, sm, hm in specs:
                grid = get_grid(cell, sm, hm)
                new_path = planner.astar(grid, (pos[0], pos[1]), target)
                if new_path:
                    grid_spec = (cell, sm, hm)
                    break
        if not new_path:
            new_path = path if path else [(pos[0], pos[1]), target]
        # ヒステリシス: 島の上回り/下回りのように長さの近い経路が毎回入れ替わると迷走する。
        # 前の経路がまだ通れて、新しい経路が 20% 以上短くなければ前の経路を使い続ける。
        if not kept_path and path is not None and len(path) >= 2:
            rest = planner.remaining_path(path, (pos[0], pos[1]))
            if (len(rest) >= 2 and planner.forward_path_clear(grid, path, (pos[0], pos[1]))
                    and planner.path_length(new_path) > 0.8 * planner.path_length(rest)):
                new_path = rest
        return RouteResult(new_path, grid, (cell, sm, hm), world_obs, detail,
                           time.perf_counter() - started, attempt)
