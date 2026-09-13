"""FastAPI service for on-demand fused change detection.

satellite-mvp's console displays precomputed results over a fixed tile grid and
five hardcoded AOIs. This serves the thing it cannot: any bounding box, any two
date windows, detected on demand.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import secrets
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

import bridge
import chips
import jobs
import locate
import pipeline
import raster
import semantic
import series_run
from sources import drone as dronesrc
from sources import s1 as s1src
from sources import s2 as s2src

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

app = FastAPI(title="geo - Earth change monitoring", version="0.1.0")

BBox = tuple[float, float, float, float]
DateStr = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _password_ok(header: str, password: str) -> bool:
    """HTTP Basic with any username; the password compares in constant time."""
    if not header.startswith("Basic "):
        return False
    try:
        given = base64.b64decode(header[6:], validate=True).decode("utf-8").partition(":")[2]
    except ValueError:  # bad base64 and bad UTF-8 are both ValueErrors
        return False
    return secrets.compare_digest(given.encode(), password.encode())


#: Wrong passwords allowed per client address per window. Past it that address
#: gets 429 until the window ends, turning online guessing from thousands of
#: tries a second into ten every ten minutes.
AUTH_MAX_FAILURES = 10
AUTH_WINDOW_S = 600
_auth_failures: dict[str, list[float]] = {}


def _record_auth_failure(client: str, recent: list[float], now: float) -> None:
    """ponytail: keyed by connecting address. Behind a reverse proxy every
    client shares the proxy's address, so one guesser locks everyone out;
    rate-limit sign-ins at the proxy there. The table is swept when it grows,
    so guesses from many addresses cannot grow it without bound."""
    recent.append(now)
    _auth_failures[client] = recent
    if len(_auth_failures) > 10_000:
        for key in [k for k, v in _auth_failures.items() if now - v[-1] >= AUTH_WINDOW_S]:
            del _auth_failures[key]


#: Sent on every response. The pages use inline scripts and style attributes,
#: so those stay allowed; everything else is limited to the origins actually
#: used: Leaflet on unpkg, Google Fonts, and the two basemap tile servers.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://unpkg.com https://server.arcgisonline.com "
        "https://*.tile.openstreetmap.org; "
        "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}


@app.middleware("http")
async def access_guard(request: Request, call_next):
    """Nothing here is safe unauthenticated: every job, export and query is
    readable by anyone who can reach the server.

    Without GEO_PASSWORD only this machine may connect. With it, every request,
    pages included, needs HTTP Basic auth, which browsers prompt for once and
    resend on each same-origin fetch. A reverse proxy on this machine connects
    from loopback, so set GEO_PASSWORD whenever the server is exposed at all.
    """
    password = os.environ.get("GEO_PASSWORD")
    client = request.client.host if request.client else ""
    if password:
        header = request.headers.get("authorization", "")
        now = time.monotonic()
        recent = [t for t in _auth_failures.get(client, ()) if now - t < AUTH_WINDOW_S]
        if len(recent) >= AUTH_MAX_FAILURES:
            return Response("Too many failed sign-ins; try again later", status_code=429,
                            headers={"Retry-After": str(AUTH_WINDOW_S)})
        if not _password_ok(header, password):
            # Only a wrong password counts. A browser's first request carries no
            # credentials at all, and counting that would lock out real users.
            if header:
                _record_auth_failure(client, recent, now)
            return Response("Authentication required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="geo"'})
        _auth_failures.pop(client, None)
    elif not _is_loopback(client):
        return JSONResponse({"detail": "this server accepts local connections only; "
                             "set GEO_PASSWORD to serve other machines"}, status_code=403)
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


def parse_bbox(raw: str) -> BBox:
    try:
        parts = [float(v) for v in raw.split(",")]
    except ValueError:
        raise HTTPException(400, f"bbox must be four numbers, got {raw!r}") from None
    if len(parts) != 4:
        raise HTTPException(400, f"bbox needs 4 values (west,south,east,north), got {len(parts)}")
    west, south, east, north = parts
    if not (west < east and south < north):
        raise HTTPException(400, "bbox must be west,south,east,north with west<east and south<north")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(400, "bbox is outside valid lon/lat range")
    return west, south, east, north


def check_aoi(box) -> None:
    """Refuse an oversized AOI before it is queued, not minutes into the run."""
    try:
        raster.target_grid(box)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def submit(work, params: dict) -> jobs.Job:
    """Queue a run, or tell the client the queue is full."""
    try:
        return jobs.STORE.submit(work, params)
    except jobs.QueueFull as exc:
        raise HTTPException(429, str(exc)) from None


def finished_result(job_id: str) -> dict:
    result = jobs.STORE.result(job_id)
    if result is None:
        raise HTTPException(410, f"the result of job {job_id} is no longer stored")
    return result


class DetectRequest(BaseModel):
    """Two-window or multi-timestamp detection.

    Supply `start`/`end`/`steps` for a series, or `window_a`/`window_b` for a
    straight two-date comparison. A series rejects transient difference and
    dates the change; two dates cannot do either.
    """

    bbox: list[float] = Field(..., min_length=4, max_length=4)
    window_a: tuple[DateStr, DateStr] | None = None
    window_b: tuple[DateStr, DateStr] | None = None
    start: DateStr | None = None
    end: DateStr | None = None
    steps: int | None = Field(None, ge=2, le=series_run.MAX_STEPS)
    min_persist: int = Field(1, ge=0, le=series_run.MAX_STEPS)
    index: str = "ndvi"
    polarization: str = Field("vv", pattern="^(vv|vh)$")
    # Bounds keep a run meaningful and finite: a threshold near zero turns noise
    # into hundreds of thousands of polygons.
    optical_threshold: float | None = Field(None, ge=0.05, le=2.0)
    sar_threshold_db: float | None = Field(None, ge=0.5, le=30.0)
    sieve_size: int | None = Field(None, ge=0, le=100_000)
    max_cloud: float = Field(80.0, ge=0, le=100)
    use_s1: bool = True
    use_s2: bool = True

    @field_validator("index")
    @classmethod
    def _known_index(cls, v: str) -> str:
        if v not in s2src.INDEX_ASSETS:
            raise ValueError(f"index must be one of {sorted(s2src.INDEX_ASSETS)}")
        return v

    @field_validator("bbox")
    @classmethod
    def _ordered(cls, v: list[float]) -> list[float]:
        if not (v[0] < v[2] and v[1] < v[3]):
            raise ValueError("bbox must be west,south,east,north with west<east and south<north")
        if not (-180 <= v[0] and v[2] <= 180 and -90 <= v[1] and v[3] <= 90):
            raise ValueError("bbox is outside valid lon/lat range")
        return v

    @model_validator(mode="after")
    def _one_mode(self):
        has_series = self.start and self.end and self.steps
        has_pair = self.window_a and self.window_b
        if not has_series and not has_pair:
            raise ValueError(
                "supply either start+end+steps (series) or window_a+window_b (pair)"
            )
        return self

    @property
    def is_series(self) -> bool:
        return bool(self.start and self.end and self.steps)


@app.get("/api/scenes")
def scenes(
    bbox: str = Query(..., description="west,south,east,north"),
    start: str = Query(...),
    end: str = Query(...),
    max_cloud: float = 80.0,
):
    """What each sensor actually has for this AOI and window.

    Reported separately per sensor: Sentinel-1 items carry no cloud-cover
    property at all, so a single merged listing would misrepresent one of them.
    """
    box = parse_bbox(bbox)
    out: dict = {"s2": [], "s1": [], "notes": []}
    try:
        out["s2"] = [s2src.describe(s) for s in s2src.search(box, start, end, max_cloud)]
    except Exception as exc:  # noqa: BLE001
        out["notes"].append("Sentinel-2 search failed: " + jobs.public_error(exc))
    try:
        out["s1"] = [s1src.describe(s) for s in s1src.search(box, start, end)]
    except Exception as exc:  # noqa: BLE001
        out["notes"].append("Sentinel-1 search failed: " + jobs.public_error(exc))
    return out


@app.post("/api/detect")
def detect(req: DetectRequest):
    """Start a detection job. Returns immediately; poll /api/jobs/{id}."""
    box = tuple(req.bbox)
    check_aoi(box)

    def work():
        if req.is_series:
            return series_run.run_series(
                box, req.start, req.end, req.steps,
                index=req.index,
                polarization=req.polarization,
                optical_threshold=req.optical_threshold,
                sar_threshold_db=req.sar_threshold_db,
                sieve_size=req.sieve_size,
                max_cloud=req.max_cloud,
                min_persist=req.min_persist,
                use_s1=req.use_s1,
                use_s2=req.use_s2,
            )
        return pipeline.run(
            box, tuple(req.window_a), tuple(req.window_b),
            index=req.index,
            polarization=req.polarization,
            optical_threshold=req.optical_threshold,
            sar_threshold_db=req.sar_threshold_db,
            sieve_size=req.sieve_size,
            max_cloud=req.max_cloud,
            use_s1=req.use_s1,
            use_s2=req.use_s2,
        )

    job = submit(work, req.model_dump())
    return {"job_id": job.id, "status": job.status}



class DroneRequest(BaseModel):
    """Two drone flights of the same site, given as local file paths.

    Paths rather than uploads: this server binds to localhost for one user, the
    orthophotos already sit on that user's disk, and pushing multi-gigabyte
    GeoTIFFs through an HTTP form would add a dependency and a copy for no gain.
    """

    before: str
    after: str
    threshold: float = Field(0.2, ge=0.02, le=2.0)
    sieve_size: int = Field(50, ge=0, le=1_000_000)


#: Flights are read only from this folder. A request names files on the
#: server's disk, so without a boundary any readable path could be probed.
DRONE_DIR = (ROOT / bridge.SETTINGS.get("drone", {}).get("data_dir", "drone_data")).resolve()


def drone_paths(req: DroneRequest) -> tuple[str, str]:
    """Resolve both flights inside DRONE_DIR; `..` and absolute paths elsewhere are refused."""
    out = []
    for raw in (req.before, req.after):
        path = (DRONE_DIR / raw).resolve()
        if not path.is_relative_to(DRONE_DIR):
            raise HTTPException(400, f"flights must be inside the drone folder {DRONE_DIR}; got {raw!r}")
        out.append(str(path))
    return out[0], out[1]


@app.post("/api/drone/inspect")
def drone_inspect(req: DroneRequest):
    """Report what each flight supports before running a comparison."""
    before, after = drone_paths(req)
    try:
        return {"before": dronesrc.inspect(before), "after": dronesrc.inspect(after)}
    except dronesrc.DroneError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/drone/detect")
def drone_detect(req: DroneRequest):
    """Compare two drone flights at their native resolution."""
    import detect as detectmod

    before, after = drone_paths(req)

    def work():
        out = dronesrc.compare(before, after)
        grid = out["grid"]
        res = detectmod.detect(
            out["diff"], out["valid"], grid.transform, grid.crs,
            threshold=req.threshold,
            sieve_size=req.sieve_size,
            pixel_size_m=grid.resolution,
            extra_warnings=out["notes"],
        )
        return {
            "type": "FeatureCollection",
            "features": res.features,
            "properties": {
                "source": "drone",
                "index": out["index"],
                "crs": grid.crs,
                "resolution_m": grid.resolution,
                "shift_px": out["shift_px"],
                "shift_confidence": out["shift_confidence"],
                "flights": out["flights"],
                "stats": {
                    "clear_fraction": round(res.clear_fraction, 3),
                    "hectares": {"loss": round(res.loss_ha, 4), "gain": round(res.gain_ha, 4)},
                    "warnings": res.warnings,
                },
            },
        }

    try:
        dronesrc.inspect(before)
        dronesrc.inspect(after)
        dronesrc.common_grid(before, after)  # size check now, not minutes into the job
    except dronesrc.DroneError as exc:
        raise HTTPException(400, str(exc)) from None

    job = submit(work, req.model_dump())
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [j.summary() for j in jobs.STORE.all()]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, include_features: bool = True):
    job = jobs.STORE.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    body = job.summary()
    if include_features and job.status == jobs.DONE:
        body["result"] = finished_result(job_id)
    return body


@app.get("/api/export/{job_id}.geojson")
def export(job_id: str):
    job = jobs.STORE.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    if job.status != jobs.DONE:
        raise HTTPException(409, f"job {job_id} is {job.status}, not done")
    return Response(
        content=json.dumps(finished_result(job_id), indent=2),
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="change_{job_id}.geojson"'},
    )



#: Concurrent chip renders. Each is a remote read against Earth Search or
#: Planetary Computer; past this a request waits CHIP_WAIT_S and then gets 429,
#: so one client cannot flood the upstreams under this server's identity.
MAX_CHIP_RENDERS = 4
CHIP_WAIT_S = 20
_chip_slots = threading.BoundedSemaphore(MAX_CHIP_RENDERS)


def _render_chip(sensor, scene_id, grid, box, polarization) -> bytes:
    try:
        if sensor == "s2":
            rgb = chips.s2_truecolor(s2src.get_item(scene_id), grid, box)
        else:
            rgb = chips.s1_backscatter(s1src.get_item(scene_id), grid, box, polarization)
        return chips.encode(rgb)
    except (s2src.SceneSearchError, s1src.SceneSearchError) as exc:
        raise HTTPException(404, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(502, "chip render failed: " + jobs.public_error(exc)) from None


@app.get("/api/chip")
async def chip(
    bbox: str = Query(..., description="west,south,east,north"),
    scene_id: str = Query(...),
    sensor: str = Query("s2", pattern="^(s1|s2)$"),
    polarization: str = Query("vv", pattern="^(vv|vh)$"),
    px: int = Query(0, ge=0, le=2048, description="long side; 0 uses the configured default"),
):
    """True-colour (S2) or backscatter (S1) imagery for the AOI.

    Rendered in EPSG:4326 so the bytes line up with the lat/lon rectangle the
    client pins them to. Cached, so flicking between before and after is
    instant after the first render.
    """
    box = parse_bbox(bbox)
    # Thumbnails are a fraction of the pixels of a full chip, so the filmstrip
    # fills in quickly instead of pulling a dozen full-size rasters.
    grid = chips.chip_grid(box, max_px=px or chips.MAX_PX)
    # Wait for a render slot without holding a server thread: threads blocked
    # here would starve every other request of the shared pool.
    deadline = time.monotonic() + CHIP_WAIT_S
    while not _chip_slots.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise HTTPException(429, "too many imagery requests at once; try again shortly")
        await asyncio.sleep(0.05)
    try:
        jpeg = await run_in_threadpool(_render_chip, sensor, scene_id, grid, box, polarization)
    finally:
        _chip_slots.release()

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )



class SemanticRequest(BaseModel):
    job_id: str = Field(..., max_length=64)
    query: str = Field(..., min_length=1, max_length=300)
    top_k: int = Field(20, ge=1, le=200)


#: Region embeddings per job. Encoding a few hundred crops takes seconds; the
#: text side is milliseconds, so caching the image side makes every query after
#: the first feel instant.
_EMBEDDINGS: dict[str, object] = {}
#: Runs whose embeddings stay in memory; the oldest is dropped past this.
MAX_EMBEDDED_RUNS = 8


@app.post("/api/semantic")
def semantic_search(req: SemanticRequest):
    """Rank a completed run's change regions against a natural-language query."""
    job = jobs.STORE.get(req.job_id)
    if job is None:
        raise HTTPException(404, f"no job {req.job_id}")
    if job.status != jobs.DONE:
        raise HTTPException(409, f"job {req.job_id} is {job.status}, not done")

    result = finished_result(req.job_id)
    features = result.get("features", [])
    if not features:
        return {"query": req.query, "matches": [], "note": "this run detected no change"}

    props = result["properties"]
    box = tuple(props["bbox"])

    try:
        emb = _EMBEDDINGS.get(req.job_id)
        if emb is None:
            # Embed against the most recent optical scene: the imagery that
            # shows the ground as it ended up, which is what a query describes.
            optical = [s for s in props.get("scenes", []) if s.get("sensor") == "S2"]
            if not optical:
                raise HTTPException(422, "semantic search needs an optical scene; this run had none")
            grid = chips.chip_grid(box, max_px=semantic.EMBED_CHIP_PX)
            rgb = chips.s2_truecolor(s2src.get_item(optical[-1]["id"]), grid, box)
            emb = semantic.embed_regions(features, rgb, box)
            while len(_EMBEDDINGS) >= MAX_EMBEDDED_RUNS:
                _EMBEDDINGS.pop(next(iter(_EMBEDDINGS)), None)  # dicts keep insertion order
            _EMBEDDINGS[req.job_id] = emb
        matches = semantic.rank(emb, req.query, req.top_k)
    except semantic.SemanticUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    for m in matches:
        m["properties"] = features[m["feature_index"]]["properties"]
        m["geometry"] = features[m["feature_index"]]["geometry"]
    return {"query": req.query, "count": len(matches), "matches": matches}



class LocateRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)
    span_deg: float = Field(locate.DEFAULT_SPAN_DEG, ge=locate.MIN_SPAN_DEG,
                            le=locate.MAX_SPAN_DEG)


@app.post("/api/locate")
def locate_aoi(req: LocateRequest):
    """Resolve a plain-language request to an AOI without running detection.

    Separate from /api/smart so the chosen place can be reviewed -- and a
    different alternative picked -- before committing to a multi-minute run.
    """
    try:
        return locate.resolve(req.query, span=req.span_deg)
    except locate.GeocodeBusy as exc:
        raise HTTPException(429, str(exc)) from None
    except locate.GeocodeError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


class SmartRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)
    start: DateStr = "2023-01-01"
    end: DateStr = "2024-12-31"
    steps: int = Field(4, ge=2, le=series_run.MAX_STEPS)
    span_deg: float = Field(locate.DEFAULT_SPAN_DEG, ge=locate.MIN_SPAN_DEG,
                            le=locate.MAX_SPAN_DEG)
    top_k: int = Field(12, ge=1, le=200)


@app.post("/api/smart")
def smart_search(req: SmartRequest):
    """Plain language in, ranked change out: locate, detect, then rank.

    The index is chosen from the stated intent -- construction responds in
    NDBI, water in NDWI, vegetation in NDVI -- so the detector is looking for
    the thing that was actually asked about.
    """
    try:
        found = locate.resolve(req.query, span=req.span_deg)
    except locate.GeocodeBusy as exc:
        raise HTTPException(429, str(exc)) from None
    except locate.GeocodeError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    box = tuple(found["bbox"])
    check_aoi(box)

    def work():
        result = series_run.run_series(
            box, req.start, req.end, req.steps, index=found["index_hint"],
        )
        result["properties"]["located"] = found

        what = (found["what"] or "").strip()
        if not what:
            return result
        # Rank by the "what" half of the request. Search failure must not
        # discard a detection run that already succeeded.
        try:
            optical = [x for x in result["properties"].get("scenes", []) if x.get("sensor") == "S2"]
            if optical and result["features"]:
                grid = chips.chip_grid(box, max_px=semantic.EMBED_CHIP_PX)
                rgb = chips.s2_truecolor(s2src.get_item(optical[-1]["id"]), grid, box)
                emb = semantic.embed_regions(result["features"], rgb, box)
                matches = semantic.rank(emb, what, req.top_k)
                order = {m["feature_index"]: m for m in matches}
                for i, f in enumerate(result["features"]):
                    if i in order:
                        f["properties"]["match_rank"] = order[i]["rank"]
                        f["properties"]["match_score"] = order[i]["score"]
                result["properties"]["ranked_for"] = what
                result["properties"]["top_matches"] = [m["feature_index"] for m in matches]
        except (semantic.SemanticUnavailable, ValueError) as exc:
            result["properties"]["stats"]["warnings"].append(
                f"ranking unavailable, showing all detections: {exc}")
        return result

    job = submit(work, {"query": req.query, "bbox": list(box),
                                   "index": found["index_hint"]})
    return {"job_id": job.id, "status": job.status, "located": found}


#: Upstreams a run depends on, each probed with one light GET: the STAC roots
#: and Nominatim's status page, never a search.
UPSTREAMS = {
    "earth_search": s2src.ROOT,
    "planetary_computer": s1src.ROOT,
    "nominatim": "https://nominatim.openstreetmap.org/status?format=json",
}


def _reachable(url: str) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": locate.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as fh:
            return 200 <= fh.status < 300
    except (OSError, ValueError):  # URLError and HTTPError are OSErrors
        return False


@app.get("/api/health")
def health(deep: bool = False):
    """Liveness by default. `?deep=true` also probes every upstream and answers
    503 if any is unreachable, so a monitor sees an outage before a run fails."""
    checks = {
        "satellite_mvp": bridge.SATELLITE_MVP.is_dir(),
        "jobs_dir_writable": os.access(jobs.STORE.root, os.W_OK),
    }
    if deep:
        checks["satellite_mvp_matches_lock"] = bridge.matches_lock()
        with ThreadPoolExecutor(max_workers=len(UPSTREAMS)) as pool:
            checks.update(zip(UPSTREAMS, pool.map(_reachable, UPSTREAMS.values())))
    ok = all(checks.values())
    body = {"ok": ok, "checks": checks, "indices": sorted(s2src.INDEX_ASSETS),
            "jobs": len(jobs.STORE.all())}
    return body if ok else JSONResponse(body, status_code=503)


# Mounted last so every /api route is matched first. html=True serves
# index.html at "/" and each page at its own path, which is what makes the
# site multi-page and deep-linkable rather than one document.
if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
