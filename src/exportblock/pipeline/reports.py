from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def iso_utc(ts_ms: int) -> str:
    return datetime.utcfromtimestamp(ts_ms / 1000).replace(microsecond=0).isoformat() + "Z"

