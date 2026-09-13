"""Query splitting, intent->index mapping, and AOI derivation. No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import locate  # noqa: E402


# ------------------------------------------------------------- splitting


def test_the_motivating_query_splits_correctly():
    what, where = locate.split_query("new construction near the gomti river")
    assert what == "new construction"
    assert where == "gomti river"      # the article is dropped


@pytest.mark.parametrize("query,what,where", [
    ("deforestation around manaus", "deforestation", "manaus"),
    ("flooding in lucknow", "flooding", "lucknow"),
    ("new buildings at delhi", "new buildings", "delhi"),
    ("erosion along the coastline", "erosion", "coastline"),
])
def test_common_phrasings(query, what, where):
    assert locate.split_query(query) == (what, where)


def test_last_preposition_wins():
    """"construction near the river in lucknow" is asking about Lucknow."""
    _, where = locate.split_query("construction near the river in lucknow")
    assert where == "lucknow"


def test_bare_place_name_is_all_location():
    assert locate.split_query("gomti river") == ("", "gomti river")


def test_preposition_inside_a_word_is_not_a_split():
    """"inundation" contains "in"; splitting on it would be nonsense."""
    what, where = locate.split_query("inundation near varanasi")
    assert what == "inundation" and where == "varanasi"


def test_trailing_preposition_falls_back_to_the_whole_string():
    assert locate.split_query("construction near") == ("", "construction near")


def test_empty_query_rejected():
    with pytest.raises(ValueError, match="empty"):
        locate.split_query("   ")


# ------------------------------------------------------- intent -> index


@pytest.mark.parametrize("what,index", [
    ("new construction", "ndbi"),
    ("urban development", "ndbi"),
    ("flooding", "ndwi"),
    ("river water levels", "ndwi"),
    ("deforestation", "ndvi"),
    ("forest clearing", "ndvi"),
    ("", "ndvi"),
])
def test_intent_picks_the_index_that_responds_to_it(what, index):
    assert locate.index_hint(what) == index


# --------------------------------------------------------- AOI derivation


def candidate(span, lat=26.8, lon=81.3, kind="river"):
    half = span / 2
    return {"name": "x", "kind": kind, "lat": lat, "lon": lon,
            "bbox": [lon - half, lat - half, lon + half, lat + half], "importance": 0.5}


def test_oversized_feature_is_clamped_and_says_so():
    """A river's full extent is not an AOI: 3 deg is ~336 km."""
    aoi = locate.to_aoi(candidate(3.0))
    west, south, east, north = aoi["bbox"]
    assert (north - south) == pytest.approx(locate.DEFAULT_SPAN_DEG, abs=1e-6)
    assert any("too large" in n for n in aoi["notes"])


def test_point_feature_is_expanded_and_says_so():
    aoi = locate.to_aoi(candidate(0.001, kind="weir"))
    west, south, east, north = aoi["bbox"]
    assert (north - south) == pytest.approx(locate.DEFAULT_SPAN_DEG, abs=1e-6)
    assert any("point feature" in n for n in aoi["notes"])


def test_reasonably_sized_feature_is_used_as_is():
    aoi = locate.to_aoi(candidate(0.08))
    west, south, east, north = aoi["bbox"]
    assert (north - south) == pytest.approx(0.08, abs=1e-4)
    assert aoi["notes"] == []          # nothing was substituted, nothing to report


def test_clamped_window_is_square_on_the_ground_not_in_degrees():
    """A degree of longitude shrinks with latitude, so the box must widen."""
    high = locate.to_aoi(candidate(3.0, lat=60.0))
    w, s, e, n = high["bbox"]
    assert (e - w) > (n - s) * 1.5     # ~1/cos(60deg) = 2x
    equator = locate.to_aoi(candidate(3.0, lat=0.0))
    w2, s2, e2, n2 = equator["bbox"]
    assert (e2 - w2) == pytest.approx(n2 - s2, abs=1e-6)


def test_bbox_is_always_ordered_west_south_east_north():
    for span in (0.001, 0.08, 3.0):
        w, s, e, n = locate.to_aoi(candidate(span))["bbox"]
        assert w < e and s < n


def test_aoi_stays_within_the_cap():
    aoi = locate.to_aoi(candidate(10.0), span=locate.MAX_SPAN_DEG)
    w, s, e, n = aoi["bbox"]
    assert (n - s) <= locate.MAX_SPAN_DEG + 1e-9
