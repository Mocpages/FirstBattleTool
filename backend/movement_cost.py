"""Movement cost raster (mv_cost.tif) lookup."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from backend.config import ROOT
from backend.hex_grid import HexGrid


@dataclass
class RasterMeta:
    loaded: bool
    west: float | None = None
    south: float | None = None
    east: float | None = None
    north: float | None = None
    failure_reason: str = ""


class MovementCostRaster:
    def __init__(self, grid: HexGrid, tif_path: Path | None = None) -> None:
        self.grid = grid
        self.tif_path = tif_path or (ROOT / "mv_cost.tif")
        self._data: np.ndarray | None = None
        self._bounds: tuple[float, float, float, float] | None = None
        self._nodata: float | None = None
        self.meta = RasterMeta(loaded=False)

    def load(self) -> bool:
        if not self.tif_path.is_file():
            self.meta = RasterMeta(loaded=False, failure_reason=f"File not found: {self.tif_path}")
            return False
        try:
            import rasterio

            with rasterio.open(self.tif_path) as ds:
                self._data = ds.read(1).astype(np.float64)
                b = ds.bounds
                self._bounds = (b.left, b.bottom, b.right, b.top)
                self._nodata = ds.nodata
                if self._nodata is not None:
                    self._nodata = float(self._nodata)
                self.meta = RasterMeta(
                    loaded=True,
                    west=b.left,
                    south=b.bottom,
                    east=b.right,
                    north=b.top,
                )
                return True
        except Exception as exc:  # noqa: BLE001
            self.meta = RasterMeta(loaded=False, failure_reason=str(exc))
            self._data = None
            return False

    def _raw_at_lat_lon(self, lat: float, lon: float) -> float | None:
        if self._data is None or self._bounds is None:
            return None
        west, south, east, north = self._bounds
        if not (west <= lon <= east and south <= lat <= north):
            return None
        h, w = self._data.shape
        col = (lon - west) / (east - west) * (w - 1) if east > west else 0
        row = (north - lat) / (north - south) * (h - 1) if north > south else 0
        ci = int(round(col))
        ri = int(round(row))
        if ri < 0 or ri >= h or ci < 0 or ci >= w:
            return None
        val = float(self._data[ri, ci])
        if self._nodata is not None and (val == self._nodata or np.isnan(val)):
            return None
        return val

    def mv_at_lat_lon(self, lat: float, lon: float) -> float | None:
        """Exact raster MP at hex centre (not rounded)."""
        raw = self._raw_at_lat_lon(lat, lon)
        if raw is None or raw < 0:
            return None
        return raw

    def mv_at_hex_key(self, hex_key: str) -> float | None:
        a = self.grid.parse_hex_key(hex_key)
        lat, lon = self.grid.axial_center_lat_lon(a.q, a.r)
        return self.mv_at_lat_lon(lat, lon)

    def hex_has_raster(self, hex_key: str) -> bool:
        return self.mv_at_hex_key(hex_key) is not None

    def exit_move_cost(self, from_key: str, direction: int) -> float | None:
        n_key = self.grid.neighbor_key(from_key, direction)
        if not self.hex_has_raster(from_key) or not self.hex_has_raster(n_key):
            return None
        return self.mv_at_hex_key(n_key)

    def segment_move_cost(self, from_key: str, to_key: str) -> float | None:
        di = self.grid.direction_index(from_key, to_key)
        if di < 0:
            return None
        return self.exit_move_cost(from_key, di)

    def hexes_in_bounds(self, west: float, south: float, east: float, north: float) -> list[dict[str, Any]]:
        """Sample hex centres in view for terrain overlay."""
        grid = self.grid
        scale_lat, scale_lon = grid.meters_per_degree(grid.origin_lat)
        from backend.config import FLAT_TO_FLAT_M, PLAY_BOUNDS, R_M

        pad = FLAT_TO_FLAT_M * 2
        x_min = (west - grid.origin_lon) * scale_lon - pad
        x_max = (east - grid.origin_lon) * scale_lon + pad
        y_min = (south - grid.origin_lat) * scale.lat - pad
        y_max = (north - grid.origin_lat) * scale.lat + pad
        horiz = R_M * math.sqrt(3)
        vert = R_M * 1.5
        q_min = int(math.floor(x_min / horiz)) - 2
        q_max = int(math.ceil(x_max / horiz)) + 2
        r_min = int(math.floor(y_min / vert)) - 2
        r_max = int(math.ceil(y_max / vert)) + 2
        out: list[dict[str, Any]] = []
        for q in range(q_min, q_max + 1):
            for r in range(r_min, r_max + 1):
                cx, cy = grid.axial_to_xy(q, r)
                if cx < x_min or cx > x_max or cy < y_min or cy > y_max:
                    continue
                lat, lon = grid.axial_center_lat_lon(q, r)
                if not grid.hex_center_in_play(lat, lon, PLAY_BOUNDS):
                    continue
                mv = self.mv_at_lat_lon(lat, lon)
                if mv is None:
                    continue
                out.append(
                    {
                        "key": grid.hex_key(q, r),
                        "q": q,
                        "r": r,
                        "mv": mv,
                    }
                )
        return out
