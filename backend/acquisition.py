"""Spotting / acquisition event detection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.config import PROXIMITY_ACQUISITION_HEX_DISTANCE

if TYPE_CHECKING:
    from backend.hex_grid import HexGrid
    from backend.zone_control import ZoneOfControlService


@dataclass
class AcquisitionEvent:
    spotter_key: str
    target_key: str
    entered_hex_key: str
    from_hex_key: str
    spot_kind: str  # "los" | "proximity"


class AcquisitionService:
    def __init__(self, grid: "HexGrid", zoc: "ZoneOfControlService") -> None:
        self.grid = grid
        self.zoc = zoc
        self._silenced: set[str] = set()

    @staticmethod
    def silence_key(target_key: str, hex_key: str, spotter_key: str) -> str:
        return f"{target_key}|{hex_key}|{spotter_key}"

    def clear_silence_for_target_hex(self, target_key: str, hex_key: str) -> None:
        prefix = f"{target_key}|{hex_key}|"
        self._silenced = {k for k in self._silenced if not k.startswith(prefix)}

    def clear_silence_when_mover_leaves(self, mover_key: str, from_hex_key: str) -> None:
        self.clear_silence_for_target_hex(mover_key, from_hex_key)
        tail = f"|{from_hex_key}|{mover_key}"
        self._silenced = {k for k in self._silenced if not k.endswith(tail)}

    def mark_silenced(self, target_key: str, hex_key: str, spotter_key: str) -> None:
        self._silenced.add(self.silence_key(target_key, hex_key, spotter_key))

    def is_silenced(self, target_key: str, hex_key: str, spotter_key: str) -> bool:
        return self.silence_key(target_key, hex_key, spotter_key) in self._silenced

    def check_hex_entry(
        self,
        units: list[dict[str, Any]],
        mover: dict[str, Any],
        from_hex_key: str,
        entered_hex_key: str,
        queue_keys: set[tuple[str, str, str, str]],
    ) -> list[AcquisitionEvent]:
        events: list[AcquisitionEvent] = []
        mover_key = mover["key"]
        self.clear_silence_when_mover_leaves(mover_key, from_hex_key)

        for spotter in units:
            if spotter["side"] == mover["side"]:
                continue
            los_cells = self.zoc.los_area_for_unit(spotter["lat"], spotter["lon"], spotter["activity"])
            if entered_hex_key in los_cells:
                sk = spotter["key"]
                sil = self.silence_key(mover_key, entered_hex_key, sk)
                if sil not in self._silenced:
                    dedup = (mover_key, entered_hex_key, sk, "los")
                    if dedup not in queue_keys:
                        queue_keys.add(dedup)
                        events.append(
                            AcquisitionEvent(sk, mover_key, entered_hex_key, from_hex_key, "los")
                        )

        for enemy in units:
            if enemy["side"] == mover["side"] or enemy["activity"] != "halted":
                continue
            enemy_hex = self.grid.lat_lon_to_hex_key(enemy["lat"], enemy["lon"])
            if self.grid.hex_key_distance(entered_hex_key, enemy_hex) > PROXIMITY_ACQUISITION_HEX_DISTANCE:
                continue
            sk = mover["key"]
            tk = enemy["key"]
            sil = self.silence_key(tk, entered_hex_key, sk)
            if sil in self._silenced:
                continue
            dedup = (tk, entered_hex_key, sk, "proximity")
            if dedup not in queue_keys:
                queue_keys.add(dedup)
                events.append(
                    AcquisitionEvent(sk, tk, entered_hex_key, from_hex_key, "proximity")
                )
        return events
