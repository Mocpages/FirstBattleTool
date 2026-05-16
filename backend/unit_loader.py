"""Load company CSV rows."""
from __future__ import annotations

import csv
from pathlib import Path

from backend.config import ROOT


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
                }
            )
        return rows
