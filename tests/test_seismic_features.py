from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from obspy import Trace, UTCDateTime

from exportblock.io.seismic import compute_minute_features


def test_compute_minute_features_one_window():
    sr = 20.0
    data = np.ones(int(sr * 120), dtype=np.float32)
    tr = Trace(data=data)
    tr.stats.network = "XX"
    tr.stats.station = "STA"
    tr.stats.location = ""
    tr.stats.channel = "BHZ"
    tr.stats.starttime = UTCDateTime("2020-01-01T00:00:00Z")
    tr.stats.sampling_rate = sr

    start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2020, 1, 1, 0, 2, 0, tzinfo=timezone.utc)
    df = compute_minute_features(tr, window_start=start, window_end=end)
    assert df.shape[0] == 8
    assert set(df["channel"].unique()) == {"rms", "energy", "peak_abs", "peak_freq"}

