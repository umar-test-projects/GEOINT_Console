"""Change-type classification. No network."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import classify  # noqa: E402
import fuse  # noqa: E402

S = classify.Signature
CRS = "EPSG:32643"
TRANSFORM = from_origin(500_000, 1_000_000, 10, 10)


def ct(**kw):
    return classify.classify(S(**kw)).change_type


# ------------------------------------------------- the fusion payoff


def test_radar_direction_separates_clearing_from_construction():
    """The whole reason classification needs both sensors.

    Identical optical signature; opposite radar. Buildings are corner
    reflectors so backscatter rises; cleared ground goes smooth so it falls.
    """
    optical = dict(ndvi=-0.40, ndbi=0.12)
    assert ct(**optical, sar_db=+3.0) == classify.CONSTRUCTION
    assert ct(**optical, sar_db=-4.0) == classify.CLEARANCE


def test_without_radar_the_cause_is_left_unresolved():
    """Optical alone must not guess between clearing and construction."""
    res = classify.classify(S(ndvi=-0.40, ndbi=0.12, has_sar=False))
    assert res.change_type == classify.VEGETATION_LOSS
    assert "radar is needed" in " ".join(res.evidence)
    assert res.confidence <= 0.6  # and it must not claim confidence it lacks


def test_ambiguous_radar_also_leaves_it_unresolved():
    res = classify.classify(S(ndvi=-0.40, sar_db=0.2))
    assert res.change_type == classify.VEGETATION_LOSS
    assert "cannot be separated" in " ".join(res.evidence)


# ----------------------------------------------------------- water


def test_flood_confirmed_by_backscatter_collapse_scores_highest():
    res = classify.classify(S(ndwi=0.30, sar_db=-6.0))
    assert res.change_type == classify.FLOODING
    assert res.confidence >= 0.85


def test_flood_detected_under_cloud_from_radar_alone():
    """Floods come with storms, which is exactly when optical fails."""
    res = classify.classify(S(sar_db=-6.0, has_optical=False))
    assert res.change_type == classify.FLOODING
    assert "under cloud" in " ".join(res.evidence)


def test_uncorroborated_water_rise_scores_lower():
    strong = classify.classify(S(ndwi=0.30, sar_db=-6.0))
    weak = classify.classify(S(ndwi=0.30, sar_db=0.0))
    assert weak.change_type == classify.FLOODING
    assert weak.confidence < strong.confidence


def test_water_recession():
    assert ct(ndwi=-0.30) == classify.WATER_RECESSION


# ------------------------------------------------------ other classes


def test_vegetation_growth():
    assert ct(ndvi=0.40) == classify.VEGETATION_GROWTH


def test_elongated_footprint_reads_as_linear_infrastructure():
    assert ct(ndbi=0.02, elongation=7.0) == classify.ROAD_DEVELOPMENT


def test_compact_footprint_with_same_values_is_not_a_road():
    assert ct(ndbi=0.02, elongation=1.2) != classify.ROAD_DEVELOPMENT


def test_bare_surface_change_without_vegetation_loss():
    assert ct(ndbi=0.20, ndvi=-0.02) == classify.SURFACE_CHANGE


def test_radar_only_structural_change():
    assert ct(sar_db=5.0, has_optical=False) == classify.STRUCTURAL_CHANGE


def test_no_signature_is_not_forced_into_a_class():
    res = classify.classify(S())
    assert res.change_type == classify.OTHER
    assert res.confidence <= 0.4


def test_subthreshold_signals_do_not_trigger_a_class():
    assert ct(ndvi=-0.05, ndwi=0.05, ndbi=0.01, sar_db=0.5) == classify.OTHER


def test_every_class_has_a_human_label():
    for name in classify.LABELS:
        assert classify.LABELS[name] and not classify.LABELS[name].endswith(".")


# -------------------------------------------------------- summarize


def test_summarize_totals_area_per_class_largest_first():
    feats = [
        {"properties": {"change_type": classify.CLEARANCE, "area_ha": 3.0, "class_confidence": 0.8}},
        {"properties": {"change_type": classify.CLEARANCE, "area_ha": 2.0, "class_confidence": 0.9}},
        {"properties": {"change_type": classify.FLOODING, "area_ha": 10.0, "class_confidence": 0.9}},
    ]
    rows = classify.summarize(feats)
    assert [r["change_type"] for r in rows] == [classify.FLOODING, classify.CLEARANCE]
    assert rows[1]["area_ha"] == pytest.approx(5.0)
    assert rows[1]["count"] == 2
    assert rows[1]["confidence"] == pytest.approx(0.85)


def test_summarize_of_nothing_is_empty():
    assert classify.summarize([]) == []


# ------------------------------------------- end to end through fuse


def _planes(n, ndvi=0.0, ndwi=0.0, ndbi=0.0, box=(slice(0, 20), slice(0, 20))):
    p = {k: np.zeros((n, n), dtype="float32") for k in ("ndvi", "ndwi", "ndbi")}
    p["ndvi"][box] = ndvi
    p["ndwi"][box] = ndwi
    p["ndbi"][box] = ndbi
    return p


def test_regions_are_classified_individually_not_scene_wide():
    """Two regions, two different change types, in one run."""
    n = 60
    box_a = (slice(0, 20), slice(0, 20))     # clearing
    box_b = (slice(30, 50), slice(30, 50))   # construction
    planes = {k: np.zeros((n, n), dtype="float32") for k in ("ndvi", "ndwi", "ndbi")}
    planes["ndvi"][box_a] = -0.4
    planes["ndvi"][box_b] = -0.4
    planes["ndbi"][box_b] = 0.12
    s1d = np.zeros((n, n), dtype="float32")
    s1d[box_a] = -4.0   # smoothing  -> clearance
    s1d[box_b] = +3.0   # roughening -> construction

    r = fuse.fuse(planes["ndvi"], np.ones((n, n), bool), s1d, np.ones((n, n), bool),
                  planes=planes)
    feats = fuse.to_features(r, TRANSFORM, CRS)
    types = sorted(f["properties"]["change_type"] for f in feats)
    assert types == [classify.CLEARANCE, classify.CONSTRUCTION]


def test_sar_gap_region_is_classified_on_radar_alone():
    """Optical could not see this ground, so its zeros must not be used."""
    n = 40
    box = (slice(0, 20), slice(0, 20))
    planes = _planes(n)
    s2v = np.zeros((n, n), dtype=bool)        # optical blind everywhere
    s1d = np.zeros((n, n), dtype="float32")
    s1d[box] = -6.0                            # water-like collapse
    r = fuse.fuse(planes["ndvi"], s2v, s1d, np.ones((n, n), bool), planes=planes)
    feats = fuse.to_features(r, TRANSFORM, CRS)
    assert [f["properties"]["tier"] for f in feats] == [fuse.SAR_GAP]
    assert feats[0]["properties"]["change_type"] == classify.FLOODING


def test_every_feature_carries_type_confidence_and_evidence():
    n = 40
    planes = _planes(n, ndvi=-0.4)
    s1d = np.zeros((n, n), dtype="float32")
    s1d[(slice(0, 20), slice(0, 20))] = -4.0
    r = fuse.fuse(planes["ndvi"], np.ones((n, n), bool), s1d, np.ones((n, n), bool),
                  planes=planes)
    for f in fuse.to_features(r, TRANSFORM, CRS):
        p = f["properties"]
        assert p["change_type"] in classify.LABELS
        assert 0.0 <= p["class_confidence"] <= 1.0
        assert p["evidence"]
        assert p["change_label"]
