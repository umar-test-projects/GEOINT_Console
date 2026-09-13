"""Turn a plain-language request into an AOI.

"new construction near the gomti river" has two halves: *what* to look for and
*where* to look. This splits them, resolves the place to coordinates, and
derives a workable bounding box.

Two facts about real geocoding results drive the whole module:

* **A river's bounding box is not an AOI.** "gomti river" resolves to a box
  2.9 x 3.0 degrees -- roughly 320 km across, the river's entire length. Used
  directly it would mean reading hundreds of scenes. Oversized results are
  therefore clamped to a workable window around the centre, and the clamping is
  reported rather than hidden.
* **Place names are ambiguous.** "gomti river" returns one match in Uttar
  Pradesh and three in Gujarat. Picking the first silently would quietly search
  the wrong side of the country, so every result carries the alternatives that
  were not chosen.

Geocoding is Nominatim (OpenStreetMap): keyless, like the rest of the stack.
Its usage policy requires a real User-Agent and at most one request a second,
both of which are enforced here.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request

ENDPOINT = "https://nominatim.openstreetmap.org/search"
#: Nominatim asks for an identifying User-Agent. Sending a generic one gets
#: the caller blocked, and deservedly.
USER_AGENT = "geo-change-monitor/0.1 (satellite change detection; research use)"
MIN_INTERVAL_S = 1.1
TIMEOUT = 45

#: Default AOI span. 0.1 deg is ~11 km, the size every measurement in this
#: project was validated at.
DEFAULT_SPAN_DEG = 0.10
#: Hard cap. Above this a run costs far more scene reads than it is worth.
MAX_SPAN_DEG = 0.25
MIN_SPAN_DEG = 0.02

#: Words that separate what is being looked for from where to look.
_WHERE_PREPOSITIONS = (
    "near", "around", "in", "at", "on", "along", "by", "next to",
    "close to", "surrounding", "beside", "within",
)

#: Intent -> the optical index that actually responds to it.
_INDEX_HINTS = (
    ("ndbi", ("construction", "building", "urban", "built", "development",
              "settlement", "housing", "industrial", "road", "infrastructure")),
    ("ndwi", ("water", "flood", "river", "lake", "reservoir", "inundation",
              "wetland", "coast", "dam")),
    ("ndvi", ("vegetation", "forest", "tree", "deforest", "clearing", "crop",
              "farm", "green", "logging", "canopy")),
)

_last_call = 0.0
_rate_lock = threading.Lock()
_cache: dict[str, list[dict]] = {}


class GeocodeError(RuntimeError):
    pass


class GeocodeBusy(GeocodeError):
    """Too many searches are already queued for the rate limit."""


def split_query(text: str) -> tuple[str, str]:
    """Split "<what> <preposition> <where>" into its two halves.

    Falls back to treating the whole string as the place, which is the right
    guess for a bare place name like "lucknow".
    """
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        raise ValueError("query is empty")

    lowered = cleaned.lower()
    best = None
    for prep in _WHERE_PREPOSITIONS:
        # Match the preposition as a whole word, and prefer the LAST one:
        # "construction near the river in lucknow" should locate "lucknow".
        for m in re.finditer(rf"\b{re.escape(prep)}\b", lowered):
            if best is None or m.start() > best[0]:
                best = (m.start(), m.end())
    if best is None:
        return "", cleaned

    what = cleaned[: best[0]].strip(" ,")
    where = cleaned[best[1]:].strip(" ,")
    # "near the gomti river" -> drop the leading article
    where = re.sub(r"^(the|a|an)\s+", "", where, flags=re.I).strip()
    if not where:
        return "", cleaned
    return what, where


def index_hint(what: str) -> str:
    """Pick the optical index whose physics matches the stated intent."""
    lowered = (what or "").lower()
    for index, keywords in _INDEX_HINTS:
        if any(k in lowered for k in keywords):
            return index
    return "ndvi"


#: Searches allowed to queue for the rate limit. Each waits in a server thread,
#: so an unbounded queue would starve every other request of the shared pool.
MAX_WAITING = 5
_waiting = 0
_waiting_lock = threading.Lock()


def _throttle():
    """Honour Nominatim's one-request-per-second policy."""
    global _last_call, _waiting
    with _waiting_lock:
        if _waiting >= MAX_WAITING:
            raise GeocodeBusy("place search is busy; try again in a few seconds")
        _waiting += 1
    try:
        with _rate_lock:
            wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
    finally:
        with _waiting_lock:
            _waiting -= 1


def geocode(place: str, limit: int = 5) -> list[dict]:
    """Resolve a place name to candidate locations, best first."""
    key = place.strip().lower()
    if not key:
        raise ValueError("place is empty")
    if key in _cache:
        return _cache[key]

    url = ENDPOINT + "?" + urllib.parse.urlencode(
        {"q": place, "format": "jsonv2", "limit": limit})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _throttle()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as fh:
            raw = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        raise GeocodeError(f"geocoding failed for {place!r}: {exc}") from exc

    out = []
    for r in raw:
        try:
            south, north, west, east = (float(v) for v in r["boundingbox"])
        except (KeyError, ValueError, TypeError):
            continue
        out.append({
            "name": r.get("display_name", place),
            "kind": r.get("type") or r.get("category") or "place",
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "bbox": [west, south, east, north],
            "importance": float(r.get("importance") or 0.0),
        })
    if not out:
        raise GeocodeError(f"no location found for {place!r}")
    _cache[key] = out
    return out


def to_aoi(candidate: dict, span: float = DEFAULT_SPAN_DEG) -> dict:
    """Derive a workable AOI from a geocoding result.

    A feature's own box is used when it is a sensible size. A river's full
    extent is far too large and a weir's is far too small, so both are replaced
    by a fixed window centred on the feature -- and the substitution is
    reported, because an AOI that is not the thing you named is worth knowing
    about.
    """
    west, south, east, north = candidate["bbox"]
    w_span, h_span = abs(east - west), abs(north - south)
    lon, lat = candidate["lon"], candidate["lat"]
    notes: list[str] = []

    too_big = max(w_span, h_span) > MAX_SPAN_DEG
    too_small = max(w_span, h_span) < MIN_SPAN_DEG

    if too_big:
        notes.append(
            f"{candidate['kind']} spans {max(w_span, h_span):.2f} deg "
            f"(~{max(w_span, h_span) * 111:.0f} km), too large to analyse at once; "
            f"using a {span * 111:.0f} km window at its centre point"
        )
    elif too_small:
        notes.append(
            f"{candidate['kind']} is a point feature; using a "
            f"{span * 111:.0f} km window around it"
        )

    if too_big or too_small:
        half = span / 2.0
        # Keep the window square on the ground: a degree of longitude shrinks
        # with latitude, so widen it by 1/cos(lat).
        lon_half = half / max(math.cos(math.radians(lat)), 0.1)
        bbox = [lon - lon_half, lat - half, lon + lon_half, lat + half]
    else:
        bbox = [min(west, east), min(south, north), max(west, east), max(south, north)]

    bbox = [round(v, 5) for v in bbox]
    return {"bbox": bbox, "centre": [round(lon, 5), round(lat, 5)], "notes": notes}


def resolve(text: str, span: float = DEFAULT_SPAN_DEG) -> dict:
    """Full pipeline: split the request, geocode the place, derive the AOI."""
    what, where = split_query(text)
    candidates = geocode(where)
    chosen = candidates[0]
    aoi = to_aoi(chosen, span)
    return {
        "query": text,
        "what": what,
        "where": where,
        "index_hint": index_hint(what),
        "bbox": aoi["bbox"],
        "centre": aoi["centre"],
        "place": {"name": chosen["name"], "kind": chosen["kind"]},
        "notes": aoi["notes"],
        # Ambiguity is surfaced, never resolved silently.
        "alternatives": [
            {"name": c["name"], "kind": c["kind"], "lat": c["lat"], "lon": c["lon"]}
            for c in candidates[1:4]
        ],
    }
