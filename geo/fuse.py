"""Combine Sentinel-1 and Sentinel-2 into tiered, honest change evidence.

The measurement that shapes this module: on a cloud-free AOI, pixel-wise
agreement between NDVI change and SAR log-ratio change was only 3.6%. That is
not a bug -- NDVI measures greenness while backscatter measures roughness,
structure and moisture, so the two sensors genuinely detect different physics.

The consequence is the central design rule:

    Sentinel-1 is a COVERAGE layer and a CORROBORATION tier, never a
    pixel-wise AND gate. Naive (s2_change & s1_change) discards ~96% of
    real detections.

So instead of merging the sensors into one number, every pixel is routed by
what was actually observable there, into mutually exclusive tiers the caller
can weigh separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

import classify
import detect
from rasterio.features import shapes
from rasterio.warp import transform_geom
from shapely.geometry import shape as shapely_shape

# Tiers, most to least corroborated.
CONFIRMED = "confirmed"    # both sensors saw it, both flag change
OPTICAL = "optical"        # S2 flags change; S1 quiet or unavailable
SAR_ONLY = "sar_only"      # both saw it, only S1 flags change
SAR_GAP = "sar_gap"        # S2 was cloud-blind here; S1 flags change
NOT_OBSERVED = "not_observed"  # neither sensor could see this ground

TIER_ORDER = (CONFIRMED, OPTICAL, SAR_ONLY, SAR_GAP)

TIER_MEANING = {
    CONFIRMED: "both sensors observed this ground and both report change",
    OPTICAL: "spectral change; SAR reports no structural change here",
    SAR_ONLY: "structural or moisture change with no spectral signature",
    SAR_GAP: "change under cloud, invisible to optical entirely",
}


@dataclass
class FusionResult:
    """Tier masks plus the coverage figures needed to read them honestly."""

    tiers: dict[str, np.ndarray]
    directional: dict[tuple[str, str], np.ndarray]
    s2_diff: np.ndarray
    s1_diff: np.ndarray
    s2_valid: np.ndarray
    s1_valid: np.ndarray
    not_observed: np.ndarray
    s2_never_imaged: np.ndarray | None = None
    #: Extra index deltas used only for classification (ndvi/ndwi/ndbi).
    planes: dict = field(default_factory=dict)
    #: Multi-date only: which observation each pixel's change was dated to.
    change_index: np.ndarray | None = None
    confirmations: np.ndarray | None = None
    dates: list = field(default_factory=list)
    #: Radar has its own observation dates, so a radar-only detection is dated
    #: from the radar series rather than left undated.
    radar_change_index: np.ndarray | None = None
    radar_confirmations: np.ndarray | None = None
    radar_dates: list = field(default_factory=list)
    pixel_area_m2: float = 100.0
    warnings: list[str] = field(default_factory=list)

    def _ha(self, mask: np.ndarray) -> float:
        return float(mask.sum()) * self.pixel_area_m2 / detect.M2_PER_HA

    @property
    def s2_usable_pct(self) -> float:
        return float(self.s2_valid.mean()) * 100.0

    @property
    def s1_usable_pct(self) -> float:
        return float(self.s1_valid.mean()) * 100.0

    @property
    def optical_gap(self) -> np.ndarray:
        """Ground that only SAR could observe."""
        return ~self.s2_valid & self.s1_valid

    @property
    def optical_gap_pct(self) -> float:
        """Share of the AOI that only SAR could observe.

        This is the number that justifies fusion: measured at 71.8% on a real
        Amazon pair whose optical scenes were reported at 4% and 16% cloud.
        """
        return float(self.optical_gap.mean()) * 100.0

    @property
    def never_imaged_pct(self) -> float:
        """Part of the optical gap that was outside the granule footprint.

        Not weather -- the satellite simply never photographed this ground on
        that pass. Kept separate so the cloud figure is not inflated by it.
        """
        if self.s2_never_imaged is None:
            return 0.0
        return float((self.optical_gap & self.s2_never_imaged).mean()) * 100.0

    @property
    def cloud_gap_pct(self) -> float:
        """The part of the optical gap genuinely caused by cloud, shadow or snow."""
        return round(self.optical_gap_pct - self.never_imaged_pct, 1)

    @property
    def not_observed_pct(self) -> float:
        return float(self.not_observed.mean()) * 100.0

    def hectares(self) -> dict[str, float]:
        return {tier: self._ha(mask) for tier, mask in self.tiers.items()}

    def stats(self) -> dict:
        return {
            "s2_usable_pct": round(self.s2_usable_pct, 1),
            "s1_usable_pct": round(self.s1_usable_pct, 1),
            "optical_gap_pct": round(self.optical_gap_pct, 1),
            "cloud_gap_pct": round(self.cloud_gap_pct, 1),
            "never_imaged_pct": round(self.never_imaged_pct, 1),
            "not_observed_pct": round(self.not_observed_pct, 1),
            "hectares": {k: round(v, 2) for k, v in self.hectares().items()},
            "warnings": self.warnings,
        }


def fuse(
    s2_diff: np.ndarray,
    s2_valid: np.ndarray,
    s1_diff: np.ndarray,
    s1_valid: np.ndarray,
    *,
    optical_threshold: float = 0.20,
    sar_threshold_db: float = 3.0,
    pixel_area_m2: float = 100.0,
    sieve_size: int = detect.DEFAULT_SIEVE_SIZE,
    s2_never_imaged: np.ndarray | None = None,
    planes: dict | None = None,
) -> FusionResult:
    """Route every pixel into exactly one tier based on sensor availability.

    Change is only ever asserted where the relevant sensor actually observed
    the ground: an unobserved pixel is neither changed nor unchanged, and the
    two must not collapse into each other.
    """
    shapes = {a.shape for a in (s2_diff, s2_valid, s1_diff, s1_valid)}
    if len(shapes) != 1:
        raise ValueError(f"all inputs must share one grid; got shapes {shapes}")

    s2_valid = np.asarray(s2_valid, dtype=bool)
    s1_valid = np.asarray(s1_valid, dtype=bool)

    # Change is evaluated only where that sensor could see.
    s2_chg = s2_valid & (np.abs(np.where(s2_valid, s2_diff, 0.0)) >= optical_threshold)
    s1_chg = s1_valid & (np.abs(np.where(s1_valid, s1_diff, 0.0)) >= sar_threshold_db)

    both = s2_valid & s1_valid
    s1_says_change = s1_valid & s1_chg

    raw = {
        CONFIRMED: both & s2_chg & s1_chg,
        # OPTICAL absorbs the case where S1 is simply unavailable, so a missing
        # SAR scene degrades to optical-only rather than erasing detections.
        OPTICAL: s2_valid & s2_chg & ~s1_says_change,
        SAR_ONLY: both & s1_chg & ~s2_chg,
        SAR_GAP: ~s2_valid & s1_valid & s1_chg,
    }

    # Sieve here, not at polygonization time. Speckle removal has to happen
    # before the areas are totalled, or the reported hectares describe a
    # noisier picture than the polygons actually drawn on the map.
    directional: dict[tuple[str, str], np.ndarray] = {}
    tiers: dict[str, np.ndarray] = {}
    for tier, mask in raw.items():
        driver = s2_diff if tier in (CONFIRMED, OPTICAL) else s1_diff
        kept = np.zeros_like(mask, dtype=bool)
        for direction, signed in (
            (detect.LOSS, mask & (driver < 0)),
            (detect.GAIN, mask & (driver > 0)),
        ):
            sieved = detect._sieved(signed, sieve_size)
            directional[(tier, direction)] = sieved
            kept |= sieved
        tiers[tier] = kept

    not_observed = ~s2_valid & ~s1_valid

    result = FusionResult(
        tiers=tiers,
        directional=directional,
        s2_diff=np.asarray(s2_diff, dtype="float32"),
        s1_diff=np.asarray(s1_diff, dtype="float32"),
        s2_valid=s2_valid,
        s1_valid=s1_valid,
        not_observed=not_observed,
        s2_never_imaged=(None if s2_never_imaged is None else np.asarray(s2_never_imaged, dtype=bool)),
        planes=dict(planes or {}),
        pixel_area_m2=pixel_area_m2,
    )

    if result.not_observed_pct > 10:
        result.warnings.append(
            f"{result.not_observed_pct:.0f}% of the area was seen by neither sensor; "
            "that ground is unknown, not unchanged"
        )
    if result.optical_gap_pct > 20:
        detail = f"{result.cloud_gap_pct:.0f}% cloud"
        if result.never_imaged_pct >= 1:
            detail += f", {result.never_imaged_pct:.0f}% outside the scene footprint"
        result.warnings.append(
            f"{result.optical_gap_pct:.0f}% of the area was unusable in Sentinel-2 "
            f"({detail}) and rests on SAR evidence alone"
        )
    return result


def _elongation(slices) -> float:
    """Bounding-box aspect ratio of a region. Roads are long and thin."""
    rows, cols = slices
    h = max(rows.stop - rows.start, 1)
    w = max(cols.stop - cols.start, 1)
    return max(h, w) / min(h, w)


def to_features(result: FusionResult, transform, crs) -> list[dict]:
    """Polygonize every tier and classify each region.

    Regions are labelled before vectorizing so that each polygon keeps an id.
    That id is what lets the mean NDVI, NDWI, NDBI and backscatter change be
    measured *inside* the region and turned into a change type -- a per-polygon
    signature rather than one verdict for the whole scene.
    """
    features: list[dict] = []
    planes = result.planes
    has_optical_planes = all(k in planes for k in ("ndvi", "ndwi", "ndbi"))
    # Past the region cap only the largest regions are polygonized; the rest are
    # counted, not drawn. Hectare totals come from the full masks regardless.
    masks, dropped = detect.keep_largest(result.directional)

    for tier in TIER_ORDER:
        for direction in (detect.LOSS, detect.GAIN):
            mask = masks.get((tier, direction))
            if mask is None or not mask.any():
                continue

            labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
            if n == 0:
                continue
            index = list(range(1, n + 1))

            def region_means(arr):
                return ndimage.mean(np.nan_to_num(arr), labels, index)

            sar = region_means(result.s1_diff)
            if has_optical_planes:
                ndvi = region_means(planes["ndvi"])
                ndwi = region_means(planes["ndwi"])
                ndbi = region_means(planes["ndbi"])
            else:
                # Fall back to whichever single index was actually computed.
                ndvi = region_means(result.s2_diff)
                ndwi = ndbi = np.zeros(n)
            boxes = ndimage.find_objects(labels)

            # Multi-date runs date each region by the modal change index of its
            # pixels, which is more robust than a mean across a boundary where
            # two dates meet.
            def _mode(values):
                v = values[values >= 0].astype(int)
                return int(np.bincount(v).argmax()) if v.size else -1

            # Optical dates the change where optical saw it; radar dates it
            # where only radar did. Each keeps its own observation calendar.
            optical_dated = result.change_index is not None and bool(result.dates)
            radar_dated = result.radar_change_index is not None and bool(result.radar_dates)

            if optical_dated:
                region_date_idx = ndimage.labeled_comprehension(
                    result.change_index, labels, index, _mode, int, -1)
                region_conf = ndimage.mean(
                    result.confirmations.astype("float32"), labels, index)
            if radar_dated:
                radar_date_idx = ndimage.labeled_comprehension(
                    result.radar_change_index, labels, index, _mode, int, -1)
                radar_conf = ndimage.mean(
                    result.radar_confirmations.astype("float32"), labels, index)
            dated = optical_dated or radar_dated

            # Optical evidence is meaningless where optical could not see, so
            # a SAR-gap region is classified on radar alone.
            optical_seen = tier in (CONFIRMED, OPTICAL, SAR_ONLY)
            sar_seen = tier in (CONFIRMED, SAR_ONLY, SAR_GAP)

            for geom, raw_label in shapes(
                labels.astype("int32"), mask=labels > 0, transform=transform
            ):
                i = int(raw_label) - 1
                area_m2 = shapely_shape(geom).area
                sig = classify.Signature(
                    ndvi=float(ndvi[i]),
                    ndwi=float(ndwi[i]),
                    ndbi=float(ndbi[i]),
                    sar_db=float(sar[i]),
                    elongation=_elongation(boxes[i]) if boxes[i] else 1.0,
                    area_ha=area_m2 / detect.M2_PER_HA,
                    has_optical=optical_seen and has_optical_planes,
                    has_sar=sar_seen,
                )
                props = {
                    "direction": direction,
                    "area_ha": round(area_m2 / detect.M2_PER_HA, 4),
                    "pixels": int(round(area_m2 / result.pixel_area_m2))
                    if result.pixel_area_m2 else None,
                    "tier": tier,
                    "meaning": TIER_MEANING[tier],
                }
                props.update(classify.classify(sig).as_dict())
                # The signature this region was classified from, measured inside
                # the polygon. Omitted for a sensor that could not see it, so an
                # absent reading is never mistaken for a zero change.
                if sig.has_optical:
                    props["ndvi_delta"] = round(sig.ndvi, 3)
                    props["ndwi_delta"] = round(sig.ndwi, 3)
                    props["ndbi_delta"] = round(sig.ndbi, 3)
                if sig.has_sar:
                    props["sar_delta_db"] = round(sig.sar_db, 2)
                if dated:
                    di = int(region_date_idx[i]) if optical_dated else -1
                    if 0 <= di < len(result.dates):
                        props["change_date"] = result.dates[di]
                        props["dated_by"] = "optical"
                        props["confirmed_by"] = int(round(float(region_conf[i])))
                    elif radar_dated:
                        ri = int(radar_date_idx[i])
                        if 0 <= ri < len(result.radar_dates):
                            props["change_date"] = result.radar_dates[ri]
                            props["dated_by"] = "radar"
                            props["confirmed_by"] = int(round(float(radar_conf[i])))
                    if "change_date" in props:
                        props["persistence"] = (
                            "confirmed" if props["confirmed_by"] > 0
                            else "unconfirmed (latest observation)"
                        )
                features.append({
                    "type": "Feature",
                    "geometry": transform_geom(crs, "EPSG:4326", geom, precision=6),
                    "properties": props,
                })
    if len(features) > detect.MAX_REGIONS:
        # Ties at the size cutoff, or one label vectorizing to several polygons,
        # can overshoot; trim to the cap by area.
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        dropped += len(features) - detect.MAX_REGIONS
        del features[detect.MAX_REGIONS:]
    if dropped:
        result.warnings.append(detect.dropped_warning(dropped))
    return features


def stamp_provenance(features: list[dict], optical_scenes: list[dict],
                     radar_scenes: list[dict]) -> list[dict]:
    """Record on every region the public scenes its evidence came from.

    A polygon is a claim; these fields are what let anyone re-open the exact
    scenes behind it. "Before" is the baseline every observation is compared
    against. "After" is the observation the region was dated to, when that
    sensor did the dating, and otherwise the latest one -- the scene a two-date
    comparison would have used. A sensor with no scenes adds no fields, rather
    than empty ones that look like data.
    """
    def pick(scenes, date, dated_here):
        if not scenes:
            return None, None
        after = scenes[-1]
        if dated_here and date:
            for s in scenes:
                if s.get("date") == date:
                    after = s
                    break
        return scenes[0], after

    orbit = radar_scenes[0].get("relative_orbit") if radar_scenes else None
    for i, feature in enumerate(features):
        p = feature["properties"]
        # The feature's position, which is what ?region=<n> addresses.
        p["id"] = i
        date, by = p.get("change_date"), p.get("dated_by")

        before, after = pick(optical_scenes, date, by == "optical")
        if before:
            p["scene_before"], p["date_before"] = before["id"], before.get("date")
            p["scene_after"], p["date_after"] = after["id"], after.get("date")

        before, after = pick(radar_scenes, date, by == "radar")
        if before:
            p["sar_scene_before"], p["sar_date_before"] = before["id"], before.get("date")
            p["sar_scene_after"], p["sar_date_after"] = after["id"], after.get("date")
            if orbit is not None:
                p["relative_orbit"] = orbit
    return features
