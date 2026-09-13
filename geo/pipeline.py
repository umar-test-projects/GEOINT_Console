"""End-to-end: an AOI and two date windows in, tiered change evidence out.

Degrades honestly. If only one sensor has usable scenes the run still produces
results, clearly labelled as single-sensor, rather than failing outright or --
worse -- quietly presenting half the evidence as if it were the whole picture.
"""
from __future__ import annotations

import numpy as np

import bridge  # noqa: F401  (puts satellite-mvp on sys.path)
import classify
import fuse
from raster import target_grid
from sources import s1 as s1src
from sources import s2 as s2src

DEFAULTS = bridge.SETTINGS["detect"]


class NoDataError(RuntimeError):
    """Neither sensor could supply a usable pair for this AOI and window."""


def _empty(shape):
    return np.zeros(shape, dtype="float32"), np.zeros(shape, dtype=bool)


def _load_s2(bbox, grid, window_a, window_b, index, max_cloud):
    a = s2src.best_scene(bbox, *window_a, max_cloud=max_cloud)
    b = s2src.best_scene(bbox, *window_b, max_cloud=max_cloud)
    # All three indices, not just the selected one: classification needs the
    # whole spectral picture, and NIR is shared so it costs four band reads
    # rather than six.
    idx_a, val_a, scl_a = s2src.load_all(a, grid, bbox)
    idx_b, val_b, scl_b = s2src.load_all(b, grid, bbox)
    planes = {name: idx_b[name] - idx_a[name] for name in idx_a}
    # Split the unusable pixels by cause: never imaged (outside the granule
    # footprint) versus imaged but obscured. Reporting a footprint edge as
    # cloud would overstate what the weather cost us.
    never_imaged = (scl_a == s2src.SCL_NODATA) | (scl_b == s2src.SCL_NODATA)
    return (planes[index], val_a & val_b,
            [s2src.describe(a), s2src.describe(b)], never_imaged, planes)


def _load_s1(bbox, grid, window_a, window_b, polarization, speckle):
    a, b = s1src.matched_pair(bbox, window_a, window_b)
    db_a, val_a = s1src.load_db(a, grid, bbox, polarization, speckle)
    db_b, val_b = s1src.load_db(b, grid, bbox, polarization, speckle)
    return db_b - db_a, val_a & val_b, [s1src.describe(a), s1src.describe(b)]


def run(
    bbox,
    window_a: tuple[str, str],
    window_b: tuple[str, str],
    *,
    index: str = "ndvi",
    polarization: str = "vv",
    resolution: float = 10.0,
    optical_threshold: float | None = None,
    sar_threshold_db: float | None = None,
    sieve_size: int | None = None,
    speckle: int | None = None,
    max_cloud: float = 80.0,
    use_s1: bool = True,
    use_s2: bool = True,
) -> dict:
    """Run S1+S2 fused change detection over one AOI between two date windows."""
    optical_threshold = optical_threshold or DEFAULTS["optical_threshold"]
    sar_threshold_db = sar_threshold_db or DEFAULTS["sar_threshold_db"]
    sieve_size = sieve_size if sieve_size is not None else DEFAULTS["sieve_size"]

    grid = target_grid(bbox, resolution)
    scenes: list[dict] = []
    notes: list[str] = []

    s2_diff = s2_valid = None
    never_imaged = None
    planes = {}
    if use_s2:
        try:
            s2_diff, s2_valid, described, never_imaged, planes = _load_s2(
                bbox, grid, window_a, window_b, index, max_cloud
            )
            scenes += described
        except Exception as exc:  # noqa: BLE001 - degrade, do not abort
            notes.append(f"Sentinel-2 unavailable: {exc}")

    s1_diff = s1_valid = None
    if use_s1:
        try:
            s1_diff, s1_valid, described = _load_s1(
                bbox, grid, window_a, window_b, polarization, speckle
            )
            scenes += described
        except Exception as exc:  # noqa: BLE001 - degrade, do not abort
            notes.append(f"Sentinel-1 unavailable: {exc}")

    if s2_diff is None and s1_diff is None:
        raise NoDataError(
            "neither sensor returned a usable pair for this AOI and date range. "
            + " ".join(notes)
        )
    if s2_diff is None:
        s2_diff, s2_valid = _empty(grid.shape)
        notes.append("optical evidence missing; results rest on SAR alone")
    if s1_diff is None:
        s1_diff, s1_valid = _empty(grid.shape)
        notes.append("SAR evidence missing; cloud-obscured ground is unobserved")

    result = fuse.fuse(
        s2_diff, s2_valid, s1_diff, s1_valid,
        optical_threshold=optical_threshold,
        sar_threshold_db=sar_threshold_db,
        pixel_area_m2=grid.pixel_area_m2,
        sieve_size=sieve_size,
        s2_never_imaged=never_imaged,
        planes=planes,
    )
    features = fuse.to_features(result, grid.transform, grid.crs)
    fuse.stamp_provenance(
        features,
        [x for x in scenes if x.get("sensor") == "S2"],
        [x for x in scenes if x.get("sensor") == "S1"],
    )

    stats = result.stats()
    stats["warnings"] = stats["warnings"] + notes
    stats["change_types"] = classify.summarize(features)
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "bbox": list(bbox),
            "crs": grid.crs,
            "resolution_m": grid.resolution,
            "grid_shape": list(grid.shape),
            "index": index,
            "polarization": polarization,
            "thresholds": {
                "optical": optical_threshold,
                "sar_db": sar_threshold_db,
                "sieve_px": sieve_size,
            },
            "scenes": scenes,
            "stats": stats,
        },
    }
