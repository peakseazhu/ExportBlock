from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from exportblock.config import load_config


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _downsample(df: pd.DataFrame, *, max_points: int, method: str = "uniform") -> pd.DataFrame:
    if df.empty or df.shape[0] <= max_points:
        return df
    if method == "uniform":
        idx = np.linspace(0, df.shape[0] - 1, num=max_points, dtype=int)
        return df.iloc[idx]
    return df.iloc[:max_points]


def create_app(*, outputs_dir: Path) -> FastAPI:
    app = FastAPI(title="ExportBlock API", version="0.2.0")

    raw_dir = outputs_dir / "raw_bronze"
    std_dir = outputs_dir / "standard_silver"
    linked_dir = outputs_dir / "linked_gold"
    features_dir = outputs_dir / "features"
    reports_dir = outputs_dir / "reports"
    plots_dir = outputs_dir / "plots" / "figures"

    @app.get("/health")
    def health():
        return {"ok": True}

    def _query_common(base: Path, *, source: str | None, station_id: str | None, channel: str | None, start: str | None, end: str | None, limit: int, downsample: str):
        if not base.exists():
            return pd.DataFrame()
        filters = []
        if source:
            filters.append(("source", "==", source))
        if station_id:
            filters.append(("station_id", "==", station_id))
        df = pd.read_parquet(base, filters=filters) if filters else pd.read_parquet(base)
        if channel:
            df = df[df["channel"] == channel]
        if start or end:
            s_dt = _parse_time(start)
            e_dt = _parse_time(end)
            if s_dt:
                df = df[pd.to_datetime(df["ts_ms"], unit="ms", utc=True) >= s_dt]
            if e_dt:
                df = df[pd.to_datetime(df["ts_ms"], unit="ms", utc=True) <= e_dt]
        df = df.sort_values("ts_ms")
        df = _downsample(df, max_points=limit, method=downsample)
        return df

    @app.get("/raw/query")
    def raw_query(
        source: str | None = Query(None),
        station_id: str | None = Query(None),
        channel: str | None = Query(None),
        start: str | None = Query(None),
        end: str | None = Query(None),
        limit: int = Query(20000),
        downsample: str = Query("uniform"),
    ):
        df = _query_common(raw_dir, source=source, station_id=station_id, channel=channel, start=start, end=end, limit=limit, downsample=downsample)
        df = df.replace({np.nan: None})
        return {"rows": df.to_dict(orient="records"), "count": len(df)}

    @app.get("/standard/query")
    def standard_query(
        source: str | None = Query(None),
        station_id: str | None = Query(None),
        channel: str | None = Query(None),
        start: str | None = Query(None),
        end: str | None = Query(None),
        limit: int = Query(20000),
        downsample: str = Query("uniform"),
    ):
        df = _query_common(std_dir, source=source, station_id=station_id, channel=channel, start=start, end=end, limit=limit, downsample=downsample)
        df = df.replace({np.nan: None})
        return {"rows": df.to_dict(orient="records"), "count": len(df)}

    @app.get("/events")
    def list_events():
        if not linked_dir.exists():
            return {"events": []}
        events = sorted([p.name.split("event_id=")[-1] for p in linked_dir.iterdir() if p.is_dir()])
        return {"events": events}

    @app.get("/events/{event_id}")
    def get_event(event_id: str):
        path = linked_dir / f"event_id={event_id}" / "event.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="event not found")
        return _read_json(path)

    @app.get("/events/{event_id}/linked")
    def get_linked(event_id: str):
        path = linked_dir / f"event_id={event_id}" / "aligned.parquet"
        if not path.exists():
            raise HTTPException(status_code=404, detail="linked not found")
        df = pd.read_parquet(path)
        df = df.replace({np.nan: None})
        return {"rows": df.to_dict(orient="records"), "count": len(df)}

    @app.get("/events/{event_id}/features")
    def get_features(event_id: str):
        path = features_dir / f"event_id={event_id}" / "features.parquet"
        if not path.exists():
            raise HTTPException(status_code=404, detail="features not found")
        df = pd.read_parquet(path)
        df = df.replace({np.nan: None})
        return {"rows": df.to_dict(orient="records"), "count": len(df)}

    @app.get("/events/{event_id}/plots/{kind}")
    def get_plot(event_id: str, kind: str):
        path = plots_dir / event_id / f"plot_{kind}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="plot not found")
        return _read_json(path)

    @app.get("/reports/{name}")
    def get_report(name: str):
        path = reports_dir / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="report not found")
        return _read_json(path)

    return app


def _read_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _load_outputs_dir_from_env() -> Path:
    config_path = os.environ.get("EXPORTBLOCK_CONFIG")
    if not config_path:
        config_path = str(Path("configs/demo.yaml").resolve())
    cfg = load_config(config_path)
    return Path(cfg["outputs_dir"])


app = create_app(outputs_dir=_load_outputs_dir_from_env())
