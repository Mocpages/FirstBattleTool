"""Shared simulation constants."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FLAT_TO_FLAT_M = 1000.0
R_M = FLAT_TO_FLAT_M / (3**0.5)
LOS_BUFFER_PAST_ZOC_M = 3000.0
PROXIMITY_ACQUISITION_HEX_DISTANCE = 3
MINUTE_MS = 60 * 1000
DEFAULT_MINUTES_PER_STEP = 5
MP_PER_MINUTE = 0.2
MINUTES_PER_STEP_MIN = 1
MINUTES_PER_STEP_MAX = 120

PLAY_BOUNDS = {
    "north": 50.919,
    "south": 49.92,
    "west": 8.145,
    "east": 11.584,
}

DEFAULT_HEX_ORIGIN_LAT = 50.55
DEFAULT_HEX_ORIGIN_LON = 9.675

FULDA_SUN_LAT = 50.55
FULDA_SUN_LON = 9.675
FULDA_OFFSET_MS = 2 * 60 * 60 * 1000

AXIAL_NEIGHBORS = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)
