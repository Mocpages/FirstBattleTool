"""Equipment parsing and combat score aggregation."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from backend.config import ROOT

EQUIPMENT_STATS_DIR = ROOT / "equipment_stats"
MANEUVER_STATS_CSV = EQUIPMENT_STATS_DIR / "maneuverStats.csv"
ARTILLERY_STATS_CSV = EQUIPMENT_STATS_DIR / "ArtilleryStats.csv"


def _parse_bool(val: str | None) -> bool:
    if not val:
        return False
    return val.strip().lower() in ("1", "true", "yes", "y")


@dataclass
class WeaponStats:
    name: str
    movement: float
    df_range: float
    df_score: float
    if_range: float
    if_score: float
    cc_score: float
    is_hard_target: bool


@dataclass
class ArtilleryStats(WeaponStats):
    emplacement_time_min: float
    displacement_time_min: float
    max_rate_of_fire: float
    sustained_rate_of_fire: float
    arty_type: str


@dataclass
class EquipmentSpec:
    name: str
    count: int


@dataclass
class CombatTotals:
    direct_fire: float
    indirect_fire: float
    close_combat: float


def _weapon_from_row(row: dict[str, str], name: str) -> WeaponStats:
    return WeaponStats(
        name=name,
        movement=float(row["movement"]),
        df_range=float(row["direct_fire_range"]),
        df_score=float(row["direct_fire_score"]),
        if_range=float(row["indirect_fire_range"]),
        if_score=float(row["indirect_fire_score"]),
        cc_score=float(row["close_combat_score"]),
        is_hard_target=_parse_bool(row.get("is_hard_target")),
    )


class UnitStatsCatalog:
    def __init__(self) -> None:
        self._maneuver: dict[str, WeaponStats] = {}
        self._artillery: dict[str, ArtilleryStats] = {}

    def load_csv(self) -> None:
        self._maneuver.clear()
        self._artillery.clear()
        self._load_maneuver_csv(MANEUVER_STATS_CSV)
        self._load_artillery_csv(ARTILLERY_STATS_CSV)

    def _load_maneuver_csv(self, path: Path) -> None:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                self._maneuver[name] = _weapon_from_row(row, name)

    def _load_artillery_csv(self, path: Path) -> None:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                base = _weapon_from_row(row, name)
                atype = (row.get("arty_type") or row.get("ArtyType") or "artillery").strip()
                self._artillery[name] = ArtilleryStats(
                    name=base.name,
                    movement=base.movement,
                    df_range=base.df_range,
                    df_score=base.df_score,
                    if_range=base.if_range,
                    if_score=base.if_score,
                    cc_score=base.cc_score,
                    is_hard_target=base.is_hard_target,
                    emplacement_time_min=float(row["emplacement_time_min"]),
                    displacement_time_min=float(row["displacement_time_min"]),
                    max_rate_of_fire=float(row["max_rate_of_fire"]),
                    sustained_rate_of_fire=float(row["sustained_rate_of_fire"]),
                    arty_type=atype,
                )

    def get(self, name: str) -> WeaponStats | None:
        if name in self._artillery:
            return self._artillery[name]
        return self._maneuver.get(name)

    def get_artillery(self, name: str) -> ArtilleryStats | None:
        return self._artillery.get(name)

    def is_hard_target(self, name: str) -> bool:
        st = self.get(name)
        return bool(st and st.is_hard_target)

    def to_api_dict(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for n, s in self._maneuver.items():
            out[n] = self._weapon_api(s)
        for n, s in self._artillery.items():
            out[n] = self._weapon_api(s)
        return out

    def maneuver_to_api_dict(self) -> dict[str, dict]:
        return {n: self._weapon_api(s) for n, s in self._maneuver.items()}

    def artillery_to_api_dict(self) -> dict[str, dict]:
        return {
            n: {
                **self._weapon_api(s),
                "emplacementTimeMin": s.emplacement_time_min,
                "displacementTimeMin": s.displacement_time_min,
                "maxRateOfFirePerTube": s.max_rate_of_fire,
                "sustainedRateOfFirePerTube": s.sustained_rate_of_fire,
                "maxRateOfFire": s.max_rate_of_fire,
                "sustainedRateOfFire": s.sustained_rate_of_fire,
                "artyType": s.arty_type,
                "isHardTarget": s.is_hard_target,
            }
            for n, s in self._artillery.items()
        }

    @staticmethod
    def _weapon_api(s: WeaponStats) -> dict:
        return {
            "movement": s.movement,
            "dfRange": s.df_range,
            "dfScore": s.df_score,
            "ifRange": s.if_range,
            "ifScore": s.if_score,
            "ccScore": s.cc_score,
            "isHardTarget": s.is_hard_target,
        }


_EQUIP_RE = re.compile(r"^(\d+)\s+(.+)$")


def parse_equipment_field(s: str) -> list[EquipmentSpec]:
    if not s:
        return []
    out: list[EquipmentSpec] = []
    for chunk in s.split(";"):
        seg = chunk.strip()
        if not seg:
            continue
        m = _EQUIP_RE.match(seg)
        if m:
            out.append(EquipmentSpec(name=m.group(2).strip(), count=int(m.group(1))))
    return out


def compute_combat_totals(specs: list[EquipmentSpec], catalog: UnitStatsCatalog) -> CombatTotals:
    df = inf = cc = 0.0
    for spec in specs:
        st = catalog.get(spec.name)
        if not st:
            continue
        n = spec.count
        df += n * st.df_score
        inf += n * st.if_score
        cc += n * st.cc_score
    return CombatTotals(df, inf, cc)


def refresh_unit_combat_totals(unit: dict, catalog: UnitStatsCatalog) -> None:
    specs = [EquipmentSpec(s["name"], int(s["count"])) for s in unit.get("equipmentSpecs") or [] if int(s.get("count") or 0) > 0]
    totals = compute_combat_totals(specs, catalog)
    unit["totalDirectFire"] = totals.direct_fire
    unit["totalIndirectFire"] = totals.indirect_fire
    unit["totalCloseCombat"] = totals.close_combat


def format_score(n: float) -> str:
    r = round(n * 100) / 100
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return str(r)
