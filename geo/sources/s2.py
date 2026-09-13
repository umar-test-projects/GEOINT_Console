"""Sentinel-2 L2A -- optical, 10 m, keyless.

Element84 Earth Search over the AWS sentinel-cogs open bucket: no API key, no
account, no signing. Scenes are COGs, so a windowed read fetches roughly 1% of
a scene for a small AOI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache  # noqa: E402
import indices  # noqa: E402
from raster import Grid, read_to_grid  # noqa: E402

ROOT = "https://earth-search.aws.element84.com/v1"
ENDPOINT = f"{ROOT}/search"
COLLECTION = "sentinel-2-l2a"
TIMEOUT = 90

#: index name -> the two reflectance assets it needs, as (numerator, denominator)
INDEX_ASSETS = {
    "ndvi": ("nir", "red"),
    "ndwi": ("green", "nir"),
    "ndbi": ("swir16", "nir"),
}


class SceneSearchError(RuntimeError):
    pass


def search(bbox, start, end, max_cloud=80.0, limit=50):
    """Scenes intersecting the AOI, clearest first.

    Sorting by cloud cover rather than taking the first hit matters: STAC's
    default order is not quality-ordered, and the clearest scene in a window is
    often not the nearest in time.
    """
    body = {
        "collections": [COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }
    try:
        r = requests.post(ENDPOINT, json=body, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SceneSearchError(f"Sentinel-2 search failed for {bbox}: {exc}") from exc
    return r.json().get("features", [])


def describe(scene):
    p = scene["properties"]
    return {
        "sensor": "S2",
        "id": scene["id"],
        "date": p["datetime"][:10],
        "cloud_cover": round(float(p.get("eo:cloud_cover", 0.0)), 1),
    }


def best_scene(bbox, start, end, max_cloud=80.0):
    scenes = search(bbox, start, end, max_cloud)
    if not scenes:
        raise SceneSearchError(
            f"no Sentinel-2 scene under {max_cloud}% cloud between {start} and {end} for bbox {bbox}"
        )
    return scenes[0]


def load(scene, grid, bbox, index):
    """Compute one spectral index, its validity mask, and the raw SCL classes.

    Validity comes from the per-pixel SCL band, not the scene-level cloud
    percentage. Measured on a real pair: scenes reported at 4% and 16% cloud
    cover left only 28% of pixels jointly usable.
    """
    if index not in INDEX_ASSETS:
        raise ValueError(f"unknown index {index!r}; expected one of {sorted(INDEX_ASSETS)}")
    num_asset, den_asset = INDEX_ASSETS[index]
    sid = scene["id"]
    assets = scene["assets"]

    for name in (num_asset, den_asset, "scl"):
        if name not in assets:
            raise SceneSearchError(f"scene {sid} is missing the {name} asset")

    def _index():
        num = read_to_grid(assets[num_asset]["href"], grid)
        den = read_to_grid(assets[den_asset]["href"], grid)
        return indices.norm_diff(num, den)

    def _scl():
        # Cache the raw classes, not just the boolean. Knowing WHY a pixel is
        # unusable -- cloud versus outside the granule footprint -- is the
        # difference between "obscured" and "never imaged", and the UI must not
        # report the second as the first.
        return np.rint(read_to_grid(assets["scl"]["href"], grid, categorical=True)).astype("uint8")

    arr = cache.get_or_compute(sid, bbox, index, _index)
    scl = cache.get_or_compute(sid, bbox, "scl", _scl)
    return arr, indices.scl_valid_mask(scl), scl


#: SCL 0 is no_data: outside the granule footprint, never imaged at all.
SCL_NODATA = 0


#: STAC item ids are letters, digits, `_`, `.` and `-`. Anything else is refused
#: before it reaches the URL, where `../`, `?` or `#` would change the request.
_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def get_item(scene_id):
    """Re-resolve a scene by id so chip rendering stays stateless."""
    if not _ITEM_ID.fullmatch(scene_id or ""):
        raise SceneSearchError(f"not a valid Sentinel-2 scene id: {scene_id!r}")
    url = f"{ROOT}/collections/{COLLECTION}/items/{scene_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SceneSearchError(f"Sentinel-2 scene {scene_id} not found: {exc}") from exc
    return r.json()


#: Every reflectance band any index needs. NIR is shared by all three, so the
#: four reads below cover NDVI, NDWI and NDBI together rather than two reads
#: per index.
ALL_BANDS = ("red", "green", "nir", "swir16")


def load_all(scene, grid, bbox):
    """All three indices for a scene, plus validity and the raw SCL classes.

    Classification needs the full spectral picture at once: NDVI alone cannot
    tell clearing from construction, and NDWI is what identifies water. Reading
    each band once and deriving three indices costs four reads instead of the
    six that three separate two-band loads would take.
    """
    sid = scene["id"]
    assets = scene["assets"]
    for name in (*ALL_BANDS, "scl"):
        if name not in assets:
            raise SceneSearchError(f"scene {sid} is missing the {name} asset")

    def _band(name):
        return cache.get_or_compute(
            sid, bbox, f"band_{name}", lambda: read_to_grid(assets[name]["href"], grid)
        )

    bands = {name: _band(name) for name in ALL_BANDS}

    def _scl():
        return np.rint(
            read_to_grid(assets["scl"]["href"], grid, categorical=True)
        ).astype("uint8")

    scl = cache.get_or_compute(sid, bbox, "scl", _scl)
    out = {
        "ndvi": indices.norm_diff(bands["nir"], bands["red"]),
        "ndwi": indices.norm_diff(bands["green"], bands["nir"]),
        "ndbi": indices.norm_diff(bands["swir16"], bands["nir"]),
    }
    return out, indices.scl_valid_mask(scl), scl
