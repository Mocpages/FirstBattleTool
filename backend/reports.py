"""Radio / written reports (spot, indirect fire, SHELREP, future EW)."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ReportType(str, Enum):
    RECEIVING_IF = "receiving_indirect_fire"
    SHELREP = "shelrep"
    SPOT = "spot"
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
