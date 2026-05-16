"""Pointy-top axial hex grid ↔ WGS84."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from backend.config import AXIAL_NEIGHBORS, DEFAULT_HEX_ORIGIN_LAT, DEFAULT_HEX_ORIGIN_LON, R_M


@dataclass
class Axial:
    q: int
    r: int


class HexGrid:
    def __init__(self, origin_lat: float = DEFAULT_HEX_ORIGIN_LAT, origin_lon: float = DEFAULT_HEX_ORIGIN_LON) -> None:
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon

    @staticmethod
    def hex_key(q: int, r: int) -> str:
        return f"{q},{r}"

    @staticmethod
    def parse_hex_key(key: str) -> Axial:
        parts = key.split(",")
        return Axial(int(parts[0]), int(parts[1]))

    def meters_per_degree(self, lat_deg: float) -> tuple[float, float]:
        lat_rad = math.radians(lat_deg)
        return 111_320.0, 111_320.0 * math.cos(lat_rad)

    def axial_to_xy(self, q: int, r: int) -> tuple[float, float]:
        x = R_M * math.sqrt(3) * (q + r / 2)
        y = R_M * (3 / 2) * r
        return x, y

    def meters_to_lat_lon(self, x: float, y: float) -> tuple[float, float]:
        scale_lat, scale_lon = self.meters_per_degree(self.origin_lat)
        lat = self.origin_lat + y / scale_lat
        lon = self.origin_lon + x / scale_lon
        return lat, lon

    def axial_center_lat_lon(self, q: int, r: int) -> tuple[float, float]:
        x, y = self.axial_to_xy(q, r)
        return self.meters_to_lat_lon(x, y)

    @staticmethod
    def _cube_round(x: float, y: float, z: float) -> tuple[int, int, int]:
        rx, ry, rz = round(x), round(y), round(z)
        x_diff, y_diff, z_diff = abs(rx - x), abs(ry - y), abs(rz - z)
        if x_diff > y_diff and x_diff > z_diff:
            rx = -ry - rz
        elif y_diff > z_diff:
            ry = -rx - rz
        else:
            rz = -rx - ry
        return int(rx), int(ry), int(rz)

    def lat_lon_to_axial(self, lat: float, lon: float) -> Axial:
        scale_lat, scale_lon = self.meters_per_degree(self.origin_lat)
        x = (lon - self.origin_lon) * scale_lon
        y = (lat - self.origin_lat) * scale_lat
        fq = (math.sqrt(3) / 3 * x - (1 / 3) * y) / R_M
        fr = (2 / 3 * y) / R_M
        x_c, z_c = fq, fr
        y_c = -fq - fr
        rx, _, rz = self._cube_round(x_c, y_c, z_c)
        return Axial(rx, rz)

    def lat_lon_to_hex_key(self, lat: float, lon: float) -> str:
        a = self.lat_lon_to_axial(lat, lon)
        return self.hex_key(a.q, a.r)

    def axial_distance(self, q1: int, r1: int, q2: int, r2: int) -> int:
        return (abs(q1 - q2) + abs(q1 + r1 - q2 - r2) + abs(r1 - r2)) // 2

    def hex_key_distance(self, key_a: str, key_b: str) -> int:
        a, b = self.parse_hex_key(key_a), self.parse_hex_key(key_b)
        return self.axial_distance(a.q, a.r, b.q, b.r)

    def distance_meters(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        scale_lat, scale_lon = self.meters_per_degree((lat1 + lat2) / 2)
        dy = (lat2 - lat1) * scale_lat
        dx = (lon2 - lon1) * scale_lon
        return math.hypot(dx, dy)

    def distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return self.distance_meters(lat1, lon1, lat2, lon2) / 1000.0

    def hex_vertices_lat_lon(self, q: int, r: int) -> list[list[float]]:
        cx, cy = self.axial_to_xy(q, r)
        ring: list[list[float]] = []
        for k in range(6):
            angle = math.pi / 2 + k * math.pi / 3
            mx = cx + R_M * math.cos(angle)
            my = cy + R_M * math.sin(angle)
            lat, lon = self.meters_to_lat_lon(mx, my)
            ring.append([lat, lon])
        ring.append(ring[0][:])
        return ring

    def direction_index(self, from_key: str, to_key: str) -> int:
        f, t = self.parse_hex_key(from_key), self.parse_hex_key(to_key)
        dq, dr = t.q - f.q, t.r - f.r
        for i, (ndq, ndr) in enumerate(AXIAL_NEIGHBORS):
            if ndq == dq and ndr == dr:
                return i
        return -1

    def neighbor_key(self, hex_key: str, direction: int) -> str:
        a = self.parse_hex_key(hex_key)
        dq, dr = AXIAL_NEIGHBORS[direction]
        return self.hex_key(a.q + dq, a.r + dr)

    def hex_center_in_play(self, lat: float, lon: float, play_bounds: dict) -> bool:
        return (
            play_bounds["south"] <= lat <= play_bounds["north"]
            and play_bounds["west"] <= lon <= play_bounds["east"]
        )
