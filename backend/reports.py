"""Radio / written reports (spot, indirect fire, SHELREP, future EW)."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ReportType(str, Enum):
    RECEIVING_IF = "receiving_indirect_fire"
    SHELREP = "shelrep"
    SPOT = "spot"
    AMMO_STATUS = "ammunition_status"
    DIRECT_FIRE_AAR = "direct_fire_after_action"
    GENERIC = "generic"


class ReportService:
    """Creates structured reports for UI and future jamming / ESM hooks."""

    @staticmethod
    def create_report(
        report_type: ReportType | str,
        *,
        text: str,
        title: str = "Message",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rtype = report_type.value if isinstance(report_type, ReportType) else str(report_type)
        return {
            "type": rtype,
            "title": title,
            "text": text,
            "meta": meta or {},
        }

    def receiving_indirect_fire(
        self,
        addressee: str,
        caller: str,
        time_str: str,
        *,
        target_key: str | None = None,
    ) -> dict[str, Any]:
        text = f"{addressee} this is {caller}, receiving indirect fire time {time_str}"
        return self.create_report(
            ReportType.RECEIVING_IF,
            title="Incoming indirect fire",
            text=text,
            meta={"addressee": addressee, "caller": caller, "time": time_str, "targetKey": target_key},
        )

    def shelrep(
        self,
        addressee: str,
        caller: str,
        *,
        mgrs: str,
        time_from: str,
        time_to: str,
        guns_line: str,
        rounds_line: str,
        damage_line: str,
        target_key: str | None = None,
    ) -> dict[str, Any]:
        lines = [
            f"{addressee} this is {caller}, SHELREP follows, break.",
            f"Unit position: {mgrs}",
            f"Time from: {time_from}",
            f"Time to: {time_to}",
            f"Number and type of guns: {guns_line}",
            f"Number and type of rounds: {rounds_line}",
            f"Damage: {damage_line}",
        ]
        return self.create_report(
            ReportType.SHELREP,
            title="SHELREP",
            text="\n".join(lines),
            meta={
                "addressee": addressee,
                "caller": caller,
                "mgrs": mgrs,
                "timeFrom": time_from,
                "timeTo": time_to,
                "gunsLine": guns_line,
                "roundsLine": rounds_line,
                "damageLine": damage_line,
                "targetKey": target_key,
            },
        )

    def ammunition_status(
        self,
        addressee: str,
        caller: str,
        time_str: str,
        *,
        authorized: float,
        on_hand: float,
        percent: float | None,
        unit_key: str | None = None,
    ) -> dict[str, Any]:
        from backend.combat import format_ammo_percent, format_ammo_tons

        pct_str = format_ammo_percent(percent)
        text = (
            f"{addressee} this is {caller}, ammunition report time {time_str}, break. "
            f"Ammunition {format_ammo_tons(authorized)} tons authorized, "
            f"{format_ammo_tons(on_hand)} tons on hand, {pct_str} remaining."
        )
        return self.create_report(
            ReportType.AMMO_STATUS,
            title="Ammunition status",
            text=text,
            meta={
                "addressee": addressee,
                "caller": caller,
                "time": time_str,
                "ammoAuthorized": authorized,
                "ammoOnHand": on_hand,
                "ammoPercent": percent,
                "unitKey": unit_key,
            },
        )

    def direct_fire_after_action(
        self,
        addressee: str,
        caller: str,
        time_str: str,
        *,
        role: str,
        dfs: str,
        ammo_authorized: float,
        ammo_on_hand: float,
        ammo_percent: float | None,
        ammo_consumed: float,
        kills_inflicted: int,
        losses: str,
        unit_key: str | None = None,
        opponent_key: str | None = None,
    ) -> dict[str, Any]:
        from backend.combat import format_ammo_percent, format_ammo_tons

        text = (
            f"{addressee} this is {caller}, direct fire after-action {role} time {time_str}, break. "
            f"Direct fire score {dfs}. "
            f"Ammunition {format_ammo_tons(ammo_authorized)} tons authorized, "
            f"{format_ammo_tons(ammo_on_hand)} tons on hand ({format_ammo_percent(ammo_percent)}), "
            f"{format_ammo_tons(ammo_consumed)} tons expended this engagement. "
            f"Kills inflicted: {kills_inflicted}. Losses: {losses}."
        )
        return self.create_report(
            ReportType.DIRECT_FIRE_AAR,
            title=f"Direct fire report — {caller}",
            text=text,
            meta={
                "addressee": addressee,
                "caller": caller,
                "time": time_str,
                "role": role,
                "dfs": dfs,
                "ammoAuthorized": ammo_authorized,
                "ammoOnHand": ammo_on_hand,
                "ammoPercent": ammo_percent,
                "ammoConsumed": ammo_consumed,
                "killsInflicted": kills_inflicted,
                "losses": losses,
                "unitKey": unit_key,
                "opponentKey": opponent_key,
            },
        )
