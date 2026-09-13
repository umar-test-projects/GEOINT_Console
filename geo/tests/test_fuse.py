"""Tier-routing truth table for S1+S2 fusion. No network."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect  # noqa: E402
import fuse  # noqa: E402

CRS = "EPSG:32643"
TRANSFORM = from_origin(500_000, 1_000_000, 10, 10)
N = 40
BLOCK = (slice(0, 20), slice(0, 20))  # 400 px, survives a 50 px sieve


def scene(s2_change=0.0, s1_change=0.0, s2_seen=True, s1_seen=True):
    """One uniform AOI, so the tier under test is unambiguous."""
    s2d = np.full((N, N), s2_change, dtype="float32")
    s1d = np.full((N, N), s1_change, dtype="float32")
    s2v = np.full((N, N), s2_seen, dtype=bool)
    s1v = np.full((N, N), s1_seen, dtype=bool)
    return s2d, s2v, s1d, s1v


def tier_of(**kw):
    r = fuse.fuse(*scene(**kw))
    hot = [t for t, m in r.tiers.items() if m.any()]
    assert len(hot) <= 1, f"expected one tier, got {hot}"
    return hot[0] if hot else None


# ------------------------------------------------------- the truth table


def test_both_sensors_agree_is_confirmed():
    assert tier_of(s2_change=-0.5, s1_change=-6.0) == fuse.CONFIRMED


def test_optical_change_alone_is_optical():
    assert tier_of(s2_change=-0.5, s1_change=0.0) == fuse.OPTICAL


def test_sar_change_alone_where_both_observed_is_sar_only():
    assert tier_of(s2_change=0.0, s1_change=-6.0) == fuse.SAR_ONLY


def test_sar_change_under_cloud_is_sar_gap():
    assert tier_of(s2_change=0.0, s1_change=-6.0, s2_seen=False) == fuse.SAR_GAP


def test_missing_sar_degrades_to_optical_not_to_nothing():
    """A missing S1 scene must not erase optical detections."""
    assert tier_of(s2_change=-0.5, s1_change=0.0, s1_seen=False) == fuse.OPTICAL


def test_no_change_anywhere_yields_no_tier():
    assert tier_of(s2_change=0.0, s1_change=0.0) is None


def test_subthreshold_change_is_not_change():
    assert tier_of(s2_change=-0.1, s1_change=-1.0) is None


def test_tiers_are_mutually_exclusive():
    s2d, s2v, s1d, s1v = scene()
    s2d[0:10, 0:10] = -0.5; s1d[0:10, 0:10] = -6.0     # confirmed
    s2d[0:10, 20:30] = -0.5                             # optical
    s1d[20:30, 0:10] = -6.0                             # sar only
    s2v[20:30, 20:30] = False; s1d[20:30, 20:30] = -6.0  # sar gap
    r = fuse.fuse(s2d, s2v, s1d, s1v)
    overlap = np.zeros((N, N), dtype=int)
    for mask in r.tiers.values():
        overlap += mask.astype(int)
    assert overlap.max() <= 1
    assert all(int(m.sum()) == 100 for m in r.tiers.values())


# -------------------------------------------- coverage must stay honest


def test_change_is_never_asserted_where_nothing_was_observed():
    r = fuse.fuse(*scene(s2_change=-0.9, s1_change=-9.0, s2_seen=False, s1_seen=False))
    assert not any(m.any() for m in r.tiers.values())
    assert r.not_observed.all()
    assert r.not_observed_pct == pytest.approx(100.0)


def test_unobserved_ground_is_flagged_not_silently_dropped():
    r = fuse.fuse(*scene(s2_seen=False, s1_seen=False))
    assert any("unchanged" in w for w in r.warnings)


def test_optical_gap_is_measured_and_warned():
    s2d, s2v, s1d, s1v = scene()
    s2v[:, :20] = False  # half the AOI unusable in optical
    r = fuse.fuse(s2d, s2v, s1d, s1v)
    assert r.optical_gap_pct == pytest.approx(50.0)
    assert r.s1_usable_pct == pytest.approx(100.0)
    assert any("SAR evidence alone" in w for w in r.warnings)


def test_gap_is_split_between_cloud_and_never_imaged():
    """A granule footprint edge is missing coverage, not weather.

    Reporting it as cloud would overstate what the weather actually cost.
    """
    s2d, s2v, s1d, s1v = scene()
    s2v[:20] = False                       # half unusable
    never = np.zeros((N, N), dtype=bool)
    never[:10] = True                      # a quarter never imaged at all
    r = fuse.fuse(s2d, s2v, s1d, s1v, s2_never_imaged=never)
    assert r.optical_gap_pct == pytest.approx(50.0)
    assert r.never_imaged_pct == pytest.approx(25.0)
    assert r.cloud_gap_pct == pytest.approx(25.0)
    # the two causes must account for the whole gap, exactly
    assert r.cloud_gap_pct + r.never_imaged_pct == pytest.approx(r.optical_gap_pct)
    assert any("outside the scene footprint" in w for w in r.warnings)


def test_gap_is_all_cloud_when_footprint_is_complete():
    s2d, s2v, s1d, s1v = scene()
    s2v[:20] = False
    r = fuse.fuse(s2d, s2v, s1d, s1v, s2_never_imaged=np.zeros((N, N), dtype=bool))
    assert r.never_imaged_pct == pytest.approx(0.0)
    assert r.cloud_gap_pct == pytest.approx(50.0)


def test_mismatched_grids_rejected():
    s2d, s2v, s1d, s1v = scene()
    with pytest.raises(ValueError, match="one grid"):
        fuse.fuse(s2d, s2v, s1d[:10, :10], s1v[:10, :10])


# ------------------------------------------------------------ features


def test_features_carry_tier_and_direction():
    s2d, s2v, s1d, s1v = scene()
    s2d[BLOCK] = -0.5
    s1d[BLOCK] = -6.0
    r = fuse.fuse(s2d, s2v, s1d, s1v)
    feats = fuse.to_features(r, TRANSFORM, CRS)
    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["tier"] == fuse.CONFIRMED
    assert props["direction"] == detect.LOSS
    assert props["area_ha"] == pytest.approx(4.0)  # 20x20 px at 10 m
    assert "meaning" in props


def test_sar_gap_direction_comes_from_sar_not_optical():
    """A cloud-blind pixel has no optical sign to borrow."""
    s2d, s2v, s1d, s1v = scene()
    s2v[:] = False            # optical sees nothing
    s1d[BLOCK] = +6.0         # SAR sees backscatter RISE
    r = fuse.fuse(s2d, s2v, s1d, s1v)
    feats = fuse.to_features(r, TRANSFORM, CRS)
    assert [f["properties"]["tier"] for f in feats] == [fuse.SAR_GAP]
    assert feats[0]["properties"]["direction"] == detect.GAIN


def test_small_patches_are_sieved_out_of_every_tier():
    s2d, s2v, s1d, s1v = scene()
    s2d[0, 0] = -0.9  # single pixel
    s1d[5, 5] = -9.0  # single pixel
    r = fuse.fuse(s2d, s2v, s1d, s1v)
    assert fuse.to_features(r, TRANSFORM, CRS) == []


def test_geometry_is_wgs84():
    s2d, s2v, s1d, s1v = scene()
    s2d[BLOCK] = -0.5
    feats = fuse.to_features(fuse.fuse(s2d, s2v, s1d, s1v), TRANSFORM, CRS)
    lon, lat = feats[0]["geometry"]["coordinates"][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_reported_hectares_equal_the_polygons_drawn():
    """The headline area must be exactly what is on the map.

    Regression guard: sieving used to happen at polygonization time while
    hectares were totalled from the unsieved masks, so the reported area
    described more change than the polygons actually showed.
    """
    s2d, s2v, s1d, s1v = scene()
    s2d[BLOCK] = -0.5              # 400 px block, survives the sieve
    s2d[35, 35] = -0.9             # speckle, must not count anywhere
    s1d[30:36, 30:36] = -6.0       # 36 px, below the 50 px sieve
    r = fuse.fuse(s2d, s2v, s1d, s1v)
    feats = fuse.to_features(r, TRANSFORM, CRS)

    from_features = sum(f["properties"]["area_ha"] for f in feats)
    from_stats = sum(r.hectares().values())
    assert from_stats == pytest.approx(from_features, abs=1e-6)
    assert from_features == pytest.approx(4.0)  # only the 20x20 block survived
