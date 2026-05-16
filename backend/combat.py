"""Equipment parsing and combat score aggregation."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from backend.config import ROOT


@dataclass
class WeaponStats:
    name: str
    movement: float
    df_range: float
    df_score: float
    if_range: float
    if_score: float
    cc_score: float


@dataclass
class EquipmentSpec:
    name: str
    count: int


@dataclass
class CombatTotals:
    direct_fire: float
    indirect_fire: float
    close_combat: float


class UnitStatsCatalog:
    def __init__(self) -> None:
        self._by_name: dict[str, WeaponStats] = {}

    def load_csv(self, path: Path | None = None) -> None:
        path = path or (ROOT / "unitStats.csv")
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                self._by_name[name] = WeaponStats(
                    name=name,
                    movement=float(row["movement"]),
                    df_range=float(row["direct_fire_range"]),
                    df_score=float(row["direct_fire_score"]),
                    if_range=float(row["indirect_fire_range"]),
                    if_score=float(row["indirect_fire_score"]),
                    cc_score=float(row["close_combat_score"]),
                )

    def get(self, name: str) -> WeaponStats | None:
        return self._by_name.get(name)

    def to_api_dict(self) -> dict[str, dict]:
        return {
            n: {
                "movement": s.movement,
                "dfRange": s.df_range,
                "dfScore": s.df_score,
                "ifRange": s.if_range,
                "ifScore": s.if_score,
                "ccScore": s.cc_score,
            }
            for n, s in self._by_name.items()
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


def format_score(n: float) -> str:
    r = round(n * 100) / 100
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return str(r)
