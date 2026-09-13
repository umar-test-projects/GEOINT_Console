# geo — Earth change monitoring

Detect change in any area of Earth between two dates, from free public
satellite data, with no API key. Sentinel-1 (radar) and Sentinel-2 (optical)
are fused so that cloud does not silently become a blind spot. Drone
orthophotos can be compared flight against flight at their native resolution.

## Why both sensors

Optical imagery is unusable under cloud, and scene-level cloud percentages
badly understate the problem. Measured on a real Amazon pair whose Sentinel-2
scenes were reported at **4% and 16% cloud cover**:

| | |
|---|---|
| Sentinel-2 usable after per-pixel SCL masking | **28.2%** |
| Sentinel-1 usable | **100.0%** |
| Optical gap (optical blind, radar sighted) | **71.8% = 8,895 ha** |
| — of which cloud, shadow or snow | 31.1% |
| — of which outside the granule footprint | 40.7% |
| Real change radar found inside that gap | **468 ha** |

An optical-only system reports that 8,895 ha as *no data* — which in practice
gets read as *no change*.

The split matters and is reported separately. Most of that gap turned out to be
**missing coverage, not weather**: the chosen scenes did not span the whole AOI,
and the two dates clipped it in different places, so the jointly-usable area is
the intersection of two partial footprints. Calling the whole 71.8% "cloud"
would overstate what the weather actually cost. Toggle the before/after imagery
in the UI and the footprint edge is plainly visible as a hard diagonal — while
the radar chip covers the AOI completely.

### Radar is not a second opinion

On a cloud-free scene, pixel-wise agreement between NDVI change and SAR
log-ratio change was only **3.6%**. That is physics, not a bug: NDVI measures
greenness, backscatter measures roughness, structure and moisture. So
Sentinel-1 is used as a **coverage layer and a corroboration tier, never as a
pixel-wise AND gate** — naive `s2_change & s1_change` would discard ~96% of
real detections.

Every pixel is routed into exactly one tier:

| Tier | Meaning |
|---|---|
| `confirmed` | Both sensors observed this ground and both report change |
| `optical` | Spectral change; radar quiet or unavailable |
| `sar_only` | Structural/moisture change with no spectral signature |
| `sar_gap` | **Change under cloud, invisible to optical entirely** |

Plus an explicit `not_observed` mask where neither sensor could see. Coverage
percentages ship with every result, because *unobserved* and *unchanged* must
never collapse into each other.

## Multiple timestamps, not two

Two dates can tell you something looks different. They cannot tell you whether
it *stayed* different — and a cloud edge, a shadow, a wet field or a
mis-registered pixel all look exactly like change between two dates.

A series fixes both. Every observation is compared against the first (never
against its predecessor — against a rolling reference a persistent change
reverts to "no change" as soon as the new state becomes the baseline), and a
change counts only where later observations keep agreeing, with the same sign.

Measured on the Manaus AOI across 6 timestamps in 2023–2024:

| | |
|---|---|
| Confirmed by later dates | 240.4 ha |
| Changed at the final observation, nothing yet to confirm it | 237.9 ha |
| **Rejected as transient** | **409.1 ha** |

**63% of threshold-crossing pixels did not persist.** A two-date comparison
would have reported those 409 ha as real change.

Three outcomes, kept distinct:

- **persistent** — crossed the threshold and every later valid observation
  still agrees. Evidence.
- **latest** — changed at the final observation with nothing after it. Real,
  but not yet corroborated, so it is labelled rather than counted as confirmed.
- **transient** — crossed, then the series contradicted it. Excluded, and
  reported as the noise figure above.

### The timeline filmstrip

A table sits over the map — **row 1 Sentinel-2, row 2 Sentinel-1** — with one
column per time window. Optical and radar are acquired on different days, so
each scene is keyed to the window it came from rather than to its own date;
that is what makes the two rows line up as columns. A window with no usable
scene for a sensor shows a hatched blank rather than shifting the row out of
alignment.

Each cell is a thumbnail of the actual acquisition with its date and
cloud/orbit. Clicking one:

- draws the full-resolution scene over the AOI, and
- filters the map to change dated **on or before** that observation, so
  detections accumulate as you move right. At the baseline the map is empty —
  nothing has been detected yet.

**Clicking a thumbnail expands that scene in place**, directly above the strip
and in the normal page flow — nothing overlays the table and there is nothing to
dismiss. Clicking the same thumbnail again collapses it.

A 96 px chip cannot tell cloud from a granule footprint edge; at 1024 px the
difference is obvious, which is the point of being able to check a scene before
trusting a detection made from it.

One click does both jobs: the scene enlarges **and** the detections filter to
what had appeared by its date. The chip carries `aria-expanded` and
`aria-controls` — a disclosure, which is what it is, rather than dialog
semantics it does not need. The image box reserves its space and shows progress,
because a cold full-resolution render is a real wait and the page must not jump
when it lands. The figure has a fixed height rather than a minimum one, so it
occupies the same space empty as it does full; combined with scroll
compensation, the strip stays exactly still through expand, image arrival and
collapse (measured: 0 px of movement in all three).

**Show all dates** clears the selection and restores every detection. There is
no autoplay; stepping is under your control. *Before* and *After* are simply
the two ends of the same filmstrip, so there is one notion of "when" rather
than two that can disagree.

Thumbnails render at 128 px (4.6 KB each) against 768 px for the full chip, and
are cached separately — chip cache keys include the render size, or a thumbnail
and a full chip would overwrite each other.

### When it happened

Each region is dated to the observation its change first appears on, by the
modal date of its pixels. Optical dates what optical saw; radar dates what only
radar saw, on its own observation calendar — so every detection gets a date, not
just the ones optical could see. Each carries `change_date`, `dated_by` and
`confirmed_by`.

Usage: supply `start`, `end` and `steps` (2–8) for a series, or `window_a` and
`window_b` for a straight pair. Dates load concurrently, so 6 timestamps cost
~110 s cold rather than six times a single pair; cached re-runs are ~9 s.

## The site

Eight pages, flat (no breadcrumbs, which are for three or more levels of depth):

| Page | Purpose |
|---|---|
| **Ask** | Plain-language entry: name what and where, get a run |
| **Explore** | Map workspace: draw an area, set dates and sensitivity, detect |
| **Results** | What changed, coverage, persistence, regions, semantic search, export |
| **Compare** | Before/after swipe between any two scenes of the run, outlines on top |
| **Timeline** | Observation filmstrip: optical on row 1, radar on row 2, one column per window |
| **Report** | One printable sheet: Print / PDF is the browser's own print |
| **Drone** | Flight-against-flight comparison |
| **About** | How it works, and the honest limits |

**Run state lives in the URL** (`?job=<id>`, plus `&region=<n>` for one
region), not in memory. Every page is deep-linkable, navigation carries the
current run, and the back button behaves.

### Region detail

Every row in the Results region table, every semantic search hit, and every
map popup on Explore opens one region drawer:

- **Evidence**: context crops about 2.2 km across with the region's outline on
  top, the signed index and backscatter change measured inside the polygon, the
  evidence string and its persistence. On a *radar under cloud* region the
  crops are the radar pair, because optical was blind there by definition.
- **Provenance**: the exact public scenes the detection was made from, the
  matched relative orbit, and the thresholds that produced the number.

To support it, every feature now carries `id`, `scene_before`/`scene_after`
(with dates), `sar_scene_before`/`sar_scene_after`, `relative_orbit`, and
`ndvi_delta`, `ndwi_delta`, `ndbi_delta`, `sar_delta_db`. A sensor that could
not see a region adds no reading, so an absent value is never mistaken for zero
change. Runs made before this change lack these fields; re-run to get them.

### Design system

The visual design is the **cartographic light** theme from the UI mockups
export: ink on parchment, hairline rules, Source Serif 4 for headings, Fira Code
small caps for labels, square corners, and colour only where it carries
meaning. Its stated purpose is to print cleanly into a briefing pack and let the
map hold the screen. Every tier and change-type colour passes 4.5:1 on both
grounds, and unobserved ground is always hatched, never a solid colour.

Where the export assumed things this repo does not provide, the integration
adapted rather than inventing data:

- The export read thresholds and a place name from fields that do not exist;
  they come from `properties.thresholds` and the run's located place instead.
- Its drawer proposed "Add to case file" and "Flag for field check" with no
  backend; those became **Open on map** and **Copy region GeoJSON**, which work.
- Compare fits scenes with `contain`, not the export's `cover`, so AOI edges,
  where change may be, are never cropped out of an analysis view.
- The report's coverage bar hatches only ground **neither** sensor saw. The
  export hatched everything outside the optical footprint, which radar did see.
- Change-type colours are read from the `--type-*` tokens at load, because
  Leaflet and SVG attributes cannot resolve `var()` references.

`build_web.py` scaffolds the pages from one shared head and nav; `web/shared.css`
is hand-maintained.

## Ask in plain language

Type a request and the system locates the place, picks the AOI, chooses the
index, detects, and ranks the result by what you asked for.

```
POST /api/smart  {"query": "new construction near the gomti river"}
```

The chain, for that exact query:

| Step | Result |
|---|---|
| Split | what = "new construction", where = "gomti river" |
| Index from intent | **NDBI** — construction responds in built-up, not vegetation |
| Geocode (Nominatim, keyless) | river "Gomti, India" |
| AOI | `[81.2225, 26.71769, 81.3345, 26.81769]` — 12.4 × 11.1 km |
| Detect + rank | ~150 s |

Two real properties of geocoding shape this, and both are surfaced rather than
hidden:

- **A river's bounding box is not an AOI.** "gomti river" resolves to 3.03° —
  about 336 km, the river's whole length. It is clamped to a workable window at
  the centre, and the UI says so in plain words. Point features (a weir) are
  expanded the same way. The clamped window is widened by 1/cos(latitude) so it
  stays square on the ground rather than square in degrees.
- **Place names are ambiguous.** "gomti river" also matches three rivers in
  Gujarat. The chosen one is named and the alternatives are listed, so a run on
  the wrong side of the country is visible rather than silent.

`POST /api/locate` does the resolution alone, without committing to a
multi-minute detection, so the AOI can be checked first. The resolved bbox is
written back into the bbox field, so it stays visible and editable.

### An honest limit

Ranking is only as good as the population it ranks. Asking for *construction*
on an AOI auto-centred on a **river** works against itself: the floodplain
dominates, that run classified ~70% of regions as seasonal water change, and
the "new construction" ranking largely returns those. The mechanism is sound —
the weakness is that the AOI a river name produces is mostly water.

For construction, a town or district name gives a far better population than a
river does.

## Semantic search

Query the detected regions in plain language. Each region's context crop is
embedded with **RemoteCLIP** — the same checkpoint satellite-mvp ships, loaded
through its own encoder class, so embeddings mean the same thing in both
systems — and ranked against the query by cosine similarity.

```
POST /api/semantic  {"job_id": "...", "query": "buildings and urban development"}
```

satellite-mvp's search answers *"which tile looks like X"* against a prebuilt
FAISS index over its fixed tile grid. This answers *"which of the changes I
just detected looks like X"*, over arbitrary AOIs that have no prebuilt index.

**No FAISS.** A run yields a few hundred regions; brute-force cosine over a few
hundred 512-d vectors is one matrix multiply. An approximate index would add a
dependency and a failure mode to save nothing.

**Crops are taken at native resolution and CLIP's native input size** — 224 px
at Sentinel-2's 10 m, so ~2.2 km of ground with no resampling, which is the
scale RemoteCLIP was trained on. An earlier version cropped ~48 px and upscaled;
rankings were visibly worse.

### How well it works

Measured on the Bangalore construction AOI (465 regions):

| Query | Top 15 |
|---|---|
| "buildings and urban development" | structural_change ×13 (66% base rate) |
| "water and flooded ground" | mixed |

Queries genuinely discriminate — **1 of 10 overlap** between the urban and water
top-10s. But be realistic about the limits:

- Scores cluster narrowly (~0.28–0.31). CLIP similarities are **not calibrated
  probabilities**; they order results, they do not measure them.
- Ranking reflects **what the imagery looks like**, independently of the change
  type the classifier assigned. Partial agreement between the two is expected,
  and is sometimes the point — you can find water-looking regions the classifier
  called structural change.
- On an AOI with little variety, results fall back toward the base rate. The
  Amazon AOI is ~71% water-change regions, and a "buildings" query there returns
  roughly that proportion, because there is nothing else to find.

First search loads the model and embeds every region (~25 s); embeddings are
cached per job, so later queries return in **~140 ms**.

## What kind of change

Every detected region is classified automatically from its combined optical and
radar signature, and carries its own evidence string.

**The discrimination that needs both sensors:** clearing and construction both
strip vegetation, so both collapse NDVI identically — optical alone cannot tell
them apart. Radar can, because they leave *opposite* roughness signatures:

| Signature | Class |
|---|---|
| NDVI ↓, backscatter **↑**, NDBI ↑ | Construction / new built-up (structures are corner reflectors) |
| NDVI ↓, backscatter **↓** | Clearing / vegetation removal (canopy → smooth bare ground) |
| NDVI ↓, **no radar** | *Vegetation loss (cause unresolved)* |
| NDWI ↑, backscatter ↓↓ | Flooding / water expansion (open water reflects the pulse away) |
| Backscatter ↓↓, no optical view | Flooding under cloud |
| NDWI ↓ | Water recession |
| NDVI ↑ | Vegetation growth / regrowth |
| Long, narrow footprint | Road / linear infrastructure |
| NDBI ↑ without NDVI loss | Bare surface / material change |
| Backscatter changed, optical quiet | Structural change (radar only) |

The third row is the point. With no radar the system reports *vegetation loss*
and refuses to guess which kind, rather than picking the more likely-sounding
label. Ambiguous radar produces the same honest answer.

Classes follow satellite-mvp's `ChangeType` vocabulary where they overlap, so a
label means the same thing in both systems. That system's classifier is
RGB-brightness-only with no NIR, SWIR or radar, so it cannot make this
distinction.

Each polygon carries `change_type`, `change_label`, `class_confidence` and
`evidence` — for example:

> **Clearing / vegetation removal** — 0.51 ha, confidence 0.85
> NDVI −0.25, NDWI +0.12, NDBI +0.23; SAR −3.8 dB; vegetation lost and
> backscatter fell — canopy volume scattering replaced by smooth bare ground

Regions are labelled before vectorizing, so the signature is measured *inside*
each polygon: one verdict per region, not one for the whole scene. Polygons can
be coloured by change type or by evidence tier.

Computing all three indices costs four band reads per date instead of two, so a
cold run went from ~52 s to ~95 s. Warm runs stay at ~6 s.

## Checking a detection against the pixels

A polygon is a claim. The **AOI imagery** toggle pins the actual scene over the
bounding box — *Before*, *After*, Sentinel-2 true colour or Sentinel-1
backscatter — so a detection can be eyeballed against the imagery it came from.
Chips render in EPSG:4326 so they align exactly with the map.

All four chips are prefetched in the background the moment a detection lands, so
the toggle is warm by the time it is clicked. Four changes took a cold chip from
~39 s to ~10 s, and the payload from 1.58 MB to 76 KB:

| Change | Effect |
|---|---|
| Use the `visual` asset | One 8-bit 3-band COG instead of three 16-bit single-band reads: **23.3 s → 7.0 s** |
| JPEG instead of optimised PNG | **0.52 s → 0.02 s** to encode, 1.58 MB → 0.19 MB on the wire |
| 768 px default (`settings.toml`) | 14.5 m/px over a 0.1° AOI, near native, ~1.2 s cheaper than 1024 px |
| GDAL tuning + per-key render lock | HTTP/2, multiplexing, 1 MB ranges; duplicate concurrent requests share one render |

Profiling drove this: the remote read was 11.8 s of a 16 s chip while encoding
was 0.5 s, so the wins had to come from bytes fetched, not from encoding.

## Run it

```bash
pip install -r requirements.txt
pip install -r <satellite-mvp>/requirements.txt
python -m uvicorn api:app --port 8770
```

On this machine the interpreter is `C:/ms2env/Scripts/python.exe` (see below).
satellite-mvp's location comes from `settings.toml`; set `GEO_SATELLITE_MVP` to
point elsewhere without editing a tracked file.

Semantic search needs a working `torch` + `open_clip` pair, and the Windows
Store Python's `torchvision` is incompatible with its `torch` (repairing it hits
the Windows 260-character path limit). `C:/ms2env` already had the working
stack, so the app runs there; `fastapi` and `uvicorn` were added to it. Every
other part of the system runs identically on either interpreter, and the test
suite passes on both.

### Access

With no `GEO_PASSWORD` set, the server answers **this machine only**; any other
client gets 403. To serve other machines, set `GEO_PASSWORD` and bind wider
(`--host 0.0.0.0`): every request, pages included, then needs HTTP Basic auth
(any username). Set it behind a reverse proxy too — the proxy connects from
loopback and would otherwise pass everyone through. Use HTTPS there, since
Basic auth sends the password with every request.

An AOI larger than 9 million pixels at 10 m (about 30 × 30 km) is refused
before it is queued; past that one run exhausts memory.

### Operations

- **Runs** live in `runs/` (`jobs_dir`): one status file and one GeoJSON per
  run, so a restart keeps every finished run and shared `?job=` link. The
  newest 100 are kept. A run in progress at a restart is reported failed. At
  most 8 runs wait for a worker; past that, starting one answers 429.
- **Cache**: `.cache/` holds derived arrays up to `cache_max_gb` (5 GB); the
  least recently used are deleted first.
- **Health**: `GET /api/health` is liveness. `GET /api/health?deep=true` also
  probes Earth Search, Planetary Computer and Nominatim, answering 503 if any
  is unreachable.
- **Errors** shown to users have URL query strings cut, since signed Planetary
  Computer URLs carry access tokens; full tracebacks go to the server log.
- **One process**: run a single uvicorn worker. Run statuses live in the
  server's memory, so `--workers N` would answer a poll from a process that
  never saw the run.
- **Imagery**: at most 4 chips render at once; a request that waits more than
  20 s gets 429, so one client cannot flood Earth Search or Planetary Computer.
  Scene ids are validated before they are put in an upstream URL.
- **Headers**: every response carries a Content-Security-Policy limited to the
  origins the pages use, plus `X-Frame-Options`, `nosniff` and a same-origin
  referrer policy.
- **Drone**: a comparison over 25 million pixels is refused before it is
  queued; export the flights coarser, or crop them.
- **Checks**: `python -m pytest` also verifies that the generated pages match
  `build_web.py`, that their scripts parse, and that every element a script
  looks up by id exists. There is no CI yet: satellite-mvp is not in a
  repository a CI runner could fetch, and every test imports it.
- **Limits**: detection settings are range-checked (thresholds, minimum patch,
  steps, dates, query lengths). A run keeps at most 2,000 regions, the largest,
  and says how many smaller ones it left out; hectare totals still count every
  region.
- **Sign-in**: 10 wrong passwords from one address lock it out for 10 minutes
  (429). Behind a reverse proxy every client shares the proxy's address, so
  rate-limit sign-ins at the proxy instead.
- **Threads**: image requests wait for a render slot without holding a server
  thread, and at most 5 place searches queue for Nominatim's one-per-second
  limit (429 past that).
- **satellite-mvp** is not version controlled, so `satellite_mvp.lock` records a
  fingerprint of the code geo was tested against. `GET /api/health?deep=true`
  reports `satellite_mvp_matches_lock: false` once it changes; after the tests
  pass against the new code, run `python bridge.py --lock`.

Then open <http://127.0.0.1:8770/> — draw an AOI, pick two date windows, and
detect. A cold run takes about a minute; re-runs hit the array cache and return
in about five seconds, so the threshold sliders stay usable.

## What it detects

| Index | Change type | Bands |
|---|---|---|
| NDVI | vegetation loss, clearing, deforestation | NIR, Red |
| NDWI | water extent, flooding | Green, NIR |
| NDBI | built-up area, construction | SWIR16, NIR |
| SAR log-ratio | structure, roughness, moisture, inundation | VV or VH |

Radar is the primary sensor for flooding (water is specular, so it reads as
near-zero backscatter — and floods come with storms, exactly when optical
fails) and is strong for construction (structures act as corner reflectors).

## Noise suppression

Raw thresholding is almost entirely speckle. Both figures below are measured,
not estimated:

| | raw polygons | after filtering |
|---|---|---|
| Sentinel-2 NDVI (Amazon) | 5,761 | **69** |
| Sentinel-1 VV (Bangalore) | 57,368 | **120** |

Optical uses a minimum-patch sieve; radar additionally needs a 5×5 boxcar
multilook first, because speckle is multiplicative and roughly ten times worse
than optical noise. Reported hectares are computed from the *sieved* masks, so
the headline area always equals the polygons drawn on the map.

## API

| Route | Purpose |
|---|---|
| `GET /api/scenes?bbox=&start=&end=` | Scene availability, reported per sensor |
| `POST /api/detect` | Start a fused detection job |
| `GET /api/jobs/{id}` | Poll status, then collect tiered GeoJSON |
| `GET /api/export/{id}.geojson` | Download |
| `POST /api/drone/inspect` | What two flights support |
| `POST /api/drone/detect` | Compare two flights |

Detection runs in a worker thread because a cold comparison takes ~52 s, well
past what an HTTP request should hold open.

## Drone

Two flights of the same site, compared at the **finer** of the two
resolutions. Given as paths rather than uploads — pushing multi-gigabyte
orthophotos through an HTTP form would add a dependency and a copy for nothing.
Paths are resolved inside the drone folder (`[drone] data_dir` in
`settings.toml`, default `drone_data/`); `..` or an absolute path elsewhere is
refused, so the route cannot be used to probe the server's disk.

Most consumer drones are RGB-only, so NDVI is not computable from them. Those
fall back to **VARI**, a visible-band vegetation proxy, and the result says so
rather than returning something NDVI-shaped. Drone imagery has no
scene-classification band, so validity degrades to nodata-only; that is
reported too.

## Architecture

```
        satellite-mvp (imported, never modified)
  sign_asset_url · phase_correlation_shift · find_coherent_regions
                        |
 sources/{s2,s1,drone} -> indices.py / sar.py -> fuse.py -> detect.py -> GeoJSON
                                       ^
                        cache.py       |  jobs.py
                                       |
                             api.py + web/index.html
```

`bridge.py` puts [satellite-mvp](../satellite-mvp/satellite-mvp) on `sys.path`
and this project reuses its proven primitives. That repository is never
modified; its own 398 tests still pass. The path lives in `settings.toml`.

Only the raster primitives are imported, so `torch` and `faiss` — which its
retrieval layer needs — are never loaded here.

| File | Role |
|---|---|
| `bridge.py` | Puts satellite-mvp on `sys.path`, fails loudly if misconfigured |
| `raster.py` | The shared analysis grid both sensors are warped onto |
| `indices.py` | NDVI / NDWI / NDBI / VARI, SCL validity |
| `sar.py` | dB conversion, speckle filter, log-ratio |
| `fuse.py` | Tier routing and coverage accounting |
| `detect.py` | Threshold, sieve, polygonize to true footprints |
| `cache.py` | Array cache; signed URLs are deliberately never cached |
| `pipeline.py` | Orchestration, with honest single-sensor degradation |

## Tests

```bash
python -m pytest tests/ -q
```

48 tests, no network and no fixtures. They cover the index math, SAR dB
handling, the sieve, the full tier truth table, coverage honesty, and drone
ingest against real GeoTIFFs written to disk.

## Notes and limits

- **Relative orbit is matched** across Sentinel-1 dates. Backscatter depends on
  viewing geometry, so comparing across tracks measures the geometry, not the
  ground. If no shared orbit exists in the window, the run says so instead of
  pairing anyway.
- **Planetary Computer SAS tokens expire in about a day.** Assets are signed
  immediately before each read; the pixels are cached, the credential is not.
- **`eo:cloud_cover` does not exist on Sentinel-1 items.** Any scene predicate
  copied from an optical pipeline rejects 100% of radar candidates.
- **Never set `CPL_VSIL_CURL_ALLOWED_EXTENSIONS`** — Sentinel-1 RTC assets are
  `.tiff`, and a `.tif` allowlist breaks every read silently.
- Drone-vs-satellite is not supported: the resolution mismatch would force the
  comparison down to 10 m and needs affine/GCP warping this does not do.
- CCTV is not implemented. The detection core takes arrays and returns masks,
  so a frame-differencing source can plug into it.

## Data sources

| Source | Endpoint | Auth |
|---|---|---|
| Sentinel-2 L2A | Element84 Earth Search | none |
| Sentinel-1 RTC | Microsoft Planetary Computer | free unauthenticated signing |
| Basemap imagery | Esri World Imagery | none |

The map opens on satellite imagery, because a street map gives nothing to check
a detection against. Streets remain available from the layer control, along
with a place-names overlay.
