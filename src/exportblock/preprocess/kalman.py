from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class KalmanParams:
    q: float
    r: float


def kalman_1d(values: np.ndarray, *, q: float, r: float) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return values.copy()

    x = np.empty_like(values, dtype=np.float64)
    p = 1.0
    q = float(q)
    r = float(r)

    valid = np.isfinite(values)
    if not valid.any():
        return np.full_like(values, np.nan, dtype=np.float64)
    first = int(np.argmax(valid))
    x[:first] = np.nan
    x_hat = float(values[first])
    x[first] = x_hat

    for i in range(first + 1, values.size):
        p = p + q
        z = values[i]
        if not np.isfinite(z):
            x[i] = x_hat
            continue
        k = p / (p + r)
        x_hat = x_hat + k * (z - x_hat)
        p = (1.0 - k) * p
        x[i] = x_hat
    return x


def auto_params(values: np.ndarray, *, q_scale: float = 1e-5, r_scale: float = 1e-2) -> KalmanParams:
    values = np.asarray(values, dtype=np.float64)
    v = values[np.isfinite(values)]
    if v.size < 2:
        return KalmanParams(q=1e-6, r=1e-2)
    var = float(np.nanvar(v))
    var = max(var, 1e-12)
    return KalmanParams(q=var * float(q_scale), r=var * float(r_scale))

