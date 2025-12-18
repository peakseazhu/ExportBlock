from __future__ import annotations

from dataclasses import dataclass

from scipy.spatial import cKDTree

from exportblock.util.geo import Station, build_station_ecef_matrix, chord_km_for_radius, haversine_km, latlon_to_ecef_km


@dataclass(frozen=True)
class SpatialQueryResult:
    station: Station
    distance_km: float


class SpatialIndex:
    def __init__(self, stations: list[Station]):
        self._stations = list(stations)
        self._xyz = build_station_ecef_matrix(self._stations)
        self._tree = cKDTree(self._xyz) if self._xyz.size else None

    @property
    def station_count(self) -> int:
        return len(self._stations)

    def query_radius(self, *, lat: float, lon: float, radius_km: float) -> list[SpatialQueryResult]:
        if not self._stations or self._tree is None:
            return []
        center = latlon_to_ecef_km(lat, lon)
        chord = chord_km_for_radius(radius_km)
        idxs = self._tree.query_ball_point(center, r=chord)
        results: list[SpatialQueryResult] = []
        for idx in idxs:
            st = self._stations[int(idx)]
            d = haversine_km(lat, lon, st.lat, st.lon)
            if d <= radius_km:
                results.append(SpatialQueryResult(station=st, distance_km=float(d)))
        results.sort(key=lambda r: r.distance_km)
        return results

