from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from exportblock.config import Event
from exportblock.io.iaga2002 import read_iaga2002_file
from exportblock.io.seismic import ingest_mseed_and_features
from exportblock.io.vlf import ingest_vlf_dir
from exportblock.pipeline.plots import make_anomaly_heatmap, make_event_timeseries_plot, save_plot_html, save_plot_json
from exportblock.pipeline.reports import iso_utc, write_json
from exportblock.preprocess.kalman import auto_params, kalman_1d
from exportblock.spatial.index import SpatialIndex
from exportblock.util.geo import Station
from exportblock.util.time import make_time_grid


def _ensure_dirs(outputs_dir: Path) -> dict[str, Path]:
    out = {
        "reports": outputs_dir / "reports",
        "standard": outputs_dir / "standard",
        "features": outputs_dir / "features",
        "linked": outputs_dir / "linked",
        "plots": outputs_dir / "plots",
        "api_tests": outputs_dir / "api_tests",
    }
    for p in out.values():
        p.mkdir(parents=True, exist_ok=True)
    return out


def _dq_basic(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0}
    return {
        "rows": int(df.shape[0]),
        "ts_min": iso_utc(int(df["ts_ms"].min())),
        "ts_max": iso_utc(int(df["ts_ms"].max())),
        "station_count": int(df["station_id"].nunique()),
        "channel_count": int(df["channel"].nunique()),
        "missing_rate": float(df["value"].isna().mean()),
    }


def _filter_window(df: pd.DataFrame, *, start: datetime, end: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df[(dt >= start) & (dt <= end)].copy()


def _ingest_geomag(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    geomag_dir: Path = config["inputs"]["geomag_dir"]
    use = str(config.get("pipeline", {}).get("geomag_use", "min")).lower()
    if use not in {"min", "sec"}:
        raise ValueError("pipeline.geomag_use must be min|sec")

    suffix = "dmin.min" if use == "min" else "psec.sec"
    files = sorted([p for p in geomag_dir.glob(f"*{suffix}") if p.is_file()])
    frames: list[pd.DataFrame] = []
    metas: list[dict[str, Any]] = []
    for path in files:
        df, meta = read_iaga2002_file(path, source="geomag")
        frames.append(df)
        metas.append(meta)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=frames[0].columns if frames else [])
    return out, {"files": metas, "file_count": len(files)}


def _ingest_aef_min(config: dict[str, Any], *, window_start: datetime, window_end: datetime) -> tuple[pd.DataFrame, dict[str, Any]]:
    aef_dir: Path = config["inputs"]["aef_min_dir"]
    files_all = sorted([p for p in aef_dir.glob("*.min") if p.is_file()])
    start_date = window_start.date()
    end_date = window_end.date()
    files: list[Path] = []
    for p in files_all:
        m = re.search(r"(\\d{8})", p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except Exception:
            continue
        if start_date <= d <= end_date:
            files.append(p)
    files = sorted(files)
    frames: list[pd.DataFrame] = []
    metas: list[dict[str, Any]] = []
    for path in files:
        df, meta = read_iaga2002_file(path, source="aef", keep_channels={"Z"})
        frames.append(df)
        metas.append(meta)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=frames[0].columns if frames else [])
    return out, {"files": metas, "selected_file_count": len(files), "file_count": len(files_all)}


def _apply_kalman(df: pd.DataFrame, *, q_scale: float, r_scale: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df.copy(), {"channels": {}}
    filtered_values = []
    report: dict[str, Any] = {"channels": {}}
    for (station_id, channel), g in df.groupby(["station_id", "channel"], sort=False):
        g = g.sort_values("ts_ms")
        params = auto_params(g["value"].to_numpy(), q_scale=q_scale, r_scale=r_scale)
        out = kalman_1d(g["value"].to_numpy(), q=params.q, r=params.r)
        raw_std = float(np.nanstd(g["value"].to_numpy()))
        filt_std = float(np.nanstd(out))
        report["channels"][f"{station_id}:{channel}"] = {
            "q": params.q,
            "r": params.r,
            "raw_std": raw_std,
            "filtered_std": filt_std,
            "std_ratio": (filt_std / raw_std) if raw_std else None,
        }
        gg = g.copy()
        gg["value"] = out
        gg["quality_flags"] = json.dumps(
            {"is_missing": False, "is_filtered": True, "filter_type": "kalman", "filter_params": {"q": params.q, "r": params.r}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        filtered_values.append(gg)
    return pd.concat(filtered_values, ignore_index=True), report


def _align_dq(dfs: dict[str, pd.DataFrame], *, grid: pd.DatetimeIndex) -> dict[str, Any]:
    expected = int(len(grid))
    out: dict[str, Any] = {"expected_minutes": expected, "sources": {}}
    for name, df in dfs.items():
        if df.empty:
            out["sources"][name] = {"rows": 0}
            continue
        dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.floor("min")
        tmp = df.copy()
        tmp["_ts"] = dt
        counts = (
            tmp.groupby(["station_id", "channel"], as_index=False)["_ts"]
            .nunique()
            .rename(columns={"_ts": "present_minutes"})
        )
        counts["missing_minutes"] = expected - counts["present_minutes"]
        counts["missing_rate"] = counts["missing_minutes"] / expected if expected else None
        out["sources"][name] = {
            "rows": int(df.shape[0]),
            "station_count": int(df["station_id"].nunique()),
            "channel_count": int(df["channel"].nunique()),
            "by_station_channel": counts.to_dict(orient="records"),
        }
    return out


def _collect_stations(*, dfs: dict[str, pd.DataFrame]) -> list[Station]:
    stations: dict[tuple[str, str], Station] = {}
    for source, df in dfs.items():
        if df.empty:
            continue
        for station_id, g in df.groupby("station_id"):
            lat = float(g["lat"].dropna().iloc[0]) if g["lat"].notna().any() else np.nan
            lon = float(g["lon"].dropna().iloc[0]) if g["lon"].notna().any() else np.nan
            elev = float(g["elev"].dropna().iloc[0]) if g["elev"].notna().any() else None
            if not np.isfinite(lat) or not np.isfinite(lon):
                continue
            stations[(source, station_id)] = Station(station_id=station_id, source=source, lat=lat, lon=lon, elev=elev)
    return list(stations.values())


def _score_anomaly(df: pd.DataFrame, *, event_time: datetime, z_threshold: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df.copy(), {"rows": 0}
    dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    baseline_mask = dt < event_time
    baseline = df[baseline_mask]
    out_rows = []
    for (station_id, channel), g in df.groupby(["station_id", "channel"], sort=False):
        b = baseline[(baseline["station_id"] == station_id) & (baseline["channel"] == channel)]
        mu = float(np.nanmean(b["value"].to_numpy())) if not b.empty else float(np.nanmean(g["value"].to_numpy()))
        sigma = float(np.nanstd(b["value"].to_numpy())) if not b.empty else float(np.nanstd(g["value"].to_numpy()))
        sigma = sigma if sigma and np.isfinite(sigma) else np.nan
        gg = g.copy()
        if not np.isfinite(sigma) or sigma == 0:
            gg["z"] = np.nan
        else:
            gg["z"] = (gg["value"] - mu) / sigma
        gg["anomaly_score"] = gg["z"].abs()
        gg["is_anomaly"] = gg["anomaly_score"] >= float(z_threshold)
        out_rows.append(gg)
    out = pd.concat(out_rows, ignore_index=True)
    report = {
        "rows": int(out.shape[0]),
        "anomaly_rows": int(out["is_anomaly"].sum()),
        "anomaly_rate": float(out["is_anomaly"].mean()) if out.shape[0] else 0.0,
        "threshold": float(z_threshold),
    }
    return out, report


def run_pipeline(config: dict[str, Any], *, config_path: Path) -> None:
    outputs_dir: Path = config["outputs_dir"]
    out_dirs = _ensure_dirs(outputs_dir)

    write_json(out_dirs["reports"] / "config_snapshot.json", {"config_path": str(config_path), "config": _stringify_paths(config)})

    for event in config["events"]:
        run_event_pipeline(config, event=event, out_dirs=out_dirs)


def _stringify_paths(config: dict[str, Any]) -> dict[str, Any]:
    def conv(v):
        if isinstance(v, Event):
            return {
                "event_id": v.event_id,
                "time_utc": v.time_utc.isoformat(),
                "lat": v.lat,
                "lon": v.lon,
                "window_before_hours": v.window_before_hours,
                "window_after_hours": v.window_after_hours,
                "radius_km": v.radius_km,
            }
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v

    return conv(config)


def run_event_pipeline(config: dict[str, Any], *, event: Event, out_dirs: dict[str, Path]) -> None:
    start = event.window_start
    end = event.window_end

    geomag_df, geomag_meta = _ingest_geomag(config)
    aef_df, aef_meta = _ingest_aef_min(config, window_start=start, window_end=end)
    seismic_df, mseed_meta = ingest_mseed_and_features(
        config["inputs"]["seismic_dir"],
        stationxml_path=config["inputs"]["stationxml_path"],
        window_start=start,
        window_end=end,
    )
    vlf_df, vlf_meta = ingest_vlf_dir(config["inputs"]["vlf_dir"], window_start=start, window_end=end)

    geomag_df = _filter_window(geomag_df, start=start, end=end)
    aef_df = _filter_window(aef_df, start=start, end=end)
    vlf_df = _filter_window(vlf_df, start=start, end=end)

    # persist (event-scoped)
    geomag_path = out_dirs["standard"] / f"geomag_{event.event_id}.parquet"
    aef_path = out_dirs["standard"] / f"aef_{event.event_id}.parquet"
    seismic_path = out_dirs["features"] / f"seismic_features_{event.event_id}.parquet"
    vlf_path = out_dirs["standard"] / f"vlf_{event.event_id}.parquet"

    if not geomag_df.empty:
        geomag_df.to_parquet(geomag_path, index=False)
    if not aef_df.empty:
        aef_df.to_parquet(aef_path, index=False)
    if not seismic_df.empty:
        seismic_df.to_parquet(seismic_path, index=False)
    if not vlf_df.empty:
        vlf_df.to_parquet(vlf_path, index=False)

    write_json(
        out_dirs["reports"] / "dq_ingest_iaga.json",
        {
            "geomag": _dq_basic(geomag_df),
            "aef": _dq_basic(aef_df),
            "meta": {"geomag": geomag_meta, "aef": aef_meta},
        },
    )
    write_json(out_dirs["reports"] / "dq_ingest_mseed.json", {"meta": mseed_meta, "features": _dq_basic(seismic_df)})

    kalman_cfg = (config.get("pipeline") or {}).get("kalman") or {}
    kalman_enabled = bool(kalman_cfg.get("enabled", True))
    geomag_filtered = geomag_df.copy()
    filter_effect: dict[str, Any] = {"enabled": kalman_enabled}
    if kalman_enabled and not geomag_df.empty:
        geomag_filtered, filter_effect = _apply_kalman(
            geomag_df,
            q_scale=float(kalman_cfg.get("q_scale", 1e-5)),
            r_scale=float(kalman_cfg.get("r_scale", 1e-2)),
        )
        geomag_filtered.to_parquet(out_dirs["standard"] / f"geomag_filtered_{event.event_id}.parquet", index=False)
    write_json(out_dirs["reports"] / "filter_effect.json", filter_effect)

    write_json(out_dirs["reports"] / "dq_features.json", {"seismic": _dq_basic(seismic_df)})

    grid = make_time_grid(start, end, interval=str((config.get("pipeline") or {}).get("align_interval", "1min")))
    align_report = _align_dq(
        {"geomag": geomag_df, "aef": aef_df, "seismic": seismic_df, "vlf": vlf_df},
        grid=grid,
    )
    write_json(out_dirs["reports"] / "dq_align.json", align_report)

    stations = _collect_stations(dfs={"geomag": geomag_df, "aef": aef_df, "seismic": seismic_df, "vlf": vlf_df})
    sindex = SpatialIndex(stations)
    hits = sindex.query_radius(lat=event.lat, lon=event.lon, radius_km=event.radius_km)
    write_json(
        out_dirs["reports"] / "dq_spatial.json",
        {
            "station_count": sindex.station_count,
            "query": {"lat": event.lat, "lon": event.lon, "radius_km": event.radius_km, "hit_count": len(hits)},
            "hits": [
                {
                    "station_id": h.station.station_id,
                    "source": h.station.source,
                    "lat": h.station.lat,
                    "lon": h.station.lon,
                    "elev": h.station.elev,
                    "distance_km": h.distance_km,
                }
                for h in hits
            ],
            "meta": {"vlf": vlf_meta},
        },
    )

    linked_dir = out_dirs["linked"] / event.event_id
    linked_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        linked_dir / "event.json",
        {
            "event_id": event.event_id,
            "time_utc": event.time_utc.isoformat(),
            "lat": event.lat,
            "lon": event.lon,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "radius_km": event.radius_km,
        },
    )
    hits_payload = json.loads((out_dirs["reports"] / "dq_spatial.json").read_text(encoding="utf-8"))["hits"]
    write_json(linked_dir / "stations.json", {"event_id": event.event_id, "stations": hits_payload})

    anomaly_cfg = (config.get("pipeline") or {}).get("anomaly") or {}
    z_threshold = float(anomaly_cfg.get("z_threshold", 3.0))
    combined = (
        pd.concat([df for df in [geomag_filtered, aef_df, seismic_df, vlf_df] if not df.empty], ignore_index=True)
        if any(not df.empty for df in [geomag_filtered, aef_df, seismic_df, vlf_df])
        else pd.DataFrame(columns=["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"])
    )
    anomaly_df, anomaly_report = _score_anomaly(combined, event_time=event.time_utc, z_threshold=z_threshold)
    if not anomaly_df.empty:
        anomaly_df.to_parquet(out_dirs["features"] / f"anomaly_{event.event_id}.parquet", index=False)
    write_json(out_dirs["reports"] / "dq_anomaly.json", anomaly_report)

    plots_dir = out_dirs["plots"] / "figures" / event.event_id
    plots_dir.mkdir(parents=True, exist_ok=True)

    series = []
    if not geomag_df.empty:
        s = geomag_df[geomag_df["channel"] == "X"].sort_values("ts_ms")
        series.append({"title": "geomag:X", "df": s[["ts_ms", "value"]]})
    if not aef_df.empty:
        s = aef_df.sort_values("ts_ms")
        series.append({"title": "aef:Z", "df": s[["ts_ms", "value"]]})
    if not seismic_df.empty:
        s = seismic_df[seismic_df["channel"] == "rms"].sort_values("ts_ms")
        series.append({"title": "seismic:rms", "df": s[["ts_ms", "value"]]})

    ts_fig = make_event_timeseries_plot(event_id=event.event_id, event_time=event.time_utc, series=series)
    save_plot_json(ts_fig, plots_dir / "plot_timeseries.json")
    save_plot_html(ts_fig, out_dirs["plots"] / "html" / event.event_id / "plot_timeseries.html")

    heatmap_fig = make_anomaly_heatmap(event_id=event.event_id, df=anomaly_df[["station_id", "ts_ms", "anomaly_score"]] if not anomaly_df.empty else anomaly_df)
    save_plot_json(heatmap_fig, plots_dir / "plot_heatmap.json")
    save_plot_html(heatmap_fig, out_dirs["plots"] / "html" / event.event_id / "plot_heatmap.html")

    from exportblock.api.smoke_test import run_smoke_test

    logs = run_smoke_test(outputs_dir=out_dirs["linked"].parent, event_id=event.event_id)
    write_json(out_dirs["api_tests"] / "logs.json", logs)
