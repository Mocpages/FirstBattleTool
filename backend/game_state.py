"""Authoritative game state and simulation."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from backend.acquisition import AcquisitionEvent, AcquisitionService
from backend.battalion_ai import BattalionMovePlanner
from backend.coordinates import parse_coordinate
from backend.battalion_ai import build_march_column, march_order_units
from backend.formation import (
    clear_formation,
    column_target_hex,
    effective_mp_for_formation,
    formation_lead,
    sync_formation_column,
    unit_station_status,
)
from backend.artillery_fire import tick_exhaustion_recovery, time_in_position_ms
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
from backend.if_mission import IFMissionManager
from backend.indirect_fire import IndirectFireService
from backend.movement_cost import MovementCostRaster
from backend.reports import ReportService
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
        self.reports = ReportService()
        self.if_mission = IFMissionManager(self.catalog, self.reports)
        self.clock = ExerciseClock()
        self.minutes_per_step = DEFAULT_MINUTES_PER_STEP
        self.units: list[dict[str, Any]] = []
        self._acquisition_queue: list[AcquisitionEvent] = []
        self._acquisition_queue_dedup: set[tuple[str, str, str, str]] = set()
        self._report_queue: list[dict[str, Any]] = []

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
            "unitType": raw.get("unitType", "infantry"),
            "unitSize": raw.get("unitSize", "company"),
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
            "ifExhausted": False,
            "ifCeaseFireSimMs": None,
        }

    def _set_unit_halted(self, u: dict[str, Any], hex_key: str | None = None) -> None:
        u["activity"] = "halted"
        if hex_key is None:
            hex_key = self.grid.lat_lon_to_hex_key(u["lat"], u["lon"])
        u["positionHexKey"] = hex_key
        u["positionSinceSimMs"] = self.clock.sim_ms

    def _on_unit_hex_changed(self, u: dict[str, Any], new_hex_key: str) -> None:
        u["positionHexKey"] = new_hex_key

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
        u["destinationKey"] = None
        u["routeWaypointKeys"] = []
        u["routePath"] = None
        u["routeLegIndex"] = 0
        u["accumMovePoints"] = 0.0
        clear_formation(u)
        self._set_unit_halted(u)

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

    def assign_lead_move_order(
        self,
        u: dict[str, Any],
        waypoint_keys: list[str],
        destination_key: str,
    ) -> str | None:
        if not self.raster.hex_has_raster(destination_key):
            return "Destination outside mv_cost.tif or nodata."
        start = self.grid.lat_lon_to_hex_key(u["lat"], u["lon"])
        if not self.raster.hex_has_raster(start):
            return "Unit not on a hex covered by mv_cost.tif."
        u["routeWaypointKeys"] = list(waypoint_keys)
        u["destinationKey"] = destination_key
        if not self.rebuild_route(u, True):
            self.clear_route(u)
            return "No route."
        u["activity"] = "moving"
        return None

    def _assign_column_move(self, u: dict[str, Any], column_hex: str) -> None:
        """Pathfind only to a column position — never through to the battalion destination."""
        path = u.get("routePath") or []
        leg = int(u.get("routeLegIndex") or 0)
        if (
            u.get("destinationKey") == column_hex
            and u.get("activity") == "moving"
            and path
            and leg < len(path) - 1
        ):
            return
        self.assign_move_order(u, column_hex)

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

    def battalion_units(self, battalion_key: str) -> list[dict[str, Any]]:
        parts = battalion_key.split("|", 1)
        if len(parts) != 2:
            return []
        side, battalion = parts[0], parts[1]
        return [
            u
            for u in self.units
            if u["side"] == side and u["battalion"] == battalion
        ]

    def execute_battalion_move_order(
        self,
        battalion_key: str,
        route_keys: list[str],
        route_texts: list[str],
        movement_type: str,
        destination_action: str,
        defense_line_keys: list[str],
        defense_line_texts: list[str],
        threat_bearing_deg: Optional[float],
    ) -> dict[str, Any]:
        bn_units = self.battalion_units(battalion_key)
        if not bn_units:
            return {"ok": False, "message": "Battalion not found.", "units": []}

        planner = BattalionMovePlanner(
            self.grid,
            self.pathfinder,
            self.catalog,
            self.raster.hex_has_raster,
        )
        def_line = list(defense_line_keys)
        for txt in defense_line_texts:
            parsed = parse_coordinate(txt)
            if not parsed:
                return {
                    "ok": False,
                    "message": f"Could not parse defensive point: {txt}",
                    "units": [],
                }
            def_line.append(self.grid.lat_lon_to_hex_key(parsed[0], parsed[1]))

        vias, dest, route_err = planner.resolve_route(route_keys, route_texts)
        if route_err and not (destination_action == "defend" and len(def_line) >= 2):
            return {"ok": False, "message": route_err, "units": []}
        if vias is None or dest is None:
            if destination_action == "defend" and len(def_line) >= 2:
                vias = []
                dest = def_line[len(def_line) // 2]
            else:
                return {
                    "ok": False,
                    "message": route_err or "Invalid route.",
                    "units": [],
                }

        column = build_march_column(bn_units, movement_type)
        ordered = march_order_units(column)
        if not ordered:
            return {"ok": False, "message": "No units in battalion.", "units": []}

        lead = ordered[0]
        ref_path = planner.reference_path_for_lead(lead, vias, dest) or []

        goals, err = planner.plan_goal_hexes(
            bn_units,
            ref_path,
            dest,
            movement_type,
            destination_action,
            def_line if destination_action == "defend" else None,
            threat_bearing_deg,
        )
        if err:
            return {"ok": False, "message": err, "units": []}

        failures: list[str] = []
        updated: list[dict[str, Any]] = []
        use_column = movement_type != "emergency" and ref_path and destination_action not in (
            "defend",
        )

        for march_i, u in enumerate(ordered):
            goal = goals.get(u["key"])
            if not goal:
                failures.append(f"{u['company']}: no assigned position")
                continue

            is_lead = march_i == 0 and use_column
            # March-order slots on ref_path (front→rear). Do not use goal hex index —
            # tactical disposition goals can sit ahead of the lead and invert the column.
            path_slot = (
                planner.column_slot_index(ref_path, march_i)
                if use_column
                else 0
            )

            if use_column:
                u["bnFormation"] = {
                    "battalionKey": battalion_key,
                    "movementType": movement_type,
                    "refPath": ref_path,
                    "pathSlot": path_slot,
                    "isLead": is_lead,
                    "maxLeadHex": 1,
                    "maxLagHex": 1,
                }
            else:
                clear_formation(u)

            if movement_type == "emergency":
                move_err = self.assign_move_order(u, goal)
            elif use_column:
                if is_lead:
                    move_err = self.assign_lead_move_order(u, vias, dest)
                else:
                    lead_slot = int(
                        (ordered[0].get("bnFormation") or {}).get(
                            "pathSlot", len(ref_path) - 1
                        )
                    )
                    if destination_action == "defend":
                        col_hex = goal
                    else:
                        col_hex = column_target_hex(
                            ref_path, 0, lead_slot, path_slot
                        )
                    self._assign_column_move(u, col_hex)
                    move_err = None
                    if u.get("activity") != "moving" and not u.get("routePath"):
                        move_err = "No route to column position"
            else:
                move_err = self.assign_move_order(u, goal)
            if move_err:
                failures.append(f"{u['company']}: {move_err}")
                clear_formation(u)
            updated.append(u)

        msg = f"Battalion move issued to {len(updated)} companies."
        if failures:
            msg += " Issues: " + "; ".join(failures[:4])
            if len(failures) > 4:
                msg += f" (+{len(failures) - 4} more)"
        return {
            "ok": len(failures) < len(bn_units),
            "message": msg,
            "units": self.units,
            "assigned": {k: v for k, v in goals.items()},
        }

    def magic_move(self, u: dict[str, Any], lat: float, lon: float) -> None:
        u["destinationKey"] = None
        u["routeWaypointKeys"] = []
        u["routePath"] = None
        u["routeLegIndex"] = 0
        u["accumMovePoints"] = 0.0
        u["lat"] = lat
        u["lon"] = lon
        self._set_unit_halted(u, self.grid.lat_lon_to_hex_key(lat, lon))

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
        formation_members: dict[str, list[dict[str, Any]]] = {}
        for u in self.units:
            bf = u.get("bnFormation")
            if bf:
                bk = bf.get("battalionKey", "")
                formation_members.setdefault(bk, []).append(u)

        for bk, members in formation_members.items():
            if len(members) < 2:
                continue
            lead = formation_lead(members)
            if lead:
                sync_formation_column(
                    self.grid,
                    lead,
                    members,
                    self._assign_column_move,
                )

        for u in self.units:
            path = u.get("routePath")
            bf = u.get("bnFormation")
            if path and u["routeLegIndex"] >= len(path) - 1:
                if bf and not bf.get("isLead"):
                    members = formation_members.get(
                        (bf or {}).get("battalionKey", ""), []
                    )
                    lead_u = formation_lead(members)
                    ref_path = (bf or {}).get("refPath") or []
                    st = (
                        unit_station_status(
                            self.grid, ref_path, u, lead_u, members
                        )
                        if lead_u and ref_path
                        else "on_station"
                    )
                    if st == "behind":
                        if u.get("activity") == "halted":
                            u["activity"] = "moving"
                    else:
                        u["activity"] = "halted"
                        continue
                elif u["activity"] == "moving":
                    self.clear_route(u)
                    continue
                else:
                    continue
            if u["activity"] != "moving" or not path:
                continue
            bk = (bf or {}).get("battalionKey", "")
            members = formation_members.get(bk, [])
            gain = effective_mp_for_formation(self.grid, u, members, mp_gain)
            if gain <= 0:
                u["accumMovePoints"] = 0.0
            else:
                u["accumMovePoints"] = float(u.get("accumMovePoints") or 0) + gain
            max_hex_steps = 48
            if bf and bf.get("movementType") != "emergency":
                max_hex_steps = 1
            guard = 0
            while guard < max_hex_steps and u["routeLegIndex"] < len(path) - 1:
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
                        if bf and not bf.get("isLead"):
                            self._set_unit_halted(
                                u, path[u["routeLegIndex"]]
                            )
                        else:
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
        if_reports: list[dict[str, Any]] = []
        for _ in range(steps):
            tick_exhaustion_recovery(self.units, self.clock.sim_ms)
            self.clock.advance_minutes(1)
            tick_exhaustion_recovery(self.units, self.clock.sim_ms)
            minute_events = self.advance_movement_mp(MP_PER_MINUTE)
            all_events.extend(minute_events)
            if_tick = self._tick_if_mission_minute()
            if_reports.extend(if_tick.get("reports") or [])
        for ev in all_events:
            self._acquisition_queue.append(ev)
        for rep in if_reports:
            self._report_queue.append(rep)
        tb = self.clock.timebar_strings()
        mission = self.if_mission.get_mission()
        return {
            "units": self.units,
            "simInstantMs": self.clock.sim_ms,
            "timebar": {"main": tb.main, "daynight": tb.daynight, "sunLine": tb.sun_line},
            "isDay": tb.is_day,
            "newAcquisitionEvents": [asdict(e) for e in all_events],
            "newReports": if_reports,
            "activeIfMission": mission.to_api_dict() if mission else None,
            "minutesPerStep": self.minutes_per_step,
            "mpPerMinute": MP_PER_MINUTE,
        }

    def _tick_if_mission_minute(self) -> dict[str, Any]:
        return self.if_mission.tick_minute(
            self.units,
            self.clock.sim_ms,
            ExerciseClock.format_main_line_at,
            self._mgrs_six_digit,
        )

    def start_if_mission(
        self,
        target_key: str,
        firing_rows: list[dict[str, Any]],
        preplanned: bool,
        dug_in: bool,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        err, _ = self.if_mission.start_mission(
            self.units,
            target_key,
            firing_rows,
            preplanned,
            dug_in,
            self.clock.sim_ms,
        )
        return err, []

    def sync_if_mission_plan(self, firing_rows: list[dict[str, Any]]) -> str | None:
        return self.if_mission.sync_firing_plan(self.units, firing_rows, self.clock.sim_ms)

    def pop_report(self) -> dict[str, Any] | None:
        if not self._report_queue:
            return None
        return self._report_queue.pop(0)

    def report_queue_length(self) -> int:
        return len(self._report_queue)

    def unit_info(self, key: str) -> dict[str, Any] | None:
        u = self.get_unit(key)
        if not u:
            return None
        equipment: list[dict[str, Any]] = []
        for spec in u.get("equipmentSpecs") or []:
            st = self.catalog.get(spec["name"])
            art = self.catalog.get_artillery(spec["name"])
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
            if art:
                row["artillery"] = {
                    "emplacementTimeMin": art.emplacement_time_min,
                    "displacementTimeMin": art.displacement_time_min,
                    "maxRateOfFirePerTube": art.max_rate_of_fire,
                    "sustainedRateOfFirePerTube": art.sustained_rate_of_fire,
                }
            equipment.append(row)
        duration_ms = time_in_position_ms(u, self.clock.sim_ms)
        since = u.get("positionSinceSimMs") if u.get("activity") == "halted" else None
        return {
            "unit": u,
            "equipment": equipment,
            "positionHexKey": u.get("positionHexKey"),
            "positionSinceSimMs": since,
            "timeInPositionMs": duration_ms,
            "timeInPosition": ExerciseClock.format_duration(duration_ms),
            "ifExhausted": bool(u.get("ifExhausted")),
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
            "maneuverStats": self.catalog.maneuver_to_api_dict(),
            "artilleryStats": self.catalog.artillery_to_api_dict(),
            "units": self.units,
            "simInstantMs": self.clock.sim_ms,
            "timebar": {"main": tb.main, "daynight": tb.daynight, "sunLine": tb.sun_line},
            "minutesPerStep": self.minutes_per_step,
            "mpPerMinute": MP_PER_MINUTE,
            "activeIfMission": (
                self.if_mission.get_mission().to_api_dict()
                if self.if_mission.get_mission()
                else None
            ),
        }
