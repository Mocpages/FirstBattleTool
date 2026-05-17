"""Battalion column stationkeeping — shared route, lead sets pace."""
from __future__ import annotations

from typing import Any, Literal, Optional

from backend.hex_grid import HexGrid

DEFAULT_MAX_LAG_HEX = 1
DEFAULT_MAX_LEAD_HEX = 1

StationStatus = Literal["behind", "on_station", "ahead", "waiting"]


def path_progress_index(grid: HexGrid, ref_path: list[str], u: dict[str, Any]) -> int:
    if not ref_path:
        return 0
    hk = u.get("positionHexKey") or grid.lat_lon_to_hex_key(u["lat"], u["lon"])
    if hk in ref_path:
        return ref_path.index(hk)
    best_i = 0
    best_d = 10**9
    for i, pk in enumerate(ref_path):
        d = grid.hex_key_distance(hk, pk)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def formation_lead(members: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for m in members:
        if (m.get("bnFormation") or {}).get("isLead"):
            return m
    return None


def column_target_hex(
    ref_path: list[str],
    lead_progress: int,
    lead_slot: int,
    follower_slot: int,
) -> str:
    offset = lead_slot - follower_slot
    idx = max(0, min(len(ref_path) - 1, lead_progress - offset))
    return ref_path[idx]


def allowed_progress(
    lead_progress: int,
    lead_slot: int,
    follower_slot: int,
) -> int:
    return max(0, lead_progress - (lead_slot - follower_slot))


def formation_has_stragglers(
    grid: HexGrid,
    ref_path: list[str],
    lead: dict[str, Any],
    members: list[dict[str, Any]],
) -> bool:
    """True if any follower is behind its assigned station on the column."""
    bf = lead.get("bnFormation") or {}
    lead_slot = int(bf.get("pathSlot", len(ref_path) - 1))
    lead_prog = path_progress_index(grid, ref_path, lead)
    max_lag = int(bf.get("maxLagHex", DEFAULT_MAX_LAG_HEX))
    for m in members:
        mbf = m.get("bnFormation") or {}
        if mbf.get("isLead"):
            continue
        f_slot = int(mbf.get("pathSlot", 0))
        need = allowed_progress(lead_prog, lead_slot, f_slot)
        actual = path_progress_index(grid, ref_path, m)
        if actual < need - max_lag:
            return True
    return False


def unit_station_status(
    grid: HexGrid,
    ref_path: list[str],
    u: dict[str, Any],
    lead: dict[str, Any],
    members: list[dict[str, Any]],
) -> StationStatus:
    bf = u.get("bnFormation") or {}
    lead_bf = lead.get("bnFormation") or {}
    lead_slot = int(lead_bf.get("pathSlot", len(ref_path) - 1))
    lead_prog = path_progress_index(grid, ref_path, lead)
    max_lag = int(lead_bf.get("maxLagHex", DEFAULT_MAX_LAG_HEX))
    max_lead = int(bf.get("maxLeadHex", DEFAULT_MAX_LEAD_HEX))

    if bf.get("isLead"):
        if formation_has_stragglers(grid, ref_path, lead, members):
            return "waiting"
        return "on_station"

    f_slot = int(bf.get("pathSlot", 0))
    need = allowed_progress(lead_prog, lead_slot, f_slot)
    actual = path_progress_index(grid, ref_path, u)
    if actual < need - max_lag:
        return "behind"
    if actual > need + max_lead:
        return "ahead"
    if formation_has_stragglers(grid, ref_path, lead, members):
        return "on_station"
    return "on_station"


def sync_formation_column(
    grid: HexGrid,
    lead: dict[str, Any],
    members: list[dict[str, Any]],
    pathfinder_rebuild: Any,
) -> bool:
    """
    Update follower goals; resume movement for units still catching up.
    Returns True while the column is waiting (stragglers exist).
    """
    bf = lead.get("bnFormation") or {}
    ref_path: list[str] = bf.get("refPath") or []
    if len(ref_path) < 2 or len(members) < 2:
        return False

    lead_slot = int(bf.get("pathSlot", len(ref_path) - 1))
    lead_prog = path_progress_index(grid, ref_path, lead)

    for m in members:
        mbf = m.get("bnFormation") or {}
        if mbf.get("isLead"):
            continue
        f_slot = int(mbf.get("pathSlot", 0))
        target = column_target_hex(ref_path, lead_prog, lead_slot, f_slot)
        if m.get("destinationKey") != target:
            pathfinder_rebuild(m, target)
        status = unit_station_status(grid, ref_path, m, lead, members)
        if status == "behind" and m.get("activity") == "halted":
            m["activity"] = "moving"

    return formation_has_stragglers(grid, ref_path, lead, members)


def effective_mp_for_formation(
    grid: HexGrid,
    u: dict[str, Any],
    members: list[dict[str, Any]],
    base_mp: float,
) -> float:
    """
    Lagging units keep moving until on station.
    Units already on station (and the lead) wait until the column can roll.
    """
    bf = u.get("bnFormation")
    if not bf:
        return base_mp
    if bf.get("movementType") == "emergency":
        return base_mp if u.get("activity") == "moving" else 0.0
    if u.get("activity") != "moving":
        return 0.0

    ref_path: list[str] = bf.get("refPath") or []
    if len(ref_path) < 2:
        return base_mp

    lead = formation_lead(members)
    if not lead:
        return base_mp

    status = unit_station_status(grid, ref_path, u, lead, members)
    if status == "behind":
        return base_mp * 1.15
    if status == "ahead":
        return 0.0
    if status == "waiting":
        return 0.0
    if bf.get("isLead") and not formation_has_stragglers(
        grid, ref_path, lead, members
    ):
        return base_mp
    return 0.0


def clear_formation(u: dict[str, Any]) -> None:
    u.pop("bnFormation", None)
