"""Multi-date persistence and change dating. No network."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import series  # noqa: E402

H = W = 4


def stack(values_and_validity):
    """Build a (T, H, W) stack where each date is uniform across the frame."""
    d = np.array([np.full((H, W), v, dtype="float32") for v, _ in values_and_validity])
    m = np.array([np.full((H, W), ok, dtype=bool) for _, ok in values_and_validity])
    return d, m


def run(rows, threshold=0.2, min_persist=1):
    d, m = stack(rows)
    return series.detect_series(d, m, threshold, min_persist)


# ------------------------------------------------- the point of a series


def test_change_that_holds_is_persistent_and_dated():
    r = run([(-0.40, True), (-0.45, True), (-0.42, True)])
    assert r.persistent.all()
    assert (r.change_index == 0).all()      # dated to the first date it appeared
    assert (r.confirmations == 2).all()     # and confirmed twice after


def test_change_that_reverts_is_rejected_as_transient():
    """The artefact a two-date comparison would have reported as real."""
    r = run([(-0.40, True), (0.0, True), (0.0, True)])
    assert r.transient.all()
    assert not r.persistent.any()
    assert not r.changed.any()              # excluded from reported change


def test_a_later_date_is_dated_correctly():
    r = run([(0.0, True), (-0.40, True), (-0.44, True)])
    assert r.persistent.all()
    assert (r.change_index == 1).all()
    assert (r.confirmations == 1).all()


def test_change_at_the_final_observation_is_flagged_not_claimed():
    """Real, but nothing after it to corroborate. Must not be called confirmed."""
    r = run([(0.0, True), (0.0, True), (-0.40, True)])
    assert r.latest.all()
    assert not r.persistent.any()
    assert r.changed.all()                  # still reported, just labelled


def test_sign_flip_does_not_count_as_persistence():
    """Down then up has oscillated, not persisted."""
    r = run([(-0.40, True), (0.40, True), (-0.40, True)])
    assert not r.persistent.any()


def test_invalid_observations_are_skipped_not_treated_as_agreement():
    r = run([(-0.40, True), (0.0, False), (-0.44, True)])
    assert r.persistent.all()
    assert (r.confirmations == 1).all()     # the cloudy date counted for nothing


def test_min_persist_requires_more_corroboration():
    rows = [(-0.40, True), (-0.42, True)]
    assert run(rows, min_persist=1).persistent.all()
    # Only one later observation exists, so demanding two cannot be satisfied.
    assert not run(rows, min_persist=2).persistent.any()


def test_subthreshold_drift_is_not_change():
    r = run([(-0.05, True), (-0.06, True), (-0.05, True)])
    assert not r.changed.any()
    assert not r.transient.any()


def test_signed_value_comes_from_the_change_date():
    r = run([(0.0, True), (-0.37, True), (-0.50, True)])
    assert r.signed[0, 0] == pytest.approx(-0.37, abs=1e-5)


def test_unchanged_pixels_carry_zero_so_they_cannot_leak_into_detection():
    r = run([(-0.40, True), (0.0, True), (0.0, True)])   # transient
    assert np.all(r.signed == 0)


def test_counts_are_reported():
    c = run([(-0.40, True), (0.0, True), (0.0, True)]).counts()
    assert c["transient_px"] == H * W
    assert c["persistent_px"] == 0


# ----------------------------------------------------------- validation


def test_mismatched_shapes_rejected():
    with pytest.raises(ValueError, match="must match"):
        series.detect_series(np.zeros((2, 4, 4), "float32"), np.ones((3, 4, 4), bool), 0.2)


def test_non_stack_rejected():
    with pytest.raises(ValueError, match=r"\(T, H, W\)"):
        series.detect_series(np.zeros((4, 4), "float32"), np.ones((4, 4), bool), 0.2)


def test_nonpositive_threshold_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        series.detect_series(np.zeros((2, 4, 4), "float32"), np.ones((2, 4, 4), bool), 0)


# -------------------------------------------------------------- windows


def test_windows_cover_the_range_without_gaps_or_overlap():
    w = series.windows("2023-01-01", "2024-12-31", 4)
    assert len(w) == 4
    assert w[0][0] == "2023-01-01"
    for earlier, later in zip(w, w[1:]):
        from datetime import date, timedelta
        assert date.fromisoformat(later[0]) == date.fromisoformat(earlier[1]) + timedelta(days=1)


def test_windows_rejects_degenerate_requests():
    with pytest.raises(ValueError, match="at least 2"):
        series.windows("2023-01-01", "2024-01-01", 1)
    with pytest.raises(ValueError, match="must be after"):
        series.windows("2024-01-01", "2023-01-01", 4)
    with pytest.raises(ValueError, match="cannot be split"):
        series.windows("2023-01-01", "2023-01-03", 8)


# ------------------------------------------- regions dated through fuse


def test_region_gets_a_change_date_and_confirmation_count():
    import fuse
    from rasterio.transform import from_origin

    n = 40
    planes = {k: np.zeros((n, n), dtype="float32") for k in ("ndvi", "ndwi", "ndbi")}
    planes["ndvi"][:20, :20] = -0.4
    s1d = np.zeros((n, n), dtype="float32")
    s1d[:20, :20] = -4.0

    r = fuse.fuse(planes["ndvi"], np.ones((n, n), bool), s1d, np.ones((n, n), bool),
                  planes=planes)
    ci = np.full((n, n), -1, dtype="int16")
    ci[:20, :20] = 1
    r.change_index = ci
    r.confirmations = np.where(ci >= 0, 2, 0).astype("int16")
    r.dates = ["2023-06-15", "2023-09-20", "2023-12-11"]

    props = fuse.to_features(r, from_origin(500_000, 1_000_000, 10, 10), "EPSG:32643")[0]["properties"]
    assert props["change_date"] == "2023-09-20"
    assert props["confirmed_by"] == 2
    assert props["persistence"] == "confirmed"


def test_two_date_runs_carry_no_change_date():
    """A pair cannot date a change, so it must not pretend to."""
    import fuse
    from rasterio.transform import from_origin

    n = 40
    planes = {k: np.zeros((n, n), dtype="float32") for k in ("ndvi", "ndwi", "ndbi")}
    planes["ndvi"][:20, :20] = -0.4
    r = fuse.fuse(planes["ndvi"], np.ones((n, n), bool),
                  np.zeros((n, n), "float32"), np.ones((n, n), bool), planes=planes)
    props = fuse.to_features(r, from_origin(500_000, 1_000_000, 10, 10), "EPSG:32643")[0]["properties"]
    assert "change_date" not in props
