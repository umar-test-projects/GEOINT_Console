"""Change mask -> true polygons.

Two measured facts drive this module:

* Raw thresholding is ~98% speckle. Amazon NDVI 2019->2024 gave 5,761 polygons
  raw and 69 after sieving, retaining 100 ha of the 300 ha raw area. Bangalore
  SAR gave 57,368 raw and 120 after speckle filter + sieve.
* Loss and gain must both be reported. Near-symmetric loss/gain is the
  signature of noise, not of change, and hiding one direction hides that tell.

satellite-mvp emits axis-aligned rectangles (analyst_workflow/export.py:108).
This emits the actual change footprint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
from rasterio.features import shapes, sieve
from scipy import ndimage
from rasterio.warp import transform_geom
from shapely.geometry import shape as shapely_shape

DEFAULT_SIEVE_SIZE = 50  # pixels; 50 px = 0.5 ha at 10 m
M2_PER_HA = 10_000.0

LOSS, GAIN = "loss", "gain"

#: Most regions one run returns. A near-zero threshold or no minimum patch
#: turns noise into hundreds of thousands of polygons, each vectorized,
#: classified, stored, drawn and embedded for search. Past this only the largest
#: are kept, and the run says so.
MAX_REGIONS = 2000


def keep_largest(masks: dict, limit: int | None = None) -> tuple[dict, int]:
    """Clear all but the `limit` largest connected regions across `masks`.

    Returns the reduced masks and how many regions were dropped. Regions tied at
    the cutoff size are all kept, so a few over `limit` can survive; callers
    trim the polygons afterwards.
    """
    limit = MAX_REGIONS if limit is None else limit
    labelled = {}
    for key, mask in masks.items():
        if mask is None or not mask.any():
            continue
        labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
        labelled[key] = (labels, np.bincount(labels.ravel(), minlength=n + 1)[1:])
    sizes = [region_sizes for _, region_sizes in labelled.values()]
    total = sum(len(s) for s in sizes)
    if total <= limit:
        return masks, 0
    everything = np.concatenate(sizes)
    cutoff = np.partition(everything, total - limit)[total - limit]
    out = dict(masks)
    for key, (labels, region_sizes) in labelled.items():
        out[key] = np.concatenate(([False], region_sizes >= cutoff))[labels]
    return out, int((everything < cutoff).sum())


def dropped_warning(dropped: int) -> str:
    return (
        f"{dropped:,} smaller regions were left out: only the {MAX_REGIONS:,} largest are "
        "returned. Hectare totals still include every region; raise the threshold or the "
        "minimum patch to see fewer, larger ones."
    )


@dataclass
class ChangeResult:
    """Detection outcome, including how much of the scene could be seen at all."""

    features: list[dict]
    clear_fraction: float
    loss_ha: float
    gain_ha: float
    threshold: float
    sieve_size: int
    warnings: list[str] = field(default_factory=list)

    @property
    def total_ha(self) -> float:
        return self.loss_ha + self.gain_ha

    @property
    def low_confidence(self) -> bool:
        return bool(self.warnings)

    def geojson(self) -> dict:
        return {"type": "FeatureCollection", "features": self.features}


def direction_masks(
    diff: np.ndarray, valid: np.ndarray, threshold: float
) -> tuple[np.ndarray, np.ndarray]:
    """Split a difference image into loss and gain masks.

    Invalid pixels are excluded from both — an unobserved pixel is neither
    changed nor unchanged.
    """
    if threshold <= 0:
        raise ValueError(f"threshold must be positive, got {threshold}")
    d = np.where(valid & np.isfinite(diff), diff, 0.0)
    return (d <= -threshold), (d >= threshold)


def _sieved(mask: np.ndarray, size: int) -> np.ndarray:
    """Drop connected patches smaller than `size` pixels."""
    m = mask.astype("uint8", copy=False)
    if size and size > 1 and m.any():
        m = sieve(m, size=size)
    return m.astype(bool)


def polygonize(
    mask: np.ndarray, transform, crs, direction: str, pixel_area_m2: float
) -> Iterator[dict]:
    """Vectorize a boolean mask into WGS84 GeoJSON features.

    Area is measured on the projected geometry (UTM metres) before reprojection,
    because computing area from degrees is wrong by a latitude-dependent factor.
    """
    if not mask.any():
        return
    m = mask.astype("uint8")
    for geom, value in shapes(m, mask=mask, transform=transform):
        if value != 1:
            continue
        area_m2 = shapely_shape(geom).area
        yield {
            "type": "Feature",
            "geometry": transform_geom(crs, "EPSG:4326", geom, precision=6),
            "properties": {
                "direction": direction,
                "area_ha": round(area_m2 / M2_PER_HA, 4),
                "pixels": int(round(area_m2 / pixel_area_m2)) if pixel_area_m2 else None,
            },
        }


def detect(
    diff: np.ndarray,
    valid: np.ndarray,
    transform,
    crs,
    *,
    threshold: float,
    sieve_size: int = DEFAULT_SIEVE_SIZE,
    pixel_size_m: float = 10.0,
    min_clear_fraction: float = 0.5,
    extra_warnings: list[str] | None = None,
) -> ChangeResult:
    """Threshold -> sieve -> polygonize, in both directions.

    `valid` is the intersection of what both dates could actually observe.
    """
    diff = np.asarray(diff, dtype="float32")
    valid = np.asarray(valid, dtype=bool)
    if diff.shape != valid.shape:
        raise ValueError(f"diff {diff.shape} and valid {valid.shape} must match")

    clear_fraction = float(valid.mean()) if valid.size else 0.0
    pixel_area = float(pixel_size_m) ** 2

    loss_mask, gain_mask = direction_masks(diff, valid, threshold)
    loss_mask = _sieved(loss_mask, sieve_size)
    gain_mask = _sieved(gain_mask, sieve_size)

    kept, dropped = keep_largest({LOSS: loss_mask, GAIN: gain_mask})
    features = [
        f
        for direction in (LOSS, GAIN)
        for f in polygonize(kept[direction], transform, crs, direction, pixel_area)
    ]
    if len(features) > MAX_REGIONS:
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        dropped += len(features) - MAX_REGIONS
        del features[MAX_REGIONS:]

    loss_ha = float(loss_mask.sum()) * pixel_area / M2_PER_HA
    gain_ha = float(gain_mask.sum()) * pixel_area / M2_PER_HA

    warnings = list(extra_warnings or [])
    if dropped:
        warnings.append(dropped_warning(dropped))
    if clear_fraction < min_clear_fraction:
        warnings.append(
            f"only {clear_fraction:.0%} of the area was observed by both dates; "
            "unobserved ground is not 'unchanged'"
        )
    # Near-symmetric loss and gain is what sensor noise looks like.
    total = loss_ha + gain_ha
    if total > 0 and min(loss_ha, gain_ha) / total > 0.4:
        warnings.append(
            f"loss ({loss_ha:.1f} ha) and gain ({gain_ha:.1f} ha) are near-symmetric, "
            "which is a noise signature rather than directional change"
        )

    return ChangeResult(
        features=features,
        clear_fraction=clear_fraction,
        loss_ha=loss_ha,
        gain_ha=gain_ha,
        threshold=float(threshold),
        sieve_size=int(sieve_size),
        warnings=warnings,
    )
