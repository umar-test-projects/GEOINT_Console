"""Sentinel-1 RTC -- SAR, 10 m, cloud-penetrating.

Radiometrically terrain-corrected gamma0 from Planetary Computer. RTC rather
than GRD because RTC is analysis-ready; GRD would need our own terrain
correction.

Two properties of SAR drive everything here:

* It sees through cloud. On a real Amazon pair where Sentinel-2 was 28% usable,
  Sentinel-1 was 100% usable -- a 71.8% coverage gap that optical alone reports
  as "no data", and that in practice reads as "no change".
* eo:cloud_cover does not exist on S1 items. Any scene predicate copied from an
  optical pipeline rejects 100% of SAR candidates.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge  # noqa: E402,F401  (puts satellite-mvp on sys.path)
import cache  # noqa: E402
import sar  # noqa: E402
from raster import Grid, read_to_grid  # noqa: E402

# Reuse satellite-mvp's signing helper rather than reimplementing it.
from landsat_acquisition.stac_client import sign_asset_url  # noqa: E402

ROOT = "https://planetarycomputer.microsoft.com/api/stac/v1"
ENDPOINT = f"{ROOT}/search"
COLLECTION = "sentinel-1-rtc"
TIMEOUT = 90
POLARIZATIONS = ("vv", "vh")


class SceneSearchError(RuntimeError):
    pass


def search(bbox, start, end, orbit_state=None, limit=50):
    """Scenes intersecting the AOI, newest first.

    There is deliberately no cloud filter: S1 items carry no eo:cloud_cover
    property, and filtering on it discards every candidate.
    """
    body = {
        "collections": [COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": limit,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    if orbit_state:
        body["query"] = {"sat:orbit_state": {"eq": orbit_state}}
    try:
        r = requests.post(ENDPOINT, json=body, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SceneSearchError(f"Sentinel-1 search failed for {bbox}: {exc}") from exc
    return r.json().get("features", [])


def describe(scene):
    p = scene["properties"]
    return {
        "sensor": "S1",
        "id": scene["id"],
        "date": p["datetime"][:10],
        "orbit_state": p.get("sat:orbit_state"),
        "relative_orbit": p.get("sat:relative_orbit"),
        "polarizations": p.get("sar:polarizations"),
    }


def _orbits(scenes):
    return sorted({str(s["properties"].get("sat:relative_orbit")) for s in scenes})


def matched_pair(bbox, window_a, window_b):
    """Pick one scene from each window that share a relative orbit.

    SAR backscatter depends on viewing geometry, so comparing two passes from
    different tracks measures the geometry change rather than the ground
    change. Rather than silently pairing across orbits, this fails and reports
    the orbits it actually found.
    """
    a_scenes = search(bbox, *window_a)
    b_scenes = search(bbox, *window_b)
    if not a_scenes:
        raise SceneSearchError(f"no Sentinel-1 scenes for bbox {bbox} in window {window_a}")
    if not b_scenes:
        raise SceneSearchError(f"no Sentinel-1 scenes for bbox {bbox} in window {window_b}")

    by_orbit_b = {}
    for s in b_scenes:
        by_orbit_b.setdefault(s["properties"].get("sat:relative_orbit"), s)

    for a in a_scenes:
        orbit = a["properties"].get("sat:relative_orbit")
        if orbit in by_orbit_b:
            return a, by_orbit_b[orbit]

    raise SceneSearchError(
        "no shared Sentinel-1 relative orbit between the two windows "
        f"(first: {_orbits(a_scenes)}, second: {_orbits(b_scenes)}); "
        "comparing across orbits would measure viewing geometry, not ground change"
    )


def load_db(scene, grid, bbox, polarization="vv", speckle=None):
    """Backscatter in dB on the analysis grid, plus its validity mask.

    The SAS token is fetched immediately before the read and never cached --
    Planetary Computer tokens expire in roughly a day, so a cached signed URL
    is a delayed failure. The resulting pixels are cached instead.
    """
    pol = polarization.lower()
    if pol not in POLARIZATIONS:
        raise ValueError(f"polarization must be one of {POLARIZATIONS}, got {polarization!r}")
    if pol not in scene["assets"]:
        raise SceneSearchError(f"scene {scene['id']} is missing the {pol} asset")

    window = sar.DEFAULT_SPECKLE_WINDOW if speckle is None else speckle

    def _db():
        href = sign_asset_url(scene["assets"][pol]["href"])
        gamma0 = read_to_grid(href, grid)
        return sar.speckle_filter(sar.to_db(gamma0), window)

    db = cache.get_or_compute(scene["id"], bbox, f"{pol}_db_s{window}", _db)
    return db, np.isfinite(db)


#: STAC item ids are letters, digits, `_`, `.` and `-`. Anything else is refused
#: before it reaches the URL, where `../`, `?` or `#` would change the request.
_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def get_item(scene_id):
    """Re-resolve a scene by id so chip rendering stays stateless."""
    if not _ITEM_ID.fullmatch(scene_id or ""):
        raise SceneSearchError(f"not a valid Sentinel-1 scene id: {scene_id!r}")
    url = f"{ROOT}/collections/{COLLECTION}/items/{scene_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SceneSearchError(f"Sentinel-1 scene {scene_id} not found: {exc}") from exc
    return r.json()
