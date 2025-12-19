from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from exportblock.api.app import create_app


def test_api_health(tmp_path: Path):
    outputs_dir = tmp_path / "outputs"
    lg = outputs_dir / "linked_gold" / "event_id=0001"
    lg.mkdir(parents=True)
    (lg / "event.json").write_text("{}", encoding="utf-8")
    pd.DataFrame({"ts_ms": [0], "source": ["geomag"], "station_id": ["KAK"], "channel": ["X"], "value": [1.0]}).to_parquet(lg / "aligned.parquet", index=False)

    feat_dir = outputs_dir / "features" / "event_id=0001"
    feat_dir.mkdir(parents=True)
    pd.DataFrame({"ts_ms": [0], "station_id": ["KAK"], "channel": ["X"], "value": [1.0]}).to_parquet(feat_dir / "features.parquet", index=False)
    (outputs_dir / "plots" / "figures" / "0001").mkdir(parents=True)
    (outputs_dir / "plots" / "figures" / "0001" / "plot_timeseries.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "plots" / "figures" / "0001" / "plot_heatmap.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "reports").mkdir(parents=True)
    (outputs_dir / "reports" / "dq_raw_bronze.json").write_text("{}", encoding="utf-8")

    app = create_app(outputs_dir=outputs_dir)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/events").status_code == 200
    assert client.get("/events/0001").status_code == 200
    assert client.get("/events/0001/linked").status_code == 200
    assert client.get("/events/0001/features").status_code == 200
    assert client.get("/events/0001/plots/timeseries").status_code == 200
    assert client.get("/reports/dq_raw_bronze.json").status_code == 200
