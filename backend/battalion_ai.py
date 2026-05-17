"""Battalion-level movement planning and order assignment."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.combat import UnitStatsCatalog
from backend.config import R_M
from backend.coordinates import parse_coordinate
from backend.hex_grid import HexGrid
from backend.pathfinding import Pathfinder

KM_PER_HEX = R_M * math.sqrt(3) / 1000.0  # ~1.0 km center-to-center

MANEUVER_TYPES = frozenset({"infantry", "mech", "armor", "command"})
FIRES_TYPES = frozenset({"artillery", "self propelled arty", "mlrs", "missile"})


@dataclass
class MarchColumn:
    recon: list[dict[str, Any]]
    advance_guard: list[dict[str, Any]]
    main_body: list[dict[str, Any]]
    fires: list[dict[str, Any]]
    logistics: list[dict[str, Any]]
    rear_guard: list[dict[str, Any]]


def unit_role(u: dict[str, Any]) -> str:
    t = (u.get("unitType") or "infantry").lower()
    co = (u.get("company") or "").lower()
    if t == "recon" or "scout" in co or "recon" in co:
        return "recon"
    if t in FIRES_TYPES:
        return "fires"
    if t == "logistics":
        return "logistics"
    if t in MANEUVER_TYPES:
        return "maneuver"
    return "maneuver"


def build_march_column(bn_units: list[dict[str, Any]], movement_type: str) -> MarchColumn:
    recon: list[dict[str, Any]] = []
    maneuver: list[dict[str, Any]] = []
    fires: list[dict[str, Any]] = []
    logistics: list[dict[str, Any]] = []
    for u in bn_units:
        role = unit_role(u)
        if role == "recon":
            recon.append(u)
        elif role == "fires":
            fires.append(u)
        elif role == "logistics":
            logistics.append(u)
        else:
            maneuver.append(u)

    advance: list[dict[str, Any]] = []
    main: list[dict[str, Any]] = list(maneuver)
    rear: list[dict[str, Any]] = []

    if movement_type == "emergency":
        return MarchColumn(recon, [], maneuver, fires, logistics, [])

    if movement_type == "tactical" and maneuver:
        mech = [u for u in maneuver if (u.get("unitType") or "").lower() == "mech"]
        armor = [u for u in maneuver if (u.get("unitType") or "").lower() == "armor"]
        pick = mech[0] if mech else (armor[0] if armor else maneuver[0])
        advance = [pick]
        main = [u for u in maneuver if u is not pick]
        if main:
            rear = [main.pop()]
    elif movement_type != "emergency" and len(maneuver) >= 2:
        rear = [maneuver.pop()]

    return MarchColumn(recon, advance, main, fires, logistics, rear)


def march_order_units(column: MarchColumn) -> list[dict[str, Any]]:
    """Front (first) to rear (last) — only the front element routes to the destination."""
    return (
        column.recon
        + column.advance_guard
        + column.main_body
        + column.fires
        + column.logistics
        + column.rear_guard
    )


def hex_km_between(grid: HexGrid, key_a: str, key_b: str) -> float:
    a = grid.parse_hex_key(key_a)
    b = grid.parse_hex_key(key_b)
    lat1, lon1 = grid.axial_center_lat_lon(a.q, a.r)
    lat2, lon2 = grid.axial_center_lat_lon(b.q, b.r)
    return grid.distance_km(lat1, lon1, lat2, lon2)


def path_km_length(grid: HexGrid, path: list[str]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        total += hex_km_between(grid, path[i], path[i + 1])
    return total


def hex_at_path_km_from_end(grid: HexGrid, path: list[str], km_from_end: float) -> str:
    """Walk backward from path end by km_from_end kilometers."""
    if not path:
        return ""
    if km_from_end <= 0 or len(path) == 1:
        return path[-1]
    remaining = km_from_end
    for i in range(len(path) - 2, -1, -1):
        seg = hex_km_between(grid, path[i], path[i + 1])
        if remaining <= seg + 1e-6:
            return path[i + 1] if remaining < seg * 0.5 else path[i]
        remaining -= seg
    return path[0]


def hex_offset_km(
    grid: HexGrid,
    origin_key: str,
    bearing_deg: float,
    distance_km: float,
) -> str:
    a = grid.parse_hex_key(origin_key)
    lat0, lon0 = grid.axial_center_lat_lon(a.q, a.r)
    br = math.radians(bearing_deg)
    scale_lat, scale_lon = grid.meters_per_degree(lat0)
    dy_m = distance_km * 1000.0 * math.cos(br)
    dx_m = distance_km * 1000.0 * math.sin(br)
    lat = lat0 + dy_m / scale_lat
    lon = lon0 + dx_m / scale_lon
    return grid.lat_lon_to_hex_key(lat, lon)


def route_bearing_deg(grid: HexGrid, path: list[str]) -> float:
    if len(path) < 2:
        return 0.0
    a = grid.parse_hex_key(path[-2])
    b = grid.parse_hex_key(path[-1])
    lat0, lon0 = grid.axial_center_lat_lon(a.q, a.r)
    lat1, lon1 = grid.axial_center_lat_lon(b.q, b.r)
    scale_lat, scale_lon = grid.meters_per_degree((lat0 + lat1) / 2)
    dy = (lat1 - lat0) * scale_lat
    dx = (lon1 - lon0) * scale_lon
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def threat_bearing_from_line(grid: HexGrid, line_keys: list[str]) -> float:
    if len(line_keys) < 2:
        return 0.0
    a = grid.parse_hex_key(line_keys[0])
    b = grid.parse_hex_key(line_keys[-1])
    lat0, lon0 = grid.axial_center_lat_lon(a.q, a.r)
    lat1, lon1 = grid.axial_center_lat_lon(b.q, b.r)
    scale_lat, scale_lon = grid.meters_per_degree((lat0 + lat1) / 2)
    dy = (lat1 - lat0) * scale_lat
    dx = (lon1 - lon0) * scale_lon
    along = math.degrees(math.atan2(dx, dy))
    return (along + 90.0) % 360.0


def polyline_length_km(
    grid: HexGrid, line_points: list[tuple[float, float]]
) -> float:
    total = 0.0
    for i in range(len(line_points) - 1):
        lat0, lon0 = line_points[i]
        lat1, lon1 = line_points[i + 1]
        total += grid.distance_km(lat0, lon0, lat1, lon1)
    return total


def interpolate_along_polyline(
    grid: HexGrid,
    line_points: list[tuple[float, float]],
    fraction: float,
) -> tuple[float, float]:
    """Point at fraction 0..1 of total polyline arc length (lat, lon)."""
    if not line_points:
        return (0.0, 0.0)
    if len(line_points) == 1:
        return line_points[0]
    frac = max(0.0, min(1.0, fraction))
    total = polyline_length_km(grid, line_points)
    if total < 1e-9:
        return line_points[0]
    target_km = frac * total
    acc = 0.0
    for i in range(len(line_points) - 1):
        lat0, lon0 = line_points[i]
        lat1, lon1 = line_points[i + 1]
        seg = grid.distance_km(lat0, lon0, lat1, lon1)
        if acc + seg >= target_km - 1e-9:
            t = (target_km - acc) / seg if seg > 1e-6 else 0.0
            return (
                lat0 + (lat1 - lat0) * t,
                lon0 + (lon1 - lon0) * t,
            )
        acc += seg
    return line_points[-1]


def distribute_hex_keys_on_line(
    grid: HexGrid,
    line_points: list[tuple[float, float]],
    count: int,
) -> list[str]:
    """Place count unit positions evenly from start to end of the defense line."""
    if count <= 0:
        return []
    if count == 1:
        lat, lon = interpolate_along_polyline(grid, line_points, 0.5)
        return [grid.lat_lon_to_hex_key(lat, lon)]
    keys: list[str] = []
    for i in range(count):
        frac = i / (count - 1)
        lat, lon = interpolate_along_polyline(grid, line_points, frac)
        keys.append(grid.lat_lon_to_hex_key(lat, lon))
    return keys


def sample_line_hex_keys(
    grid: HexGrid,
    line_points: list[tuple[float, float]],
    spacing_km: float,
) -> list[str]:
    if not line_points:
        return []
    if len(line_points) == 1:
        return [grid.lat_lon_to_hex_key(line_points[0][0], line_points[0][1])]
    samples: list[str] = []
    acc = 0.0
    for i in range(len(line_points) - 1):
        lat0, lon0 = line_points[i]
        lat1, lon1 = line_points[i + 1]
        seg_km = grid.distance_km(lat0, lon0, lat1, lon1)
        if seg_km < 1e-6:
            continue
        steps = max(1, int(math.ceil(seg_km / max(spacing_km, 0.25))))
        for s in range(steps):
            t = s / steps
            lat = lat0 + (lat1 - lat0) * t
            lon = lon0 + (lon1 - lon0) * t
            key = grid.lat_lon_to_hex_key(lat, lon)
            if not samples or samples[-1] != key:
                samples.append(key)
    end_key = grid.lat_lon_to_hex_key(line_points[-1][0], line_points[-1][1])
    if not samples or samples[-1] != end_key:
        samples.append(end_key)
    return samples


def max_artillery_if_range_km(units: list[dict[str, Any]], catalog: UnitStatsCatalog) -> float:
    best = 8.0
    for u in units:
        for spec in u.get("equipmentSpecs") or []:
            st = catalog.get(spec["name"])
            art = catalog.get_artillery(spec["name"])
            if art and art.if_range:
                best = max(best, art.if_range)
            elif st and st.if_range:
                best = max(best, st.if_range)
    return best


class BattalionMovePlanner:
    def __init__(
        self,
        grid: HexGrid,
        pathfinder: Pathfinder,
        catalog: UnitStatsCatalog,
        hex_has_raster: Callable[[str], bool],
    ) -> None:
        self.grid = grid
        self.pathfinder = pathfinder
        self.catalog = catalog
        self.hex_has_raster = hex_has_raster

    def resolve_route(
        self,
        route_keys: list[str],
        route_texts: list[str],
    ) -> tuple[Optional[list[str]], Optional[str], Optional[str]]:
        """Return (via_keys, destination_key, error). Last point is destination."""
        keys: list[str] = []
        for k in route_keys:
            if k and self.hex_has_raster(k):
                keys.append(k)
        for txt in route_texts:
            parsed = parse_coordinate(txt)
            if not parsed:
                return None, None, f"Could not parse waypoint: {txt}"
            hk = self.grid.lat_lon_to_hex_key(parsed[0], parsed[1])
            if not self.hex_has_raster(hk):
                return None, None, f"Waypoint outside movement map: {txt}"
            keys.append(hk)
        if not keys:
            return None, None, "At least one waypoint is required (last is destination)."
        return keys[:-1], keys[-1], None

    def reference_path_for_lead(
        self,
        lead_unit: dict[str, Any],
        waypoint_keys: list[str],
        destination_key: str,
    ) -> Optional[list[str]]:
        start = self.grid.lat_lon_to_hex_key(lead_unit["lat"], lead_unit["lon"])
        return self.pathfinder.build_compound_route(start, waypoint_keys, destination_key)

    def column_slot_index(self, ref_path: list[str], march_index: int) -> int:
        """Path index for march element (0=front at destination end of path)."""
        if not ref_path:
            return 0
        return max(0, len(ref_path) - 1 - march_index)

    def plan_goal_hexes(
        self,
        bn_units: list[dict[str, Any]],
        ref_path: list[str],
        destination_key: str,
        movement_type: str,
        destination_action: str,
        defense_line_keys: Optional[list[str]],
        threat_bearing_deg: Optional[float],
    ) -> tuple[dict[str, str], Optional[str]]:
        if not ref_path or len(ref_path) < 1:
            return {}, "Could not build battalion reference route."

        travel_bearing = route_bearing_deg(self.grid, ref_path)
        column = build_march_column(bn_units, movement_type)
        goals: dict[str, str] = {}

        if destination_action == "defend":
            return self._plan_defend(
                bn_units,
                column,
                defense_line_keys or [],
                threat_bearing_deg,
                goals,
            )

        if destination_action == "assembly":
            return self._plan_assembly(
                bn_units, column, ref_path, destination_key, travel_bearing, goals
            )

        if destination_action == "attack":
            return self._plan_attack(
                bn_units, column, ref_path, destination_key, travel_bearing, goals
            )

        # Default march to destination disposition
        if movement_type == "emergency":
            for u in bn_units:
                goals[u["key"]] = destination_key
            return goals, None

        if movement_type == "tactical":
            self._assign_tactical_goals(column, ref_path, destination_key, goals)
        else:
            self._assign_admin_column_goals(column, ref_path, destination_key, goals)
        return goals, None

    def _assign_admin_column_goals(
        self,
        column: MarchColumn,
        ref_path: list[str],
        destination_key: str,
        goals: dict[str, str],
    ) -> None:
        ordered = (
            column.recon
            + column.advance_guard
            + column.main_body
            + column.fires
            + column.logistics
            + column.rear_guard
        )
        for i, u in enumerate(ordered):
            idx = max(0, len(ref_path) - 1 - i)
            goals[u["key"]] = ref_path[idx]

    def _assign_tactical_goals(
        self,
        column: MarchColumn,
        ref_path: list[str],
        destination_key: str,
        goals: dict[str, str],
    ) -> None:
        scout_hex = hex_at_path_km_from_end(self.grid, ref_path, 10.0)
        advance_hex = hex_at_path_km_from_end(self.grid, ref_path, 8.0)
        for u in column.recon:
            goals[u["key"]] = scout_hex
        for u in column.advance_guard:
            goals[u["key"]] = advance_hex
        main_ordered = column.main_body + column.fires + column.logistics
        base_idx = max(0, len(ref_path) - 1 - int(round(8.0 / max(KM_PER_HEX, 0.5))))
        for i, u in enumerate(main_ordered):
            idx = max(0, base_idx - i)
            goals[u["key"]] = ref_path[idx]
        for i, u in enumerate(column.rear_guard):
            idx = max(0, base_idx - len(main_ordered) - i)
            goals[u["key"]] = ref_path[idx]

    def _plan_assembly(
        self,
        bn_units: list[dict[str, Any]],
        column: MarchColumn,
        ref_path: list[str],
        destination_key: str,
        travel_bearing: float,
        goals: dict[str, str],
    ) -> tuple[dict[str, str], Optional[str]]:
        center = destination_key
        used: set[str] = {center}
        support = column.fires + column.logistics
        for u in support:
            hk = self._nearest_free_hex(center, used)
            goals[u["key"]] = hk
            used.add(hk)
        maneuver = column.recon + column.advance_guard + column.main_body + column.rear_guard
        for u in maneuver:
            hk = self._nearest_free_hex(center, used)
            goals[u["key"]] = hk
            used.add(hk)
        for u in column.recon:
            goals[u["key"]] = hex_offset_km(self.grid, center, travel_bearing, 4.0)
        return goals, None

    def _plan_attack(
        self,
        bn_units: list[dict[str, Any]],
        column: MarchColumn,
        ref_path: list[str],
        destination_key: str,
        travel_bearing: float,
        goals: dict[str, str],
    ) -> tuple[dict[str, str], Optional[str]]:
        maneuver = (
            column.recon
            + column.advance_guard
            + column.main_body
            + column.rear_guard
        )
        used: set[str] = {destination_key}
        for i, u in enumerate(maneuver):
            idx = max(0, len(ref_path) - 1 - i)
            goals[u["key"]] = ref_path[idx]
            used.add(ref_path[idx])
        fires_hex = hex_at_path_km_from_end(self.grid, ref_path, 2.0)
        log_hex = hex_at_path_km_from_end(self.grid, ref_path, 3.0)
        for u in column.fires:
            goals[u["key"]] = fires_hex
        for u in column.logistics:
            goals[u["key"]] = log_hex
        return goals, None

    def _plan_defend(
        self,
        bn_units: list[dict[str, Any]],
        column: MarchColumn,
        defense_line_keys: list[str],
        threat_bearing_deg: Optional[float],
        goals: dict[str, str],
    ) -> tuple[dict[str, str], Optional[str]]:
        if len(defense_line_keys) < 2:
            return {}, "Defensive line requires at least two points."
        threat = threat_bearing_deg
        if threat is None:
            threat = threat_bearing_from_line(self.grid, defense_line_keys)
        maneuver = column.advance_guard + column.main_body + column.rear_guard
        spacing_km = 3.0

        line_pts: list[tuple[float, float]] = []
        for k in defense_line_keys:
            a = self.grid.parse_hex_key(k)
            line_pts.append(self.grid.axial_center_lat_lon(a.q, a.r))

        line_km = polyline_length_km(self.grid, line_pts)
        need = len(maneuver)
        reserve: Optional[dict[str, Any]] = None
        if need > 1 and line_km < spacing_km * (need - 1):
            armor = [u for u in maneuver if (u.get("unitType") or "").lower() == "armor"]
            reserve = armor[0] if armor else maneuver[-1]
            maneuver = [u for u in maneuver if u is not reserve]

        placed_line = distribute_hex_keys_on_line(
            self.grid, line_pts, len(maneuver)
        )
        for i, u in enumerate(maneuver):
            goals[u["key"]] = placed_line[i]

        center_lat, center_lon = interpolate_along_polyline(
            self.grid, line_pts, 0.5
        )
        center_key = self.grid.lat_lon_to_hex_key(center_lat, center_lon)

        rear_bearing = (threat + 180.0) % 360.0
        for u in column.logistics:
            goals[u["key"]] = hex_offset_km(self.grid, center_key, rear_bearing, 4.0)
        arty_range = max_artillery_if_range_km(column.fires, self.catalog)
        arty_dist = arty_range * (2.0 / 3.0)
        fires_hex = hex_offset_km(self.grid, center_key, rear_bearing, arty_dist)
        for u in column.fires:
            goals[u["key"]] = fires_hex
        if reserve:
            goals[reserve["key"]] = hex_offset_km(self.grid, center_key, rear_bearing, 4.0)
        for u in column.recon:
            goals[u["key"]] = hex_offset_km(self.grid, center_key, threat, 5.0)
        return goals, None

    def _nearest_free_hex(self, center: str, used: set[str]) -> str:
        if center not in used:
            return center
        c = self.grid.parse_hex_key(center)
        for dist in range(1, 8):
            for dq in range(-dist, dist + 1):
                for dr in range(-dist, dist + 1):
                    if abs(dq) + abs(dr) + abs(dq + dr) > dist * 2:
                        continue
                    nk = self.grid.hex_key(c.q + dq, c.r + dr)
                    if nk not in used and self.hex_has_raster(nk):
                        return nk
        return center
