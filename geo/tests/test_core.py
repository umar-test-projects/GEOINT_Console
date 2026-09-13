"""Pure-function tests for the detection core. No network, no fixtures.

One file rather than the four the plan sketched: these are small pure functions
and four near-empty modules would be more scaffolding than test. test_fuse.py
follows separately once fuse.py lands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect  # noqa: E402
import indices  # noqa: E402
import sar  # noqa: E402

CRS = "EPSG:32643"
TRANSFORM = from_origin(500_000, 1_000_000, 10, 10)  # 10 m pixels


# ---------------------------------------------------------------- indices


def test_ndvi_matches_hand_calculation():
    s = np.zeros((11, 8, 8), dtype="float32")
    s[indices.NIR], s[indices.RED] = 0.6, 0.2
    assert indices.from_stack(s, "ndvi")[0, 0] == pytest.approx(0.5)


def test_each_index_uses_its_own_bands():
    s = np.zeros((11, 4, 4), dtype="float32")
    s[indices.GREEN], s[indices.NIR], s[indices.SWIR16] = 0.3, 0.1, 0.5
    assert indices.from_stack(s, "ndwi")[0, 0] == pytest.approx(0.5)   # (.3-.1)/(.3+.1)
    assert indices.from_stack(s, "ndbi")[0, 0] == pytest.approx(2 / 3)  # (.5-.1)/(.5+.1)


def test_zero_denominator_yields_zero_not_nan():
    out = indices.norm_diff(np.zeros((3, 3)), np.zeros((3, 3)))
    assert np.all(out == 0) and np.isfinite(out).all()


def test_index_rejects_stack_without_required_band():
    thin = np.zeros((4, 4, 4), dtype="float32")  # no SWIR
    with pytest.raises(ValueError, match="band index 8"):
        indices.from_stack(thin, "ndbi")


def test_unknown_index_name_rejected():
    with pytest.raises(ValueError, match="unknown index"):
        indices.from_stack(np.zeros((11, 2, 2), dtype="float32"), "nvdi")


def test_scl_mask_excludes_cloud_and_shadow():
    scl = np.array([[4, 5, 6], [3, 8, 9], [10, 11, 0]])
    keep = indices.scl_valid_mask(scl)
    assert keep[0].all()            # vegetation, bare soil, water
    assert not keep[1].any()        # shadow, cloud medium, cloud high
    assert not keep[2].any()        # cirrus, snow, no_data


# -------------------------------------------------------------------- sar


def test_to_db_is_correct_and_nodata_stays_nodata():
    db = sar.to_db(np.array([1.0, 0.1, 0.0, -1.0, np.nan], dtype="float32"))
    assert db[0] == pytest.approx(0.0)
    assert db[1] == pytest.approx(-10.0)
    # zero and negative backscatter are absent measurements, not -inf dB
    assert np.isnan(db[2]) and np.isnan(db[3]) and np.isnan(db[4])


def test_boxcar_ignores_nan_instead_of_smearing_it():
    a = np.array([[1.0, 1.0, 1.0], [1.0, np.nan, 1.0], [1.0, 1.0, 1.0]], dtype="float32")
    out = sar.nan_safe_boxcar(a, 3)
    assert np.isfinite(out).all()
    assert out[1, 1] == pytest.approx(1.0)  # mean of the observed neighbours


def test_log_ratio_is_db_difference():
    before = np.full((4, 4), -12.0, dtype="float32")
    after = np.full((4, 4), -6.0, dtype="float32")
    assert np.allclose(sar.log_ratio(before, after), 6.0)


def test_six_db_step_detected_at_three_db_threshold():
    before = np.full((60, 60), -12.0, dtype="float32")
    after = before.copy()
    after[20:40, 20:40] = -6.0  # 20x20 px = 4 ha
    lr = sar.log_ratio(sar.speckle_filter(before), sar.speckle_filter(after))
    res = detect.detect(
        lr, np.ones_like(lr, bool), TRANSFORM, CRS,
        threshold=sar.DEFAULT_THRESHOLD_DB, sieve_size=50,
    )
    assert len(res.features) == 1
    assert res.gain_ha == pytest.approx(4.0, abs=0.5)


# ----------------------------------------------------------------- detect


def _diff_with_block(value: float = -0.5) -> np.ndarray:
    d = np.zeros((100, 100), dtype="float32")
    d[40:50, 40:50] = value  # 10x10 px = 1 ha at 10 m
    return d


def test_block_becomes_one_polygon_of_exact_area():
    res = detect.detect(
        _diff_with_block(), np.ones((100, 100), bool), TRANSFORM, CRS,
        threshold=0.2, sieve_size=50,
    )
    assert len(res.features) == 1
    assert res.features[0]["properties"]["area_ha"] == pytest.approx(1.0)
    assert res.features[0]["properties"]["direction"] == detect.LOSS
    assert res.loss_ha == pytest.approx(1.0)


def test_sieve_removes_single_pixel_speckle():
    d = _diff_with_block()
    d[5, 5] = -0.9  # lone pixel, far above threshold
    res = detect.detect(
        d, np.ones((100, 100), bool), TRANSFORM, CRS, threshold=0.2, sieve_size=50
    )
    assert len(res.features) == 1  # the speck is gone, the block survives


def test_geometry_is_reprojected_to_wgs84():
    res = detect.detect(
        _diff_with_block(), np.ones((100, 100), bool), TRANSFORM, CRS,
        threshold=0.2, sieve_size=50,
    )
    lon, lat = res.features[0]["geometry"]["coordinates"][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_gain_and_loss_are_reported_separately():
    d = np.zeros((100, 100), dtype="float32")
    d[10:20, 10:20] = -0.5
    d[60:70, 60:70] = 0.5
    res = detect.detect(
        d, np.ones((100, 100), bool), TRANSFORM, CRS, threshold=0.2, sieve_size=50
    )
    dirs = sorted(f["properties"]["direction"] for f in res.features)
    assert dirs == [detect.GAIN, detect.LOSS]
    assert res.loss_ha == pytest.approx(1.0) and res.gain_ha == pytest.approx(1.0)


def test_fully_clouded_scene_yields_nothing_and_says_so():
    res = detect.detect(
        _diff_with_block(), np.zeros((100, 100), bool), TRANSFORM, CRS,
        threshold=0.2, sieve_size=50,
    )
    assert res.features == []
    assert res.clear_fraction == 0.0
    assert res.low_confidence
    assert any("observed" in w for w in res.warnings)


def test_invalid_pixels_cannot_produce_change():
    valid = np.ones((100, 100), bool)
    valid[40:50, 40:50] = False  # mask out exactly the changed block
    res = detect.detect(
        _diff_with_block(), valid, TRANSFORM, CRS, threshold=0.2, sieve_size=50
    )
    assert res.features == []


def test_symmetric_loss_and_gain_is_flagged_as_noise():
    d = np.zeros((100, 100), dtype="float32")
    d[10:20, 10:20] = -0.5
    d[60:70, 60:70] = 0.5
    res = detect.detect(
        d, np.ones((100, 100), bool), TRANSFORM, CRS, threshold=0.2, sieve_size=50
    )
    assert any("symmetric" in w for w in res.warnings)


def test_mismatched_shapes_rejected():
    with pytest.raises(ValueError, match="must match"):
        detect.detect(
            np.zeros((10, 10), "float32"), np.ones((5, 5), bool), TRANSFORM, CRS,
            threshold=0.2,
        )


def test_nonpositive_threshold_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        detect.detect(
            np.zeros((10, 10), "float32"), np.ones((10, 10), bool), TRANSFORM, CRS,
            threshold=0.0,
        )
