"""Direct fire range checks and candidate listing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.combat import UnitStatsCatalog
from backend.hex_grid import HexGrid


@dataclass
class DFCandidate:
    unit_key: str
    company: str
    battalion: str
    side: str
    df_score: float
    df_range_km: float
    dist_km: float
    activity: str


def equipment_df_range_km(name: str, catalog: UnitStatsCatalog) -> float:
    st = catalog.get_maneuver(name) or catalog.get_artillery(name)
    if not st or st.df_score <= 0:
        return 0.0
    return st.df_range


def unit_max_df_range_km(unit: dict[str, Any], catalog: UnitStatsCatalog) -> float:
    best = 0.0
    for spec in unit.get("equipmentSpecs") or []:
        if int(spec.get("count") or 0) <= 0:
            continue
        r = equipment_df_range_km(spec["name"], catalog)
        if r > best:
            best = r
    return best


def unit_has_direct_fire(unit: dict[str, Any]) -> bool:
    return float(unit.get("totalDirectFire") or 0) > 1e-9


def in_direct_fire_range(
    grid: HexGrid,
    shooter: dict[str, Any],
    target: dict[str, Any],
    catalog: UnitStatsCatalog,
) -> bool:
    if not unit_has_direct_fire(shooter):
        return False
    rng = unit_max_df_range_km(shooter, catalog)
    if rng <= 0:
        return False
    d = grid.distance_km(shooter["lat"], shooter["lon"], target["lat"], target["lon"])
    return d <= rng + 1e-6


class DirectFireService:
    def __init__(self, grid: HexGrid, catalog: UnitStatsCatalog) -> None:
        self.grid = grid
        self.catalog = catalog

    def candidate_shooters(
        self,
        units: list[dict[str, Any]],
        victim: dict[str, Any],
    ) -> list[DFCandidate]:
        enemy_side = "red" if victim["side"] == "blue" else "blue"
        out: list[DFCandidate] = []
        for u in units:
            if u["side"] != enemy_side:
                continue
            if not unit_has_direct_fire(u):
                continue
            if not in_direct_fire_range(self.grid, u, victim, self.catalog):
                continue
            d_km = self.grid.distance_km(u["lat"], u["lon"], victim["lat"], victim["lon"])
            out.append(
                DFCandidate(
                    unit_key=u["key"],
                    company=u.get("company", ""),
                    battalion=u.get("battalion", ""),
                    side=u["side"],
                    df_score=float(u.get("totalDirectFire") or 0),
                    df_range_km=unit_max_df_range_km(u, self.catalog),
                    dist_km=d_km,
                    activity=u.get("activity") or "halted",
                )
            )
        out.sort(key=lambda c: c.dist_km)
        return out
