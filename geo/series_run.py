"""Multi-date orchestration: N timestamps in, dated persistent change out.

`pipeline.run` compares two dates. This compares a series, which buys two
things no two-date comparison can provide:

* **Rejection of transient difference.** A cloud edge, a shadow, a wet field or
  a mis-registered pixel all look exactly like change between two dates. Across
  a series they revert; real change does not.
* **A date for the change**, rather than only the knowledge that it happened
  somewhere inside the interval.

Every observation is compared against the first, never against its predecessor
-- see the note in series.py for why.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

import bridge  # noqa: F401  (puts satellite-mvp on sys.path)
import classify
import fuse
import series
from pipeline import DEFAULTS, NoDataError
from raster import target_grid
from sources import s1 as s1src
from sources import s2 as s2src

#: Remote reads dominate a multi-date run and are almost entirely I/O wait, so
#: dates load concurrently. Kept modest: past a handful of parallel COG reads
#: the remote endpoint, not the client, becomes the bottleneck.
MAX_PARALLEL_DATES = 4
#: Guard rail. Each timestamp costs four optical band reads plus SCL plus SAR.
MAX_STEPS = 8


def _plane_at(stack: np.ndarray, change_index: np.ndarray) -> np.ndarray:
    """Pick each pixel's value from the date its change was detected on."""
    if stack.shape[0] == 0:
        return np.zeros(change_index.shape, dtype="float32")
    idx = np.clip(change_index, 0, stack.shape[0] - 1).astype("int64")
    picked = np.take_along_axis(stack, idx[None, :, :], axis=0)[0]
    return np.where(change_index >= 0, picked, 0.0).astype("float32")


def _load_one_date(bbox, grid, window, max_cloud, polarization, speckle, want_s1, want_s2):
    """Everything for a single timestamp.

    Failures are returned rather than raised: one unusable period should cost
    one observation, not the entire run.
    """
    out = {"window": window, "s2": None, "s1": None, "notes": []}
    if want_s2:
        try:
            scene = s2src.best_scene(bbox, *window, max_cloud=max_cloud)
            planes, valid, scl = s2src.load_all(scene, grid, bbox)
            out["s2"] = {
                "scene": scene,
                "planes": planes,
                "valid": valid,
                "never_imaged": scl == s2src.SCL_NODATA,
                "described": s2src.describe(scene),
            }
        except Exception as exc:  # noqa: BLE001
            out["notes"].append(f"{window[0]} to {window[1]}: no usable Sentinel-2 ({exc})")

    if want_s1:
        try:
            scenes = s1src.search(bbox, *window, orbit_state="descending")
            if not scenes:
                raise s1src.SceneSearchError("no descending pass in this window")
            scene = scenes[0]
            db, valid = s1src.load_db(scene, grid, bbox, polarization, speckle)
            out["s1"] = {
                "scene": scene, "db": db, "valid": valid,
                "described": s1src.describe(scene),
            }
        except Exception as exc:  # noqa: BLE001
            out["notes"].append(f"{window[0]} to {window[1]}: no usable Sentinel-1 ({exc})")
    return out


def run_series(
    bbox,
    start: str,
    end: str,
    steps: int = 4,
    *,
    index: str = "ndvi",
    polarization: str = "vv",
    resolution: float = 10.0,
    optical_threshold: float | None = None,
    sar_threshold_db: float | None = None,
    sieve_size: int | None = None,
    speckle: int | None = None,
    max_cloud: float = 80.0,
    min_persist: int = series.DEFAULT_MIN_PERSIST,
    use_s1: bool = True,
    use_s2: bool = True,
) -> dict:
    """Detect dated, persistent change across `steps` timestamps."""
    optical_threshold = optical_threshold or DEFAULTS["optical_threshold"]
    sar_threshold_db = sar_threshold_db or DEFAULTS["sar_threshold_db"]
    sieve_size = sieve_size if sieve_size is not None else DEFAULTS["sieve_size"]
    steps = max(2, min(int(steps), MAX_STEPS))

    grid = target_grid(bbox, resolution)
    wins = series.windows(start, end, steps)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_DATES) as pool:
        loaded = list(pool.map(
            lambda w: _load_one_date(bbox, grid, w, max_cloud, polarization,
                                     speckle, use_s1, use_s2),
            wins,
        ))

    # Stamp the window index on every scene. Optical and radar are acquired on
    # different days, so they only line up as a table if each is keyed to the
    # window it came from rather than to its own date.
    for step, d in enumerate(loaded):
        for key in ("s2", "s1"):
            if d[key]:
                d[key]["described"]["step"] = step

    notes = [n for d in loaded for n in d["notes"]]
    s2_dates = [d for d in loaded if d["s2"]]
    s1_dates = [d for d in loaded if d["s1"]]

    if len(s2_dates) < 2 and len(s1_dates) < 2:
        raise NoDataError(
            f"need at least two usable observations; found {len(s2_dates)} optical "
            f"and {len(s1_dates)} radar. " + " ".join(notes)
        )

    shape = grid.shape
    scenes: list[dict] = []
    timeline: list[dict] = []

    # --- optical series --------------------------------------------------
    s2_diff = np.zeros(shape, dtype="float32")
    s2_valid = np.zeros(shape, dtype=bool)
    planes: dict[str, np.ndarray] = {}
    never_imaged = None
    optical = None

    if len(s2_dates) >= 2:
        base = s2_dates[0]["s2"]
        later = [d["s2"] for d in s2_dates[1:]]
        diffs = np.stack([d["planes"][index] - base["planes"][index] for d in later])
        valids = np.stack([d["valid"] & base["valid"] for d in later])
        optical = series.detect_series(diffs, valids, optical_threshold, min_persist)

        # `signed` is zero wherever the change did not persist, so transient
        # difference is filtered out before it can reach the detector at all.
        s2_diff = optical.signed
        s2_valid = valids.any(axis=0)
        for name in base["planes"]:
            stack = np.stack([d["planes"][name] - base["planes"][name] for d in later])
            planes[name] = _plane_at(stack, optical.change_index)
        never_imaged = base["never_imaged"]
        for d in later:
            never_imaged = never_imaged | d["never_imaged"]
        scenes += [d["s2"]["described"] for d in s2_dates]
        timeline += [dict(d["s2"]["described"]) for d in s2_dates]
    elif use_s2:
        notes.append("fewer than two usable optical dates; optical evidence unavailable")

    # --- radar series ----------------------------------------------------
    s1_diff = np.zeros(shape, dtype="float32")
    s1_valid = np.zeros(shape, dtype=bool)
    radar_dating = None
    if len(s1_dates) >= 2:
        base = s1_dates[0]["s1"]
        later = [d["s1"] for d in s1_dates[1:]]
        diffs = np.stack([d["db"] - base["db"] for d in later])
        valids = np.stack([d["valid"] & base["valid"] for d in later])
        radar = series.detect_series(diffs, valids, sar_threshold_db, min_persist)
        # Where optical dated the change, report radar at that same date, so
        # the two sensors describe one moment rather than two that merely fall
        # inside the same run.
        if optical is not None:
            s1_diff = np.where(
                optical.change_index >= 0,
                _plane_at(diffs, optical.change_index),
                radar.signed,
            )
        else:
            s1_diff = radar.signed
        s1_valid = valids.any(axis=0)
        radar_dating = (radar.change_index, radar.confirmations,
                        [d["s1"]["described"]["date"] for d in s1_dates[1:]])
        scenes += [d["s1"]["described"] for d in s1_dates]
        timeline += [dict(d["s1"]["described"]) for d in s1_dates]
    elif use_s1:
        notes.append("fewer than two usable radar dates; radar evidence unavailable")

    result = fuse.fuse(
        s2_diff, s2_valid, s1_diff, s1_valid,
        optical_threshold=optical_threshold,
        sar_threshold_db=sar_threshold_db,
        pixel_area_m2=grid.pixel_area_m2,
        sieve_size=sieve_size,
        s2_never_imaged=never_imaged,
        planes=planes,
    )
    if optical is not None:
        result.change_index = optical.change_index
        result.confirmations = optical.confirmations
        result.dates = [d["s2"]["described"]["date"] for d in s2_dates[1:]]
    if radar_dating is not None:
        result.radar_change_index, result.radar_confirmations, result.radar_dates = radar_dating

    features = fuse.to_features(result, grid.transform, grid.crs)
    fuse.stamp_provenance(
        features,
        [d["s2"]["described"] for d in s2_dates],
        [d["s1"]["described"] for d in s1_dates],
    )

    stats = result.stats()
    stats["warnings"] = stats["warnings"] + notes
    stats["change_types"] = classify.summarize(features)
    if optical is not None:
        counts = optical.counts()
        stats["persistence"] = {
            **counts,
            "transient_ha": round(counts["transient_px"] * grid.pixel_area_m2 / 10_000, 2),
            "min_persist": min_persist,
        }

    timeline.sort(key=lambda t: (t["date"], t["sensor"]))
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
            "mode": "series",
            "steps_requested": steps,
            "observations": {"s2": len(s2_dates), "s1": len(s1_dates)},
            "timeline": timeline,
            "thresholds": {
                "optical": optical_threshold,
                "sar_db": sar_threshold_db,
                "sieve_px": sieve_size,
                "min_persist": min_persist,
            },
            "scenes": scenes,
            "stats": stats,
        },
    }
