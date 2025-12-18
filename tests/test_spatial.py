from __future__ import annotations

from exportblock.spatial.index import SpatialIndex
from exportblock.util.geo import Station


def test_spatial_index_radius_query():
    stations = [
        Station(station_id="A", source="x", lat=0.0, lon=0.0),
        Station(station_id="B", source="x", lat=0.0, lon=1.0),
        Station(station_id="C", source="x", lat=50.0, lon=50.0),
    ]
    idx = SpatialIndex(stations)
    hits = idx.query_radius(lat=0.0, lon=0.0, radius_km=200)
    assert [h.station.station_id for h in hits] == ["A", "B"]

