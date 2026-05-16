"""Authoritative game state and simulation."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.acquisition import AcquisitionEvent, AcquisitionService
from backend.clock import ExerciseClock
from backend.combat import UnitStatsCatalog, compute_combat_totals, parse_equipment_field
from backend.config import (
    DEFAULT_MINUTES_PER_STEP,
    MINUTES_PER_STEP_MAX,
    MINUTES_PER_STEP_MIN,
    MP_PER_MINUTE,
    PLAY_BOUNDS,
    ROOT,
)
from backend.hex_grid import HexGrid
from backend.indirect_fire import IndirectFireService
from backend.movement_cost import MovementCostRaster
from backend.pathfinding import Pathfinder
from backend.unit_loader import load_unit_csv
from backend.zone_control import ZoneOfControlService


def unit_key(side: str, company: str, battalion: str) -> str:
    return f"{side}|{company}|{battalion}"


class GameState:
    def __init__(self) -> None:
        self.grid = HexGrid()
        self.raster = MovementCostRaster(self.grid)
        self.pathfinder = Pathfinder(self.grid, self.raster)
        self.zoc = ZoneOfControlService(self.grid)
        self.catalog = UnitStatsCatalog()
        self.acquisition = AcquisitionService(self.grid, self.zoc)
        self.indirect_fire = IndirectFireService(self.grid, self.catalog)
        self.clock = ExerciseClock()
        self.minutes_per_step = DEFAULT_MINUTES_PER_STEP
        self.units: list[dict[str, Any]] = []
        self._acquisition_queue: list[AcquisitionEvent] = []
        self._acquisition_queue_dedup: set[tuple[str, str, str, str]] = set()

    def initialize(self) -> None:
        self.catalog.load_csv()
        raster_ok = self.raster.load()
        blue_rows = load_unit_csv(ROOT / "blue_units.csv")
        red_rows = load_unit_csv(ROOT / "red_units.csv")
        all_rows = blue_rows + red_rows
        if not raster_ok and all_rows:
            self.grid.origin_lat = sum(r["lat"] for r in all_rows) / len(all_rows)
            self.grid.origin_lon = sum(r["lon"] for r in all_rows) / len(all_rows)
        self.units = []
        for side, rows in (("blue", blue_rows), ("red", red_rows)):
            for raw in rows:
                self.units.append(self._make_unit(raw, side))

    def _make_unit(self, raw: dict, side: str) -> dict[str, Any]:
        specs = parse_equipment_field(raw.get("equipment") or "")
        totals = compute_combat_totals(specs, self.catalog)
        pos_hex = self.grid.lat_lon_to_hex_key(raw["lat"], raw["lon"])
        return {
            "key": unit_key(side, raw["company"], raw["battalion"]),
            "company": raw["company"],
            "battalion": raw["battalion"],
            "lat": raw["lat"],
            "lon": raw["lon"],
            "side": side,
            "vehicle": raw["vehicle"],
            "activity": "halted",
            "destinationKey": None,
            "routeWaypointKeys": [],
            "routePath": None,
            "routeLegIndex": 0,
            "accumMovePoints": 0.0,
            "spotted": False,
            "equipmentSpecs": [{"name": s.name, "count": s.count} for s in specs],
            "totalDirectFire": totals.direct_fire,
            "totalIndirectFire": totals.indirect_fire,
            "totalCloseCombat": totals.close_combat,
            "positionHexKey": pos_hex,
            "positionSinceSimMs": self.clock.sim_ms,
        }

    def _on_unit_hex_changed(self, u: dict[str, Any], new_hex_key: str) -> None:
        if u.get("positionHexKey") != new_hex_key:
            u["positionHexKey"] = new_hex_key
            u["positionSinceSimMs"] = self.clock.sim_ms

    def get_unit(self, key: str) -> dict[str, Any] | None:
        for u in self.units:
            if u["key"] == key:
                return u
        return None

    def sync_leg_index(self, u: dict[str, Any]) -> None:
        if not u.get("routePath"):
            return
        ck = self.grid.lat_lon_to_hex_key(u["lat"], u["lon"])
        path = u["routePath"]
        if ck in path:
            u["routeLegIndex"] = min(path.index(ck), len(path) - 1)
        else:
            u["routeLegIndex"] = 0
            u["accumMovePoints"] = 0.0

    def rebuild_route(self, u: dict[str, Any], reset_progress: bool) -> bool:
        if not u.get("destinationKey"):
            return False
        start = self.grid.lat_lon_to_hex_key(u["lat"], u["lon"])
        path = self.pathfinder.build_compound_route(
            start,
            u.get("routeWaypointKeys") or [],
            u["destinationKey"],
        )
        if not path or len(path) < 2:
            return False
        u["routePath"] = path
        if reset_progress:
            u["routeLegIndex"] = 0
            u["accumMovePoints"] = 0.0
        self.sync_leg_index(u)
        return True

    def clear_route(self, u: dict[str, Any]) -> None:
        u["activity"] = "halted"
        u["destinationKey"] = None
        u["routeWaypointKeys"] = []
        u["routePath"] = None
        u["routeLegIndex"] = 0
        u["accumMovePoints"] = 0.0

    def assign_move_order(self, u: dict[str, Any], goal_key: str) -> str | None:
        if not self.raster.hex_has_raster(goal_key):
            return "Destination outside mv_cost.tif or nodata."
        start = self.grid.lat_lon_to_hex_key(u["lat"], u["lon"])
        if not self.raster.hex_has_raster(start):
            return "Unit not on a hex covered by mv_cost.tif."
        u["routeWaypointKeys"] = []
        u["destinationKey"] = goal_key
        if not self.rebuild_route(u, True):
            self.clear_route(u)
            return "No route."
        u["activity"] = "moving"
        return None

    def extend_route(self, u: dict[str, Any], goal_key: str) -> str | None:
        if not u.get("destinationKey"):
            return self.assign_move_order(u, goal_key)
        if goal_key == u["destinationKey"]:
            return "Already the final destination hex."
        if not self.raster.hex_has_raster(goal_key):
            return "New destination outside mv_cost.tif or nodata."
        prev_dest = u["destinationKey"]
        prev_via = list(u.get("routeWaypointKeys") or [])
        u.setdefault("routeWaypointKeys", []).append(prev_dest)
        u["destinationKey"] = goal_key
        if not self.rebuild_route(u, True):
            u["destinationKey"] = prev_dest
            u["routeWaypointKeys"] = prev_via
            return "No route through vias to new destination."
        u["activity"] = "moving"
        return None

    def magic_move(self, u: dict[str, Any], lat: float, lon: float) -> None:
        self.clear_route(u)
        u["lat"] = lat
        u["lon"] = lon
        self._on_unit_hex_changed(u, self.grid.lat_lon_to_hex_key(lat, lon))

    def remove_waypoint(self, u: dict[str, Any], index_in_intermediate: int) -> str | None:
        vias = u.get("routeWaypointKeys") or []
        if index_in_intermediate < 0 or index_in_intermediate >= len(vias):
            return None
        vias.pop(index_in_intermediate)
        u["routeWaypointKeys"] = vias
        if not vias and not u.get("destinationKey"):
            u["routePath"] = None
            return None
        if not self.rebuild_route(u, True):
            self.clear_route(u)
            return "Route cleared"
        return None

    def apply_waypoint_drag(self, u: dict[str, Any], kind: str, via_index: int, lat: float, lon: float) -> str | None:
        nk = self.grid.lat_lon_to_hex_key(lat, lon)
        if not self.raster.hex_has_raster(nk):
            return None
        prev_dest = u.get("destinationKey")
        prev_via = list(u.get("routeWaypointKeys") or [])
        if kind == "dest":
            if not prev_dest or nk == prev_dest:
                return None
            u["destinationKey"] = nk
        elif kind == "via":
            vias = u.get("routeWaypointKeys") or []
            if via_index < 0 or via_index >= len(vias):
                return None
            u["routeWaypointKeys"][via_index] = nk
        if not self.rebuild_route(u, True):
            u["destinationKey"] = prev_dest
            u["routeWaypointKeys"] = prev_via
            return "Waypoint move breaks the route"
        return None

    def advance_movement_mp(self, mp_gain: float) -> list[AcquisitionEvent]:
        new_events: list[AcquisitionEvent] = []
        for u in self.units:
            if u["activity"] != "moving" or not u.get("routePath"):
                continue
            path = u["routePath"]
            if u["routeLegIndex"] >= len(path) - 1:
                self.clear_route(u)
                continue
            u["accumMovePoints"] = float(u.get("accumMovePoints") or 0) + mp_gain
            guard = 0
            while guard < 48 and u["routeLegIndex"] < len(path) - 1:
                guard += 1
                cur_key = path[u["routeLegIndex"]]
                nx_key = path[u["routeLegIndex"] + 1]
                cost = self.raster.segment_move_cost(cur_key, nx_key)
                if cost is None:
                    break
                if u["accumMovePoints"] + 1e-9 >= cost:
                    u["accumMovePoints"] -= cost
                    u["routeLegIndex"] += 1
                    a = self.grid.parse_hex_key(path[u["routeLegIndex"]])
                    lat, lon = self.grid.axial_center_lat_lon(a.q, a.r)
                    u["lat"] = lat
                    u["lon"] = lon
                    self._on_unit_hex_changed(u, path[u["routeLegIndex"]])
                    evs = self.acquisition.check_hex_entry(
                        self.units, u, cur_key, nx_key, self._acquisition_queue_dedup
                    )
                    new_events.extend(evs)
                    if u["routeLegIndex"] >= len(path) - 1:
                        self.clear_route(u)
                        break
                else:
                    break
        return new_events

    def sim_tick(self, minutes_per_step: int | None = None) -> dict[str, Any]:
        steps = minutes_per_step if minutes_per_step is not None else self.minutes_per_step
        steps = max(MINUTES_PER_STEP_MIN, min(MINUTES_PER_STEP_MAX, int(steps)))
        self.minutes_per_step = steps
        all_events: list[AcquisitionEvent] = []
        for _ in range(steps):
            self.clock.advance_minutes(1)
            minute_events = self.advance_movement_mp(MP_PER_MINUTE)
            all_events.extend(minute_events)
        for ev in all_events:
            self._acquisition_queue.append(ev)
        tb = self.clock.timebar_strings()
        return {
            "units": self.units,
            "simInstantMs": self.clock.sim_ms,
            "timebar": {"main": tb.main, "daynight": tb.daynight, "sunLine": tb.sun_line},
            "isDay": tb.is_day,
            "newAcquisitionEvents": [asdict(e) for e in all_events],
            "minutesPerStep": self.minutes_per_step,
            "mpPerMinute": MP_PER_MINUTE,
        }

    def unit_info(self, key: str) -> dict[str, Any] | None:
        u = self.get_unit(key)
        if not u:
            return None
        equipment: list[dict[str, Any]] = []
        for spec in u.get("equipmentSpecs") or []:
            st = self.catalog.get(spec["name"])
            row: dict[str, Any] = {"name": spec["name"], "count": spec["count"]}
            if st:
                row["stats"] = {
                    "movement": st.movement,
                    "dfRange": st.df_range,
                    "dfScore": st.df_score,
                    "ifRange": st.if_range,
                    "ifScore": st.if_score,
                    "ccScore": st.cc_score,
                }
            equipment.append(row)
        since = int(u.get("positionSinceSimMs") or self.clock.sim_ms)
        duration_ms = max(0, self.clock.sim_ms - since)
        return {
            "unit": u,
            "equipment": equipment,
            "positionHexKey": u.get("positionHexKey"),
            "positionSinceSimMs": since,
            "timeInPositionMs": duration_ms,
            "timeInPosition": ExerciseClock.format_duration(duration_ms),
        }

    def pop_acquisition_event(self) -> AcquisitionEvent | None:
        if not self._acquisition_queue:
            return None
        return self._acquisition_queue.pop(0)

    def acquisition_queue_length(self) -> int:
        return len(self._acquisition_queue)

    def resolve_acquisition(self, event: AcquisitionEvent, confirm: bool) -> dict[str, Any]:
        self.acquisition.mark_silenced(event.target_key, event.entered_hex_key, event.spotter_key)
        for i, queued in enumerate(self._acquisition_queue):
            if (
                queued.spotter_key == event.spotter_key
                and queued.target_key == event.target_key
                and queued.entered_hex_key == event.entered_hex_key
                and queued.spot_kind == event.spot_kind
            ):
                self._acquisition_queue.pop(i)
                break
        result: dict[str, Any] = {"confirmed": confirm}
        if confirm:
            target = self.get_unit(event.target_key)
            if target:
                target["spotted"] = True
            result["spotReport"] = self.build_spot_report(event)
        return result

    def build_spot_report(self, event: AcquisitionEvent) -> dict[str, Any]:
        target = self.get_unit(event.target_key)
        if not target:
            return {}
        from backend.combat import format_score

        activity = "halted"
        if event.spot_kind == "los":
            card = self._travel_cardinal(event.from_hex_key, event.entered_hex_key)
            activity = f"traveling — {card}"
        mgrs = self._mgrs_six_digit(target["lat"], target["lon"])
        return {
            "size": "",
            "activity": activity,
            "location": mgrs,
            "time": self.clock.format_main_line(),
            "equipment": "",
        }

    def _travel_cardinal(self, from_key: str, to_key: str) -> str:
        f = self.grid.parse_hex_key(from_key)
        t = self.grid.parse_hex_key(to_key)
        lat0, lon0 = self.grid.axial_center_lat_lon(f.q, f.r)
        lat1, lon1 = self.grid.axial_center_lat_lon(t.q, t.r)
        import math

        phi1, phi2 = math.radians(lat0), math.radians(lat1)
        dlam = math.radians(lon1 - lon0)
        y = math.sin(dlam) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
        deg = (math.degrees(math.atan2(y, x)) + 360) % 360
        names = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")
        return names[round(deg / 45) % 8]

    def _mgrs_six_digit(self, lat: float, lon: float) -> str:
        try:
            import mgrs as mgrs_mod

            return mgrs_mod.toMgrs(lat, lon)
        except Exception:
            return ""

    def selection_overlay(self, mode: str, key: str) -> dict[str, Any]:
        zoc_keys: set[str] = set()
        if mode == "company":
            u = self.get_unit(key)
            if u:
                zoc_keys = self.zoc.hexes_for_unit(u["lat"], u["lon"], u["activity"])
        elif mode == "battalion":
            parts = key.split("|", 1)
            if len(parts) == 2:
                side, battalion = parts
                for u in self.units:
                    if u["side"] == side and u["battalion"] == battalion:
                        zoc_keys |= self.zoc.hexes_for_unit(u["lat"], u["lon"], u["activity"])
        los_keys = self.zoc.los_buffer_beyond_zoc(zoc_keys)
        zoc_lines = self.zoc.perimeter_polylines(zoc_keys)
        los_lines = self.zoc.perimeter_polylines(los_keys)
        return {
            "zocKeys": sorted(zoc_keys),
            "losKeys": sorted(los_keys),
            "zocLines": zoc_lines,
            "losLines": los_lines,
        }

    def bootstrap(self) -> dict[str, Any]:
        tb = self.clock.timebar_strings()
        meta = self.raster.meta
        return {
            "config": {
                "hexOriginLat": self.grid.origin_lat,
                "hexOriginLon": self.grid.origin_lon,
                "playBounds": PLAY_BOUNDS,
                "flatToFlatM": 1000,
                "minuteMs": 60 * 1000,
                "mpPerMinute": MP_PER_MINUTE,
                "defaultMinutesPerStep": DEFAULT_MINUTES_PER_STEP,
                "minutesPerStepMin": MINUTES_PER_STEP_MIN,
                "minutesPerStepMax": MINUTES_PER_STEP_MAX,
            },
            "mvCost": {
                "loaded": meta.loaded,
                "west": meta.west,
                "south": meta.south,
                "east": meta.east,
                "north": meta.north,
                "failureReason": meta.failure_reason,
            },
            "unitStats": self.catalog.to_api_dict(),
            "units": self.units,
            "simInstantMs": self.clock.sim_ms,
            "timebar": {"main": tb.main, "daynight": tb.daynight, "sunLine": tb.sun_line},
            "minutesPerStep": self.minutes_per_step,
            "mpPerMinute": MP_PER_MINUTE,
        }
