from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


_HEADER_KV_RE = re.compile(r"^\s*([^#|][^|]{0,40}?)\s{2,}(.+?)\s*\|\s*$")


@dataclass(frozen=True)
class IagaHeader:
    raw: dict[str, str]

    @property
    def iaga_code(self) -> str | None:
        for k in ("IAGA Code", "IAGA CODE"):
            if k in self.raw:
                return self.raw[k].strip().upper()
        return None

    @property
    def latitude(self) -> float | None:
        for k in ("Geodetic Latitude", "Geodetic latitude"):
            if k in self.raw:
                return float(self.raw[k])
        return None

    @property
    def longitude(self) -> float | None:
        for k in ("Geodetic Longitude", "Geodetic longitude"):
            if k in self.raw:
                return float(self.raw[k])
        return None

    @property
    def elevation(self) -> float | None:
        if "Elevation" in self.raw:
            return float(self.raw["Elevation"])
        return None

    @property
    def reported(self) -> str | None:
        if "Reported" in self.raw:
            return self.raw["Reported"].strip()
        return None


def _read_text_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def parse_iaga2002_header(lines: Iterable[str]) -> tuple[IagaHeader, int]:
    header: dict[str, str] = {}
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("DATE"):
            return IagaHeader(header), idx
        m = _HEADER_KV_RE.match(line)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            header[key] = value
    raise ValueError("IAGA2002 header: missing DATE header line")


def _detect_data_columns(date_header_line: str) -> list[str]:
    tokens = re.split(r"\s+", date_header_line.strip().rstrip("|").strip())
    tokens = [t for t in tokens if t != "|"]
    if len(tokens) < 4:
        raise ValueError(f"IAGA2002 header columns too short: {tokens!r}")
    return tokens


def _infer_station_from_columns(columns: Iterable[str]) -> str | None:
    for c in columns:
        if c == "ts_ms":
            continue
        m = re.match(r"^([A-Za-z0-9]{3})[A-Za-z]$", str(c))
        if m:
            return m.group(1).upper()
    return None


def read_iaga2002_file(
    path: str | Path,
    *,
    source: str,
    keep_channels: set[str] | None = None,
    compact_quality: bool = False,
    nrows: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = Path(path)
    lines = _read_text_lines(path)
    header, date_header_idx = parse_iaga2002_header(lines)
    col_names = _detect_data_columns(lines[date_header_idx])

    df = pd.read_csv(
        path,
        sep=r"\s+",
        skiprows=date_header_idx + 1,
        names=col_names,
        encoding="utf-8",
        engine="python",
        nrows=nrows,
    )

    ts = pd.to_datetime(df["DATE"].astype(str) + " " + df["TIME"].astype(str), utc=True)
    df = df.drop(columns=["DATE", "TIME", "DOY"], errors="ignore")
    df.insert(0, "ts_ms", (ts.astype("int64") // 1_000_000).astype("int64"))

    station_id = header.iaga_code or _infer_station_from_columns(df.columns)
    station_id = (station_id or "UNKNOWN").upper()
    lat = header.latitude
    lon = header.longitude
    elev = header.elevation

    value_cols = [c for c in df.columns if c != "ts_ms"]
    long_df = df.melt(id_vars=["ts_ms"], value_vars=value_cols, var_name="field", value_name="value_raw")

    long_df["channel"] = long_df["field"].astype(str).str[-1].str.upper()
    if keep_channels is not None:
        long_df = long_df[long_df["channel"].isin({c.upper() for c in keep_channels})].copy()

    long_df["value"] = pd.to_numeric(long_df["value_raw"], errors="coerce")
    long_df = long_df.drop(columns=["value_raw"])

    abs_val = long_df["value"].abs()
    mask_dummy = abs_val.eq(88888.0)
    mask_sentinel = abs_val.ge(88888.0)
    mask_missing = long_df["value"].isna() | mask_sentinel
    long_df.loc[mask_missing, "value"] = np.nan

    long_df.insert(1, "source", source)
    long_df.insert(2, "station_id", station_id)
    long_df["lat"] = float(lat) if lat is not None else np.nan
    long_df["lon"] = float(lon) if lon is not None else np.nan
    long_df["elev"] = float(elev) if elev is not None else np.nan

    if compact_quality:
        quality = pd.Series(np.full(len(long_df), "ok", dtype=object))
        quality.loc[mask_dummy] = "dummy"
        quality.loc[mask_sentinel & ~mask_dummy] = "sentinel"
        long_df["quality_flags"] = quality
    else:
        q_ok = json.dumps({"is_missing": False}, ensure_ascii=False, separators=(",", ":"))
        q_sentinel = json.dumps({"is_missing": True, "missing_reason": "sentinel"}, ensure_ascii=False, separators=(",", ":"))
        q_dummy = json.dumps({"is_missing": True, "missing_reason": "dummy"}, ensure_ascii=False, separators=(",", ":"))
        quality_flags = np.where(~is_missing, q_ok, np.where(missing_reason == "dummy", q_dummy, q_sentinel))
        long_df["quality_flags"] = quality_flags

    meta = {
        "path": str(path),
        "iaga_code": station_id,
        "latitude": lat,
        "longitude": lon,
        "elevation": elev,
        "reported": header.reported,
        "header": header.raw,
    }
    return long_df[["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"]], meta
