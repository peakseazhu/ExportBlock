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
    req(f"/events/{event_id}/linked")
    req(f"/events/{event_id}/features")
    req(f"/events/{event_id}/plots/timeseries")
    req(f"/events/{event_id}/plots/heatmap")
    req("/reports/dq_raw_bronze.json")

    ok = all(item["status_code"] == 200 for item in logs if item["path"].startswith("/health") or item["path"].startswith("/events"))
    return {"ok": ok, "event_id": event_id, "requests": logs}
