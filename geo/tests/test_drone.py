"""Drone ingest tests. Builds real GeoTIFFs on disk; no network."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect  # noqa: E402
from sources import drone  # noqa: E402

CRS = "EPSG:32643"
RES = 0.05  # 5 cm, a realistic drone GSD
SIZE = 200


def write_tif(path, bands, *, crs=CRS, res=RES, origin=(500_000, 1_000_000)):
    arr = np.stack(bands).astype("float32")
    with rasterio.open(
        path, "w", driver="GTiff",
        height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
        dtype="float32", crs=crs, transform=from_origin(origin[0], origin[1], res, res),
    ) as dst:
        dst.write(arr)
    return path


def flat(value):
    return np.full((SIZE, SIZE), value, dtype="float32")


def rgb(veg=False):
    """Green-dominant when vegetated, so VARI separates the two cases."""
    return [flat(0.2), flat(0.6 if veg else 0.2), flat(0.2)]


def rgbn(veg=False):
    return rgb(veg) + [flat(0.8 if veg else 0.2)]


# ------------------------------------------------------------- validation


def test_missing_crs_is_rejected_with_actionable_message(tmp_path):
    p = write_tif(tmp_path / "nocrs.tif", rgb(), crs=None)
    with pytest.raises(drone.DroneError, match="no CRS"):
        drone.inspect(p)


def test_too_few_bands_rejected(tmp_path):
    p = write_tif(tmp_path / "gray.tif", [flat(0.5)])
    with pytest.raises(drone.DroneError, match="at least 3"):
        drone.inspect(p)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(drone.DroneError, match="does not exist"):
        drone.inspect(tmp_path / "nope.tif")


def test_non_raster_rejected(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not a raster")
    with pytest.raises(drone.DroneError, match="not a readable raster"):
        drone.inspect(p)


# ------------------------------------------------------- band handling


def test_rgb_only_falls_back_to_vari_and_says_so(tmp_path):
    info = drone.inspect(write_tif(tmp_path / "rgb.tif", rgb()))
    assert info["has_nir"] is False
    assert info["index"] == "vari"
    assert "not computable" in info["index_note"]
    assert "should not be read as one" in info["index_note"]


def test_four_band_flight_uses_ndvi(tmp_path):
    info = drone.inspect(write_tif(tmp_path / "rgbn.tif", rgbn()))
    assert info["has_nir"] is True and info["index"] == "ndvi"


def test_mixed_band_counts_degrade_to_vari_for_both(tmp_path):
    a = write_tif(tmp_path / "a.tif", rgbn(veg=True))
    b = write_tif(tmp_path / "b.tif", rgb(veg=False))
    out = drone.compare(a, b)
    assert out["index"] == "vari"
    assert any("only one flight carries a NIR" in n for n in out["notes"])


# --------------------------------------------------------------- grid


def test_common_grid_keeps_the_finer_resolution(tmp_path):
    """Never downsample to the coarser flight -- that is the whole point."""
    fine = write_tif(tmp_path / "fine.tif", rgb(), res=0.05)
    coarse = write_tif(tmp_path / "coarse.tif", rgb(), res=0.20)
    grid = drone.common_grid(fine, coarse)
    assert grid.resolution == pytest.approx(0.05)


def test_non_overlapping_flights_rejected(tmp_path):
    a = write_tif(tmp_path / "a.tif", rgb(), origin=(500_000, 1_000_000))
    b = write_tif(tmp_path / "b.tif", rgb(), origin=(900_000, 1_000_000))
    with pytest.raises(drone.DroneError, match="do not overlap"):
        drone.common_grid(a, b)


def test_oversized_overlap_rejected_before_reading(tmp_path, monkeypatch):
    """A flight too large to hold in memory is refused from its header alone."""
    monkeypatch.setattr(drone, "MAX_GRID_PX", SIZE * SIZE // 2)
    a = write_tif(tmp_path / "a.tif", rgb())
    b = write_tif(tmp_path / "b.tif", rgb())
    with pytest.raises(drone.DroneError, match="coarser resolution"):
        drone.common_grid(a, b)


# ------------------------------------------------------------ detection


def test_vegetation_loss_between_flights_is_detected(tmp_path):
    before = rgbn(veg=True)
    after = rgbn(veg=True)
    for band, value in zip(after, (0.2, 0.2, 0.2, 0.2)):  # cleared patch
        band[50:100, 50:100] = value
    a = write_tif(tmp_path / "before.tif", before)
    b = write_tif(tmp_path / "after.tif", after)

    out = drone.compare(a, b)
    assert out["index"] == "ndvi"
    res = detect.detect(
        out["diff"], out["valid"], out["grid"].transform, out["grid"].crs,
        threshold=0.2, sieve_size=50, pixel_size_m=out["grid"].resolution,
    )
    assert len(res.features) == 1
    assert res.loss_ha > 0 and res.gain_ha == 0
    # 50x50 px at 5 cm = 2.5 m x 2.5 m = 6.25 m2 = 0.000625 ha
    assert res.features[0]["properties"]["area_ha"] == pytest.approx(0.000625, rel=0.05)


def test_identical_flights_report_no_change(tmp_path):
    a = write_tif(tmp_path / "a.tif", rgbn(veg=True))
    b = write_tif(tmp_path / "b.tif", rgbn(veg=True))
    out = drone.compare(a, b)
    res = detect.detect(
        out["diff"], out["valid"], out["grid"].transform, out["grid"].crs,
        threshold=0.2, sieve_size=50, pixel_size_m=out["grid"].resolution,
    )
    assert res.features == []


def test_drone_results_declare_the_absent_cloud_mask(tmp_path):
    a = write_tif(tmp_path / "a.tif", rgbn())
    b = write_tif(tmp_path / "b.tif", rgbn())
    out = drone.compare(a, b)
    assert any("nodata only" in n for n in out["notes"])
