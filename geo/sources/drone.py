"""Drone orthophoto change detection, flight against flight.

Two deliberate departures from the satellite path:

* Resolution. satellite-mvp's compute_common_grid resamples a pair to the
  COARSER of the two grids (registration.py:86-87). That is right for mixed
  satellite sensors and wrong here: pairing a 5 cm flight against anything
  coarser throws away the entire reason to fly a drone. This module builds the
  common grid at the FINER resolution of the two flights.

* Bands. Most consumer drones are RGB only, so NDVI, NDWI and NDBI are simply
  not computable from them. Rather than silently returning something
  NDVI-shaped, an RGB-only pair falls back to VARI and says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge  # noqa: E402,F401  (puts satellite-mvp on sys.path)
import indices  # noqa: E402
from raster import Grid  # noqa: E402

from change_detection.alignment import phase_correlation_shift  # noqa: E402

#: Band naming by count. Anything with a 4th band is assumed NIR, which is the
#: convention for multispectral drone payloads (RGN / RGNIR).
RGB_BANDS = 3


class DroneError(ValueError):
    """The uploaded raster cannot be used for change detection."""


def inspect(path) -> dict:
    """Validate an orthophoto and report what can be computed from it."""
    path = Path(path)
    if not path.exists():
        raise DroneError(f"{path} does not exist")
    try:
        with rasterio.open(path) as src:
            crs, count, res, bounds = src.crs, src.count, src.res, src.bounds
            dtype = src.dtypes[0]
    except rasterio.errors.RasterioIOError as exc:
        raise DroneError(f"{path.name} is not a readable raster: {exc}") from None

    # Without a CRS the pixels cannot be placed on the ground, so nothing
    # downstream can be trusted. This mirrors ingestion/validator.py:98-134.
    if crs is None:
        raise DroneError(
            f"{path.name} has no CRS. Export the orthophoto georeferenced "
            "(GeoTIFF with a projection) rather than as a plain image."
        )
    if count < RGB_BANDS:
        raise DroneError(
            f"{path.name} has {count} band(s); at least {RGB_BANDS} (RGB) are needed"
        )

    has_nir = count > RGB_BANDS
    return {
        "path": str(path),
        "crs": str(crs),
        "bands": count,
        "resolution_m": round(float(res[0]), 4),
        "bounds": list(bounds),
        "dtype": dtype,
        "has_nir": has_nir,
        "index": "ndvi" if has_nir else "vari",
        "index_note": (
            "NIR band present; NDVI available"
            if has_nir
            else "RGB only, so NDVI is not computable. Falling back to VARI, a "
                 "visible-band vegetation proxy that is weaker than NDVI and "
                 "should not be read as one."
        ),
    }


#: Largest shared grid, in pixels: 5,000 x 5,000, e.g. 250 m square at 5 cm or
#: 1 km at 20 cm. Several float planes this size are held at once, plus the
#: shift search, so past it one comparison exhausts memory.
MAX_GRID_PX = 25_000_000


def common_grid(a, b) -> Grid:
    """Shared grid for two flights: overlapping extent at the FINER resolution.

    Taking the finer resolution is the whole point -- downsampling to the
    coarser flight would discard the detail the drone was flown to capture.
    """
    with rasterio.open(a) as sa, rasterio.open(b) as sb:
        if sa.crs is None or sb.crs is None:
            raise DroneError("both flights must be georeferenced")
        crs = sa.crs
        ba = sa.bounds
        bb = sb.bounds if sb.crs == crs else transform_bounds(sb.crs, crs, *sb.bounds)
        res = min(float(sa.res[0]), float(sb.res[0]))

    left, bottom = max(ba[0], bb[0]), max(ba[1], bb[1])
    right, top = min(ba[2], bb[2]), min(ba[3], bb[3])
    if not (left < right and bottom < top):
        raise DroneError(
            "the two flights do not overlap on the ground; check they cover the same site"
        )

    width = int((right - left) / res)
    height = int((top - bottom) / res)
    if width < 1 or height < 1:
        raise DroneError("flight overlap is smaller than one pixel")
    if width * height > MAX_GRID_PX:
        raise DroneError(
            f"the flights overlap over {width} x {height} px at {res:g} m; the limit is "
            f"{MAX_GRID_PX:,} px. Export the orthophotos at a coarser resolution or "
            "crop them to the area of interest."
        )
    return Grid(str(crs), from_origin(left, top, res, res), width, height, res)


def _read(path, grid: Grid, band: int) -> np.ndarray:
    with rasterio.open(path) as src:
        with WarpedVRT(
            src, crs=grid.crs, transform=grid.transform,
            width=grid.width, height=grid.height, resampling=Resampling.bilinear,
        ) as vrt:
            return vrt.read(band).astype("float32")


def vegetation_index(path, grid: Grid, has_nir: bool) -> np.ndarray:
    """NDVI when a NIR band exists, otherwise the VARI fallback."""
    red, green, blue = _read(path, grid, 1), _read(path, grid, 2), _read(path, grid, 3)
    if has_nir:
        return indices.norm_diff(_read(path, grid, 4), red)
    return indices.vari(green, red, blue)


def compare(before, after, *, max_shift_px: int = 32) -> dict:
    """Detect change between two flights of the same site.

    Returns the difference image, validity mask and grid, ready for detect().
    Alignment uses satellite-mvp's phase-correlation estimator; drone
    orthomosaics of the same site routinely sit tens of pixels apart at drone
    GSD, so the shift tolerance is far wider than the satellite default of 10.
    """
    info_a, info_b = inspect(before), inspect(after)
    has_nir = info_a["has_nir"] and info_b["has_nir"]
    grid = common_grid(before, after)

    idx_a = vegetation_index(before, grid, has_nir)
    idx_b = vegetation_index(after, grid, has_nir)

    notes: list[str] = []
    if info_a["has_nir"] != info_b["has_nir"]:
        notes.append(
            "only one flight carries a NIR band, so both were compared using VARI"
        )
    if not has_nir:
        notes.append(info_a["index_note"])
    # No SCL equivalent exists for drone imagery, so quality degrades to
    # nodata only -- there is no cloud mask to lean on here.
    notes.append("drone imagery has no scene-classification band; validity is nodata only")

    dy, dx, sharpness = phase_correlation_shift(idx_a, idx_b)
    if abs(dy) > max_shift_px or abs(dx) > max_shift_px:
        notes.append(
            f"estimated misalignment of ({dy:.0f}, {dx:.0f}) px exceeds the "
            f"{max_shift_px} px tolerance; co-register the flights before trusting this result"
        )
    else:
        idx_b = np.roll(idx_b, (int(round(dy)), int(round(dx))), axis=(0, 1))

    valid = np.isfinite(idx_a) & np.isfinite(idx_b)
    return {
        "diff": (idx_b - idx_a).astype("float32"),
        "valid": valid,
        "grid": grid,
        "index": "ndvi" if has_nir else "vari",
        "shift_px": [round(float(dy), 2), round(float(dx), 2)],
        "shift_confidence": round(float(sharpness), 2),
        "flights": [info_a, info_b],
        "notes": notes,
    }
