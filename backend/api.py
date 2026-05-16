"""FastAPI HTTP API for game simulation."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
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


class PathBody(BaseModel):
    path: list[str] = Field(default_factory=list)


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
def sim_tick() -> dict[str, Any]:
    return game.sim_tick()


@app.get("/api/acquisition/queue")
def acquisition_queue() -> dict[str, Any]:
    from dataclasses import asdict

    return {"events": [asdict(e) for e in game._acquisition_queue]}


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


@app.post("/api/units/{unit_key}/move-order")
def move_order(unit_key: str, body: MoveOrderBody) -> dict[str, Any]:
    u = game.get_unit(unit_key)
    if not u:
        raise HTTPException(404, "Unit not found")
    err = game.extend_route(u, body.goal_key) if body.extend else game.assign_move_order(u, body.goal_key)
    return {"ok": err is None, "message": err, "unit": u}


@app.post("/api/units/{unit_key}/clear-route")
def clear_route(unit_key: str) -> dict[str, Any]:
    u = game.get_unit(unit_key)
    if not u:
        raise HTTPException(404, "Unit not found")
    game.clear_route(u)
    return {"ok": True, "unit": u}


@app.post("/api/units/{unit_key}/magic-move")
def magic_move(unit_key: str, body: MagicMoveBody) -> dict[str, Any]:
    u = game.get_unit(unit_key)
    if not u:
        raise HTTPException(404, "Unit not found")
    game.magic_move(u, body.lat, body.lon)
    return {"ok": True, "unit": u}


@app.post("/api/units/{unit_key}/remove-waypoint")
def remove_waypoint(unit_key: str, body: WaypointRemoveBody) -> dict[str, Any]:
    u = game.get_unit(unit_key)
    if not u:
        raise HTTPException(404, "Unit not found")
    err = game.remove_waypoint(u, body.index)
    return {"ok": err is None, "message": err, "unit": u}


@app.post("/api/units/{unit_key}/waypoint")
def waypoint(unit_key: str, body: WaypointBody) -> dict[str, Any]:
    u = game.get_unit(unit_key)
    if not u:
        raise HTTPException(404, "Unit not found")
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


@app.get("/api/terrain/hexes")
def terrain_hexes(west: float, south: float, east: float, north: float) -> dict[str, Any]:
    hexes = game.raster.hexes_in_bounds(west, south, east, north)
    return {"hexes": hexes}


@app.get("/api/indirect-fire/candidates")
def if_candidates(target_key: str) -> dict[str, Any]:
    target = game.get_unit(target_key)
    if not target:
        raise HTTPException(404, "Target not found")
    groups = game.indirect_fire.candidate_groups(game.units, target)
    return {
        "targetKey": target_key,
        "groups": [
            {
                "unitKey": g.unit_key,
                "company": g.company,
                "battalion": g.battalion,
                "side": g.side,
                "weapons": [
                    {
                        "name": w.name,
                        "count": w.count,
                        "ifRange": w.if_range,
                        "ifScore": w.if_score,
                        "distKm": w.dist_km,
                    }
                    for w in g.weapons
                ],
            }
            for g in groups
        ],
    }


@app.post("/api/indirect-fire/resolve")
def if_resolve(body: IndirectFireResolveBody) -> dict[str, Any]:
    rows = [
        {
            "tubeCount": r.tube_count,
            "ifScore": r.if_score,
            "rounds": r.rounds,
            "company": (game.get_unit(r.unit_key) or {}).get("company", ""),
            "weaponName": r.weapon_name,
        }
        for r in body.firing_rows
    ]
    total = game.indirect_fire.resolve_total_score(rows, body.preplanned, body.dug_in)
    msg = game.indirect_fire.format_mission_status(rows, total, body.preplanned, body.dug_in)
    return {"ok": True, "totalScore": total, "message": msg}


# Static files (index.html, app.js, …) — mount last
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")
