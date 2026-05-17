"""Equipment parsing, combat score aggregation, and ammunition."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from backend.config import ROOT

EQUIPMENT_STATS_DIR = ROOT / "equipment_stats"
MANEUVER_STATS_CSV = EQUIPMENT_STATS_DIR / "maneuverStats.csv"
ARTILLERY_STATS_CSV = EQUIPMENT_STATS_DIR / "ArtilleryStats.csv"
LOGISTIC_STATS_CSV = EQUIPMENT_STATS_DIR / "LogisticStats.csv"


def _parse_bool(val: str | None) -> bool:
    if not val:
        return False
    return val.strip().lower() in ("1", "true", "yes", "y")


def _float(row: dict[str, str], key: str, default: float = 0.0) -> float:
    raw = row.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


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
class ManeuverStats(WeaponStats):
    ammo_tons: float
    ammo_tons_per_min_direct_fire: float
    ammo_tons_per_min_returning_fire: float
    ammo_tons_per_min_cc_attack: float
    ammo_tons_per_min_cc_defend: float


@dataclass
class ArtilleryStats(WeaponStats):
    emplacement_time_min: float
    displacement_time_min: float
    max_rate_of_fire: float
    sustained_rate_of_fire: float
    arty_type: str
    ammo_tons: float
    tons_per_round: float


@dataclass
class LogisticStats:
    name: str
    movement: float
    dry_cargo_tons: float
    fuel_liters: float


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


def _maneuver_from_row(row: dict[str, str], name: str) -> ManeuverStats:
    base = _weapon_from_row(row, name)
    return ManeuverStats(
        name=base.name,
        movement=base.movement,
        df_range=base.df_range,
        df_score=base.df_score,
        if_range=base.if_range,
        if_score=base.if_score,
        cc_score=base.cc_score,
        is_hard_target=base.is_hard_target,
        ammo_tons=_float(row, "ammo_tons"),
        ammo_tons_per_min_direct_fire=_float(row, "ammo_tons_per_min_direct_fire"),
        ammo_tons_per_min_returning_fire=_float(row, "ammo_tons_per_min_returning_fire"),
        ammo_tons_per_min_cc_attack=_float(row, "ammo_tons_per_min_cc_attack"),
        ammo_tons_per_min_cc_defend=_float(row, "ammo_tons_per_min_cc_defend"),
    )


class UnitStatsCatalog:
    def __init__(self) -> None:
        self._maneuver: dict[str, ManeuverStats] = {}
        self._artillery: dict[str, ArtilleryStats] = {}
        self._logistics: dict[str, LogisticStats] = {}

    def load_csv(self) -> None:
        self._maneuver.clear()
        self._artillery.clear()
        self._logistics.clear()
        self._load_maneuver_csv(MANEUVER_STATS_CSV)
        self._load_artillery_csv(ARTILLERY_STATS_CSV)
        self._load_logistic_csv(LOGISTIC_STATS_CSV)

    def _load_maneuver_csv(self, path: Path) -> None:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                self._maneuver[name] = _maneuver_from_row(row, name)

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
                    ammo_tons=_float(row, "ammo_tons"),
                    tons_per_round=_float(row, "tons_per_round"),
                )

    def _load_logistic_csv(self, path: Path) -> None:
        if not path.is_file():
            return
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                self._logistics[name] = LogisticStats(
                    name=name,
                    movement=float(row["movement"]),
                    dry_cargo_tons=_float(row, "dry_cargo_tons"),
                    fuel_liters=_float(row, "fuel_liters"),
                )

    def get(self, name: str) -> WeaponStats | None:
        if name in self._artillery:
            return self._artillery[name]
        return self._maneuver.get(name)

    def get_maneuver(self, name: str) -> ManeuverStats | None:
        return self._maneuver.get(name)

    def get_artillery(self, name: str) -> ArtilleryStats | None:
        return self._artillery.get(name)

    def get_logistics(self, name: str) -> LogisticStats | None:
        return self._logistics.get(name)

    def is_hard_target(self, name: str) -> bool:
        st = self.get(name)
        return bool(st and st.is_hard_target)

    def to_api_dict(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for n, s in self._maneuver.items():
            out[n] = self._maneuver_api(s)
        for n, s in self._artillery.items():
            out[n] = self._artillery_api(s)
        return out

    def maneuver_to_api_dict(self) -> dict[str, dict]:
        return {n: self._maneuver_api(s) for n, s in self._maneuver.items()}

    def artillery_to_api_dict(self) -> dict[str, dict]:
        return {n: self._artillery_api(s) for n, s in self._artillery.items()}

    def logistic_to_api_dict(self) -> dict[str, dict]:
        return {
            n: {
                "movement": s.movement,
                "dryCargoTons": s.dry_cargo_tons,
                "fuelLiters": s.fuel_liters,
            }
            for n, s in self._logistics.items()
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

    def _maneuver_api(self, s: ManeuverStats) -> dict:
        return {
            **self._weapon_api(s),
            "ammoTons": s.ammo_tons,
            "ammoTonsPerMinDirectFire": s.ammo_tons_per_min_direct_fire,
            "ammoTonsPerMinReturningFire": s.ammo_tons_per_min_returning_fire,
            "ammoTonsPerMinCcAttack": s.ammo_tons_per_min_cc_attack,
            "ammoTonsPerMinCcDefend": s.ammo_tons_per_min_cc_defend,
        }

    def _artillery_api(self, s: ArtilleryStats) -> dict:
        return {
            **self._weapon_api(s),
            "emplacementTimeMin": s.emplacement_time_min,
            "displacementTimeMin": s.displacement_time_min,
            "maxRateOfFirePerTube": s.max_rate_of_fire,
            "sustainedRateOfFirePerTube": s.sustained_rate_of_fire,
            "maxRateOfFire": s.max_rate_of_fire,
            "sustainedRateOfFire": s.sustained_rate_of_fire,
            "artyType": s.arty_type,
            "isHardTarget": s.is_hard_target,
            "ammoTons": s.ammo_tons,
            "tonsPerRound": s.tons_per_round,
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


def equipment_ammo_tons_per_piece(name: str, catalog: UnitStatsCatalog) -> float:
    man = catalog.get_maneuver(name)
    if man and man.ammo_tons > 0:
        return man.ammo_tons
    art = catalog.get_artillery(name)
    if art and art.ammo_tons > 0:
        return art.ammo_tons
    return 0.0


def compute_unit_ammo_authorized(
    specs: list[EquipmentSpec], catalog: UnitStatsCatalog
) -> float:
    total = 0.0
    for spec in specs:
        per = equipment_ammo_tons_per_piece(spec.name, catalog)
        if per > 0:
            total += per * spec.count
    return total


def ammo_percent(authorized: float, on_hand: float) -> float | None:
    if authorized <= 1e-9:
        return None
    return max(0.0, min(100.0, 100.0 * on_hand / authorized))


def init_unit_ammunition(unit: dict, catalog: UnitStatsCatalog) -> None:
    specs = [
        EquipmentSpec(s["name"], int(s["count"]))
        for s in unit.get("equipmentSpecs") or []
        if int(s.get("count") or 0) > 0
    ]
    auth = compute_unit_ammo_authorized(specs, catalog)
    unit["ammoAuthorized"] = auth
    unit["ammoOnHand"] = auth


def refresh_unit_ammo_authorized(unit: dict, catalog: UnitStatsCatalog) -> None:
    specs = [
        EquipmentSpec(s["name"], int(s["count"]))
        for s in unit.get("equipmentSpecs") or []
        if int(s.get("count") or 0) > 0
    ]
    auth = compute_unit_ammo_authorized(specs, catalog)
    unit["ammoAuthorized"] = auth
    if float(unit.get("ammoOnHand") or 0) > auth:
        unit["ammoOnHand"] = auth


def lose_equipment_ammunition(
    unit: dict, equipment_name: str, count_lost: int, catalog: UnitStatsCatalog
) -> float:
    """Remove ammunition stored on destroyed vehicles; returns tons lost."""
    per = equipment_ammo_tons_per_piece(equipment_name, catalog)
    if per <= 0 or count_lost <= 0:
        return 0.0
    lost = per * count_lost
    unit["ammoAuthorized"] = max(0.0, float(unit.get("ammoAuthorized") or 0) - lost)
    unit["ammoOnHand"] = max(0.0, float(unit.get("ammoOnHand") or 0) - lost)
    return lost


def cap_artillery_rounds_by_ammo(
    unit: dict, art: ArtilleryStats, desired_rounds: int
) -> tuple[int, float]:
    """
    Limit rounds fired by ammunition on hand.
    Returns (rounds_to_fire, tons_consumed).
    """
    if desired_rounds <= 0:
        return 0, 0.0
    if art.tons_per_round <= 1e-9:
        return desired_rounds, 0.0
    on_hand = float(unit.get("ammoOnHand") or 0)
    needed = desired_rounds * art.tons_per_round
    if needed <= on_hand + 1e-9:
        unit["ammoOnHand"] = on_hand - needed
        return desired_rounds, needed
    max_rounds = int(on_hand / art.tons_per_round)
    if max_rounds <= 0:
        return 0, 0.0
    consumed = max_rounds * art.tons_per_round
    unit["ammoOnHand"] = on_hand - consumed
    return max_rounds, consumed


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
    specs = [
        EquipmentSpec(s["name"], int(s["count"]))
        for s in unit.get("equipmentSpecs") or []
        if int(s.get("count") or 0) > 0
    ]
    totals = compute_combat_totals(specs, catalog)
    unit["totalDirectFire"] = totals.direct_fire
    unit["totalIndirectFire"] = totals.indirect_fire
    unit["totalCloseCombat"] = totals.close_combat
    refresh_unit_ammo_authorized(unit, catalog)


def format_score(n: float) -> str:
    r = round(n * 100) / 100
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return str(r)


def format_ammo_tons(n: float) -> str:
    r = round(n * 100) / 100
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return f"{r:.2f}"


def format_ammo_percent(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{round(pct)}%"
