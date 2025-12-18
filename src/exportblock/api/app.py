from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException, Query

from exportblock.config import load_config


def create_app(*, outputs_dir: Path) -> FastAPI:
    app = FastAPI(title="ExportBlock API", version="0.1.0")

    def linked_dir() -> Path:
        return outputs_dir / "linked"

    def reports_dir() -> Path:
        return outputs_dir / "reports"

    def plots_dir() -> Path:
        return outputs_dir / "plots" / "figures"

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/events")
    def list_events():
        base = linked_dir()
        if not base.exists():
            return {"events": []}
        events = sorted([p.name for p in base.iterdir() if p.is_dir()])
        return {"events": events}

    @app.get("/events/{event_id}")
    def get_event(event_id: str):
        path = linked_dir() / event_id / "event.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="event not found")
        return _read_json(path)

    @app.get("/events/{event_id}/stations")
    def get_stations(event_id: str):
        path = linked_dir() / event_id / "stations.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="stations not found")
        return _read_json(path)

    @app.get("/events/{event_id}/plots/{kind}")
    def get_plot(event_id: str, kind: str):
        path = plots_dir() / event_id / f"plot_{kind}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="plot not found")
        return _read_json(path)

    @app.get("/raw/{source}")
    def query_raw(
        source: str,
        event_id: str = Query(...),
        station_id: str | None = Query(None),
        channel: str | None = Query(None),
    ):
        standard_dir = outputs_dir / "standard"
        path = standard_dir / f"{source}_{event_id}.parquet"
        if not path.exists():
            raise HTTPException(status_code=404, detail="data not found")
        df = pd.read_parquet(path)
        if station_id:
            df = df[df["station_id"] == station_id]
        if channel:
            df = df[df["channel"] == channel]
        df = df.replace({np.nan: None})
        return {"rows": df.to_dict(orient="records")}

    @app.get("/features/{event_id}")
    def get_features(event_id: str):
        path = outputs_dir / "features" / f"seismic_features_{event_id}.parquet"
        if not path.exists():
            raise HTTPException(status_code=404, detail="features not found")
        df = pd.read_parquet(path)
        df = df.replace({np.nan: None})
        return {"rows": df.to_dict(orient="records")}

    @app.get("/reports/{name}")
    def get_report(name: str):
        path = reports_dir() / name
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
