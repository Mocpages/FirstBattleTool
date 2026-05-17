"""Active direct fire engagements — per-minute fires, casualties, reports."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from backend.combat import (
    UnitStatsCatalog,
    ammo_percent,
    format_ammo_percent,
    format_ammo_tons,
    format_score,
    lose_equipment_ammunition,
    refresh_unit_combat_totals,
)
from backend.direct_fire import in_direct_fire_range, unit_has_direct_fire
from backend.reports import ReportService

VictimResponse = Literal["return_fire", "continue_moving"]

DF_KILL_BASE = (0.14333, 0.17)
DF_KILL_DIVISOR = 7.0


@dataclass
class DFModifiers:
    dug_in: bool = False
    halted_obstacles: bool = False
    flank_shot: bool = False

    def loss_multiplier(self) -> float:
        m = 1.0
        if self.dug_in:
            m *= 0.5
        if self.halted_obstacles:
            m *= 2.0
        if self.flank_shot:
            m *= 2.0
        return m


@dataclass
class ShooterEngagementStats:
    unit_key: str
    kills: int = 0
    ammo_consumed: float = 0.0
    losses: dict[str, int] = field(default_factory=dict)


@dataclass
class ActiveDFMission:
    shooter_keys: list[str]
    victim_key: str
    victim_response: VictimResponse
    victim_mods_as_target: DFModifiers
    victim_mods_return: DFModifiers = field(default_factory=DFModifiers)
    shooter_stats: dict[str, ShooterEngagementStats] = field(default_factory=dict)
    started_sim_ms: int = 0
    ended_sim_ms: int | None = None
    end_reason: str | None = None
    victim_losses: dict[str, int] = field(default_factory=dict)
    victim_kills: int = 0
    victim_ammo_consumed: float = 0.0

    def is_active(self) -> bool:
        return self.ended_sim_ms is None

    def total_shooter_kills(self) -> int:
        return sum(s.kills for s in self.shooter_stats.values())

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "shooterKeys": list(self.shooter_keys),
            "shooterKey": self.shooter_keys[0] if self.shooter_keys else "",
            "victimKey": self.victim_key,
            "victimResponse": self.victim_response,
            "victimModsAsTarget": {
                "dugIn": self.victim_mods_as_target.dug_in,
                "haltedObstacles": self.victim_mods_as_target.halted_obstacles,
                "flankShot": self.victim_mods_as_target.flank_shot,
            },
            "victimModsReturn": {
                "dugIn": self.victim_mods_return.dug_in,
                "haltedObstacles": self.victim_mods_return.halted_obstacles,
                "flankShot": self.victim_mods_return.flank_shot,
            },
            "startedSimMs": self.started_sim_ms,
            "endedSimMs": self.ended_sim_ms,
            "endReason": self.end_reason,
            "active": self.is_active(),
            "shooterKills": self.total_shooter_kills(),
            "victimKills": self.victim_kills,
            "victimLosses": dict(self.victim_losses),
            "shooterStats": {
                k: {
                    "unitKey": s.unit_key,
                    "kills": s.kills,
                    "ammoConsumed": s.ammo_consumed,
                    "losses": dict(s.losses),
                }
                for k, s in self.shooter_stats.items()
            },
        }


@dataclass
class DFOpportunityEvent:
    shooter_key: str
    victim_key: str
    entered_hex_key: str
    from_hex_key: str


class DFMissionManager:
    def __init__(self, catalog: UnitStatsCatalog, reports: ReportService) -> None:
        self.catalog = catalog
        self.reports = reports
        self.mission: ActiveDFMission | None = None
        self._silenced: set[str] = set()

    @staticmethod
    def silence_key(shooter_key: str, victim_key: str, entered_hex_key: str) -> str:
        return f"{shooter_key}|{victim_key}|{entered_hex_key}"

    def mark_silenced(self, shooter_key: str, victim_key: str, entered_hex_key: str) -> None:
        self._silenced.add(self.silence_key(shooter_key, victim_key, entered_hex_key))

    def is_silenced(self, shooter_key: str, victim_key: str, entered_hex_key: str) -> bool:
        return self.silence_key(shooter_key, victim_key, entered_hex_key) in self._silenced

    def get_mission(self) -> ActiveDFMission | None:
        return self.mission

    def start_mission(
        self,
        units: list[dict[str, Any]],
        shooter_keys: list[str],
        victim_key: str,
        victim_response: VictimResponse,
        victim_mods_as_target: DFModifiers,
        victim_mods_return: DFModifiers,
        sim_ms: int,
        halt_victim_fn: Callable[[dict[str, Any]], None],
        in_range_fn: Callable[[dict[str, Any], dict[str, Any]], bool],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        if self.mission and self.mission.is_active():
            return "A direct fire engagement is already in progress.", []
        if not shooter_keys:
            return "Select at least one shooting unit.", []
        victim = next((u for u in units if u["key"] == victim_key), None)
        if not victim:
            return "Victim unit not found.", []
        shooters: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sk in shooter_keys:
            if sk in seen:
                continue
            seen.add(sk)
            u = next((x for x in units if x["key"] == sk), None)
            if not u:
                return f"Shooter unit not found: {sk}", []
            if u["side"] == victim["side"]:
                return "Shooters and victim must be on opposite sides.", []
            if not unit_has_direct_fire(u):
                return f"{u['company']} has no direct fire capability.", []
            if not in_range_fn(u, victim):
                return f"{u['company']} is not in direct fire range of the victim.", []
            shooters.append(u)
        if victim_response == "return_fire":
            if not unit_has_direct_fire(victim):
                return f"{victim['company']} cannot return direct fire.", []
            halt_victim_fn(victim)
        stats = {u["key"]: ShooterEngagementStats(unit_key=u["key"]) for u in shooters}
        self.mission = ActiveDFMission(
            shooter_keys=[u["key"] for u in shooters],
            victim_key=victim_key,
            victim_response=victim_response,
            victim_mods_as_target=victim_mods_as_target,
            victim_mods_return=victim_mods_return,
            shooter_stats=stats,
            started_sim_ms=sim_ms,
        )
        return None, []

    def cancel_mission(
        self,
        units: list[dict[str, Any]],
        sim_ms: int,
        clock_main_line_fn: Callable[[int], str],
    ) -> tuple[str | None, dict[str, Any]]:
        if not self.mission or not self.mission.is_active():
            return "No active direct fire engagement.", {"active": False, "reports": []}
        self.mission.end_reason = "cancelled"
        return self._finish_mission(units, sim_ms, clock_main_line_fn)

    def tick_minute(
        self,
        units: list[dict[str, Any]],
        sim_ms: int,
        clock_main_line_fn: Callable[[int], str],
        in_range_fn: Callable[[dict[str, Any], dict[str, Any]], bool],
    ) -> dict[str, Any]:
        if not self.mission or self.mission.ended_sim_ms is not None:
            return {"active": False, "reports": [], "mission": None}

        victim = next((u for u in units if u["key"] == self.mission.victim_key), None)
        if not victim:
            self.mission.end_reason = "victim_gone"
            return self._finish_mission(units, sim_ms, clock_main_line_fn)[1]

        active_shooters: list[dict[str, Any]] = []
        for sk in self.mission.shooter_keys:
            shooter = next((u for u in units if u["key"] == sk), None)
            if not shooter or not unit_has_direct_fire(shooter):
                continue
            if in_range_fn(shooter, victim):
                active_shooters.append(shooter)

        if not active_shooters:
            self.mission.end_reason = "out_of_range"
            return self._finish_mission(units, sim_ms, clock_main_line_fn)[1]

        minute_sk = 0
        for shooter in active_shooters:
            st = self.mission.shooter_stats.setdefault(
                shooter["key"], ShooterEngagementStats(unit_key=shooter["key"])
            )
            k = self._fire_one_minute(
                shooter,
                victim,
                self.mission.victim_mods_as_target,
                self.mission.victim_losses,
            )
            st.kills += k
            minute_sk += k
            st.ammo_consumed += self._consume_df_ammo(shooter, returning=False)

        minute_vk = 0
        if self.mission.victim_response == "return_fire":
            minute_vk = self._victim_return_fire_minute(
                victim,
                active_shooters,
                self.mission.victim_mods_return,
                rate_mult=1.0,
                dfs_mult=1.0,
            )
            self.mission.victim_ammo_consumed += self._consume_df_ammo(
                victim, returning=True, rate_mult=1.0
            )
        elif self.mission.victim_response == "continue_moving":
            minute_vk = self._victim_return_fire_minute(
                victim,
                active_shooters,
                DFModifiers(),
                rate_mult=1.0 / 3.0,
                dfs_mult=1.0 / 3.0,
            )
            self.mission.victim_ammo_consumed += self._consume_df_ammo(
                victim, returning=True, rate_mult=1.0 / 3.0
            )
        self.mission.victim_kills += minute_vk

        return {
            "active": True,
            "minuteShooterKills": minute_sk,
            "minuteVictimKills": minute_vk,
            "reports": [],
            "mission": self.mission.to_api_dict(),
            "shootersInRange": len(active_shooters),
        }

    def _victim_return_fire_minute(
        self,
        victim: dict[str, Any],
        shooters: list[dict[str, Any]],
        target_mods: DFModifiers,
        *,
        rate_mult: float,
        dfs_mult: float,
    ) -> int:
        if not shooters:
            return 0
        assert self.mission is not None
        dfs = float(victim.get("totalDirectFire") or 0) * dfs_mult
        if dfs <= 1e-9:
            return 0
        if not self._roll_kill_this_minute(dfs, target_mods, rate_mult):
            return 0
        shooter = random.choice(shooters)
        st = self.mission.shooter_stats.setdefault(
            shooter["key"], ShooterEngagementStats(unit_key=shooter["key"])
        )
        if self._apply_one_kill(shooter, st.losses):
            refresh_unit_combat_totals(shooter, self.catalog)
            return 1
        return 0

    def _finish_mission(
        self,
        units: list[dict[str, Any]],
        sim_ms: int,
        clock_main_line_fn: Callable[[int], str],
    ) -> tuple[str | None, dict[str, Any]]:
        assert self.mission is not None
        self.mission.ended_sim_ms = sim_ms
        victim = next((u for u in units if u["key"] == self.mission.victim_key), None)
        reports: list[dict[str, Any]] = []
        if victim:
            time_str = clock_main_line_fn(sim_ms)
            reports.extend(self._end_reports(units, victim, time_str))
        return None, {
            "active": False,
            "reports": reports,
            "mission": self.mission.to_api_dict(),
            "endReason": self.mission.end_reason,
        }

    def _pk_per_minute(self, dfs: float, mods: DFModifiers) -> float:
        base = (DF_KILL_BASE[0] * dfs + DF_KILL_BASE[1]) / DF_KILL_DIVISOR
        return min(1.0, max(0.0, base * mods.loss_multiplier()))

    def _roll_kill_this_minute(
        self, dfs: float, mods: DFModifiers, rate_mult: float = 1.0
    ) -> bool:
        p_k = min(1.0, max(0.0, self._pk_per_minute(dfs, mods) * rate_mult))
        if p_k <= 0.0:
            return False
        return random.random() < p_k

    def _fire_one_minute(
        self,
        shooter: dict[str, Any],
        target: dict[str, Any],
        target_mods: DFModifiers,
        loss_map: dict[str, int],
        *,
        rate_mult: float = 1.0,
        dfs_mult: float = 1.0,
    ) -> int:
        dfs = float(shooter.get("totalDirectFire") or 0) * dfs_mult
        if dfs <= 1e-9:
            return 0
        if not self._roll_kill_this_minute(dfs, target_mods, rate_mult):
            return 0
        if self._apply_one_kill(target, loss_map):
            refresh_unit_combat_totals(target, self.catalog)
            return 1
        return 0

    def _apply_one_kill(self, target: dict[str, Any], loss_map: dict[str, int]) -> bool:
        specs = target.get("equipmentSpecs") or []
        pool: list[str] = []
        for spec in specs:
            name = spec["name"]
            count = int(spec.get("count") or 0)
            if count <= 0:
                continue
            for _ in range(count):
                pool.append(name)
        if not pool:
            return False
        name = random.choice(pool)
        for spec in specs:
            if spec["name"] == name and int(spec.get("count") or 0) > 0:
                spec["count"] = int(spec["count"]) - 1
                lose_equipment_ammunition(target, name, 1, self.catalog)
                loss_map[name] = loss_map.get(name, 0) + 1
                return True
        return False

    def _unit_df_ammo_per_min(
        self, unit: dict[str, Any], *, returning: bool
    ) -> float:
        total = 0.0
        for spec in unit.get("equipmentSpecs") or []:
            n = int(spec.get("count") or 0)
            if n <= 0:
                continue
            man = self.catalog.get_maneuver(spec["name"])
            if man and man.df_score > 0:
                rate = (
                    man.ammo_tons_per_min_returning_fire
                    if returning
                    else man.ammo_tons_per_min_direct_fire
                )
                total += n * rate
        return total

    def _consume_df_ammo(
        self,
        unit: dict[str, Any],
        *,
        returning: bool,
        rate_mult: float = 1.0,
    ) -> float:
        need = self._unit_df_ammo_per_min(unit, returning=returning) * rate_mult
        if need <= 0:
            return 0.0
        on_hand = float(unit.get("ammoOnHand") or 0)
        consumed = min(need, on_hand)
        unit["ammoOnHand"] = on_hand - consumed
        return consumed

    def _format_losses(self, loss_map: dict[str, int]) -> str:
        if not loss_map:
            return "None"
        return "; ".join(f"{n} {name}" for name, n in sorted(loss_map.items()))

    def _end_reports(
        self,
        units: list[dict[str, Any]],
        victim: dict[str, Any],
        time_str: str,
    ) -> list[dict[str, Any]]:
        assert self.mission is not None
        reports: list[dict[str, Any]] = []
        for sk, st in self.mission.shooter_stats.items():
            shooter = next((u for u in units if u["key"] == sk), None)
            if not shooter:
                continue
            auth = float(shooter.get("ammoAuthorized") or 0)
            on_hand = float(shooter.get("ammoOnHand") or 0)
            pct = ammo_percent(auth, on_hand)
            reports.append(
                self.reports.direct_fire_after_action(
                    shooter.get("battalion") or "ALL STATIONS",
                    shooter.get("company") or sk,
                    time_str,
                    role="shooter",
                    dfs=format_score(float(shooter.get("totalDirectFire") or 0)),
                    ammo_authorized=auth,
                    ammo_on_hand=on_hand,
                    ammo_percent=pct,
                    ammo_consumed=st.ammo_consumed,
                    kills_inflicted=st.kills,
                    losses=self._format_losses(st.losses),
                    unit_key=sk,
                    opponent_key=victim["key"],
                )
            )
        auth_v = float(victim.get("ammoAuthorized") or 0)
        on_hand_v = float(victim.get("ammoOnHand") or 0)
        pct_v = ammo_percent(auth_v, on_hand_v)
        reports.append(
            self.reports.direct_fire_after_action(
                victim.get("battalion") or "ALL STATIONS",
                victim.get("company") or victim["key"],
                time_str,
                role="victim",
                dfs=format_score(float(victim.get("totalDirectFire") or 0)),
                ammo_authorized=auth_v,
                ammo_on_hand=on_hand_v,
                ammo_percent=pct_v,
                ammo_consumed=self.mission.victim_ammo_consumed,
                kills_inflicted=self.mission.victim_kills,
                losses=self._format_losses(self.mission.victim_losses),
                unit_key=victim["key"],
                opponent_key=", ".join(self.mission.shooter_keys),
            )
        )
        return reports
