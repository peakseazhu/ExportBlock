from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def dt_to_ts_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def ts_ms_to_dt(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def make_time_grid(start: datetime, end: datetime, *, interval: str = "1min") -> pd.DatetimeIndex:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    return pd.date_range(start=start, end=end, freq=interval, tz="UTC")


def floor_dt(dt: datetime, *, freq: str = "min") -> datetime:
    ts = pd.Timestamp(dt).tz_convert("UTC") if dt.tzinfo else pd.Timestamp(dt, tz="UTC")
    return ts.floor(freq).to_pydatetime()

