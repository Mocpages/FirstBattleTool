"""Indirect fire mission planning."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.artillery_fire import (
    compute_time_to_fire,
    is_artillery_emplaced,
    time_in_position_minutes,
)
from backend.combat import UnitStatsCatalog, format_score
from backend.hex_grid import HexGrid


@dataclass
class IFWeaponOption:
    name: str
    count: int
    if_range: float
    if_score: float
    dist_km: float
    emplacement_time_min: float
    emplaced: bool
    can_fire: bool
    exhausted: bool
    time_in_position_min: float


@dataclass
class IFCandidateGroup:
    unit_key: str
    company: str
    battalion: str
    side: str
    weapons: list[IFWeaponOption]
    activity: str
    time_in_position_min: float


class IndirectFireService:
    def __init__(self, grid: HexGrid, catalog: UnitStatsCatalog) -> None:
        self.grid = grid
        self.catalog = catalog

    def candidate_groups(
        self,
        units: list[dict[str, Any]],
        target: dict[str, Any],
        sim_ms: int,
    ) -> list[IFCandidateGroup]:
        enemy_side = "red" if target["side"] == "blue" else "blue"
        groups: list[IFCandidateGroup] = []
        for u in units:
            if u["side"] != enemy_side:
                continue
            weapons: list[IFWeaponOption] = []
            tip = time_in_position_minutes(u, sim_ms)
            for spec in u.get("equipmentSpecs") or []:
                art = self.catalog.get_artillery(spec["name"])
                if not art or art.if_score <= 0:
                    continue
                d_km = self.grid.distance_km(u["lat"], u["lon"], target["lat"], target["lon"])
                if art.if_range < d_km:
                    continue
                emplaced = is_artillery_emplaced(u, art, sim_ms)
                weapons.append(
                    IFWeaponOption(
                        name=spec["name"],
                        count=spec["count"],
                        if_range=art.if_range,
                        if_score=art.if_score,
                        dist_km=d_km,
                        emplacement_time_min=art.emplacement_time_min,
                        emplaced=emplaced,
                        can_fire=emplaced,
                        exhausted=bool(u.get("ifExhausted")),
                        time_in_position_min=tip,
                    )
                )
            if weapons:
                groups.append(
                    IFCandidateGroup(
                        unit_key=u["key"],
                        company=u["company"],
                        battalion=u["battalion"],
                        side=u["side"],
                        weapons=weapons,
                        activity=u.get("activity") or "halted",
                        time_in_position_min=tip,
                    )
                )
        return groups

    def time_to_fire_for_row(
        self,
        unit: dict[str, Any],
        weapon_name: str,
        tube_count: int,
        rounds_per_tube: int,
        sim_ms: int,
    ) -> dict[str, Any] | None:
        art = self.catalog.get_artillery(weapon_name)
        if not art:
            return None
        return compute_time_to_fire(unit, art, tube_count, rounds_per_tube, sim_ms)

    def resolve_total_score(
        self,
        firing_rows: list[dict[str, Any]],
        preplanned: bool,
        dug_in: bool,
    ) -> float:
        base = 0.0
        for row in firing_rows:
            base += row["tubeCount"] * row["ifScore"] * row["rounds"]
        mul = (2.0 if preplanned else 1.0) * (0.5 if dug_in else 1.0)
        return base * mul

    def format_mission_status(
        self,
        firing_rows: list[dict[str, Any]],
        total: float,
        preplanned: bool,
        dug_in: bool,
    ) -> str:
        parts = [f'{r["company"]} {r["weaponName"]} ×{r["rounds"]} rds' for r in firing_rows]
        msg = f"Indirect fire mission executed — total IF score {format_score(total)}"
        if preplanned:
            msg += " (preplanned ×2)"
        if dug_in:
            msg += " (dug-in / complex ×½)"
        msg += ": " + "; ".join(parts)
        return msg
