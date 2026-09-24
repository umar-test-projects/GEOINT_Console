# Audit: GEOINT Console (`geo/`)

Date: 2026-09-24. Scope: every Python module, the API, the generated web pages,
the tests, configuration and repository layout.

## How it was checked

- Read every backend module and the page scripts, looking for correctness,
  security and robustness problems.
- Ran the test suite. `satellite-mvp` is not in this repository, so it ran
  against a small stub of the three functions geo imports from it
  (`sign_asset_url`, `phase_correlation_shift`, and the `features.py` marker).
  Before the changes: **194 passed, 2 failed** (both Windows-path cases, which
  fail on Linux). After: **204 passed, 0 failed**.
- Every fix below has a regression test. Each of those tests was run against
  the original code and failed there.

## Fixed in this change

| # | Severity | Area | Problem | Fix |
|---|----------|------|---------|-----|
| 1 | High | `series_run.py` | Multi-date runs read radar at the date optical picked for the change by reusing the optical **list index** as a radar index. If either sensor missed a window (a cloudy month is enough), the index pointed at a different date. Radar evidence, the confirmed/optical tier split and the change class were then taken from the wrong moment. | Translate the index through the window (step) it came from. If radar has no scene in that window, fall back to radar's own dating. |
| 2 | High | `series_run.py` | The radar series took the newest descending pass in each window, whatever its relative orbit. Most places are seen by more than one track, so a series routinely mixed viewing geometries. That produced false radar change and a "matched across dates" orbit label that wasn't true. The two-date path (`matched_pair`) already refuses to mix orbits. | Search every window first, then keep the one relative orbit that covers the most windows (ties go to the earliest) and load only passes on it. |
| 3 | High | `api.py` | **DNS rebinding.** With no `GEO_PASSWORD`, the only check was that the client address is loopback. A web page can point its own domain at 127.0.0.1, and the browser then connects from loopback. That page could read every run, export and query, and start runs. | Without a password, the `Host` header must be `localhost`, `127.0.0.1` or `[::1]`. |
| 4 | Medium | `api.py` | **Cross-site request forgery.** FastAPI parses a body with no `Content-Type` as JSON, so a `no-cors` fetch from any site the user visits could queue detection, drone and geocoding jobs. | `POST`/`PUT`/`PATCH`/`DELETE` require `application/json`, which a browser sends cross-site only after a CORS preflight, and this server never grants one. This works behind a reverse proxy that rewrites `Host`, where an Origin-vs-Host comparison would fail. |
| 5 | Medium | `sources/drone.py` | After alignment, `np.roll` wrapped the rows and columns it shifted out back onto the opposite edge. Those strips were compared as real ground and reported as change. | The wrapped strips are marked unobserved. |
| 6 | Medium | `sources/drone.py` | Any 4th band was treated as NIR. **RGBA**, the most common orthophoto export, had its alpha band used as NIR, so the "NDVI" values were meaningless. | Bands tagged alpha are not counted. An RGBA flight uses VARI, as RGB does. |
| 7 | Medium | `sources/drone.py` | Validity was `isfinite` only. The warp fills ground outside a flight with zeros, and nodata or transparent padding is zero too. All of it counted as observed, so footprint edges became change. | Validity now comes from a warp-added alpha band, or from the source's own alpha mask. |
| 8 | Low | `api.py` | Dates were checked only against a pattern, so `2023-02-30` and ranges whose end is before the start were queued and failed minutes later. | Checked as calendar dates and ordered windows at request time (422), for `/api/detect` and `/api/smart`. `/api/scenes` dates and `max_cloud` are now bounded too. |
| 9 | Low | `api.py` | The drone-path boundary depended on the host OS. `C:/…` and `..\` were refused on Windows but read as file names on Linux. Still confined, but the tests failed and the behaviour differed. | Paths are refused by Windows rules on every OS. |
| 10 | Low | `api.py` | Chips were sent with `Cache-Control: public`, which allows a shared cache to store content that is behind a password. | Now `private`. |
| 11 | Low | `web/timeline.html` (via `build_web.py`) | The scene date and orbit went into HTML attributes unescaped. Every other page escapes upstream values. | Escaped. |
| 12 | Hygiene | repo | 35 compiled `__pycache__` files were committed and there was no `.gitignore`. The API tests wrote into the real `runs/` folder. | Removed the `.pyc` files and added `.gitignore`. The test client now uses a temporary job store. |

## Open findings (not changed)

These need a decision from the maintainer, or are minor enough to note only.

1. **External dependency with no version.** `satellite-mvp` is imported at
   startup (`bridge.install()`) and isn't in the repo, so the suite can't
   run anywhere it is missing, and there is no CI. `satellite_mvp.lock`
   detects drift but can't restore it. Suggestion: vendor the three imported
   functions, or publish `satellite-mvp` as a package, then add CI.
2. **Machine-specific paths are tracked.** `settings.toml` points at
   `C:\Users\abdul\Downloads\...` and `start_geo.bat` at
   `C:\ms2env\Scripts\python.exe`. `GEO_SATELLITE_MVP` overrides the first.
   The launcher could use `python` on `PATH` or a venv relative to the repo.
3. **`README.md` links `docs/NOTES.md`**, which doesn't exist.
4. **`runs/` holds three committed sample runs.** New runs show up as
   untracked files. Either ignore `geo/runs/` or move the samples to a
   fixtures folder.
5. **`min_persist` isn't checked against `steps`.** Values of `steps - 1`
   or more can never be met, so the run silently reports only "latest"
   change. It's worth rejecting or clamping, but the default (1 with
   `steps=2`) would need a decision first.
6. **Region means use `nan_to_num`.** A region partly outside radar coverage
   at its dated observation averages those pixels as 0 dB. That pulls
   `sar_delta_db` toward zero and can change the class. `ndimage.mean` over
   finite pixels only would avoid it.
7. **Connectivity mismatch.** `rasterio.features.sieve` uses 4-connectivity
   while labelling uses 8. Diagonal-only patches are sieved as separate
   pieces but then counted as one region.
8. **Unbounded in-process maps.** `cache._locks` keeps one lock per cache key
   forever. `api._EMBEDDINGS` is changed from worker threads without a
   lock. Both are small in practice.
9. **Drone flights in different CRSs.** `common_grid` takes the finer of the
   two `res` values even when one flight is in degrees and the other in
   metres.
10. **`/api/chip` with a polarization the scene lacks** raises `KeyError` and
    returns 502. It should return 404.
11. **Single shared credential.** Everyone who knows `GEO_PASSWORD` sees every
    run and query (`/api/jobs`). This is documented and fits a single-user
    tool, but anyone exposing the server to a team should know it.

## Reproducing the test run

```bash
cd geo
pip install -r requirements.txt   # torch/open_clip optional
GEO_SATELLITE_MVP=/path/to/satellite-mvp python -m pytest -q
```
