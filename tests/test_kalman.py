from __future__ import annotations

import numpy as np

from exportblock.preprocess.kalman import auto_params, kalman_1d


def test_kalman_basic_smooth():
    x = np.array([0.0, 0.0, 10.0, 0.0, 0.0], dtype=float)
    params = auto_params(x, q_scale=1e-5, r_scale=1e-2)
    y = kalman_1d(x, q=params.q, r=params.r)
    assert y.shape == x.shape
    assert np.isfinite(y).all()
    assert y[2] < 10.0

