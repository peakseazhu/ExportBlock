from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from obspy import Stream, Trace, UTCDateTime, read, read_inventory

from exportblock.util.time import dt_to_ts_ms


@dataclass(frozen=True)
class SeismicTraceMeta:
    trace_id: str
    start_utc: datetime
    end_utc: datetime
    sampling_rate: float
    npts: int
    station_match: str
    lat: float | None
    lon: float | None
    elev: float | None


def load_inventory(stationxml_path: str | Path):
    return read_inventory(str(stationxml_path))


def read_mseed_stream(path: str | Path) -> Stream:
    return read(str(path))


def match_trace_coordinates(inv, trace: Trace) -> tuple[str, float | None, float | None, float | None]:
    try:
        coords = inv.get_coordinates(trace.id, trace.stats.starttime)
        return "exact", float(coords["latitude"]), float(coords["longitude"]), float(coords.get("elevation", np.nan))
    except Exception:
        return "unmatched", None, None, None


def _minute_aligned_start(t0: UTCDateTime) -> UTCDateTime:
    seconds = t0.second + t0.microsecond / 1_000_000.0
    if seconds == 0:
        return t0
    delta = 60.0 - seconds
    return t0 + delta


def compute_minute_features(trace: Trace, *, window_start: datetime, window_end: datetime) -> pd.DataFrame:
    sr = float(trace.stats.sampling_rate)
    if sr <= 0:
        raise ValueError("invalid sampling rate")
    samples_per_min = int(round(sr * 60.0))
    if samples_per_min <= 0:
        raise ValueError("invalid samples_per_min")

    t0 = trace.stats.starttime
    t1 = trace.stats.endtime

    start = max(UTCDateTime(window_start), t0)
    end = min(UTCDateTime(window_end), t1)
    if end <= start:
        return pd.DataFrame(columns=["ts_ms", "station_id", "channel", "value"])

    tr = trace.copy()
    tr.trim(starttime=start, endtime=end, pad=False)

    t_first = _minute_aligned_start(tr.stats.starttime)
    offset_s = float(t_first - tr.stats.starttime)
    offset_n = int(round(offset_s * sr))
    data = tr.data.astype(np.float64, copy=False)
    if offset_n < 0 or offset_n >= data.size:
        return pd.DataFrame(columns=["ts_ms", "station_id", "channel", "value"])

    usable = data[offset_n:]
    n_windows = usable.size // samples_per_min
    if n_windows <= 0:
        return pd.DataFrame(columns=["ts_ms", "station_id", "channel", "value"])

    usable = usable[: n_windows * samples_per_min]
    windows = usable.reshape((n_windows, samples_per_min))

    rms = np.sqrt(np.mean(windows**2, axis=1))
    energy = np.sum(windows**2, axis=1)
    peak_abs = np.max(np.abs(windows), axis=1)

    fft = np.fft.rfft(windows, axis=1)
    amp = np.abs(fft)
    freq = np.fft.rfftfreq(samples_per_min, d=1.0 / sr)
    peak_idx = np.argmax(amp, axis=1)
    peak_freq = freq[peak_idx]

    ts0 = datetime.fromtimestamp(float(t_first.timestamp), tz=timezone.utc)
    ts_ms = np.array([dt_to_ts_ms(ts0) + i * 60_000 for i in range(n_windows)], dtype=np.int64)

    station_id = trace.id
    out = pd.DataFrame(
        {
            "ts_ms": np.repeat(ts_ms, 4),
            "station_id": station_id,
            "channel": np.tile(["rms", "energy", "peak_abs", "peak_freq"], n_windows),
            "value": np.concatenate([rms, energy, peak_abs, peak_freq]),
        }
    )
    return out


def ingest_mseed_and_features(
    seismic_dir: str | Path,
    *,
    stationxml_path: str | Path,
    window_start: datetime,
    window_end: datetime,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    seismic_dir = Path(seismic_dir)
    inv = load_inventory(stationxml_path)

    mseed_files = sorted([p for p in seismic_dir.glob("*.mseed") if p.is_file()])
    metas: list[SeismicTraceMeta] = []
    feature_frames: list[pd.DataFrame] = []

    for path in mseed_files:
        st = read_mseed_stream(path)
        for tr in st:
            match, lat, lon, elev = match_trace_coordinates(inv, tr)
            metas.append(
                SeismicTraceMeta(
                    trace_id=tr.id,
                    start_utc=tr.stats.starttime.datetime.replace(tzinfo=timezone.utc),
                    end_utc=tr.stats.endtime.datetime.replace(tzinfo=timezone.utc),
                    sampling_rate=float(tr.stats.sampling_rate),
                    npts=int(tr.stats.npts),
                    station_match=match,
                    lat=lat,
                    lon=lon,
                    elev=elev,
                )
            )
            feats = compute_minute_features(tr, window_start=window_start, window_end=window_end)
            if not feats.empty:
                feats.insert(1, "source", "seismic")
                feats = feats.rename(columns={"station_id": "station_id"})
                feats["lat"] = float(lat) if lat is not None else np.nan
                feats["lon"] = float(lon) if lon is not None else np.nan
                feats["elev"] = float(elev) if elev is not None else np.nan
                feats["quality_flags"] = json.dumps({"is_missing": False}, ensure_ascii=False, separators=(",", ":"))
                feature_frames.append(feats)

    features_df = (
        pd.concat(feature_frames, ignore_index=True)
        if feature_frames
        else pd.DataFrame(columns=["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"])
    )

    meta = {
        "file_count": len(mseed_files),
        "traces": [
            {
                "trace_id": m.trace_id,
                "start_utc": m.start_utc.isoformat(),
                "end_utc": m.end_utc.isoformat(),
                "sampling_rate": m.sampling_rate,
                "npts": m.npts,
                "station_match": m.station_match,
                "lat": m.lat,
                "lon": m.lon,
                "elev": m.elev,
            }
            for m in metas
        ],
        "station_match_ratio": (sum(1 for m in metas if m.station_match == "exact") / len(metas)) if metas else 0.0,
    }
    return features_df[["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"]], meta

