"""Indirect fire mission planning."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.combat import UnitStatsCatalog, format_score
from backend.hex_grid import HexGrid


@dataclass
class IFWeaponOption:
    name: str
    count: int
    if_range: float
    if_score: float
    dist_km: float


@dataclass
class IFCandidateGroup:
    unit_key: str
    company: str
    battalion: str
    side: str
    weapons: list[IFWeaponOption]


class IndirectFireService:
    def __init__(self, grid: HexGrid, catalog: UnitStatsCatalog) -> None:
        self.grid = grid
        self.catalog = catalog

    def candidate_groups(self, units: list[dict[str, Any]], target: dict[str, Any]) -> list[IFCandidateGroup]:
        enemy_side = "red" if target["side"] == "blue" else "blue"
        groups: list[IFCandidateGroup] = []
        for u in units:
            if u["side"] != enemy_side:
                continue
            weapons: list[IFWeaponOption] = []
            for spec in u.get("equipmentSpecs") or []:
                st = self.catalog.get(spec["name"])
                if not st or st.if_score <= 0:
                    continue
                d_km = self.grid.distance_km(u["lat"], u["lon"], target["lat"], target["lon"])
                if st.if_range < d_km:
                    continue
                weapons.append(
                    IFWeaponOption(
                        name=spec["name"],
                        count=spec["count"],
                        if_range=st.if_range,
                        if_score=st.if_score,
                        dist_km=d_km,
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
                    )
                )
        return groups

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
