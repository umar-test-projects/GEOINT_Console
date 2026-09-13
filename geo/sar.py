"""Sentinel-1 SAR processing.

Speckle is multiplicative and severe: on a real Bangalore pair, raw thresholding
produced 57,368 polygons versus 5,761 for optical on the same AOI. Filtering is
not optional here.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter

#: Backscatter change considered significant, in dB.
DEFAULT_THRESHOLD_DB = 3.0
#: Boxcar multilook window. 5x5 measured: 57,368 -> 3,764 raw polygons.
DEFAULT_SPECKLE_WINDOW = 5


def to_db(gamma0: np.ndarray) -> np.ndarray:
    """Linear gamma0 backscatter -> decibels.

    Non-positive and non-finite samples become NaN: log of zero is not a low
    value, it is an absent measurement, and conflating the two invents change
    at every nodata pixel.
    """
    g = np.asarray(gamma0, dtype="float32")
    valid = np.isfinite(g) & (g > 0)
    return np.where(valid, 10.0 * np.log10(np.where(valid, g, 1.0)), np.nan).astype("float32")


def nan_safe_boxcar(arr: np.ndarray, size: int) -> np.ndarray:
    """Boxcar mean that ignores NaN instead of propagating it.

    A plain uniform_filter over NaN smears the hole across the whole window.
    Filtering values and the validity mask separately, then dividing, gives the
    mean of the *observed* samples in each window.
    """
    if size <= 1:
        return np.asarray(arr, dtype="float32")
    a = np.asarray(arr, dtype="float32")
    valid = np.isfinite(a)
    filled = np.where(valid, a, 0.0)
    total = uniform_filter(filled, size=size, mode="nearest")
    weight = uniform_filter(valid.astype("float32"), size=size, mode="nearest")
    out = np.where(weight > 0, total / np.where(weight > 0, weight, 1.0), np.nan)
    return out.astype("float32")


def speckle_filter(db: np.ndarray, size: int = DEFAULT_SPECKLE_WINDOW) -> np.ndarray:
    """Multilook a dB image to suppress speckle.

    ponytail: boxcar multilook. Swap for Lee / Refined-Lee if edge blurring
    starts hiding small features — boxcar trades edge sharpness for simplicity.
    """
    return nan_safe_boxcar(db, size)


def log_ratio(db_before: np.ndarray, db_after: np.ndarray) -> np.ndarray:
    """SAR change signal.

    In dB space the classic log-ratio 10*log10(after/before) is just the
    difference, so this stays a subtraction.
    """
    return (np.asarray(db_after, dtype="float32") - np.asarray(db_before, dtype="float32"))
