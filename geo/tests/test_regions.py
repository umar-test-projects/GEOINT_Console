"""The region cap: a noisy run keeps its largest regions and says what it left out."""
import numpy as np
import pytest
from rasterio.transform import from_origin

import detect
import fuse

TRANSFORM = from_origin(500_000, 1_000_000, 10, 10)
CRS = "EPSG:32643"


def runs(sizes, shape=(4, 200)):
    """One-row runs of the given lengths, far enough apart to stay separate."""
    mask = np.zeros(shape, bool)
    x = 0
    for size in sizes:
        mask[1, x:x + size] = True
        x += size + 2
    return mask


def test_keep_largest_keeps_the_biggest_regions():
    reduced, dropped = detect.keep_largest({"a": runs([1, 5, 3]), "b": runs([8, 2])}, limit=2)
    assert dropped == 3
    assert reduced["a"].sum() == 5 and reduced["b"].sum() == 8


def test_keep_largest_is_a_no_op_under_the_limit():
    masks = {"a": runs([1, 2])}
    reduced, dropped = detect.keep_largest(masks, limit=5)
    assert dropped == 0 and reduced is masks


def test_drone_detection_caps_regions_and_says_so(monkeypatch):
    monkeypatch.setattr(detect, "MAX_REGIONS", 2)
    mask = runs([3, 7, 5, 9])
    diff = np.where(mask, -1.0, 0.0).astype("float32")
    res = detect.detect(diff, np.ones_like(mask), TRANSFORM, CRS, threshold=0.5, sieve_size=0)
    assert sorted(f["properties"]["pixels"] for f in res.features) == [7, 9]
    assert any("2 largest" in w for w in res.warnings)
    assert res.loss_ha == pytest.approx(24 * 100 / 10_000)  # every region still counted


def test_fused_run_caps_regions_and_says_so(monkeypatch):
    monkeypatch.setattr(detect, "MAX_REGIONS", 2)
    mask = runs([3, 7, 5, 9])
    s2_diff = np.where(mask, -0.5, 0.0).astype("float32")
    result = fuse.fuse(
        s2_diff, np.ones_like(mask), np.zeros_like(s2_diff), np.zeros_like(mask),
        optical_threshold=0.2, sar_threshold_db=3.0, pixel_area_m2=100.0, sieve_size=0,
    )
    features = fuse.to_features(result, TRANSFORM, CRS)
    assert sorted(f["properties"]["pixels"] for f in features) == [7, 9]
    assert any("2 largest" in w for w in result.stats()["warnings"])
