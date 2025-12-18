from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from exportblock.api.app import create_app


def test_api_health(tmp_path: Path):
    outputs_dir = tmp_path / "outputs"
    (outputs_dir / "linked" / "0001").mkdir(parents=True)
    (outputs_dir / "linked" / "0001" / "event.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "linked" / "0001" / "stations.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "plots" / "figures" / "0001").mkdir(parents=True)
    (outputs_dir / "plots" / "figures" / "0001" / "plot_timeseries.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "plots" / "figures" / "0001" / "plot_heatmap.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "reports").mkdir(parents=True)
    (outputs_dir / "reports" / "dq_ingest_iaga.json").write_text("{}", encoding="utf-8")

    app = create_app(outputs_dir=outputs_dir)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/events").status_code == 200
    assert client.get("/events/0001").status_code == 200
    assert client.get("/events/0001/stations").status_code == 200
    assert client.get("/events/0001/plots/timeseries").status_code == 200
    assert client.get("/reports/dq_ingest_iaga.json").status_code == 200

