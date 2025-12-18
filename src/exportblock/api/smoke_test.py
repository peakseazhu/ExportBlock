from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from exportblock.api.app import create_app


def run_smoke_test(*, outputs_dir: Path, event_id: str) -> dict[str, Any]:
    app = create_app(outputs_dir=outputs_dir)
    client = TestClient(app)

    logs: list[dict[str, Any]] = []

    def req(path: str):
        r = client.get(path)
        logs.append({"path": path, "status_code": r.status_code})
        return r

    req("/health")
    req("/events")
    req(f"/events/{event_id}")
    req(f"/events/{event_id}/stations")
    req(f"/events/{event_id}/plots/timeseries")
    req(f"/events/{event_id}/plots/heatmap")
    req(f"/raw/geomag?event_id={event_id}")
    req(f"/raw/aef?event_id={event_id}")
    req(f"/features/{event_id}")
    req("/reports/dq_ingest_iaga.json")

    ok = all(item["status_code"] == 200 for item in logs[:4])
    return {"ok": ok, "event_id": event_id, "requests": logs}

