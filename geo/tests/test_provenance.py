"""Per-region provenance: the scenes and signature a polygon was made from."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fuse  # noqa: E402

CRS = "EPSG:32643"
TRANSFORM = from_origin(500_000, 1_000_000, 10, 10)
N = 40
BOX = (slice(0, 20), slice(0, 20))

S2 = [
    {"sensor": "S2", "id": "S2_A", "date": "2023-03-01", "cloud_cover": 3.0},
    {"sensor": "S2", "id": "S2_B", "date": "2023-09-01", "cloud_cover": 1.0},
    {"sensor": "S2", "id": "S2_C", "date": "2024-03-01", "cloud_cover": 2.0},
]
S1 = [
    {"sensor": "S1", "id": "S1_A", "date": "2023-03-05", "relative_orbit": 10},
    {"sensor": "S1", "id": "S1_B", "date": "2023-09-05", "relative_orbit": 10},
    {"sensor": "S1", "id": "S1_C", "date": "2024-03-05", "relative_orbit": 10},
]


def detected(s2_change=0.0, s1_change=0.0, s2_seen=True):
    planes = {k: np.zeros((N, N), dtype="float32") for k in ("ndvi", "ndwi", "ndbi")}
    planes["ndvi"][BOX] = s2_change
    s1d = np.zeros((N, N), dtype="float32")
    s1d[BOX] = s1_change
    s2v = np.full((N, N), s2_seen, dtype=bool)
    r = fuse.fuse(planes["ndvi"], s2v, s1d, np.ones((N, N), dtype=bool), planes=planes)
    return fuse.to_features(r, TRANSFORM, CRS)


def feat(**props):
    return {"type": "Feature", "geometry": None, "properties": dict(props)}


# ------------------------------------------------------------ signature


def test_both_sensors_record_their_signature():
    p = detected(s2_change=-0.4, s1_change=-6.0)[0]["properties"]
    assert p["tier"] == fuse.CONFIRMED
    assert p["ndvi_delta"] == pytest.approx(-0.4, abs=1e-3)
    assert p["sar_delta_db"] == pytest.approx(-6.0, abs=1e-2)


def test_optical_only_region_records_no_radar_reading():
    p = detected(s2_change=-0.4)[0]["properties"]
    assert p["tier"] == fuse.OPTICAL
    assert "ndvi_delta" in p
    assert "sar_delta_db" not in p


def test_region_under_cloud_records_no_optical_reading_not_a_zero():
    """Optical could not see it, so a 0.0 there would be an invented value."""
    p = detected(s1_change=-6.0, s2_seen=False)[0]["properties"]
    assert p["tier"] == fuse.SAR_GAP
    assert "ndvi_delta" not in p and "ndwi_delta" not in p and "ndbi_delta" not in p
    assert p["sar_delta_db"] == pytest.approx(-6.0, abs=1e-2)


# ---------------------------------------------------------------- scenes


def test_before_is_the_baseline_and_after_is_the_dated_observation():
    p = fuse.stamp_provenance([feat(change_date="2023-09-01", dated_by="optical")], S2, S1)[0]["properties"]
    assert (p["scene_before"], p["date_before"]) == ("S2_A", "2023-03-01")
    assert (p["scene_after"], p["date_after"]) == ("S2_B", "2023-09-01")


def test_undated_region_uses_the_latest_observation():
    p = fuse.stamp_provenance([feat()], S2, S1)[0]["properties"]
    assert p["scene_after"] == "S2_C"
    assert p["sar_scene_after"] == "S1_C"


def test_radar_dating_picks_the_radar_scene_and_leaves_optical_at_latest():
    p = fuse.stamp_provenance([feat(change_date="2023-09-05", dated_by="radar")], S2, S1)[0]["properties"]
    assert (p["sar_scene_after"], p["sar_date_after"]) == ("S1_B", "2023-09-05")
    assert p["scene_after"] == "S2_C"
    assert p["relative_orbit"] == 10


def test_missing_sensor_adds_no_fields_rather_than_empty_ones():
    no_radar = fuse.stamp_provenance([feat()], S2, [])[0]["properties"]
    assert "sar_scene_before" not in no_radar and "relative_orbit" not in no_radar
    no_optical = fuse.stamp_provenance([feat()], [], S1)[0]["properties"]
    assert "scene_before" not in no_optical


def test_regions_get_ids_matching_their_position():
    """?region=<n> addresses a feature by position, so the id must agree."""
    fs = fuse.stamp_provenance([feat(), feat(), feat()], S2, S1)
    assert [f["properties"]["id"] for f in fs] == [0, 1, 2]
