from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Event:
    event_id: str
    time_utc: datetime
    lat: float
    lon: float
    window_before_hours: int
    window_after_hours: int
    radius_km: float

    @property
    def window_start(self) -> datetime:
        return self.time_utc - timedelta_hours(self.window_before_hours)

    @property
    def window_end(self) -> datetime:
        return self.time_utc + timedelta_hours(self.window_after_hours)


def timedelta_hours(hours: int):
    from datetime import timedelta

    return timedelta(hours=int(hours))


def _parse_utc_datetime(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_config(config_path: str | Path) -> dict[str, Any]:
    config_path = Path(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config must be a mapping")
    return resolve_config_paths(raw, config_path=config_path)


def resolve_config_paths(config: dict[str, Any], *, config_path: Path) -> dict[str, Any]:
    base_dir = config_path.parent
    data_root = Path(config.get("data_root", "."))
    if not data_root.is_absolute():
        data_root = (base_dir / data_root).resolve()

    outputs_dir = Path(config.get("outputs_dir", "outputs"))
    if not outputs_dir.is_absolute():
        outputs_dir = (data_root / outputs_dir).resolve()

    inputs = dict(config.get("inputs") or {})
    resolved_inputs: dict[str, Any] = {}
    for key, value in inputs.items():
        if value is None:
            resolved_inputs[key] = None
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (data_root / path).resolve()
        resolved_inputs[key] = path

    events_raw = config.get("events") or []
    events: list[Event] = []
    for item in events_raw:
        if not isinstance(item, dict):
            raise ValueError("events must be a list of mappings")
        events.append(
            Event(
                event_id=str(item["event_id"]),
                time_utc=_parse_utc_datetime(str(item["time_utc"])),
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                window_before_hours=int(item["window_before_hours"]),
                window_after_hours=int(item["window_after_hours"]),
                radius_km=float(item["radius_km"]),
            )
        )

    pipeline = dict(config.get("pipeline") or {})

    return {
        **config,
        "data_root": data_root,
        "outputs_dir": outputs_dir,
        "inputs": resolved_inputs,
        "events": events,
        "pipeline": pipeline,
    }

