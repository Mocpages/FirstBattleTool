"""Artillery emplacement, rate-of-fire, and time-to-fire calculations."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from backend.config import MINUTE_MS
from math import ceil

if TYPE_CHECKING:
    from backend.combat import ArtilleryStats

MAX_RATE_BURST_MINUTES = 3
EXHAUSTION_RECOVERY_MINUTES = 10


def time_in_position_ms(unit: dict[str, Any], sim_ms: int) -> int:
    if unit.get("activity") != "halted":
        return 0
    since = unit.get("positionSinceSimMs")
    if since is None:
        return 0
    return max(0, sim_ms - int(since))


def time_in_position_minutes(unit: dict[str, Any], sim_ms: int) -> float:
    return time_in_position_ms(unit, sim_ms) / MINUTE_MS


def is_artillery_emplaced(unit: dict[str, Any], art: "ArtilleryStats", sim_ms: int) -> bool:
    if unit.get("activity") != "halted":
        return False
    return time_in_position_minutes(unit, sim_ms) + 1e-9 >= art.emplacement_time_min


def emplacement_wait_minutes(unit: dict[str, Any], art: "ArtilleryStats", sim_ms: int) -> float:
    if unit.get("activity") != "halted":
        return float(art.emplacement_time_min)
    wait = art.emplacement_time_min - time_in_position_minutes(unit, sim_ms)
    return max(0.0, wait)


def compute_fire_duration_minutes(
    volleys: int,
    tubes_per_volley: int,
    max_rof_per_tube: float,
    sustained_rof_per_tube: float,
    exhausted: bool,
) -> tuple[float, bool]:
    """
    Minutes to fire `volleys` (UI rounds = volleys; each volley fires one round per tube in parallel).

    Rates are rounds per tube per minute. Battery throughput = rate * tubes_per_volley.
    One volley takes 1 / rate minutes (all tubes fire together).
    """
    if volleys <= 0 or tubes_per_volley <= 0:
        return 0.0, False
    sustained = sustained_rof_per_tube if sustained_rof_per_tube > 0 else 1.0
    max_rate = max_rof_per_tube if max_rof_per_tube > 0 else sustained

    minutes_per_volley_max = 1.0 / max_rate
    minutes_per_volley_sustained = 1.0 / sustained

    if exhausted:
        return ceil(volleys * minutes_per_volley_sustained), False

    # Each tube may fire at max rate for MAX_RATE_BURST_MINUTES (volleys = rounds per tube).
    max_volleys_at_max = int(MAX_RATE_BURST_MINUTES * max_rate)
    volleys_at_max = min(volleys, max_volleys_at_max)
    minutes_at_max = volleys_at_max * minutes_per_volley_max
    sustained_volleys = volleys - volleys_at_max
    minutes_sustained = sustained_volleys * minutes_per_volley_sustained
    used_max = volleys_at_max > 0
    return ceil(minutes_at_max + minutes_sustained), used_max


def compute_time_to_fire(
    unit: dict[str, Any],
    art: "ArtilleryStats",
    tube_count: int,
    rounds_per_tube: int,
    sim_ms: int,
) -> dict[str, Any]:
    volleys = max(0, rounds_per_tube)
    tubes = max(0, tube_count)
    total_rounds = tubes * volleys
    exhausted = bool(unit.get("ifExhausted"))
    emplace_wait = emplacement_wait_minutes(unit, art, sim_ms)
    emplaced = is_artillery_emplaced(unit, art, sim_ms)
    fire_min, used_max = compute_fire_duration_minutes(
        volleys,
        tubes,
        art.max_rate_of_fire,
        art.sustained_rate_of_fire,
        exhausted,
    )
    total_min = emplace_wait + fire_min
    battery_max_rpm = art.max_rate_of_fire * tubes if tubes > 0 else 0.0
    battery_sustained_rpm = art.sustained_rate_of_fire * tubes if tubes > 0 else 0.0
    return {
        "emplacementWaitMin": round(emplace_wait, 2),
        "fireDurationMin": round(fire_min, 2),
        "timeToFireMin": round(total_min, 2),
        "canFire": emplaced and volleys > 0,
        "emplaced": emplaced,
        "exhausted": exhausted,
        "usedMaxRate": used_max,
        "totalRounds": total_rounds,
        "volleys": volleys,
        "batteryMaxRoundsPerMin": round(battery_max_rpm, 2),
        "batterySustainedRoundsPerMin": round(battery_sustained_rpm, 2),
    }


def tick_exhaustion_recovery(units: list[dict[str, Any]], sim_ms: int) -> None:
    recovery_ms = EXHAUSTION_RECOVERY_MINUTES * MINUTE_MS
    for u in units:
        if not u.get("ifExhausted"):
            continue
        cease = u.get("ifCeaseFireSimMs")
        if cease is None:
            continue
        if sim_ms - int(cease) >= recovery_ms:
            u["ifExhausted"] = False
            u["ifCeaseFireSimMs"] = None


def apply_fire_mission(
    unit: dict[str, Any],
    art: "ArtilleryStats",
    tube_count: int,
    rounds_per_tube: int,
    sim_ms: int,
) -> None:
    exhausted = bool(unit.get("ifExhausted"))
    _, used_max = compute_fire_duration_minutes(
        max(0, rounds_per_tube),
        max(0, tube_count),
        art.max_rate_of_fire,
        art.sustained_rate_of_fire,
        exhausted,
    )
    unit["ifCeaseFireSimMs"] = sim_ms
    if used_max:
        unit["ifExhausted"] = True
