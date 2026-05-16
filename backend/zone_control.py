"""Zone of control and line-of-sight hex sets."""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config import AXIAL_NEIGHBORS, FLAT_TO_FLAT_M, LOS_BUFFER_PAST_ZOC_M, R_M

if TYPE_CHECKING:
    from backend.hex_grid import HexGrid


class ZoneOfControlService:
    def __init__(self, grid: "HexGrid") -> None:
        self.grid = grid

    def hexes_for_unit(self, lat: float, lon: float, activity: str) -> set[str]:
        a = self.grid.lat_lon_to_axial(lat, lon)
        keys = {self.grid.hex_key(a.q, a.r)}
        if activity == "moving":
            return keys
        for dq, dr in AXIAL_NEIGHBORS:
            keys.add(self.grid.hex_key(a.q + dq, a.r + dr))
        return keys

    def merge_keys(self, *sets: set[str]) -> set[str]:
        out: set[str] = set()
        for s in sets:
            out |= s
        return out

    def los_buffer_beyond_zoc(self, zoc_keys: set[str]) -> set[str]:
        if not zoc_keys:
            return set()
        zocos: list[tuple[int, int, float, float]] = []
        for key in zoc_keys:
            a = self.grid.parse_hex_key(key)
            lat, lon = self.grid.axial_center_lat_lon(a.q, a.r)
            zocos.append((a.q, a.r, lat, lon))
        qmin = min(z[0] for z in zocos)
        qmax = max(z[0] for z in zocos)
        rmin = min(z[1] for z in zocos)
        rmax = max(z[1] for z in zocos)
        step_margin = int((LOS_BUFFER_PAST_ZOC_M + R_M + FLAT_TO_FLAT_M / 2) / (R_M * 0.82)) + 3
        cutoff = LOS_BUFFER_PAST_ZOC_M + R_M
        expanded: set[str] = set()
        for tq in range(qmin - step_margin, qmax + step_margin + 1):
            for tr in range(rmin - step_margin, rmax + step_margin + 1):
                hub_lat, hub_lon = self.grid.axial_center_lat_lon(tq, tr)
                dnearest = min(
                    self.grid.distance_meters(hub_lat, hub_lon, zlat, zlon) for _, _, zlat, zlon in zocos
                )
                if dnearest <= cutoff + 1e-6:
                    expanded.add(self.grid.hex_key(tq, tr))
        return expanded

    def los_area_for_unit(self, lat: float, lon: float, activity: str) -> set[str]:
        return self.los_buffer_beyond_zoc(self.hexes_for_unit(lat, lon, activity))

    def perimeter_polylines(self, hex_keys: set[str]) -> list[list[list[float]]]:
        """Return closed-edge perimeter segments as [[lat,lon],[lat,lon], ...]."""
        if not hex_keys:
            return []
        edge_count: dict[str, int] = {}
        edge_seg: dict[str, list[list[float]]] = {}

        def edge_key(p0: list[float], p1: list[float]) -> str:
            def rnd(c: float) -> float:
                return round(c * 1e5) / 1e5

            a = f"{rnd(p0[0])},{rnd(p0[1])}"
            b = f"{rnd(p1[0])},{rnd(p1[1])}"
            return f"{a}|{b}" if a < b else f"{b}|{a}"

        for key in hex_keys:
            a = self.grid.parse_hex_key(key)
            ring = self.grid.hex_vertices_lat_lon(a.q, a.r)
            for ei in range(6):
                p0, p1 = ring[ei], ring[ei + 1]
                ek = edge_key(p0, p1)
                edge_count[ek] = edge_count.get(ek, 0) + 1
                edge_seg[ek] = [p0, p1]

        lines: list[list[list[float]]] = []
        for ek, count in edge_count.items():
            if count == 1:
                lines.append(edge_seg[ek])
        return lines
