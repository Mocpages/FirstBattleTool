"""Parse MGRS and lat/lon coordinate strings."""
from __future__ import annotations

import re
from typing import Optional

_LATLON_PAIR = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$"
)
_LATLON_DMS = re.compile(
    r"^\s*([NnSs])\s*(\d+(?:\.\d+)?)\s*[,;\s]\s*([EeWw])\s*(-?\d+(?:\.\d+)?)\s*$"
)


def parse_coordinate(text: str) -> Optional[tuple[float, float]]:
    """Return (lat, lon) or None if unparseable."""
    raw = (text or "").strip()
    if not raw:
        return None
    mgrs_latlon = _try_mgrs(raw)
    if mgrs_latlon:
        return mgrs_latlon
    m = _LATLON_DMS.match(raw)
    if m:
        ns, lat_v, ew, lon_v = m.group(1), float(m.group(2)), m.group(3), float(m.group(4))
        lat = lat_v if ns.upper() == "N" else -lat_v
        lon = lon_v if ew.upper() == "E" else -lon_v
        return lat, lon
    m2 = _LATLON_PAIR.match(raw)
    if m2:
        a, b = float(m2.group(1)), float(m2.group(2))
        if abs(a) <= 90 and abs(b) <= 180:
            return a, b
        if abs(b) <= 90 and abs(a) <= 180:
            return b, a
    return None


def _try_mgrs(raw: str) -> Optional[tuple[float, float]]:
    compact = re.sub(r"\s+", "", raw.upper())
    try:
        import mgrs as mgrs_mod

        m = mgrs_mod.MGRS()
        lat, lon = m.toLatLon(compact)
        return float(lat), float(lon)
    except Exception:
        pass
    try:
        import mgrs as mgrs_mod

        lat, lon = mgrs_mod.toLatLon(compact)
        return float(lat), float(lon)
    except Exception:
        return None
