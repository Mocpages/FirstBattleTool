"""Active indirect fire missions — per-minute fires, casualties, reports."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from backend.artillery_fire import MAX_RATE_BURST_MINUTES, apply_fire_mission, is_artillery_emplaced
from backend.combat import (
    UnitStatsCatalog,
    ammo_percent,
    cap_artillery_rounds_by_ammo,
    format_score,
    lose_equipment_ammunition,
    refresh_unit_combat_totals,
)
from backend.reports import ReportService

HARD_TARGET_KILL_FACTOR = 0.05


@dataclass
class FirerState:
    unit_key: str
    weapon_name: str
    tube_count: int
    if_score: float
    volleys_remaining: int
    max_burst_minutes_used: float = 0.0
    active: bool = True
    total_volleys_fired: int = 0
    total_rounds_fired: int = 0

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "unitKey": self.unit_key,
            "weaponName": self.weapon_name,
            "tubeCount": self.tube_count,
            "ifScore": self.if_score,
            "volleysRemaining": self.volleys_remaining,
            "active": self.active,
            "totalVolleysFired": self.total_volleys_fired,
            "totalRoundsFired": self.total_rounds_fired,
        }


@dataclass
class ActiveIFMission:
    target_key: str
    started_sim_ms: int
    preplanned: bool
    dug_in: bool
    firers: list[FirerState] = field(default_factory=list)
    receiving_if_report_sent: bool = False
    mission_losses: dict[str, int] = field(default_factory=dict)
    total_rounds_fired: int = 0
    ended_sim_ms: int | None = None

    def is_active(self) -> bool:
        return self.ended_sim_ms is None and any(
            f.active and f.volleys_remaining > 0 for f in self.firers
        )

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "targetKey": self.target_key,
            "startedSimMs": self.started_sim_ms,
            "endedSimMs": self.ended_sim_ms,
            "preplanned": self.preplanned,
            "dugIn": self.dug_in,
            "active": self.is_active(),
            "firers": [f.to_api_dict() for f in self.firers],
            "totalRoundsFired": self.total_rounds_fired,
            "missionLosses": dict(self.mission_losses),
        }


class IFMissionManager:
    def __init__(self, catalog: UnitStatsCatalog, reports: ReportService) -> None:
        self.catalog = catalog
        self.reports = reports
        self.mission: ActiveIFMission | None = None

    def get_mission(self) -> ActiveIFMission | None:
        return self.mission

    def start_mission(
        self,
        units: list[dict[str, Any]],
        target_key: str,
        firing_rows: list[dict[str, Any]],
        preplanned: bool,
        dug_in: bool,
        sim_ms: int,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        if self.mission and self.mission.is_active():
            return "An indirect fire mission is already in progress.", []
        target = next((u for u in units if u["key"] == target_key), None)
        if not target:
            return "Target unit not found.", []
        firers: list[FirerState] = []
        for row in firing_rows:
            err = self._append_firer(units, firers, row, sim_ms)
            if err:
                return err, []
        if not firers:
            return "No valid firing units.", []
        self.mission = ActiveIFMission(
            target_key=target_key,
            started_sim_ms=sim_ms,
            preplanned=preplanned,
            dug_in=dug_in,
            firers=firers,
        )
        return None, []

    def sync_firing_plan(
        self,
        units: list[dict[str, Any]],
        firing_rows: list[dict[str, Any]],
        sim_ms: int,
    ) -> str | None:
        if not self.mission or not self.mission.is_active():
            return "No active indirect fire mission."
        incoming_keys = {(r["unit_key"], r["weapon_name"]) for r in firing_rows}
        for f in self.mission.firers:
            key = (f.unit_key, f.weapon_name)
            if key not in incoming_keys:
                f.active = False
        for row in firing_rows:
            key = (row["unit_key"], row["weapon_name"])
            existing = next(
                (f for f in self.mission.firers if (f.unit_key, f.weapon_name) == key),
                None,
            )
            if existing:
                existing.active = True
                new_rem = int(row["rounds"])
                if new_rem > existing.volleys_remaining:
                    existing.volleys_remaining = new_rem
                existing.tube_count = int(row.get("tube_count") or row.get("tubeCount") or 0)
                existing.if_score = float(row.get("if_score", row.get("ifScore", 0)))
            else:
                err = self._append_firer(units, self.mission.firers, row, sim_ms)
                if err:
                    return err
        return None

    def _append_firer(
        self,
        units: list[dict[str, Any]],
        firers: list[FirerState],
        row: dict[str, Any],
        sim_ms: int,
    ) -> str | None:
        u = next((x for x in units if x["key"] == row["unit_key"]), None)
        if not u:
            return f"Unknown firing unit: {row['unit_key']}"
        art = self.catalog.get_artillery(row["weapon_name"])
        if not art:
            return f"{row['weapon_name']} is not artillery."
        if not is_artillery_emplaced(u, art, sim_ms):
            return f"{u['company']} is not emplaced."
        volleys = int(row["rounds"])
        if volleys < 1:
            return f"Invalid rounds for {u['company']}."
        firers.append(
            FirerState(
                unit_key=row["unit_key"],
                weapon_name=row["weapon_name"],
                tube_count=int(row.get("tube_count") or row.get("tubeCount") or 0),
                if_score=float(row.get("if_score", row.get("ifScore", 0))),
                volleys_remaining=volleys,
            )
        )
        return None

    def tick_minute(
        self,
        units: list[dict[str, Any]],
        sim_ms: int,
        clock_main_line_fn,
        mgrs_fn,
    ) -> dict[str, Any]:
        """Advance mission one minute; returns reports and status."""
        out_reports: list[dict[str, Any]] = []
        minute_ifs = 0.0
        minute_kills = 0
        if not self.mission or self.mission.ended_sim_ms is not None:
            return {
                "active": False,
                "minuteIfs": 0,
                "minuteKills": 0,
                "reports": [],
                "mission": None,
            }

        target = next((u for u in units if u["key"] == self.mission.target_key), None)
        if not target:
            self.mission.ended_sim_ms = sim_ms
            return {"active": False, "minuteIfs": 0, "minuteKills": 0, "reports": [], "mission": None}

        mul = (2.0 if self.mission.preplanned else 1.0) * (0.5 if self.mission.dug_in else 1.0)

        for firer in self.mission.firers:
            if not firer.active or firer.volleys_remaining <= 0:
                continue
            u = next((x for x in units if x["key"] == firer.unit_key), None)
            if not u:
                firer.active = False
                continue
            art = self.catalog.get_artillery(firer.weapon_name)
            if not art:
                firer.active = False
                continue
            if not is_artillery_emplaced(u, art, sim_ms):
                firer.active = False
                continue

            volleys, used_max_minute = self._volleys_this_minute(firer, u, art)
            if volleys <= 0:
                continue

            desired_physical = volleys * firer.tube_count
            physical, _tons = cap_artillery_rounds_by_ammo(u, art, desired_physical)
            if physical <= 0:
                firer.active = False
                continue

            volleys_used = max(1, (physical + firer.tube_count - 1) // firer.tube_count)
            volleys_used = min(volleys, volleys_used)
            firer.volleys_remaining -= volleys_used
            firer.total_volleys_fired += volleys_used
            firer.total_rounds_fired += physical
            self.mission.total_rounds_fired += physical

            minute_ifs += physical * firer.if_score * mul

            if used_max_minute and firer.max_burst_minutes_used >= MAX_RATE_BURST_MINUTES:
                u["ifExhausted"] = True
                u["ifCeaseFireSimMs"] = sim_ms

        if minute_ifs > 0 and not self.mission.receiving_if_report_sent:
            addressee = target.get("battalion") or "ALL STATIONS"
            caller = target.get("company") or target["key"]
            time_str = clock_main_line_fn(sim_ms)
            out_reports.append(
                self.reports.receiving_indirect_fire(
                    addressee,
                    caller,
                    time_str,
                    target_key=target["key"],
                )
            )
            self.mission.receiving_if_report_sent = True

        if minute_ifs > 0:
            minute_kills = self._apply_minute_kills(target, minute_ifs)

        if not self.mission.is_active():
            self.mission.ended_sim_ms = sim_ms
            shelrep = self._build_shelrep(target, clock_main_line_fn, mgrs_fn)
            out_reports.append(shelrep)
            out_reports.extend(
                self._ammo_reports_after_mission(units, clock_main_line_fn)
            )

        return {
            "active": self.mission.is_active(),
            "minuteIfs": round(minute_ifs, 3),
            "minuteKills": minute_kills,
            "reports": out_reports,
            "mission": self.mission.to_api_dict() if self.mission else None,
        }

    def _volleys_this_minute(
        self,
        firer: FirerState,
        unit: dict[str, Any],
        art: Any,
    ) -> tuple[int, bool]:
        exhausted = bool(unit.get("ifExhausted")) or firer.max_burst_minutes_used >= MAX_RATE_BURST_MINUTES
        rate = art.sustained_rate_of_fire if exhausted else art.max_rate_of_fire
        rate = max(rate, 0.01)
        volleys = min(firer.volleys_remaining, max(1, int(rate)))
        used_max = not exhausted
        if used_max:
            firer.max_burst_minutes_used += 1.0
        return volleys, used_max

    def _apply_minute_kills(self, target: dict[str, Any], minute_ifs: float) -> int:
        avg_kills = 0.15 * minute_ifs - 0.06
        mult = max(0.0, min(2.0, random.gauss(1.0, 0.5)))
        kills = int(round(avg_kills * mult))
        if kills <= 0:
            return 0
        applied = 0
        for _ in range(kills):
            if self._apply_one_kill(target):
                applied += 1
        refresh_unit_combat_totals(target, self.catalog)
        return applied

    def _apply_one_kill(self, target: dict[str, Any]) -> bool:
        specs = target.get("equipmentSpecs") or []
        pool: list[tuple[str, bool]] = []
        for spec in specs:
            name = spec["name"]
            count = int(spec.get("count") or 0)
            if count <= 0:
                continue
            hard = self.catalog.is_hard_target(name)
            for _ in range(count):
                pool.append((name, hard))
        if not pool:
            return False
        name, hard = random.choice(pool)
        if hard:
            p = HARD_TARGET_KILL_FACTOR * random.uniform(0.5, 1.5)
            p = max(0.0, min(1.0, p))
            if random.random() > p:
                return False
        for spec in specs:
            if spec["name"] == name and int(spec.get("count") or 0) > 0:
                spec["count"] = int(spec["count"]) - 1
                lose_equipment_ammunition(target, name, 1, self.catalog)
                self.mission.mission_losses[name] = self.mission.mission_losses.get(name, 0) + 1
                return True
        return False

    def _build_shelrep(
        self,
        target: dict[str, Any],
        clock_main_line_fn,
        mgrs_fn,
    ) -> dict[str, Any]:
        assert self.mission is not None
        addressee = target.get("battalion") or "ALL STATIONS"
        caller = target.get("company") or target["key"]
        guns_line = self._format_guns_line()
        rounds_line = self._format_rounds_line()
        damage_line = self._format_damage_line()
        return self.reports.shelrep(
            addressee,
            caller,
            mgrs=mgrs_fn(target["lat"], target["lon"]) or "—",
            time_from=clock_main_line_fn(self.mission.started_sim_ms),
            time_to=clock_main_line_fn(self.mission.ended_sim_ms if self.mission.ended_sim_ms is not None else self.mission.started_sim_ms),
            guns_line=guns_line,
            rounds_line=rounds_line,
            damage_line=damage_line,
            target_key=target["key"],
        )

    def _format_guns_line(self) -> str:
        assert self.mission is not None
        tubes_by_weapon: dict[str, int] = {}
        for f in self.mission.firers:
            if f.total_rounds_fired <= 0 and f.volleys_remaining <= 0:
                continue
            tubes_by_weapon[f.weapon_name] = tubes_by_weapon.get(f.weapon_name, 0) + f.tube_count
        total_guns = sum(tubes_by_weapon.values())
        if total_guns < 10:
            echelon = "Battery"
        elif total_guns <= 30:
            echelon = "Battalion"
        else:
            echelon = "Multiple battalions"
        if len(tubes_by_weapon) == 1:
            weapon, tubes = next(iter(tubes_by_weapon.items()))
            art = self.catalog.get_artillery(weapon)
            atype = art.arty_type if art else "artillery"
            return f"{echelon}, {atype}."
        parts = [f"{n} {w}" for w, n in tubes_by_weapon.items()]
        return f"{echelon} ({', '.join(parts)})."

    @staticmethod
    def _round_reported_rounds(n: int) -> int:
        if n < 50:
            return int(round(n / 10.0) * 10)
        if n <= 200:
            return int(round(n / 50.0) * 50)
        return int(round(n / 100.0) * 100)

    def _format_rounds_line(self) -> str:
        assert self.mission is not None
        total = self.mission.total_rounds_fired
        reported = self._round_reported_rounds(total)
        types: list[str] = []
        for f in self.mission.firers:
            if f.total_rounds_fired > 0:
                types.append(f.weapon_name)
        type_str = types[0] if len(set(types)) == 1 else "mixed"
        return f"Approximately {reported} rounds, {type_str}."

    def _format_damage_line(self) -> str:
        assert self.mission is not None
        if not self.mission.mission_losses:
            return "No losses reported."
        parts = [f"{n} {name}" for name, n in sorted(self.mission.mission_losses.items())]
        return "; ".join(parts)

    def _ammo_reports_after_mission(
        self,
        units: list[dict[str, Any]],
        clock_main_line_fn,
    ) -> list[dict[str, Any]]:
        assert self.mission is not None
        reports: list[dict[str, Any]] = []
        seen: set[str] = set()
        time_str = clock_main_line_fn(
            self.mission.ended_sim_ms
            if self.mission.ended_sim_ms is not None
            else self.mission.started_sim_ms
        )
        for firer in self.mission.firers:
            if firer.total_rounds_fired <= 0 or firer.unit_key in seen:
                continue
            seen.add(firer.unit_key)
            u = next((x for x in units if x["key"] == firer.unit_key), None)
            if not u:
                continue
            auth = float(u.get("ammoAuthorized") or 0)
            on_hand = float(u.get("ammoOnHand") or 0)
            pct = ammo_percent(auth, on_hand)
            if auth <= 1e-9:
                continue
            addressee = u.get("battalion") or "ALL STATIONS"
            caller = u.get("company") or u["key"]
            reports.append(
                self.reports.ammunition_status(
                    addressee,
                    caller,
                    time_str,
                    authorized=auth,
                    on_hand=on_hand,
                    percent=pct,
                    unit_key=u["key"],
                )
            )
        return reports
