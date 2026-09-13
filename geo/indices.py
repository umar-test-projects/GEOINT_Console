"""Spectral indices.

satellite-mvp acquires NIR and SWIR in every derived stack but never uses them —
`change_detection/analyze.py:58-63` truncates the band list to [red, green, blue].
These are the indices that unlock the bands already sitting on disk.
"""
from __future__ import annotations

import numpy as np

# Position of each band within sentinel2_acquisition.config.DERIVED_BAND_ORDER:
# ['B02','B03','B04','B08','B05','B06','B07','B8A','B11','B12','SCL']
BLUE, GREEN, RED, NIR = 0, 1, 2, 3
SWIR16 = 8
SCL = 10

#: index name -> (numerator band, denominator band) as stack positions
INDEX_BANDS = {
    "ndvi": (NIR, RED),      # vegetation
    "ndwi": (GREEN, NIR),    # water
    "ndbi": (SWIR16, NIR),   # built-up
}


def norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b), zero-safe.

    Returns 0 where the denominator vanishes rather than inf/nan, so a dead
    pixel reads as "no signal" instead of poisoning downstream statistics.
    """
    a = np.asarray(a, dtype="float32")
    b = np.asarray(b, dtype="float32")
    denom = a + b
    safe = np.where(denom == 0, 1, denom)
    return np.where(denom == 0, 0, (a - b) / safe).astype("float32")


def from_stack(stack: np.ndarray, name: str) -> np.ndarray:
    """Compute a named index from a full derived stack (bands, rows, cols)."""
    try:
        num, den = INDEX_BANDS[name]
    except KeyError:
        raise ValueError(
            f"unknown index {name!r}; expected one of {sorted(INDEX_BANDS)}"
        ) from None
    if stack.ndim != 3:
        raise ValueError(f"expected a (bands, rows, cols) stack, got shape {stack.shape}")
    if stack.shape[0] <= max(num, den):
        raise ValueError(
            f"{name} needs band index {max(num, den)} but stack has only "
            f"{stack.shape[0]} bands"
        )
    return norm_diff(stack[num], stack[den])


def vari(green: np.ndarray, red: np.ndarray, blue: np.ndarray) -> np.ndarray:
    """Visible Atmospherically Resistant Index — (G-R)/(G+R-B).

    The RGB-only fallback for consumer drones with no NIR band. A weaker
    vegetation proxy than NDVI; label it as such in any UI. Never present
    this as NDVI.
    """
    green = np.asarray(green, dtype="float32")
    red = np.asarray(red, dtype="float32")
    blue = np.asarray(blue, dtype="float32")
    denom = green + red - blue
    safe = np.where(denom == 0, 1, denom)
    return np.where(denom == 0, 0, (green - red) / safe).astype("float32")


#: Sentinel-2 SCL classes kept as valid observations.
#: 2 dark_area, 4 vegetation, 5 bare_soil, 6 water, 7 unclassified.
#: Excluded: 0 no_data, 1 saturated, 3 cloud_shadow, 8/9 cloud, 10 cirrus, 11 snow.
SCL_VALID = (2, 4, 5, 6, 7)


def scl_valid_mask(scl: np.ndarray) -> np.ndarray:
    """Boolean mask of usable pixels from a Sentinel-2 scene classification band."""
    return np.isin(scl, SCL_VALID)
