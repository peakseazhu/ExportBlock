from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import cdflib
from cdflib import cdfepoch


_VLF_NAME_RE = re.compile(r"isee_vlf_(?P<station>[a-z0-9]+)_(?P<yyyymmddhh>\\d{10})_v\\d+\\.cdf$", re.IGNORECASE)


@dataclass(frozen=True)
class VlfFileInfo:
    path: Path
    station_id: str
    start_utc: datetime | None
    end_utc: datetime | None
    lat: float | None
    lon: float | None
    elev: float | None


def _parse_filename(path: Path) -> tuple[str, datetime] | None:
    m = _VLF_NAME_RE.search(path.name)
    if not m:
        return None
    station = m.group("station").upper()
    yyyymmddhh = m.group("yyyymmddhh")
    dt = datetime.strptime(yyyymmddhh, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return station, dt


def _safe_float(value: Any) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    if abs(v) >= 1e30:
        return None
    return v


def read_vlf_cdf(path: str | Path) -> tuple[pd.DataFrame, VlfFileInfo]:
    path = Path(path)
    cdf = cdflib.CDF(str(path))

    attrs = cdf.globalattsget()
    station_code = (attrs.get("Station_code") or [None])[0]
    station_id = str(station_code).upper() if station_code else None

    lat = _safe_float((attrs.get("Geographic_latitude") or [None])[0])
    lon = _safe_float((attrs.get("Geographic_longitude") or [None])[0])
    elev = _safe_float((attrs.get("Elevation") or [None])[0])

    epoch = cdf.varget("epoch_vlf")
    dt_str = cdfepoch.to_datetime(epoch)
    ts = pd.to_datetime(dt_str, utc=True)
    ts_ms = (ts.astype("int64") // 1_000_000).astype("int64")

    freq = np.asarray(cdf.varget("freq_vlf"), dtype=np.float64)
    ch1 = np.asarray(cdf.varget("ch1"), dtype=np.float64)
    ch2 = np.asarray(cdf.varget("ch2"), dtype=np.float64)

    power_ch1 = np.mean(ch1**2, axis=1)
    power_ch2 = np.mean(ch2**2, axis=1)
    peak_idx1 = np.argmax(ch1, axis=1)
    peak_idx2 = np.argmax(ch2, axis=1)
    peak_freq1 = freq[peak_idx1]
    peak_freq2 = freq[peak_idx2]

    df = pd.DataFrame(
        {
            "ts_ms": np.repeat(ts_ms, 4),
            "channel": np.tile(["power_ch1", "power_ch2", "peak_freq_ch1", "peak_freq_ch2"], ts_ms.shape[0]),
            "value": np.concatenate([power_ch1, power_ch2, peak_freq1, peak_freq2]),
        }
    )

    # 下采样到 1min（取均值）
    dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.floor("min")
    df["ts_ms"] = (dt.astype("int64") // 1_000_000).astype("int64")
    df = df.groupby(["ts_ms", "channel"], as_index=False)["value"].mean()

    if station_id is None:
        parsed = _parse_filename(path)
        station_id = parsed[0] if parsed else "UNKNOWN"

    df.insert(1, "source", "vlf")
    df.insert(2, "station_id", station_id)
    df["lat"] = float(lat) if lat is not None else np.nan
    df["lon"] = float(lon) if lon is not None else np.nan
    df["elev"] = float(elev) if elev is not None else np.nan
    df["quality_flags"] = json.dumps({"is_missing": False}, ensure_ascii=False, separators=(",", ":"))

    start_utc = ts.min().to_pydatetime() if len(ts) else None
    end_utc = ts.max().to_pydatetime() if len(ts) else None

    info = VlfFileInfo(path=path, station_id=station_id, start_utc=start_utc, end_utc=end_utc, lat=lat, lon=lon, elev=elev)
    return df[["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"]], info


def ingest_vlf_dir(vlf_dir: str | Path, *, window_start: datetime | None = None, window_end: datetime | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    vlf_dir = Path(vlf_dir)
    cdf_files = sorted(vlf_dir.rglob("*.cdf"))

    frames: list[pd.DataFrame] = []
    infos: list[dict[str, Any]] = []

    for path in cdf_files:
        df, info = read_vlf_cdf(path)
        if window_start and window_end:
            dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
            df = df[(dt >= window_start) & (dt <= window_end)].copy()
        if not df.empty:
            frames.append(df)
        infos.append(
            {
                "path": str(info.path),
                "station_id": info.station_id,
                "start_utc": info.start_utc.isoformat() if info.start_utc else None,
                "end_utc": info.end_utc.isoformat() if info.end_utc else None,
                "lat": info.lat,
                "lon": info.lon,
                "elev": info.elev,
            }
        )

    out = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"])
    )
    meta = {"file_count": len(cdf_files), "selected_file_count": len(infos), "files": infos}
    return out, meta
