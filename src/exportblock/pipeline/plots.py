from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from exportblock.util.time import dt_to_ts_ms


def _vline_ts_ms(fig: go.Figure, ts_ms: int) -> None:
    fig.add_vline(x=ts_ms, line_width=1, line_dash="dash", line_color="red")


def make_event_timeseries_plot(
    *,
    event_id: str,
    event_time: datetime,
    series: list[dict[str, Any]],
) -> go.Figure:
    fig = make_subplots(rows=len(series), cols=1, shared_xaxes=True, subplot_titles=[s["title"] for s in series])
    for idx, s in enumerate(series, start=1):
        df = s["df"]
        fig.add_trace(
            go.Scatter(x=df["ts_ms"], y=df["value"], mode="lines", name=s["title"]),
            row=idx,
            col=1,
        )
        _vline_ts_ms(fig, dt_to_ts_ms(event_time))
    fig.update_layout(title=f"Event {event_id} - Timeseries", showlegend=False, height=240 * max(len(series), 1))
    fig.update_xaxes(title_text="ts_ms (UTC)")
    return fig


def make_anomaly_heatmap(*, event_id: str, df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return go.Figure()
    pivot = df.pivot_table(index="station_id", columns="ts_ms", values="anomaly_score", aggfunc="max", fill_value=0.0)
    fig = go.Figure(data=go.Heatmap(z=pivot.values, x=pivot.columns, y=pivot.index, colorscale="Viridis"))
    fig.update_layout(title=f"Event {event_id} - Anomaly Heatmap", height=400)
    return fig


def save_plot_json(fig: go.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fig.to_json(), encoding="utf-8")


def save_plot_html(fig: go.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fig.to_html(include_plotlyjs=True, full_html=True), encoding="utf-8")

