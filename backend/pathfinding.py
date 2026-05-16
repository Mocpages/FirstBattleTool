"""A* routing on hex grid using movement cost raster."""
from __future__ import annotations

import heapq
from typing import TYPE_CHECKING

from backend.config import AXIAL_NEIGHBORS

if TYPE_CHECKING:
    from backend.hex_grid import HexGrid
    from backend.movement_cost import MovementCostRaster


class Pathfinder:
    def __init__(self, grid: "HexGrid", raster: "MovementCostRaster") -> None:
        self.grid = grid
        self.raster = raster

    def heuristic(self, from_key: str, goal_key: str) -> float:
        a = self.grid.parse_hex_key(from_key)
        b = self.grid.parse_hex_key(goal_key)
        d = self.grid.axial_distance(a.q, a.r, b.q, b.r)
        ck = self.raster.rounded_at_hex_key(from_key)
        mn = ck if ck is not None and ck > 0 else 1
        return d * mn

    def find_route(self, start_key: str, goal_key: str) -> list[str] | None:
        if not self.raster.hex_has_raster(start_key) or not self.raster.hex_has_raster(goal_key):
            return None
        if start_key == goal_key:
            return [start_key]

        g_score: dict[str, float] = {start_key: 0.0}
        came: dict[str, str] = {}
        open_heap: list[tuple[float, str]] = [(self.heuristic(start_key, goal_key), start_key)]
        open_set: set[str] = {start_key}

        while open_heap:
            _, cur = heapq.heappop(open_heap)
            if cur not in open_set:
                continue
            open_set.discard(cur)
            if cur == goal_key:
                break
            cq = self.grid.parse_hex_key(cur)
            for di in range(6):
                step = self.raster.exit_move_cost(cur, di)
                if step is None:
                    continue
                dq, dr = AXIAL_NEIGHBORS[di]
                nk = self.grid.hex_key(cq.q + dq, cq.r + dr)
                tentative = g_score[cur] + step
                if nk in g_score and tentative >= g_score[nk]:
                    continue
                g_score[nk] = tentative
                came[nk] = cur
                if nk not in open_set:
                    open_set.add(nk)
                    heapq.heappush(open_heap, (tentative + self.heuristic(nk, goal_key), nk))

        if goal_key not in g_score:
            return None

        seq: list[str] = []
        w = goal_key
        for _ in range(250_000):
            seq.append(w)
            if w == start_key:
                break
            if w not in came:
                return None
            w = came[w]
        else:
            return None
        seq.reverse()
        if seq[0] != start_key or seq[-1] != goal_key:
            return None
        return seq

    def build_compound_route(
        self,
        start_key: str,
        waypoint_keys: list[str],
        destination_key: str,
    ) -> list[str] | None:
        if not destination_key:
            return None
        chain = list(waypoint_keys) + [destination_key]
        merged: list[str] = []
        prev = start_key
        for gk in chain:
            if prev == gk:
                if not merged:
                    merged = [gk]
                prev = gk
                continue
            seg = self.find_route(prev, gk)
            if seg is None:
                return None
            if not merged:
                merged = seg[:]
            else:
                merged.extend(seg[1:])
            prev = gk
        return merged if merged else None
