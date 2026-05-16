"""Load company CSV rows."""
from __future__ import annotations

import csv
from pathlib import Path

VALID_UNIT_TYPES = frozenset(
    {
        "infantry",
        "mech",
        "armor",
        "artillery",
        "self propelled arty",
        "mlrs",
        "missile",
        "command",
        "logistics",
    }
)
VALID_UNIT_SIZES = frozenset(
    {
        "team",
        "squad",
        "section",
        "platoon",
        "company",
        "battalion",
        "regiment",
        "brigade",
        "division",
    }
)
DEFAULT_UNIT_TYPE = "infantry"
DEFAULT_UNIT_SIZE = "company"


def _norm_unit_type(raw: str | None) -> str:
    if not raw:
        return DEFAULT_UNIT_TYPE
    t = raw.strip().lower()
    if t in VALID_UNIT_TYPES:
        return t
    aliases = {
        "mechanized": "mech",
        "mechanised": "mech",
        "motorized": "mech",
        "motorised": "mech",
        "tank": "armor",
        "sp arty": "self propelled arty",
        "spa": "self propelled arty",
        "self-propelled arty": "self propelled arty",
        "self_propelled_arty": "self propelled arty",
    }
    return aliases.get(t, DEFAULT_UNIT_TYPE)


def _norm_unit_size(raw: str | None) -> str:
    if not raw:
        return DEFAULT_UNIT_SIZE
    s = raw.strip().lower()
    return s if s in VALID_UNIT_SIZES else DEFAULT_UNIT_SIZE


def load_unit_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            company = (row.get("company") or "").strip()
            battalion = (row.get("battalion") or "").strip()
            lat = float(row["lat"])
            lon = float(row["lon"])
            vehicle = (row.get("vehicle") or "tracked").strip().lower()
            if vehicle != "truck":
                vehicle = "tracked"
            equipment = ""
            if "equipment" in row and row["equipment"]:
                equipment = row["equipment"].strip()
            rows.append(
                {
                    "company": company,
                    "battalion": battalion,
                    "lat": lat,
                    "lon": lon,
                    "vehicle": vehicle,
                    "equipment": equipment,
                    "unitType": _norm_unit_type(row.get("unitType") or row.get("unittype")),
                    "unitSize": _norm_unit_size(row.get("unitSize") or row.get("unitsize")),
                }
            )
        return rows
