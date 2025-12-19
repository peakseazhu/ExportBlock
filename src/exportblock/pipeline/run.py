from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from exportblock.config import Event
from exportblock.io.iaga2002 import read_iaga2002_file
from exportblock.io.seismic import ingest_mseed_and_features
from exportblock.io.vlf import read_vlf_cdf
from exportblock.pipeline.plots import make_anomaly_heatmap, make_event_timeseries_plot, save_plot_html, save_plot_json
from exportblock.pipeline.reports import iso_utc, write_json
from exportblock.preprocess.kalman import auto_params, kalman_1d
from exportblock.spatial.index import SpatialIndex
from exportblock.util.geo import Station

PROC_VERSION = "0.2.0"


def _ensure_dirs(outputs_dir: Path) -> dict[str, Path]:
    out = {
        "manifests": outputs_dir / "manifests",
        "raw": outputs_dir / "raw_bronze",
        "standard": outputs_dir / "standard_silver",
        "linked": outputs_dir / "linked_gold",
        "features": outputs_dir / "features",
        "models": outputs_dir / "models",
        "reports": outputs_dir / "reports",
        "plots": outputs_dir / "plots",
        "api_tests": outputs_dir / "api_tests",
    }
    for p in out.values():
        p.mkdir(parents=True, exist_ok=True)
    return out


# ------------------------ Manifest ------------------------ #
def _hash_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def _build_manifest(inputs: dict[str, Path], out_path: Path) -> None:
    records: list[dict[str, Any]] = []
    for key, base in inputs.items():
        if base is None:
            continue
        paths = [p for p in base.rglob("*") if p.is_file()]
        if len(paths) > 50:
            paths = paths[:50]
        for p in paths:
            records.append(
                {
                    "group": key,
                    "path": str(p),
                    "size": p.stat().st_size,
                    "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "sha256": None,  # 体积较大时跳过哈希以降低内存压力
                }
            )
    payload = {"generated_at": datetime.now(tz=timezone.utc).isoformat(), "files": records}
    write_json(out_path, payload)


# ------------------------ Helpers ------------------------ #
def _add_common_columns(df: pd.DataFrame, *, units: str, stage: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["units"] = units
    out["proc_stage"] = stage
    out["proc_version"] = PROC_VERSION
    out["date"] = pd.to_datetime(out["ts_ms"], unit="ms", utc=True).dt.date.astype(str)
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


def _write_partitioned(df: pd.DataFrame, base_dir: Path, *, partition_cols: Iterable[str], compression: str) -> None:
    if df.empty:
        return
    base_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(base_dir, partition_cols=list(partition_cols), compression=compression, index=False)


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _collect_stations(dfs: Iterable[pd.DataFrame]) -> list[Station]:
    stations: dict[str, Station] = {}
    for df in dfs:
        if df.empty:
            continue
        for station_id, g in df.groupby("station_id"):
            lat = float(g["lat"].dropna().iloc[0]) if g["lat"].notna().any() else np.nan
            lon = float(g["lon"].dropna().iloc[0]) if g["lon"].notna().any() else np.nan
            elev = float(g["elev"].dropna().iloc[0]) if g["elev"].notna().any() else None
            if not np.isfinite(lat) or not np.isfinite(lon):
                continue
            stations[station_id] = Station(station_id=station_id, source=str(g["source"].iloc[0]), lat=lat, lon=lon, elev=elev)
    return list(stations.values())


# ------------------------ Ingest (Raw/Bronze) ------------------------ #
def _ingest_geomag_raw(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    geomag_dir: Path = config["inputs"].get("geomag_dir")
    if geomag_dir is None:
        return pd.DataFrame(), {"files": []}
    # 默认仅取首个分钟级文件，避免内存占用过大；后续可按需扩展
    files = sorted([p for p in geomag_dir.glob("*.min")] + [p for p in geomag_dir.glob("*dmin.min")])[:1]
    frames: list[pd.DataFrame] = []
    metas: list[dict[str, Any]] = []
    for path in files:
        df, meta = read_iaga2002_file(path, source="geomag", compact_quality=True, nrows=50000)
        meta["truncated_rows"] = 50000
        frames.append(df)
        metas.append(meta)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"])
    return out, {"files": metas, "file_count": len(files)}


def _ingest_aef_raw(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    aef_dir: Path = config["inputs"].get("aef_dir")
    if aef_dir is None:
        return pd.DataFrame(), {"files": []}
    files = sorted([p for p in aef_dir.rglob("*.min") if p.is_file()] + [p for p in aef_dir.rglob("*.hor") if p.is_file()])[:1]
    frames: list[pd.DataFrame] = []
    metas: list[dict[str, Any]] = []
    for path in files:
        df, meta = read_iaga2002_file(path, source="aef", keep_channels={"Z"}, compact_quality=True, nrows=50000)
        meta["truncated_rows"] = 50000
        frames.append(df)
        metas.append(meta)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"])
    return out, {"files": metas, "file_count": len(files)}


def _ingest_vlf_raw(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    vlf_dir: Path = config["inputs"].get("vlf_dir")
    if vlf_dir is None:
        return pd.DataFrame(), {"files": []}
    files = sorted(vlf_dir.rglob("*.cdf"))[:2]
    frames: list[pd.DataFrame] = []
    infos: list[dict[str, Any]] = []
    for path in files:
        df, info = read_vlf_cdf(path)
        frames.append(df)
        infos.append(
            {
                "path": str(info.path),
                "station_id": info.station_id,
                "start_utc": info.start_utc.isoformat() if info.start_utc else None,
                "end_utc": info.end_utc.isoformat() if info.end_utc else None,
                "lat": info.lat,
                "lon": info.lon,
                "elev": info.elev,
            }
        )
    out = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["ts_ms", "source", "station_id", "channel", "value", "lat", "lon", "elev", "quality_flags"])
    )
    meta = {"file_count": len(files), "files": infos}
    return out, meta


def _ingest_seismic_raw(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    seismic_dir: Path = config["inputs"].get("seismic_dir")
    stationxml_path: Path = config["inputs"].get("stationxml_path")
    if seismic_dir is None or stationxml_path is None:
        return pd.DataFrame(), {"files": []}
    df, meta = ingest_mseed_and_features(
        seismic_dir,
        stationxml_path=stationxml_path,
        window_start=None,
        window_end=None,
        align_interval=config.get("link", {}).get("align_interval", "1min"),
    )
    if not df.empty and df.shape[0] > 2000:
        df = df.head(2000).copy()
        meta["truncated_rows"] = 2000
    return df, meta


# ------------------------ Standard化 ------------------------ #
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


# ------------------------ Build Pipeline ------------------------ #
def build_pipeline(config: dict[str, Any], *, config_path: Path) -> dict[str, Any]:
    outputs_dir: Path = config["outputs_dir"]
    out_dirs = _ensure_dirs(outputs_dir)

    # 0) manifest
    manifest_path = out_dirs["manifests"] / f"run_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    _build_manifest(config["inputs"], manifest_path)

    storage = config.get("storage", {})
    partition_cols = storage.get("partition_cols", ["source", "station_id", "date"])
    compression = storage.get("compression", "zstd")

    # 1) raw ingest (full, no裁窗)
    geomag_raw, geomag_meta = _ingest_geomag_raw(config)
    aef_raw, aef_meta = _ingest_aef_raw(config)
    vlf_raw, vlf_meta = _ingest_vlf_raw(config)
    seismic_raw, seismic_meta = _ingest_seismic_raw(config)

    geomag_raw = _add_common_columns(geomag_raw, units="nT", stage="raw_bronze")
    aef_raw = _add_common_columns(aef_raw, units="V/m", stage="raw_bronze")
    vlf_raw = _add_common_columns(vlf_raw, units="unknown", stage="raw_bronze")
    seismic_raw = _add_common_columns(seismic_raw, units="counts", stage="raw_bronze")

    _write_partitioned(geomag_raw, out_dirs["raw"], partition_cols=partition_cols, compression=compression)
    _write_partitioned(aef_raw, out_dirs["raw"], partition_cols=partition_cols, compression=compression)
    _write_partitioned(vlf_raw, out_dirs["raw"], partition_cols=partition_cols, compression=compression)
    _write_partitioned(seismic_raw, out_dirs["raw"], partition_cols=partition_cols, compression=compression)

    dq_raw = {
        "geomag": _dq_basic(geomag_raw),
        "aef": _dq_basic(aef_raw),
        "vlf": _dq_basic(vlf_raw),
        "seismic": _dq_basic(seismic_raw),
        "meta": {"geomag": geomag_meta, "aef": aef_meta, "vlf": vlf_meta, "seismic": seismic_meta},
    }
    write_json(out_dirs["reports"] / "dq_raw_bronze.json", dq_raw)

    # 2) standard/clean
    preprocess_cfg = config.get("preprocess", {})
    kalman_cfg = (preprocess_cfg.get("geomag") or {"method": "kalman"})
    geomag_std = geomag_raw.copy()
    filter_effect = {"enabled": False}
    if not geomag_raw.empty and (kalman_cfg.get("method") == "kalman"):
        filter_effect = {"enabled": True}
        geomag_std, filter_effect = _apply_kalman(
            geomag_raw,
            q_scale=float(kalman_cfg.get("params", {}).get("Q", 1e-5)),
            r_scale=float(kalman_cfg.get("params", {}).get("R", 1e-2)),
        )
    aef_std = aef_raw.copy()
    vlf_std = vlf_raw.copy()
    seismic_std = seismic_raw.copy()

    geomag_std = _add_common_columns(geomag_std, units="nT", stage="standard_silver")
    aef_std = _add_common_columns(aef_std, units="V/m", stage="standard_silver")
    vlf_std = _add_common_columns(vlf_std, units="unknown", stage="standard_silver")
    seismic_std = _add_common_columns(seismic_std, units="counts", stage="standard_silver")

    _write_partitioned(geomag_std, out_dirs["standard"], partition_cols=partition_cols, compression=compression)
    _write_partitioned(aef_std, out_dirs["standard"], partition_cols=partition_cols, compression=compression)
    _write_partitioned(vlf_std, out_dirs["standard"], partition_cols=partition_cols, compression=compression)
    _write_partitioned(seismic_std, out_dirs["standard"], partition_cols=partition_cols, compression=compression)

    dq_standard = {
        "geomag": _dq_basic(geomag_std),
        "aef": _dq_basic(aef_std),
        "vlf": _dq_basic(vlf_std),
        "seismic": _dq_basic(seismic_std),
    }
    write_json(out_dirs["reports"] / "dq_standard_silver.json", dq_standard)
    write_json(out_dirs["reports"] / "filter_effect.json", filter_effect)

    write_json(
        out_dirs["reports"] / "compression.json",
        {
            "raw_bytes": _dir_size_bytes(out_dirs["raw"]),
            "standard_bytes": _dir_size_bytes(out_dirs["standard"]),
            "compression_ratio": (_dir_size_bytes(out_dirs["raw"]) / _dir_size_bytes(out_dirs["standard"])) if _dir_size_bytes(out_dirs["standard"]) else None,
        },
    )

    write_json(out_dirs["reports"] / "config_snapshot.json", {"config_path": str(config_path), "config": _stringify(config)})
    return {
        "raw": {"geomag": geomag_raw, "aef": aef_raw, "vlf": vlf_raw, "seismic": seismic_raw},
        "standard": {"geomag": geomag_std, "aef": aef_std, "vlf": vlf_std, "seismic": seismic_std},
        "out_dirs": out_dirs,
    }


# ------------------------ Link Pipeline ------------------------ #
def _align_by_interval(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    dt = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.floor(interval)
    out = df.copy()
    out["_ts"] = dt
    grouped = (
        out.groupby(["_ts", "source", "station_id", "channel", "units", "lat", "lon", "elev", "quality_flags"], as_index=False)["value"]
        .mean()
        .rename(columns={"_ts": "ts"})
    )
    grouped["ts_ms"] = (grouped["ts"].astype("int64") // 1_000_000).astype("int64")
    return grouped.drop(columns=["ts"])


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


def link_pipeline(config: dict[str, Any], *, config_path: Path) -> None:
    outputs_dir: Path = config["outputs_dir"]
    out_dirs = _ensure_dirs(outputs_dir)

    # 加载 standard 数据
    std_dir = out_dirs["standard"]
    if not std_dir.exists():
        raise RuntimeError("standard_silver not found, please run build first")
    std_df = pd.read_parquet(std_dir) if any(std_dir.glob("*")) else pd.DataFrame()

    link_cfg = config.get("link", {})
    align_interval = link_cfg.get("align_interval", "1min")
    n_hours = int(link_cfg.get("N_hours", 72))
    m_hours = int(link_cfg.get("M_hours", 24))
    radius_km = float(link_cfg.get("K_km", 100))

    stations = _collect_stations([std_df])
    sindex = SpatialIndex(stations)

    dq_linked_list: list[dict[str, Any]] = []
    dq_features_list: list[dict[str, Any]] = []

    for event in config.get("events", []):
        window_start = event.time_utc - timedelta(hours=n_hours)
        window_end = event.time_utc + timedelta(hours=m_hours)

        # 时窗筛选
        window_mask = (pd.to_datetime(std_df["ts_ms"], unit="ms", utc=True) >= window_start) & (
            pd.to_datetime(std_df["ts_ms"], unit="ms", utc=True) <= window_end
        )
        df_event = std_df[window_mask].copy()

        # 空间筛选
        hits = sindex.query_radius(lat=event.lat, lon=event.lon, radius_km=radius_km)
        keep_ids = {h.station.station_id for h in hits}
        df_event = df_event[df_event["station_id"].isin(keep_ids)].copy()

        aligned = _align_by_interval(df_event, align_interval)

        # 保存 linked
        event_dir = out_dirs["linked"] / f"event_id={event.event_id}"
        event_dir.mkdir(parents=True, exist_ok=True)
        if not aligned.empty:
            aligned.to_parquet(event_dir / "aligned.parquet", index=False)
        write_json(
            event_dir / "event.json",
            {
                "event_id": event.event_id,
                "time_utc": event.time_utc.isoformat(),
                "lat": event.lat,
                "lon": event.lon,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "radius_km": radius_km,
            },
        )
        write_json(
            event_dir / "stations.json",
            {
                "event_id": event.event_id,
                "stations": [
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
            },
        )

        dq_linked_list.append({"event_id": event.event_id, "aligned_rows": int(aligned.shape[0]), "station_hits": len(hits)})

        # 特征/异常
        anomaly_df, anomaly_report = _score_anomaly(aligned, event_time=event.time_utc, z_threshold=float(link_cfg.get("z_threshold", 3.0)))
        features_dir = out_dirs["features"] / f"event_id={event.event_id}"
        features_dir.mkdir(parents=True, exist_ok=True)
        if not anomaly_df.empty:
            anomaly_df.to_parquet(features_dir / "features.parquet", index=False)
        dq_features_list.append({"event_id": event.event_id, **anomaly_report})

        # 简单可视化
        if not aligned.empty:
            plots_dir = out_dirs["plots"] / "figures" / event.event_id
            plots_dir.mkdir(parents=True, exist_ok=True)
            series = []
            sample_geomag = aligned[aligned["source"] == "geomag"]
            if not sample_geomag.empty:
                x = sample_geomag[sample_geomag["channel"] == "X"].sort_values("ts_ms")
                if not x.empty:
                    series.append({"title": "geomag:X", "df": x[["ts_ms", "value"]]})
            sample_aef = aligned[aligned["source"] == "aef"]
            if not sample_aef.empty:
                series.append({"title": "aef:Z", "df": sample_aef.sort_values("ts_ms")[["ts_ms", "value"]]})
            sample_seismic = aligned[aligned["source"] == "seismic"]
            if not sample_seismic.empty:
                s = sample_seismic[sample_seismic["channel"] == "rms"].sort_values("ts_ms")
                if not s.empty:
                    series.append({"title": "seismic:rms", "df": s[["ts_ms", "value"]]})
            if series:
                ts_fig = make_event_timeseries_plot(event_id=event.event_id, event_time=event.time_utc, series=series)
                save_plot_json(ts_fig, plots_dir / "plot_timeseries.json")
                save_plot_html(ts_fig, out_dirs["plots"] / "html" / event.event_id / "plot_timeseries.html")

            heatmap_fig = make_anomaly_heatmap(event_id=event.event_id, df=anomaly_df[["station_id", "ts_ms", "anomaly_score"]] if not anomaly_df.empty else anomaly_df)
            save_plot_json(heatmap_fig, plots_dir / "plot_heatmap.json")
            save_plot_html(heatmap_fig, out_dirs["plots"] / "html" / event.event_id / "plot_heatmap.html")

        # API smoke test
        from exportblock.api.smoke_test import run_smoke_test

        logs = run_smoke_test(outputs_dir=outputs_dir, event_id=event.event_id)
        write_json(out_dirs["api_tests"] / f"logs_{event.event_id}.json", logs)

    write_json(out_dirs["reports"] / "dq_linked.json", {"events": dq_linked_list})
    write_json(out_dirs["reports"] / "dq_features.json", {"events": dq_features_list})


# ------------------------ Utils ------------------------ #
def _stringify(config: dict[str, Any]) -> dict[str, Any]:
    def conv(v):
        if isinstance(v, Event):
            return {
                "event_id": v.event_id,
                "time_utc": v.time_utc.isoformat(),
                "lat": v.lat,
                "lon": v.lon,
                "depth_km": v.depth_km,
                "mag": v.mag,
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
