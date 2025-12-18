from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Iterable

import numpy as np

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1r = radians(lat1)
    lon1r = radians(lon1)
    lat2r = radians(lat2)
    lon2r = radians(lon2)
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = sin(dlat / 2) ** 2 + cos(lat1r) * cos(lat2r) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return EARTH_RADIUS_KM * c


def latlon_to_ecef_km(lat: float, lon: float) -> np.ndarray:
    lat_r = radians(lat)
    lon_r = radians(lon)
    x = cos(lat_r) * cos(lon_r)
    y = cos(lat_r) * sin(lon_r)
    z = sin(lat_r)
    return EARTH_RADIUS_KM * np.array([x, y, z], dtype=float)


@dataclass(frozen=True)
class Station:
    station_id: str
    source: str
    lat: float
    lon: float
    elev: float | None = None


def chord_km_for_radius(radius_km: float) -> float:
    radius_km = float(radius_km)
    return 2.0 * EARTH_RADIUS_KM * sin(radius_km / (2.0 * EARTH_RADIUS_KM))


def build_station_ecef_matrix(stations: Iterable[Station]) -> np.ndarray:
    rows = [latlon_to_ecef_km(s.lat, s.lon) for s in stations]
    return np.vstack(rows) if rows else np.zeros((0, 3), dtype=float)

