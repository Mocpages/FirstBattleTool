"""FastAPI HTTP API for game simulation."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import ROOT
from backend.game_state import GameState

app = FastAPI(title="FB Automation Umpire API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

game = GameState()


def _require_unit(unit_key: str) -> dict[str, Any]:
    """Resolve unit by key (battalion names may contain '/')."""
    u = game.get_unit(unit_key)
    if not u:
        raise HTTPException(404, "Unit not found")
    return u


@app.on_event("startup")
def _startup() -> None:
    game.initialize()


class MoveOrderBody(BaseModel):
    goal_key: str
    extend: bool = False


class MagicMoveBody(BaseModel):
    lat: float
    lon: float


class WaypointBody(BaseModel):
    kind: str
    via_index: int = -1
    lat: float
    lon: float


class WaypointRemoveBody(BaseModel):
    index: int


class AcquisitionResolveBody(BaseModel):
    spotter_key: str
    target_key: str
    entered_hex_key: str
    from_hex_key: str
    spot_kind: str
    confirm: bool


class DFOpportunityResolveBody(BaseModel):
    shooter_key: str
    victim_key: str
    entered_hex_key: str
    from_hex_key: str = ""
    confirm: bool


class DirectFireResolveBody(BaseModel):
    shooter_keys: list[str] = Field(default_factory=list)
    shooter_key: str = ""
    victim_key: str
    victim_response: str = "continue_moving"
    target_dug_in: bool = False
    target_halted_obstacles: bool = False
    target_flank_shot: bool = False
    return_dug_in: bool = False
    return_halted_obstacles: bool = False
    return_flank_shot: bool = False


class IFFiringRow(BaseModel):
    unit_key: str
    weapon_name: str
    tube_count: int
    if_score: float
    rounds: int = 1


class IndirectFireResolveBody(BaseModel):
    target_key: str
    firing_rows: list[IFFiringRow]
    preplanned: bool = False
    dug_in: bool = False


class IFTimeToFireRow(BaseModel):
    unit_key: str
    weapon_name: str
    tube_count: int
    rounds: int = 1


class IFTimeToFireBody(BaseModel):
    firing_rows: list[IFTimeToFireRow]


class IFMissionPlanBody(BaseModel):
    target_key: str
    firing_rows: list[IFTimeToFireRow]
    preplanned: bool = False
    dug_in: bool = False


class PathBody(BaseModel):
    path: list[str] = Field(default_factory=list)


class SimTickBody(BaseModel):
    minutes_per_step: Optional[int] = None


class ParseCoordinateBody(BaseModel):
    text: str


class BattalionMoveBody(BaseModel):
    battalion_key: str
    route_hex_keys: list[str] = Field(default_factory=list)
    route_texts: list[str] = Field(default_factory=list)
    movement_type: str = "administrative"
    destination_action: str = "assembly"
    defense_line_hex_keys: list[str] = Field(default_factory=list)
    defense_line_texts: list[str] = Field(default_factory=list)
    threat_bearing_deg: Optional[float] = None


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return game.bootstrap()


@app.get("/api/state")
def state() -> dict[str, Any]:
    tb = game.clock.timebar_strings()
    return {
        "units": game.units,
        "simInstantMs": game.clock.sim_ms,
        "timebar": {"main": tb.main, "daynight": tb.daynight, "sunLine": tb.sun_line},
        "acquisitionQueueLength": game.acquisition_queue_length(),
    }


@app.post("/api/sim/tick")
def sim_tick(body: Optional[SimTickBody] = None) -> dict[str, Any]:
    minutes = body.minutes_per_step if body else None
    return game.sim_tick(minutes)


@app.get("/api/units/info")
def unit_info(unit_key: str = Query(..., alias="unit_key")) -> dict[str, Any]:
    info = game.unit_info(unit_key)
    if not info:
        raise HTTPException(404, "Unit not found")
    return info


@app.get("/api/acquisition/queue")
def acquisition_queue() -> dict[str, Any]:
    from dataclasses import asdict

    return {"events": [asdict(e) for e in game._acquisition_queue]}


@app.get("/api/direct-fire/candidates")
def df_candidates(victim_key: str) -> dict[str, Any]:
    victim = game.get_unit(victim_key)
    if not victim:
        raise HTTPException(404, "Victim unit not found")
    candidates = game.direct_fire.candidate_shooters(game.units, victim)
    return {
        "victimKey": victim_key,
        "candidates": [
            {
                "unitKey": c.unit_key,
                "company": c.company,
                "battalion": c.battalion,
                "side": c.side,
                "dfScore": c.df_score,
                "dfRangeKm": c.df_range_km,
                "distKm": c.dist_km,
                "activity": c.activity,
            }
            for c in candidates
        ],
    }


@app.post("/api/direct-fire/opportunity/resolve")
def df_opportunity_resolve(body: DFOpportunityResolveBody) -> dict[str, Any]:
    return game.resolve_df_opportunity(
        body.shooter_key,
        body.victim_key,
        body.entered_hex_key,
        body.confirm,
    )


@app.post("/api/direct-fire/resolve")
def df_resolve(body: DirectFireResolveBody) -> dict[str, Any]:
    keys = list(body.shooter_keys) if body.shooter_keys else []
    if body.shooter_key and body.shooter_key not in keys:
        keys.insert(0, body.shooter_key)
    err, _ = game.start_df_mission(
        keys,
        body.victim_key,
        body.victim_response,
        body.target_dug_in,
        body.target_halted_obstacles,
        body.target_flank_shot,
        body.return_dug_in,
        body.return_halted_obstacles,
        body.return_flank_shot,
    )
    if err:
        raise HTTPException(400, err)
    mission = game.df_mission.get_mission()
    return {
        "ok": True,
        "message": (
            "Direct fire engagement started. Use Play each minute; "
            "continues while the victim stays in range. Cancel to end."
        ),
        "units": game.units,
        "activeDfMission": mission.to_api_dict() if mission else None,
    }


@app.post("/api/direct-fire/cancel")
def df_cancel() -> dict[str, Any]:
    err, result = game.cancel_df_mission()
    if err:
        raise HTTPException(400, err)
    return {
        "ok": True,
        "message": "Direct fire engagement ended.",
        "units": game.units,
        "activeDfMission": None,
        "endReason": result.get("endReason"),
        "newReports": result.get("reports") or [],
    }


@app.post("/api/acquisition/resolve")
def acquisition_resolve(body: AcquisitionResolveBody) -> dict[str, Any]:
    from backend.acquisition import AcquisitionEvent

    ev = AcquisitionEvent(
        body.spotter_key,
        body.target_key,
        body.entered_hex_key,
        body.from_hex_key,
        body.spot_kind,
    )
    return game.resolve_acquisition(ev, body.confirm)


@app.post("/api/units/move-order")
def move_order(
    body: MoveOrderBody, unit_key: str = Query(..., alias="unit_key")
) -> dict[str, Any]:
    u = _require_unit(unit_key)
    err = game.extend_route(u, body.goal_key) if body.extend else game.assign_move_order(u, body.goal_key)
    return {"ok": err is None, "message": err, "unit": u}


@app.post("/api/units/clear-route")
def clear_route(unit_key: str = Query(..., alias="unit_key")) -> dict[str, Any]:
    u = _require_unit(unit_key)
    game.clear_route(u)
    return {"ok": True, "unit": u}


@app.post("/api/units/magic-move")
def magic_move(
    body: MagicMoveBody, unit_key: str = Query(..., alias="unit_key")
) -> dict[str, Any]:
    u = _require_unit(unit_key)
    game.magic_move(u, body.lat, body.lon)
    return {"ok": True, "unit": u}


@app.post("/api/units/remove-waypoint")
def remove_waypoint(
    body: WaypointRemoveBody, unit_key: str = Query(..., alias="unit_key")
) -> dict[str, Any]:
    u = _require_unit(unit_key)
    err = game.remove_waypoint(u, body.index)
    return {"ok": err is None, "message": err, "unit": u}


@app.post("/api/units/waypoint")
def waypoint(
    body: WaypointBody, unit_key: str = Query(..., alias="unit_key")
) -> dict[str, Any]:
    u = _require_unit(unit_key)
    err = game.apply_waypoint_drag(u, body.kind, body.via_index, body.lat, body.lon)
    return {"ok": err is None, "message": err, "unit": u}


@app.get("/api/selection/overlay")
def selection_overlay(mode: str, key: str) -> dict[str, Any]:
    if mode not in ("company", "battalion"):
        raise HTTPException(400, "mode must be company or battalion")
    return game.selection_overlay(mode, key)


@app.post("/api/route/segment-costs")
def segment_costs(body: PathBody) -> dict[str, Any]:
    costs: list[float | None] = []
    path = body.path
    for i in range(len(path) - 1):
        costs.append(game.raster.segment_move_cost(path[i], path[i + 1]))
    return {"costs": costs}


@app.get("/api/geo/hex-key")
def geo_hex_key(lat: float, lon: float) -> dict[str, Any]:
    key = game.grid.lat_lon_to_hex_key(lat, lon)
    return {"key": key, "hasRaster": game.raster.hex_has_raster(key)}


@app.post("/api/geo/parse-coordinate")
def geo_parse_coordinate(body: ParseCoordinateBody) -> dict[str, Any]:
    from backend.coordinates import parse_coordinate

    parsed = parse_coordinate(body.text)
    if not parsed:
        return {"ok": False, "message": "Could not parse coordinate."}
    lat, lon = parsed
    key = game.grid.lat_lon_to_hex_key(lat, lon)
    return {
        "ok": True,
        "lat": lat,
        "lon": lon,
        "key": key,
        "hasRaster": game.raster.hex_has_raster(key),
    }


@app.post("/api/battalion/move-order")
def battalion_move_order(body: BattalionMoveBody) -> dict[str, Any]:
    mt = (body.movement_type or "administrative").strip().lower()
    if mt not in ("administrative", "tactical", "emergency"):
        raise HTTPException(400, "movement_type must be administrative, tactical, or emergency")
    da = (body.destination_action or "assembly").strip().lower()
    if da not in ("assembly", "defend", "attack"):
        raise HTTPException(400, "destination_action must be assembly, defend, or attack")
    return game.execute_battalion_move_order(
        body.battalion_key,
        body.route_hex_keys,
        body.route_texts,
        mt,
        da,
        body.defense_line_hex_keys,
        body.defense_line_texts,
        body.threat_bearing_deg,
    )


@app.get("/api/terrain/hexes")
def terrain_hexes(west: float, south: float, east: float, north: float) -> dict[str, Any]:
    hexes = game.raster.hexes_in_bounds(west, south, east, north)
    return {"hexes": hexes}


@app.get("/api/indirect-fire/candidates")
def if_candidates(target_key: str) -> dict[str, Any]:
    target = game.get_unit(target_key)
    if not target:
        raise HTTPException(404, "Target not found")
    groups = game.indirect_fire.candidate_groups(game.units, target, game.clock.sim_ms)
    return {
        "targetKey": target_key,
        "simInstantMs": game.clock.sim_ms,
        "groups": [
            {
                "unitKey": g.unit_key,
                "company": g.company,
                "battalion": g.battalion,
                "side": g.side,
                "activity": g.activity,
                "timeInPositionMin": g.time_in_position_min,
                "weapons": [
                    {
                        "name": w.name,
                        "count": w.count,
                        "ifRange": w.if_range,
                        "ifScore": w.if_score,
                        "distKm": w.dist_km,
                        "emplacementTimeMin": w.emplacement_time_min,
                        "emplaced": w.emplaced,
                        "canFire": w.can_fire,
                        "exhausted": w.exhausted,
                        "timeInPositionMin": w.time_in_position_min,
                    }
                    for w in g.weapons
                ],
            }
            for g in groups
        ],
    }


@app.post("/api/indirect-fire/time-to-fire")
def if_time_to_fire(body: IFTimeToFireBody) -> dict[str, Any]:
    rows_out = []
    for row in body.firing_rows:
        u = game.get_unit(row.unit_key)
        if not u:
            raise HTTPException(404, f"Unit not found: {row.unit_key}")
        ttf = game.indirect_fire.time_to_fire_for_row(
            u,
            row.weapon_name,
            row.tube_count,
            row.rounds,
            game.clock.sim_ms,
        )
        if not ttf:
            raise HTTPException(400, f"Not artillery: {row.weapon_name}")
        rows_out.append(
            {
                "unitKey": row.unit_key,
                "weaponName": row.weapon_name,
                **ttf,
            }
        )
    return {"rows": rows_out}


def _if_api_rows(firing_rows: list) -> list[dict[str, Any]]:
    return [
        {
            "unit_key": r.unit_key,
            "weapon_name": r.weapon_name,
            "tube_count": r.tube_count,
            "rounds": r.rounds,
            "if_score": r.if_score,
            "tubeCount": r.tube_count,
            "ifScore": r.if_score,
            "company": (game.get_unit(r.unit_key) or {}).get("company", ""),
            "weaponName": r.weapon_name,
        }
        for r in firing_rows
    ]


@app.post("/api/indirect-fire/resolve")
def if_resolve(body: IndirectFireResolveBody) -> dict[str, Any]:
    api_rows = _if_api_rows(body.firing_rows)
    err, _ = game.start_if_mission(
        body.target_key,
        api_rows,
        body.preplanned,
        body.dug_in,
    )
    if err:
        raise HTTPException(400, err)
    mission = game.if_mission.get_mission()
    total = game.indirect_fire.resolve_total_score(api_rows, body.preplanned, body.dug_in)
    return {
        "ok": True,
        "totalScore": total,
        "message": "Indirect fire mission started. Advance time with Play to fire.",
        "units": game.units,
        "activeIfMission": mission.to_api_dict() if mission else None,
    }


@app.post("/api/indirect-fire/mission/plan")
def if_mission_plan(body: IFMissionPlanBody) -> dict[str, Any]:
    api_rows = _if_api_rows(body.firing_rows)
    mission = game.if_mission.get_mission()
    if mission and mission.is_active():
        err = game.sync_if_mission_plan(api_rows)
        if err:
            raise HTTPException(400, err)
    else:
        err, _ = game.start_if_mission(
            body.target_key,
            api_rows,
            body.preplanned,
            body.dug_in,
        )
        if err:
            raise HTTPException(400, err)
    mission = game.if_mission.get_mission()
    return {
        "ok": True,
        "activeIfMission": mission.to_api_dict() if mission else None,
        "units": game.units,
    }


@app.get("/api/reports/queue")
def reports_queue() -> dict[str, Any]:
    return {"length": game.report_queue_length()}


@app.post("/api/reports/pop")
def reports_pop() -> dict[str, Any]:
    rep = game.pop_report()
    if not rep:
        return {"report": None}
    return {"report": rep}


# Static files (index.html, app.js, …) — mount last
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")
