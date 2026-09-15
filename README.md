# GEOINT Console: Earth change monitoring

Detect and explain change anywhere on Earth from free public satellite data,
with no API key.

geo fuses **Sentinel-1 radar** and **Sentinel-2 optical** imagery, so cloud
doesn't quietly become a blind spot. It compares **several dates**, not just
two, so changes that don't persist are thrown out. Every region it finds gets
a **date, a change type and its evidence**. You can ask in plain language
("new construction near lucknow"), explore the result on a map, check it
against the source imagery, and export it as GeoJSON.

Drone orthophotos can also be compared flight against flight, at their native
resolution.

## Why

- **Cloud is not "no change".** On one Amazon pair whose Sentinel-2 scenes
  were reported at 4% and 16% cloud, only **28.2%** of the area was usable
  after per-pixel masking. Radar saw all of it, and found 468 ha of real change
  inside the gap that an optical-only system would have reported as nothing.
- **Most difference is not change.** Across a 6-date series over Manaus, **63%**
  of pixels that crossed the change threshold didn't persist. A two-date
  comparison reports those as real.
- **Optical alone can't separate the causes.** Clearing and construction both
  remove vegetation. Radar tells them apart, because backscatter *falls* for
  bare ground and *rises* for structures.

The measurements and the reasoning behind each design choice are in
[docs/NOTES.md](docs/NOTES.md).

## Features

- **Two-sensor fusion with honest coverage.** Every pixel is routed into one
  evidence tier (`confirmed`, `optical`, `sar_only`, `sar_gap`). Ground neither
  sensor saw is reported as *not observed*, never as *unchanged*.
- **Multi-date persistence.** Every date is compared against the first, and
  change counts only where later observations keep agreeing. Each region is
  dated to when it first appeared.
- **Change classification** from each region's combined optical and radar
  signature: construction, clearing, flooding (including under cloud), water
  recession, regrowth, roads, and more. Each label comes with its evidence.
- **Plain-language requests.** The place is geocoded (OpenStreetMap
  Nominatim), the area and spectral index are chosen from what you asked
  for, and results are ranked by the question.
- **Semantic search** over detected regions with RemoteCLIP ("buildings and
  urban development").
- **Provenance on every polygon.** Each region records the exact public scenes,
  dates, orbit and thresholds that produced it, so anyone can re-check it.
- **Drone flight comparison** at the finer of the two resolutions, with a VARI
  fallback for RGB-only cameras.

## The web app

| Page | What it's for |
|---|---|
| **Ask** | Type what to find and where; get a run |
| **Explore** | Map workspace: search a place, draw an area, set dates and sensitivity, detect |
| **Results** | The run on a map beside a region panel: evidence crops, signature, persistence, provenance |
| **Compare** | Drag a swipe between any two scenes of the run, with detection outlines on top |
| **Timeline** | Filmstrip of every observation, optical and radar, one column per time window |
| **Report** | One printable sheet (Print / PDF) |
| **Drone** | Compare two drone flights |
| **About** | How it works, and its limits |

The current run lives in the URL (`?job=<id>`, plus `&region=<n>`), so every
page and region can be linked to directly.

## Quick start

### Requirements

- Python 3.12
- **[satellite-mvp](#satellite-mvp)**, available on the same machine. geo
  imports its raster primitives as a library.
- Internet access to Element84 Earth Search, Microsoft Planetary Computer and
  OpenStreetMap Nominatim. None of them needs an account or a key.

### Install

```bash
git clone <this-repository-url> geo
cd geo
python -m venv .venv
.venv/Scripts/activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -r <path-to-satellite-mvp>/requirements.txt
```

`torch`, `torchvision` and `open_clip_torch` are only needed for semantic
search. Without them, search reports itself unavailable and everything else
works.

### Point geo at satellite-mvp

Either set an environment variable:

```bash
export GEO_SATELLITE_MVP=/path/to/satellite-mvp     # PowerShell: $env:GEO_SATELLITE_MVP = "..."
```

or edit `[paths] satellite_mvp` in `settings.toml`. A relative path is resolved
against the geo folder. geo stops at startup with a clear message if the path
is wrong.

### Run

```bash
python -m uvicorn api:app --host 127.0.0.1 --port 8770
```

Open <http://127.0.0.1:8770/>. A cold run takes about a minute; repeat runs
over the same area use the local cache and return in about five seconds.

On Windows, `start_geo.bat` starts the server if it isn't running and opens
the browser. Edit the Python path inside it to match your environment, then
point a desktop shortcut at it.

> Run **one** server process. Run status lives in memory, so `--workers N`
> would answer a poll from a process that never saw the run.

## Configuration

`settings.toml`:

| Key | Default | Meaning |
|---|---|---|
| `paths.satellite_mvp` | — | Location of satellite-mvp (overridden by `GEO_SATELLITE_MVP`) |
| `paths.cache_dir` | `.cache` | Derived raster arrays |
| `paths.cache_max_gb` | `5` | Cache budget; least recently used arrays are deleted first |
| `paths.jobs_dir` | `runs` | One status file and one GeoJSON per run; the newest 100 are kept |
| `detect.optical_threshold` | `0.20` | Minimum index change |
| `detect.sar_threshold_db` | `3.0` | Minimum backscatter change |
| `detect.sieve_size` | `50` | Minimum patch in pixels (0.5 ha at 10 m) |
| `detect.speckle_window` | `5` | Radar multilook window |
| `chips.max_px` | `768` | Long side of rendered imagery |
| `drone.data_dir` | `drone_data` | The only folder drone flights are read from |

Environment variables:

| Variable | Effect |
|---|---|
| `GEO_SATELLITE_MVP` | Path to satellite-mvp |
| `GEO_PASSWORD` | Enables HTTP Basic auth on every request (see [Security](#security)) |

## API

| Method and route | Purpose |
|---|---|
| `POST /api/smart` | Plain-language request → locate, detect, rank. Returns a job id |
| `POST /api/locate` | Resolve a request to an area without running detection |
| `POST /api/detect` | Start a detection over a bounding box |
| `GET /api/jobs` | List runs |
| `GET /api/jobs/{id}` | Poll a run; add `?include_features=false` while polling |
| `GET /api/export/{id}.geojson` | Download a run as GeoJSON |
| `POST /api/semantic` | Rank a finished run's regions against a text query |
| `GET /api/scenes` | Scenes available per sensor for an area and date range |
| `GET /api/chip` | Rendered imagery for an area: S2 true colour or S1 backscatter |
| `POST /api/drone/inspect` | What two drone flights support |
| `POST /api/drone/detect` | Compare two drone flights |
| `GET /api/health` | Liveness; `?deep=true` also checks every upstream service |

A multi-date detection:

```bash
curl -X POST http://127.0.0.1:8770/api/detect \
  -H "Content-Type: application/json" \
  -d '{"bbox": [80.88, 26.79, 80.99, 26.89], "start": "2023-01-01", "end": "2024-12-31", "steps": 4, "index": "ndbi"}'
```

Detection runs in the background: poll `/api/jobs/{id}` until `status` is
`done` or `failed`. Every feature carries `change_type`, `change_label`,
`class_confidence`, `evidence`, `tier`, `area_ha`, `change_date`, and the
scene ids it was made from.

## Security

geo was built as a single-user local tool. Its defaults are safe for that, and
it can be exposed with care:

- **Without `GEO_PASSWORD`**, the server answers only this machine. Every
  other client gets 403.
- **With `GEO_PASSWORD`**, every request, pages included, needs HTTP Basic
  auth (any username). Ten wrong passwords from one address lock it out for
  ten minutes.
- **Put it behind a reverse proxy with HTTPS** before exposing it: Basic auth
  sends the password with every request. Behind a proxy, set `GEO_PASSWORD`
  anyway, since the proxy connects from loopback. Rate-limit sign-ins at the
  proxy too, because every client shares the proxy's address.
- Inputs are bounded:
  - Areas over 9 million pixels (about 30 × 30 km at 10 m) and drone
    comparisons over 25 million pixels are refused.
  - Detection settings are range-checked.
  - A run keeps at most 2,000 regions.
- Drone paths are confined to `drone.data_dir`. Scene ids are validated
  before they reach an upstream URL.
- Every response carries a Content-Security-Policy, `X-Frame-Options`,
  `nosniff` and a same-origin referrer policy.
- Error messages shown to users have URL query strings removed, because signed
  Planetary Computer URLs carry access tokens.

## Operations

- **Runs survive a restart.** A run that was still in progress when the server
  stopped is reported as failed. At most 8 runs wait for a worker; beyond that,
  starting one returns 429.
- **Imagery rendering** is limited to 4 concurrent renders. Place searches
  respect Nominatim's one-request-per-second policy, with at most 5 queued.
- **`GET /api/health?deep=true`** checks Earth Search, Planetary Computer,
  Nominatim, the runs folder, and whether satellite-mvp still matches
  `satellite_mvp.lock`. It returns 503 if anything fails.

### satellite-mvp

geo imports satellite-mvp's raster primitives (asset signing, image
alignment, region tools) and never modifies it. Because that code isn't pinned
by version, `satellite_mvp.lock` records a fingerprint of the copy geo was
tested against. If the deep health check reports
`satellite_mvp_matches_lock: false`, run the tests against the new code, then
record it:

```bash
python bridge.py --lock
```

## Project layout

| Path | Role |
|---|---|
| `api.py` | FastAPI app: routes, access control, limits, static site |
| `jobs.py` | Background runs, saved to `runs/` |
| `series_run.py` | Multi-date detection |
| `pipeline.py` | Two-date detection |
| `series.py` | Persistence across dates |
| `fuse.py` | Evidence tiers, coverage accounting, per-region signatures |
| `detect.py` | Threshold, sieve, polygonize, region cap |
| `classify.py` | Change type from the optical + radar signature |
| `indices.py`, `sar.py`, `raster.py` | Index math, radar handling, the shared analysis grid |
| `sources/` | Sentinel-2, Sentinel-1 and drone readers |
| `locate.py` | Plain-language request → area of interest |
| `semantic.py` | RemoteCLIP region embeddings and ranking |
| `chips.py`, `cache.py` | Imagery rendering and the array cache |
| `bridge.py` | Loads satellite-mvp; fingerprint and lock |
| `build_web.py` | Generates `web/*.html` and `web/shared.js` |
| `web/shared.css` | Stylesheet (hand-maintained) |
| `tests/` | Test suite |
| `docs/NOTES.md` | Measurements and design notes |

The pages are generated: edit `build_web.py`, then run `python build_web.py`.

## Tests

```bash
python -m pytest -q
```

196 tests, with no network access and no fixtures. They cover:
- index and radar math, evidence tiers and coverage
- persistence, classification and region provenance
- drone ingest against real GeoTIFFs written to disk
- the API's access control, limits, and submit → poll → export path
- job persistence across restarts, and cache eviction

They also check that the generated pages match `build_web.py`, that their
scripts parse, and that every element a script looks up by id exists.

The tests import satellite-mvp, so they need it installed as described above.

## Limitations

- Change classification is rule-based, from index and backscatter signatures.
  Confidence values order results; they aren't calibrated probabilities, and
  semantic search scores aren't either.
- Areas are capped at about 30 × 30 km per run.
- Drone flights can't be compared against satellite imagery (the resolution gap
  would force the comparison down to 10 m).
- A plain-language request is only as good as the area it produces: a river's
  name yields an area that is mostly water.
- One server process; no CI yet.

## Data sources and attribution

| Data | Provider |
|---|---|
| Sentinel-2 L2A | [Element84 Earth Search](https://earth-search.aws.element84.com/v1) |
| Sentinel-1 RTC | [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/) |
| Place search | [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/) |
| Basemap imagery | Esri World Imagery |

- Contains modified Copernicus Sentinel data.
- Geocoding © OpenStreetMap contributors, under the ODbL.
- Basemap tiles © Esri and its data providers.
- Semantic search uses the RemoteCLIP model.

## License

No license has been chosen yet. Until one is added, all rights are reserved by
the author.
