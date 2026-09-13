"""Scaffold the multi-page front end.

Six pages share one head, one nav and one design system. Writing them by hand
would mean six copies of the shell, and six places for it to drift; this keeps
the shell in one place and each page owns only its own content.

The visual design is the cartographic light theme handed off from the UI
mockups export: web/shared.css, the Compare and Report pages and the Results
region drawer. Every semantic token kept its name, so the older pages retheme
without markup changes. The page structure and the accessibility rules it
enforces -- visible labels, 44px targets, focus rings, 4.5:1 contrast,
prefers-reduced-motion -- predate it and carried over unchanged.

Run:  python build_web.py
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
WEB.mkdir(exist_ok=True)

# Lucide-style stroke icons. The checklist is explicit: no emoji as icons.
ICONS = {
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8"/><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "map": '<path d="m9 4-6 2v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "drone": '<rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 9 5 5M15 9l4-4M9 15l-4 4M15 15l4 4"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "columns": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 4v16"/>',
    "file": '<path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 17h4"/>',
}

PAGES = [
    ("index.html",    "Ask",      "search",  "Ask"),
    ("explore.html",  "Explore",  "map",     "Explore"),
    ("results.html",  "Results",  "layers",  "Results"),
    ("compare.html",  "Compare",  "columns", "Compare"),
    ("timeline.html", "Timeline", "clock",   "Timeline"),
    ("report.html",   "Report",   "file",    "Report"),
    ("drone.html",    "Drone",    "drone",   "Drone"),
    ("about.html",    "About",    "info",    "About"),
]


def icon(name: str) -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{ICONS[name]}</svg>')


def shell(title: str, body: str, page_script: str = "") -> str:
    """One head + one nav for every page."""
    links = "".join(
        f'    <a class="nav__link" href="{f}" data-page="{f}">{icon(ic)}<span>{label}</span></a>\n'
        for f, label, ic, _ in PAGES
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} &middot; geo</title>
<meta name="description" content="Sentinel-1 and Sentinel-2 change detection over any area of Earth.">
<link rel="stylesheet" href="shared.css">
</head>
<body>
<div class="shell">
<nav class="nav" aria-label="Primary">
  <a class="nav__brand" href="index.html">{icon('globe')}<span>geo</span></a>
  <div class="nav__links">
{links}  </div>
  <div class="nav__job" id="navjob" aria-live="polite"></div>
</nav>
{body}
</div>
<script src="shared.js"></script>
{page_script}
</body>
</html>
"""


SHARED_JS = r"""/* geo - shared behaviour across pages.
 *
 * State lives in the URL (?job=<id>), not in memory. That is what makes every
 * page deep-linkable and shareable, and it is why moving between pages keeps
 * the current run instead of losing it.
 */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const TIERS = {
  confirmed: ["Confirmed (S2 + S1 agree)", "var(--tier-confirmed)"],
  optical:   ["Optical only",              "var(--tier-optical)"],
  sar_only:  ["Radar only",                "var(--tier-sar-only)"],
  sar_gap:   ["Radar under cloud",         "var(--tier-sar-gap)"],
};

/* Change-type colours come from the --type-* tokens in shared.css, resolved
 * to concrete values once. Leaflet and the Compare overlay write colours into
 * SVG presentation attributes, where var() does not resolve -- a token
 * reference there would draw nothing. Resolving here keeps the stylesheet the
 * single source of truth. */
const CLASS_COLOURS = (() => {
  const tokens = {
    construction: "--type-construction", clearance: "--type-clearance",
    vegetation_loss: "--type-vegetation-loss", vegetation_growth: "--type-vegetation-growth",
    flooding: "--type-flooding", water_recession: "--type-water-recession",
    road_development: "--type-road", surface_change: "--type-surface",
    structural_change: "--type-structural", other: "--type-other",
  };
  const css = getComputedStyle(document.documentElement);
  const out = {};
  Object.keys(tokens).forEach((k) => {
    out[k] = css.getPropertyValue(tokens[k]).trim() || "#5C5A4E";
  });
  return out;
})();

/* ----------------------------------------------------------- job state */

function currentJob() {
  return new URLSearchParams(location.search).get("job");
}

function setJob(id, options) {
  const replace = options && options.replace;
  const url = new URL(location.href);
  url.searchParams.set("job", id);
  // pushState, not replace: the back button must keep working.
  history[replace ? "replaceState" : "pushState"]({ job: id }, "", url);
  renderJobBadge(id);
}

function linkWithJob(href) {
  const id = currentJob();
  return id ? href + "?job=" + encodeURIComponent(id) : href;
}

function renderJobBadge(id) {
  const el = $("navjob");
  if (!el) return;
  el.innerHTML = id
    ? '<span class="dot"></span><span>run ' + esc(id.slice(0, 8)) + "</span>"
    : '<span class="dot dot--idle"></span><span>no run yet</span>';
}

/* --------------------------------------------------------------- api - */

async function api(path, options) {
  const r = await fetch(path, options);
  let body;
  try { body = await r.json(); } catch (e) { body = {}; }
  if (!r.ok) {
    // Validation errors carry a list of {msg} objects rather than a string.
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    throw new Error(detail || (r.status + " " + r.statusText));
  }
  return body;
}

async function postJSON(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function loadResult(id) {
  return api("/api/jobs/" + encodeURIComponent(id));
}

/** Poll a job, reporting elapsed seconds, until it settles. */
async function pollJob(id, onTick) {
  const t0 = Date.now();
  for (;;) {
    const d = await api("/api/jobs/" + encodeURIComponent(id) + "?include_features=false");
    const secs = Math.round((Date.now() - t0) / 1000);
    if (d.status === "done" || d.status === "failed") {
      d.secs = secs;
      return d;
    }
    if (onTick) onTick(secs, d.status);
    await new Promise((r) => setTimeout(r, 1500));
  }
}

/* ------------------------------------------------------------- utils - */

const fmtHa = (v) => (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1));

function bboxKm(bbox) {
  const w = bbox[0], s = bbox[1], e = bbox[2], n = bbox[3];
  return ((e - w) * 111).toFixed(1) + " x " + ((n - s) * 111).toFixed(1) + " km";
}

/** A page that needs a completed run but has none. */
function needRun(container, what) {
  container.innerHTML =
    '<div class="card"><p class="card__title">Nothing to show yet</p>' +
    "<p>" + esc(what) + " appears here once a detection has run.</p>" +
    '<a class="btn btn--primary btn--auto" href="index.html">Start with a question</a>' +
    '<p class="help" style="margin-top:var(--space-3)">Or set an area manually on ' +
    '<a href="explore.html">Explore</a>.</p></div>';
}

/* --------------------------------------------------------------- init */

(function init() {
  const here = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".nav__link").forEach((a) => {
    const target = a.dataset.page;
    if (target === here) a.setAttribute("aria-current", "page");
    // Carry the active run across navigation.
    a.href = linkWithJob(target);
  });
  renderJobBadge(currentJob());
  window.addEventListener("popstate", () => renderJobBadge(currentJob()));
})();
"""


ASK = """
<main class="container">
  <section class="card reveal" style="text-align:center;padding:var(--space-6) var(--space-4)">
    <h1>Detect change anywhere on Earth</h1>
    <p style="color:var(--color-muted-foreground);max-width:62ch;margin:0 auto var(--space-5)">
      Sentinel&#8209;1 radar and Sentinel&#8209;2 optical, fused so cloud does not become a
      blind spot. Free public data, no API key.
    </p>

    <div style="max-width:620px;margin:0 auto;text-align:left">
      <label for="ask">What do you want to find, and where?</label>
      <div class="field-join">
        <input id="ask" placeholder="new construction near the gomti river"
               autocomplete="off" aria-describedby="askhelp">
        <button class="btn btn--primary" id="go">Find and detect</button>
      </div>
      <p class="help" id="askhelp">
        The place is located, the area and spectral index are chosen for you, then
        results are ranked by what you asked for.
      </p>
      <div id="out" aria-live="polite"></div>
    </div>
  </section>

  <section class="grid grid--3">
    <div class="card reveal">
      <p class="card__title">Cloud is not no-change</p>
      <p class="stat-value stat-value--lg">71.8%</p>
      <p class="help">of one Amazon area was unusable in Sentinel&#8209;2 even though the
      scenes were reported at 4% and 16% cloud. Radar covered all of it.</p>
    </div>
    <div class="card reveal">
      <p class="card__title">Most difference is not change</p>
      <p class="stat-value stat-value--lg">63%</p>
      <p class="help">of threshold-crossing pixels did not persist across a
      6&#8209;date series. A two-date comparison reports those as real.</p>
    </div>
    <div class="card reveal">
      <p class="card__title">Radar separates the causes</p>
      <p class="stat-value stat-value--lg">&plusmn;dB</p>
      <p class="help">Clearing and construction look identical to optical. Backscatter
      rises for one and falls for the other.</p>
    </div>
  </section>

  <section class="card">
    <p class="card__title">How it works</p>
    <ol style="color:var(--color-muted-foreground);padding-left:1.1rem;margin:0">
      <li>The place name is resolved to coordinates and a workable area.</li>
      <li>Scenes are found for several dates and read straight from cloud-optimised
          rasters &mdash; about 1% of each scene.</li>
      <li>Change is kept only where later observations keep agreeing.</li>
      <li>Each region is classified from its combined optical and radar signature.</li>
    </ol>
  </section>
</main>
"""

ASK_JS = r"""<script>
const go = $("go"), out = $("out"), ask = $("ask");
// A run owns #out for its whole lifetime. Without this guard a second
// invocation (Enter, or a click while polling) rewrites #out and the first
// run's progress node is detached under it.
let busy = false;

async function run() {
  if (busy) return;
  const q = ask.value.trim();
  if (!q) { ask.focus(); return; }
  busy = true;
  go.disabled = true;
  out.innerHTML = '<div class="note">Locating &ldquo;' + esc(q) + '&rdquo;...</div>';
  try {
    const steps = 4;
    const d = await postJSON("/api/smart", {
      query: q, start: "2023-01-01", end: "2024-12-31", steps: steps,
    });
    const f = d.located;
    out.innerHTML =
      '<div class="note"><b>' + esc(f.place.kind) + "</b> &middot; " +
      esc(f.place.name.split(",").slice(0, 3).join(",")) + "<br>Looking for <b>" +
      esc(f.what || "any change") + "</b> using " + esc(f.index_hint.toUpperCase()) +
      " over " + bboxKm(f.bbox) + ".</div>" +
      f.notes.map((n) => '<div class="warn">' + esc(n) + "</div>").join("") +
      (f.alternatives.length
        ? '<div class="note">That name also matches: ' +
          f.alternatives.map((a) => esc(a.name.split(",").slice(0, 2).join(","))).join("; ") +
          ". Refine the question if this is the wrong one.</div>"
        : "");

    // Keep a direct handle on the progress node. Looking it up by id after an
    // await assumes the DOM still holds it, which is what broke before.
    const prog = document.createElement("div");
    prog.className = "note";
    prog.textContent = "Detecting...";
    out.appendChild(prog);

    setJob(d.job_id, { replace: true });
    const done = await pollJob(d.job_id, (s) => {
      prog.textContent =
        "Detecting across " + steps + " dates... " + s + "s (a cold run takes a minute or two)";
    });

    if (done.status === "failed") {
      prog.className = "err";
      prog.textContent = done.error;
    } else {
      prog.className = "note";
      prog.innerHTML = "Found <b>" + done.feature_count + "</b> change regions." +
        '<a class="btn btn--primary btn--auto" style="margin-top:var(--space-2)" href="' +
        linkWithJob("results.html") + '">See what changed</a>';
    }
  } catch (e) {
    out.innerHTML = '<div class="err">' + esc(e.message) + "</div>";
  } finally {
    busy = false;
    go.disabled = false;
  }
}
go.onclick = run;
ask.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
</script>
"""

EXPLORE = """
<main class="container container--wide" style="display:flex;flex:1;min-height:0">
  <aside class="explore__side" id="side" hidden style="width:340px;flex:none;overflow-y:auto;padding:var(--space-4);
                border-right:1px solid var(--color-border)">
    <div class="card">
      <p class="card__title">Area</p>
      <label for="bbox">Bounding box (west, south, east, north)</label>
      <input id="bbox" value="-60.05,-3.15,-59.95,-3.05">
      <button class="btn btn--ghost" id="draw" style="margin-top:var(--space-2)">
        Draw on the map
      </button>
      <p class="help">Click draw, then drag across the map.</p>
    </div>

    <div class="card">
      <p class="card__title">Dates</p>
      <label for="s0">Range</label>
      <div class="row">
        <input type="date" id="s0" value="2023-01-01" aria-label="Range start">
        <input type="date" id="s1d" value="2024-12-31" aria-label="Range end">
      </div>
      <label for="steps">Timestamps: <b id="stepsLbl">4</b></label>
      <input type="range" id="steps" min="2" max="8" step="1" value="4">
      <p class="help">More dates reject more transient difference, and cost more time.</p>
    </div>

    <div class="card">
      <p class="card__title">Detection</p>
      <label for="index">Optical index</label>
      <select id="index">
        <option value="ndvi">NDVI &mdash; vegetation</option>
        <option value="ndwi">NDWI &mdash; water</option>
        <option value="ndbi">NDBI &mdash; built-up</option>
      </select>
      <label for="ot">Optical threshold: <b id="otLbl">0.20</b></label>
      <input type="range" id="ot" min="0.05" max="0.6" step="0.05" value="0.2">
      <label for="st">Radar threshold: <b id="stLbl">3.0</b> dB</label>
      <input type="range" id="st" min="1" max="8" step="0.5" value="3">
      <label for="sv">Minimum patch: <b id="svLbl">50</b> px</label>
      <input type="range" id="sv" min="0" max="300" step="10" value="50">
      <p class="help">Minimum patch suppresses speckle. Raw radar gave 57,368 polygons;
      50 px gave 120.</p>
    </div>

    <button class="btn btn--primary" id="detect">Detect change</button>
    <div id="status" aria-live="polite"></div>
  </aside>

  <div class="explore__map" style="flex:1;position:relative;min-width:0">
    <div id="map" style="position:absolute;inset:0"></div>
  </div>
</main>
"""

EXPLORE_JS = r"""<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H" crossorigin="anonymous">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH" crossorigin="anonymous"></script>
<script>
const map = L.map("map", { zoomControl: false }).setView([-3.1, -60.0], 11);

// The controls panel starts hidden so the map has the whole screen. This
// button sits above the zoom control and shows or hides it.
function toggleSide() {
  const side = $("side"), open = side.hidden;
  side.hidden = !open;
  const b = $("sideToggle");
  b.setAttribute("aria-expanded", open ? "true" : "false");
  b.setAttribute("aria-label", open ? "Hide controls" : "Show controls");
  // Leaflet measures its container once; tell it the width changed.
  map.invalidateSize();
}
const PanelToggle = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const wrap = L.DomUtil.create("div", "map-tools");
    const bar = L.DomUtil.create("div", "leaflet-bar", wrap);
    const b = L.DomUtil.create("button", "panel-toggle__btn", bar);
    b.type = "button";
    b.id = "sideToggle";
    b.setAttribute("aria-controls", "side");
    b.setAttribute("aria-expanded", "false");
    b.setAttribute("aria-label", "Show controls");
    b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';
    // Find and detect beside the menu button: the place is located, the map
    // moves there, and a detection runs with the dates set in the panel.
    const find = L.DomUtil.create("div", "map-search-wrap", wrap);
    const form = L.DomUtil.create("form", "map-search", find);
    form.setAttribute("role", "search");
    form.innerHTML =
      '<input id="mapSearch" type="search" placeholder="Find and detect, e.g. construction near lucknow" ' +
      'autocomplete="off" aria-label="Find a place and detect change">' +
      '<button type="submit" aria-label="Find and detect">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/>' +
      '<path d="m20 20-3.5-3.5"/></svg></button>';
    const msg = L.DomUtil.create("div", "map-search__msg", find);
    msg.setAttribute("aria-live", "polite");
    let searching = false;
    L.DomEvent.on(form, "submit", async (e) => {
      L.DomEvent.preventDefault(e);
      const q = form.querySelector("input").value.trim();
      if (!q || searching) return;
      searching = true;
      const go = form.querySelector("button");
      go.disabled = $("detect").disabled = true;
      msg.textContent = "Locating...";
      try {
        const steps = Number($("steps").value);
        const d = await postJSON("/api/smart", {
          query: q, start: $("s0").value, end: $("s1d").value, steps: steps,
        });
        const f = d.located;
        $("bbox").value = f.bbox.map((v) => v.toFixed(4)).join(",");
        $("index").value = f.index_hint;
        showAoi();
        const place = esc(f.place.name.split(",").slice(0, 3).join(",")) +
          (f.alternatives.length ? " (" + f.alternatives.length + " other matches)" : "");
        setJob(d.job_id, { replace: true });
        const done = await pollJob(d.job_id, (s) => {
          msg.innerHTML = place + "<br>Detecting " + esc(f.what || "any change") + " in " +
            esc(f.index_hint.toUpperCase()) + " across " + steps + " dates... " + s + "s";
        });
        if (done.status === "failed") {
          msg.innerHTML = place + "<br>" + esc(done.error);
        } else {
          drawResult((await loadResult(d.job_id)).result);
          msg.innerHTML = place + "<br>" + done.feature_count + " change regions. " +
            '<a href="' + linkWithJob("results.html") + '">See what changed &rarr;</a>';
        }
      } catch (err) {
        msg.textContent = err.message;
      } finally {
        searching = false;
        go.disabled = $("detect").disabled = false;
      }
    });
    // Enter runs the search through the handler above. Cancelling the keydown
    // means Enter never falls back to a native form submit, which would reload
    // the page. Skipped while an input method is still composing characters.
    L.DomEvent.on(form.querySelector("input"), "keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      L.DomEvent.preventDefault(e);
      form.requestSubmit();
    });
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);
    L.DomEvent.on(b, "click", toggleSide);
    return wrap;
  },
});
new PanelToggle().addTo(map);
L.control.zoom({ position: "topleft" }).addTo(map);
const sat = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Tiles &copy; Esri, Maxar" }).addTo(map);
const streets = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  { maxZoom: 19, attribution: "&copy; OpenStreetMap" });
const labels = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19 }).addTo(map);
L.control.layers({ Satellite: sat, Streets: streets }, { "Place names": labels }).addTo(map);

let aoi = null, result = null, drawing = false, start = null, rubber = null;

const bboxVal = () => {
  const p = $("bbox").value.split(",").map(Number);
  return p.length === 4 && !p.some(isNaN) ? p : null;
};
function showAoi(fit) {
  const b = bboxVal(); if (!b) return;
  if (aoi) map.removeLayer(aoi);
  aoi = L.rectangle([[b[1], b[0]], [b[3], b[2]]],
    { color: "#fff", weight: 1.5, dashArray: "5 4", fill: false }).addTo(map);
  if (fit !== false) map.fitBounds(aoi.getBounds(), { padding: [28, 28] });
}
$("bbox").addEventListener("change", () => showAoi());

$("draw").onclick = () => {
  drawing = !drawing;
  $("draw").textContent = drawing ? "Drag on the map..." : "Draw on the map";
  map.dragging[drawing ? "disable" : "enable"]();
};
map.on("mousedown", (e) => { if (drawing) start = e.latlng; });
map.on("mousemove", (e) => {
  if (!drawing || !start) return;
  if (rubber) map.removeLayer(rubber);
  rubber = L.rectangle(L.latLngBounds(start, e.latlng), { color: "#FBFAF7", weight: 2 }).addTo(map);
});
map.on("mouseup", (e) => {
  if (!drawing || !start) return;
  const b = L.latLngBounds(start, e.latlng);
  $("bbox").value = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((v) => v.toFixed(4)).join(",");
  if (rubber) { map.removeLayer(rubber); rubber = null; }
  start = null; drawing = false;
  $("draw").textContent = "Draw on the map";
  map.dragging.enable();
  showAoi(false);
});

const sync = (id, lbl, fmt) => {
  const f = () => { $(lbl).textContent = fmt($(id).value); };
  $(id).addEventListener("input", f); f();
};
sync("ot", "otLbl", (v) => Number(v).toFixed(2));
sync("st", "stLbl", (v) => Number(v).toFixed(1));
sync("sv", "svLbl", (v) => v);
sync("steps", "stepsLbl", (v) => v);

let byId = {};
function drawResult(fc) {
  if (result) map.removeLayer(result);
  byId = {};
  let n = 0;
  result = L.geoJSON(fc, {
    style: (f) => ({
      color: CLASS_COLOURS[f.properties.change_type] || CLASS_COLOURS.other,
      weight: 2,
      fillOpacity: f.properties.direction === "loss" ? 0.45 : 0.18,
      dashArray: f.properties.direction === "gain" ? "4 3" : null,
    }),
    onEachFeature: (f, l) => {
      const q = f.properties;
      const rid = q.id != null ? q.id : n;
      n += 1;
      byId[rid] = l;
      const job = currentJob();
      l.bindPopup("<b>" + esc(q.change_label || "Change") + "</b><br>" + q.area_ha + " ha" +
        (q.change_date ? " &middot; detected " + q.change_date : "") +
        "<br><i>" + esc(q.evidence || "") + "</i>" +
        (job ? '<br><a href="results.html?job=' + encodeURIComponent(job) + "&region=" + rid +
          '">Region detail &rarr;</a>' : ""));
    },
  }).addTo(map);
}

$("detect").onclick = async () => {
  const b = bboxVal();
  if (!b) { $("status").innerHTML = '<div class="err">Bounding box needs four numbers.</div>'; return; }
  $("detect").disabled = true;
  $("status").innerHTML = '<div class="note">Submitting...</div>';
  try {
    const d = await postJSON("/api/detect", {
      bbox: b, start: $("s0").value, end: $("s1d").value, steps: Number($("steps").value),
      index: $("index").value, optical_threshold: Number($("ot").value),
      sar_threshold_db: Number($("st").value), sieve_size: Number($("sv").value),
    });
    setJob(d.job_id, { replace: true });
    const done = await pollJob(d.job_id, (s) => {
      $("status").innerHTML = '<div class="note">Reading imagery and detecting... ' + s + "s</div>";
    });
    if (done.status === "failed") {
      $("status").innerHTML = '<div class="err">' + esc(done.error) + "</div>";
    } else {
      const full = await loadResult(d.job_id);
      drawResult(full.result);
      $("status").innerHTML = '<div class="note">' + done.feature_count + " regions in " +
        done.secs + 's.<a class="btn btn--primary btn--auto" style="margin-top:var(--space-2)" href="' +
        linkWithJob("results.html") + '">See what changed</a></div>';
    }
  } catch (e) {
    $("status").innerHTML = '<div class="err">' + esc(e.message) + "</div>";
  } finally {
    $("detect").disabled = false;
  }
};

// Arriving with ?job=... - show that run rather than a blank map; with
// &region=<n>, the link from a Results drawer, open that region as well.
(async () => {
  const id = currentJob();
  if (!id) { showAoi(); return; }
  try {
    const d = await loadResult(id);
    if (d.status !== "done") { showAoi(); return; }
    const p = d.result.properties;
    $("bbox").value = p.bbox.map((v) => v.toFixed(4)).join(",");
    if (p.index) $("index").value = p.index;
    showAoi();
    drawResult(d.result);
    $("status").innerHTML = '<div class="note">Showing run ' + esc(id.slice(0, 8)) +
      " &middot; " + d.result.features.length + " regions.</div>";
    const want = new URLSearchParams(location.search).get("region");
    const layer = want !== null ? byId[Number(want)] : null;
    if (layer) {
      map.fitBounds(layer.getBounds(), { padding: [80, 80], maxZoom: 16 });
      layer.openPopup();
    }
  } catch (e) { showAoi(); }
})();
</script>
"""

RESULTS = """
<main class="results">
<div class="results__map"><div id="rmap"></div></div>
<div class="results__side">
<div class="results__summary" id="summary">
  <h1>What changed</h1>
  <div id="body"><div class="card skeleton">Loading run...</div></div>
</div>

<aside class="drawer" id="drawer" hidden aria-labelledby="drTitle">
  <div class="drawer__head">
    <p class="label-cap" id="drTier"></p>
    <h2 class="drawer__title" id="drTitle"></h2>
    <p class="mono help" id="drMeta"></p>
    <button class="drawer__close" id="drClose" aria-label="Close region detail">&times;</button>
  </div>
  <div class="drawer__tabs" role="tablist" aria-label="Region detail">
    <button class="drawer__tab" id="tabEv" role="tab" aria-selected="true"
            aria-controls="panelEv">evidence</button>
    <button class="drawer__tab" id="tabPr" role="tab" aria-selected="false"
            aria-controls="panelPr">provenance</button>
  </div>
  <div class="drawer__body">
    <div id="panelEv" role="tabpanel" aria-labelledby="tabEv"></div>
    <div id="panelPr" role="tabpanel" aria-labelledby="tabPr" hidden></div>
  </div>
  <div class="drawer__foot">
    <a class="btn btn--primary" id="drMap">Open on Explore</a>
    <button class="btn" id="drCopy" aria-live="polite">Copy region GeoJSON</button>
  </div>
</aside>
</div>
</main>
"""

RESULTS_DRAWER_JS = r"""<script>
/* Region drawer. One surface, opened from the region and search rows on this
   page and from ?region=<n> - which is how the Explore map popup links here.
   Everything it shows travels with the feature in the GeoJSON export. */
(function () {
  let opener = null;

  function ringsOf(f) {
    return f.geometry.type === "Polygon" ? f.geometry.coordinates : f.geometry.coordinates.flat();
  }

  /* A window about 2.2 km across, the scale semantic search crops at. Square
     in degrees because chips render in EPSG:4326, which keeps the outline
     overlay in exact register with the imagery. */
  function cropBox(f) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    ringsOf(f).forEach((ring) => ring.forEach((c) => {
      x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]);
      y0 = Math.min(y0, c[1]); y1 = Math.max(y1, c[1]);
    }));
    const half = Math.max(0.0101, (x1 - x0) * 0.8, (y1 - y0) * 0.8);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return [cx - half, cy - half, cx + half, cy + half];
  }

  function outline(f, box, colour) {
    const sx = (lon) => ((lon - box[0]) / (box[2] - box[0]) * 100).toFixed(2);
    const sy = (lat) => ((box[3] - lat) / (box[3] - box[1]) * 100).toFixed(2);
    const d = ringsOf(f).map((ring) =>
      "M" + ring.map((c) => sx(c[0]) + " " + sy(c[1])).join("L") + "Z").join(" ");
    return '<svg class="crop__outline" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + d + '" fill="none" stroke="' + colour + '" stroke-width="2" ' +
      'vector-effect="non-scaling-stroke"' +
      (f.properties.direction === "gain" ? ' stroke-dasharray="4 3"' : "") + "/></svg>";
  }

  function crop(f, box, colour, scene, sensor, date, when) {
    const kind = sensor === "s2" ? "optical" : "radar";
    if (!scene) {
      return '<figure><div class="crop__ph"></div><figcaption>no ' + kind + " view</figcaption></figure>";
    }
    const src = "/api/chip?bbox=" + box.map((v) => v.toFixed(5)).join(",") +
      "&scene_id=" + encodeURIComponent(scene) + "&sensor=" + sensor + "&px=448";
    return '<figure><div class="crop__frame"><img src="' + src + '" alt="' +
      (sensor === "s2" ? "Sentinel-2" : "Sentinel-1") + " " + when + ", " + esc(date || "") + '">' +
      outline(f, box, colour) + "</div><figcaption>" + (sensor === "s2" ? "S2 " : "S1 ") +
      esc(date || "") + " &middot; " + when + "</figcaption></figure>";
  }

  /* Signed bar: zero is the centre line, so a fall reads left of it. */
  function bar(label, val, unit, scale, colour) {
    const pct = Math.min(50, Math.abs(val) / scale * 50);
    return "<span>" + label + '</span><div class="sig__bar"><i class="sig__fill sig__fill--' +
      (val < 0 ? "neg" : "pos") + '" style="width:' + pct.toFixed(1) + "%;background:" +
      colour + '"></i></div><span class="sig__val">' + (val > 0 ? "+" : "") + val +
      (unit || "") + "</span>";
  }

  /* One cell per observation from the sensor that dated the region: ink from
     the detection date through the later dates it held at, blank otherwise. */
  function strip(q, ctx) {
    const sensor = q.dated_by === "radar" ? "S1" : "S2";
    const dates = (ctx.timeline || []).filter((t) => t.sensor === sensor).map((t) => t.date);
    const k = dates.indexOf(q.change_date);
    if (k < 1 || q.confirmed_by == null) return "";
    return '<div class="persist">' + dates.map((d, i) => {
      const held = i >= k && i <= k + q.confirmed_by;
      return '<div class="persist__cell' + (held ? " persist__cell--held" : "") + '"><i></i><b>' +
        (i === 0 ? "base" : esc(d.slice(0, 7))) + "</b></div>";
    }).join("") + "</div>";
  }

  function showTab(evidence) {
    $("tabEv").setAttribute("aria-selected", evidence ? "true" : "false");
    $("tabPr").setAttribute("aria-selected", evidence ? "false" : "true");
    $("panelEv").hidden = !evidence;
    $("panelPr").hidden = evidence;
  }

  function setRegionParam(rid) {
    const url = new URL(location.href);
    if (rid === null) url.searchParams.delete("region");
    else url.searchParams.set("region", rid);
    history.replaceState(history.state, "", url);
  }

  window.openRegion = function (f, ctx, trigger) {
    const q = f.properties;
    const rid = q.id != null ? q.id : ctx.region;
    const colour = CLASS_COLOURS[q.change_type] || CLASS_COLOURS.other;
    opener = trigger || null;

    $("drTier").textContent = "region " + rid + " \u00b7 " +
      (TIERS[q.tier] ? TIERS[q.tier][0] : (q.tier || "evidence tier unknown"));
    $("drTitle").textContent = q.change_label || "Change";
    $("drMeta").textContent = q.area_ha + " ha \u00b7 first seen " +
      (q.change_date || "undated") + " \u00b7 confidence " +
      (q.class_confidence != null ? q.class_confidence : "\u2014") +
      (q.confirmed_by ? " \u00b7 held at " + q.confirmed_by + " later date" +
        (q.confirmed_by === 1 ? "" : "s") : "");

    const box = cropBox(f);
    // On a radar-under-cloud region optical was blind by definition, so its
    // scenes show only cloud or the footprint edge; the radar pair is the
    // evidence there.
    const optical = q.tier !== "sar_gap" && (q.scene_before || q.scene_after);
    const crops = optical
      ? crop(f, box, colour, q.scene_before, "s2", q.date_before, "before") +
        crop(f, box, colour, q.scene_after, "s2", q.date_after, "after")
      : crop(f, box, colour, q.sar_scene_before, "s1", q.sar_date_before, "before") +
        crop(f, box, colour, q.sar_scene_after, "s1", q.sar_date_after, "after");

    const bars =
      (q.ndvi_delta != null ? bar("NDVI", q.ndvi_delta, "", 1, "var(--type-clearance)") : "") +
      (q.ndwi_delta != null ? bar("NDWI", q.ndwi_delta, "", 1, "var(--type-flooding)") : "") +
      (q.ndbi_delta != null ? bar("NDBI", q.ndbi_delta, "", 1, "var(--type-construction)") : "") +
      (q.sar_delta_db != null ? bar("SAR " + (ctx.polarization || "").toUpperCase(),
        q.sar_delta_db, " dB", 8, "var(--type-road)") : "");

    $("panelEv").innerHTML =
      '<p class="label-cap">' + (optical ? "Optical" : "Radar") +
        " context &mdash; about 2.2 km around the region</p>" +
      '<div class="crops">' + crops + "</div>" +
      (q.tier === "sar_gap" ? '<p class="help">Optical was cloud-covered or outside its ' +
        "footprint here, so the radar scenes are shown.</p>" : "") +
      '<p class="help">The outline is this region. Crops render on first open, so a cold ' +
        "one takes a few seconds.</p>" +
      '<p class="label-cap" style="margin-top:22px">Signature measured inside this polygon</p>' +
      (bars ? '<div class="sig">' + bars + "</div>"
            : '<p class="help">This run predates per-region signatures. Re-run to record them.</p>') +
      (q.evidence ? '<div class="note">' + esc(q.evidence) + "</div>" : "") +
      (q.persistence
        ? '<p class="label-cap" style="margin-top:22px">Persistence across the series</p>' +
          strip(q, ctx) + "<p>" + esc(q.persistence) +
          (q.confirmed_by ? " &mdash; held at " + q.confirmed_by + " later observation" +
            (q.confirmed_by === 1 ? "" : "s") : "") +
          (q.dated_by ? ", dated by " + esc(q.dated_by) : "") + ".</p>" +
          '<p class="help">Every date is compared against the <b>first</b>, never a rolling ' +
          "reference: against a rolling one a persistent change reverts to no-change as soon " +
          "as the new state becomes the baseline.</p>"
        : "");

    const sceneRow = (label, scene, date) => scene
      ? "<tr><td>" + label + (date ? '<div class="help mono">' + esc(date) + "</div>" : "") +
        '</td><td class="num" style="word-break:break-all">' + esc(scene) + "</td></tr>"
      : "";
    const scenes =
      sceneRow("Optical before", q.scene_before, q.date_before) +
      sceneRow("Optical after", q.scene_after, q.date_after) +
      sceneRow("Radar before", q.sar_scene_before, q.sar_date_before) +
      sceneRow("Radar after", q.sar_scene_after, q.sar_date_after) +
      (q.relative_orbit != null
        ? '<tr><td>Relative orbit</td><td class="num">' + q.relative_orbit +
          " &mdash; matched across dates</td></tr>" : "");

    $("panelPr").innerHTML =
      '<p class="label-cap">Scenes this detection was made from</p>' +
      (scenes ? "<table><tbody>" + scenes + "</tbody></table>"
              : '<p class="help">This run predates per-region scene IDs. Re-run to record them.</p>') +
      '<p class="label-cap" style="margin-top:22px">How the number was produced</p><table><tbody>' +
        '<tr><td>Index / optical threshold</td><td class="num">' +
          esc((ctx.index || "").toUpperCase()) +
          (ctx.optical_threshold != null ? " " + ctx.optical_threshold : "") + "</td></tr>" +
        '<tr><td>Radar threshold</td><td class="num">' +
          (ctx.sar_threshold_db != null ? ctx.sar_threshold_db + " dB" : "\u2014") +
          " &middot; 5&times;5 multilook</td></tr>" +
        '<tr><td>Minimum patch</td><td class="num">' +
          (ctx.sieve_size != null ? ctx.sieve_size + " px" : "\u2014") +
          " &middot; area from the sieved mask</td></tr>" +
        (q.dated_by ? '<tr><td>Dated by</td><td class="num">' + esc(q.dated_by) +
          " &middot; modal pixel date</td></tr>" : "") +
        '<tr><td>Run</td><td class="num">' + esc(ctx.jobId) + "</td></tr>" +
      "</tbody></table>" +
      '<div class="note">Every field here travels with the polygon in the GeoJSON export, so ' +
      "the claim can be re-checked against the same public scenes by anyone who disputes it.</div>";

    $("drMap").href = "explore.html?job=" + encodeURIComponent(ctx.jobId) + "&region=" + rid;
    const copy = $("drCopy");
    copy.textContent = "Copy region GeoJSON";
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(f));
        copy.textContent = "Copied";
      } catch (e) {
        copy.textContent = "Clipboard blocked";
      }
    };

    renderJobBadge(ctx.jobId);
    $("navjob").insertAdjacentHTML("beforeend",
      "<span>&middot; region " + rid + " of " + ctx.count + "</span>");
    lastJob = ctx.jobId;
    if (window.onRegion) onRegion(ctx.region);

    showTab(true);
    $("summary").hidden = true;
    $("drawer").hidden = false;
    setRegionParam(rid);
    $("tabEv").focus();
  };

  let lastJob = null;
  function close() {
    $("drawer").hidden = true;
    $("summary").hidden = false;
    if (lastJob) renderJobBadge(lastJob);
    if (window.onRegion) onRegion(null);
    setRegionParam(null);
    document.querySelectorAll("tr.is-open").forEach((t) => t.classList.remove("is-open"));
    if (opener && document.contains(opener)) opener.focus();
  }

  $("tabEv").onclick = () => showTab(true);
  $("tabPr").onclick = () => showTab(false);
  // Arrow keys move between the two tabs, per the tablist pattern.
  [$("tabEv"), $("tabPr")].forEach((t) => t.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const toEvidence = $("tabEv").getAttribute("aria-selected") !== "true";
    showTab(toEvidence);
    (toEvidence ? $("tabEv") : $("tabPr")).focus();
  }));
  $("drClose").onclick = close;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("drawer").hidden) close();
  });
})();
</script>
"""

RESULTS_JS = r"""<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H" crossorigin="anonymous">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH" crossorigin="anonymous"></script>
<script>
(async () => {
  const host = $("body"), id = currentJob();
  if (!id) { needRun(host, "Detected change"); return; }
  let d;
  try { d = await loadResult(id); }
  catch (e) { host.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; return; }
  if (d.status !== "done") {
    host.innerHTML = '<div class="card"><p>This run is <b>' + esc(d.status) + "</b>.</p></div>";
    return;
  }

  const r = d.result, p = r.properties, s = p.stats;
  const types = s.change_types || [];
  const gap = s.optical_gap_pct == null ? 0 : s.optical_gap_pct;
  const th = p.thresholds || {};
  const ctx = (i) => ({
    bbox: p.bbox, jobId: id, index: p.index, region: i, count: r.features.length,
    timeline: p.timeline, polarization: p.polarization,
    optical_threshold: th.optical, sar_threshold_db: th.sar_db, sieve_size: th.sieve_px,
  });
  const colourOf = (t) => CLASS_COLOURS[t] || CLASS_COLOURS.other;

  // The run on the map: the area of interest and every region. Clicking a
  // region opens it in the side panel, which outlines it here in turn.
  const map = L.map("rmap");
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Tiles &copy; Esri, Maxar" }).addTo(map);
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  const bb = p.bbox;
  const aoi = L.rectangle([[bb[1], bb[0]], [bb[3], bb[2]]],
    { color: "#FBFAF7", weight: 1.5, fill: false, interactive: false }).addTo(map);
  L.marker([bb[3], bb[0]], { interactive: false, keyboard: false, icon: L.divIcon({
    className: "mapnote mapnote--aoi", html: "Area of interest", iconSize: null }) }).addTo(map);
  map.fitBounds(aoi.getBounds(), { padding: [30, 30] });
  const layerOf = {};
  let n = 0;
  const regions = L.geoJSON(r.features, {
    style: (f) => ({
      color: colourOf(f.properties.change_type), weight: 1.5,
      fillOpacity: f.properties.direction === "loss" ? 0.45 : 0.25,
      dashArray: f.properties.direction === "gain" ? "4 3" : null,
    }),
    onEachFeature: (f, l) => {
      const i = n++;
      layerOf[i] = l;
      l.on("click", () => openRegion(f, ctx(i)));
    },
  }).addTo(map);
  let picked = null;
  window.onRegion = (i) => {
    if (picked) { regions.resetStyle(picked); picked.unbindTooltip(); }
    picked = i === null ? null : layerOf[i];
    if (!picked) return;
    const q = picked.feature.properties;
    picked.setStyle({ color: "#1A1A17", weight: 3, fillColor: colourOf(q.change_type), fillOpacity: 0.5 });
    picked.bringToFront();
    picked.bindTooltip("Region " + (q.id != null ? q.id : i) + '<br><span style="color:' +
      colourOf(q.change_type) + '">' + esc(q.change_label || "Change") + " &middot; " +
      q.area_ha + " ha</span>",
      { permanent: true, direction: "auto", offset: [0, 0], className: "region-callout" }).openTooltip();
    if (!map.getBounds().contains(picked.getBounds())) {
      map.fitBounds(picked.getBounds(), { padding: [80, 80], maxZoom: 16 });
    }
  };
  const detailBtn = (i, label) =>
    '<button class="btn btn--auto rowbtn" data-region="' + i + '" aria-label="Detail for ' +
    esc(label) + ", region " + i + '">Detail</button>';

  const typeRows = types.length ? types.map((c) =>
    '<tr><td><span class="swatch" style="background:' + colourOf(c.change_type) + '"></span>' +
    esc(c.label) + '<div class="help">' + c.count + " region" + (c.count === 1 ? "" : "s") +
    " &middot; mean confidence " + c.confidence + "</div></td>" +
    '<td class="num">' + fmtHa(c.area_ha) + " ha</td></tr>").join("")
    : '<tr><td>No change classified</td><td class="num">&mdash;</td></tr>';

  const tierRows = Object.keys(TIERS).map((k) => {
    const ha = (s.hectares || {})[k] || 0;
    if (!(ha > 0)) return "";
    return '<tr><td><span class="swatch" style="background:' + TIERS[k][1] + '"></span>' +
      TIERS[k][0] + '</td><td class="num">' + fmtHa(ha) + " ha</td></tr>";
  }).join("");

  const REGION_ROWS = 25;
  const largest = r.features.map((f, i) => [f, i])
    .sort((a, b) => (b[0].properties.area_ha || 0) - (a[0].properties.area_ha || 0))
    .slice(0, REGION_ROWS);
  const regionRows = largest.map(([f, i]) => {
    const q = f.properties;
    return '<tr><td><span class="swatch" style="background:' + colourOf(q.change_type) + '"></span>' +
      esc(q.change_label || "Change") + '<div class="help">' +
      esc(TIERS[q.tier] ? TIERS[q.tier][0] : (q.tier || "")) + "</div></td>" +
      '<td class="mono">' + esc(q.change_date || "\u2014") + "</td>" +
      '<td class="num">' + fmtHa(q.area_ha || 0) + " ha</td>" +
      '<td class="num">' + (q.class_confidence != null ? q.class_confidence : "\u2014") + "</td>" +
      '<td class="num">' + detailBtn(i, q.change_label || "change") + "</td></tr>";
  }).join("");

  host.innerHTML =
    '<div class="grid grid--2">' +
      '<section class="card reveal"><p class="card__title">What changed</p>' +
        '<div class="scroll-x"><table><tbody>' + typeRows + "</tbody></table></div>" +
        '<p class="help">Clearing and construction are separated by radar: backscatter ' +
        "rises for structures and falls for cleared ground.</p></section>" +

      '<section class="card reveal"><p class="card__title">Coverage</p><table><tbody>' +
        '<tr><td>Sentinel-2 usable</td><td class="num">' + s.s2_usable_pct + "%</td></tr>" +
        '<tr><td>Sentinel-1 usable</td><td class="num">' + s.s1_usable_pct + "%</td></tr>" +
        '<tr><td>Optical gap (radar only)</td><td class="num stat-value">' + gap + "%</td></tr>" +
        '<tr><td>&mdash; cloud, shadow or snow</td><td class="num">' + s.cloud_gap_pct + "%</td></tr>" +
        '<tr><td>&mdash; outside the scene footprint</td><td class="num">' + s.never_imaged_pct + "%</td></tr>" +
        '<tr><td>Seen by neither</td><td class="num">' + s.not_observed_pct + "%</td></tr>" +
      "</tbody></table>" +
      (gap >= 1 ? '<div class="warn"><b>' + gap + "%</b> of this area was unusable in " +
        "Sentinel-2. Only radar could observe it &mdash; an optical-only system would " +
        "report that ground as <i>no data</i>, which in practice reads as <i>no change</i>.</div>" : "") +
      "</section></div>" +

    (s.persistence ?
      '<section class="card reveal"><p class="card__title">Persistence filter</p>' +
      '<div class="grid--butted">' +
        '<div><p class="stat-label">Confirmed by later dates</p><div class="stat-value stat-value--lg">' +
          fmtHa(s.persistence.persistent_px / 100) + " ha</div></div>" +
        '<div><p class="stat-label">Latest observation only</p><div class="stat-value stat-value--lg">' +
          fmtHa(s.persistence.latest_px / 100) + " ha</div></div>" +
        '<div><p class="stat-label">Rejected as transient</p><div class="stat-value stat-value--lg">' +
          fmtHa(s.persistence.transient_ha) + " ha</div></div>" +
      "</div>" +
      '<div class="warn">A two-date comparison would have reported that ' +
        fmtHa(s.persistence.transient_ha) + " ha as real change. Across the series it " +
        "reverted, so it is cloud edge, shadow or seasonality.</div></section>" : "") +

    (r.features.length ?
      '<section class="card"><p class="card__title">Regions &mdash; largest first</p>' +
        '<div class="scroll-x"><table><thead><tr><th>Region</th><th>First seen</th>' +
        '<th class="num">Area</th><th class="num">Confidence</th><th class="num">Open</th>' +
        "</tr></thead><tbody>" + regionRows + "</tbody></table></div>" +
        (r.features.length > REGION_ROWS
          ? '<p class="help">The ' + REGION_ROWS + " largest of " + r.features.length +
            " regions. Every region is in the GeoJSON export.</p>" : "") +
      "</section>" : "") +

    '<div class="grid grid--2">' +
      '<section class="card"><p class="card__title">Evidence tier</p><table><tbody>' +
        (tierRows || "<tr><td>None</td><td></td></tr>") + "</tbody></table>" +
        '<p class="help">Radar is a coverage and corroboration layer, never a gate: ' +
        "requiring both sensors to agree would discard most real detections.</p></section>" +

      '<section class="card"><p class="card__title">Run</p><table><tbody>' +
        '<tr><td>Area</td><td class="num">' + bboxKm(p.bbox) + "</td></tr>" +
        '<tr><td>Index</td><td class="num">' + esc((p.index || "").toUpperCase()) + "</td></tr>" +
        '<tr><td>Regions</td><td class="num">' + r.features.length + "</td></tr>" +
        (p.observations ? '<tr><td>Observations</td><td class="num">' + p.observations.s2 +
          " optical, " + p.observations.s1 + " radar</td></tr>" : "") +
      "</tbody></table>" +
      '<div class="row" style="margin-top:var(--space-3)">' +
        '<a class="btn btn--auto" href="/api/export/' + encodeURIComponent(id) +
          '.geojson">Download GeoJSON</a>' +
        '<a class="btn btn--auto" href="' + linkWithJob("report.html") + '">Printable report</a>' +
      "</div>" +
      '<p class="help">The export opens in QGIS and Google Earth.</p></section></div>' +

    '<section class="card"><p class="card__title">Search these changes</p>' +
      '<label for="sq">Describe what you are looking for</label>' +
      '<div class="field-join"><input id="sq" placeholder="buildings and urban development" ' +
        'autocomplete="off"><button class="btn btn--primary" id="sgo">Search</button></div>' +
      '<div id="snote" aria-live="polite"></div><div id="sres" class="scroll-x"></div>' +
      '<p class="help">RemoteCLIP ranks each region by how its imagery looks, independently ' +
      "of the change type assigned to it. Scores order results; they are not probabilities.</p></section>" +

    (s.warnings || []).map((w) => '<div class="warn">' + esc(w) + "</div>").join("");

  host.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-region]");
    if (!b) return;
    const i = Number(b.dataset.region), f = r.features[i];
    if (!f) return;
    document.querySelectorAll("tr.is-open").forEach((t) => t.classList.remove("is-open"));
    const row = b.closest("tr");
    if (row) row.classList.add("is-open");
    openRegion(f, ctx(i), b);
  });

  let searching = false;
  async function search() {
    if (searching) return;
    const q = $("sq").value.trim(); if (!q) return;
    searching = true;
    $("sgo").disabled = true;
    $("snote").innerHTML = '<div class="note">Encoding regions... the first search loads the model (~25s).</div>';
    $("sres").innerHTML = "";
    try {
      const res = await postJSON("/api/semantic", { job_id: id, query: q, top_k: 12 });
      if (!res.matches.length) {
        $("snote").innerHTML = '<div class="note">' + esc(res.note || "No matches") + "</div>";
        return;
      }
      $("snote").innerHTML = '<div class="note">' + res.count + " best matches.</div>";
      $("sres").innerHTML =
        '<table><thead><tr><th>Region</th><th class="num">Score</th><th class="num">Open</th>' +
        "</tr></thead><tbody>" +
        res.matches.map((m) => {
          const q2 = m.properties;
          return '<tr><td><span class="swatch" style="background:' + colourOf(q2.change_type) +
            '"></span>#' + m.rank + " " + esc(q2.change_label) + '<div class="help">' +
            q2.area_ha + " ha" + (q2.change_date ? " &middot; " + q2.change_date : "") +
            '</div></td><td class="num">' + m.score.toFixed(3) + "</td>" +
            '<td class="num">' + detailBtn(m.feature_index, q2.change_label || "change") +
            "</td></tr>";
        }).join("") + "</tbody></table>";
    } catch (e) {
      $("snote").innerHTML = '<div class="err">' + esc(e.message) + "</div>";
    } finally {
      searching = false;
      $("sgo").disabled = false;
    }
  }
  $("sgo").onclick = search;
  $("sq").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });

  // Deep link: from the Explore map popup, or a shared URL.
  const want = new URLSearchParams(location.search).get("region");
  if (want !== null && r.features[Number(want)]) {
    openRegion(r.features[Number(want)], ctx(Number(want)));
  }
})();
</script>
"""

TIMELINE = """
<main class="container">
  <h1>Timeline</h1>
  <p style="color:var(--color-muted-foreground);max-width:74ch">
    One column per time window. Optical and radar are acquired on different days, so each
    scene is keyed to the window it came from &mdash; that is what makes the rows line up.
  </p>
  <div id="body"><div class="card skeleton">Loading run...</div></div>
</main>

"""

TIMELINE_JS = r"""<script>
(async () => {
  const host = $("body"), id = currentJob();
  if (!id) { needRun(host, "The observation timeline"); return; }
  let d;
  try { d = await loadResult(id); }
  catch (e) { host.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; return; }
  if (d.status !== "done") {
    host.innerHTML = '<div class="card"><p>This run is <b>' + esc(d.status) + "</b>.</p></div>";
    return;
  }

  const r = d.result, p = r.properties;
  const tl = (p.timeline || []).filter((t) => t.id && t.step !== undefined);
  if (tl.length < 2) {
    host.innerHTML = '<div class="card"><p>This run used a single pair of dates, so there ' +
      'is no timeline to step through. Run a series from <a href="' +
      linkWithJob("explore.html") + '">Explore</a>.</p></div>';
    return;
  }

  const bbox = p.bbox;
  const steps = [];
  tl.forEach((t) => { if (steps.indexOf(t.step) === -1) steps.push(t.step); });
  steps.sort((a, b) => a - b);

  const table = [["S2", "Sentinel-2"], ["S1", "Sentinel-1"]].map((row) => {
    const sensor = row[0], label = row[1];
    const cells = steps.map((step) => {
      const stop = tl.find((t) => t.step === step && t.sensor === sensor);
      if (!stop) {
        return '<td><div class="chip chip--empty"><div class="chip__img"></div>' +
          '<div class="help">no scene</div></div></td>';
      }
      const detail = stop.cloud_cover !== undefined
        ? stop.cloud_cover + "% cloud" : "orbit " + stop.relative_orbit;
      const url = "/api/chip?bbox=" + bbox.join(",") + "&scene_id=" +
        encodeURIComponent(stop.id) + "&sensor=" + sensor.toLowerCase() + "&px=128";
      return '<td><button class="chip" data-id="' + esc(stop.id) + '" data-sensor="' + sensor +
        '" data-date="' + stop.date + '" data-detail="' + detail +
        '" aria-label="View ' + sensor + " " + stop.date + ", " + detail + '">' +
        '<img class="chip__img" loading="lazy" src="' + url + '" alt="">' +
        '<div class="chip__date mono">' + stop.date + "</div>" +
        '<div class="help">' + detail + "</div></button></td>";
    }).join("");
    return '<tr><th scope="row">' + label + "</th>" + cells + "</tr>";
  }).join("");

  host.innerHTML =
    '<section class="card">' +
      '<div style="display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap">' +
      '<p class="card__title" style="margin:0;flex:1" id="tlTitle">' + steps.length +
      ' time windows</p><button class="btn btn--ghost btn--auto" id="all">Show all dates</button></div>' +
      // The enlarged scene sits above the strip so it is visible the moment it
      // is opened, rather than below the fold.
      '<div class="preview" id="preview" hidden>' +
        '<div class="preview__head"><h2 id="pvTitle"></h2>' +
        '<span class="help" id="pvMeta"></span>' +
        '<span style="flex:1"></span>' +
        '<a class="btn btn--auto" id="pvOpen" target="_blank" rel="noopener">Open image</a></div>' +
        '<div class="preview__figure">' +
          '<div class="preview__loading" id="pvLoading">Loading full resolution...</div>' +
          '<img class="preview__img" id="pvImg" alt=""></div>' +
      "</div>" +
      '<div class="scroll-x"><table class="chips">' + table + "</table></div>" +
      '<p class="help" id="tlNote">Select a scene to enlarge it and see the change detected up to that date.</p>' +
    "</section>" +
    '<section class="card"><p class="card__title">Detections up to the selected date</p>' +
    '<div id="list" class="scroll-x"></div></section>';

  const feats = r.features;
  function show(dateStr, label) {
    const keep = dateStr
      ? feats.filter((f) => !f.properties.change_date || f.properties.change_date <= dateStr)
      : feats;
    $("tlNote").textContent = dateStr
      ? keep.length + " of " + feats.length + " detections by " + dateStr
      : "all " + feats.length + " detections";
    $("tlTitle").textContent = label || steps.length + " time windows";

    const byType = {};
    keep.forEach((f) => {
      const t = f.properties.change_type || "other";
      if (!byType[t]) byType[t] = { n: 0, ha: 0, label: f.properties.change_label || t };
      byType[t].n += 1;
      byType[t].ha += f.properties.area_ha || 0;
    });
    const rowsHtml = Object.keys(byType)
      .sort((a, b) => byType[b].ha - byType[a].ha)
      .map((t) => '<tr><td><span class="swatch" style="background:' +
        (CLASS_COLOURS[t] || CLASS_COLOURS.other) + '"></span>' + esc(byType[t].label) +
        '</td><td class="num">' + byType[t].n + '</td><td class="num">' +
        fmtHa(byType[t].ha) + " ha</td></tr>").join("");
    $("list").innerHTML = rowsHtml
      ? '<table><thead><tr><th>Type</th><th class="num">Regions</th><th class="num">Area</th></tr></thead><tbody>' +
        rowsHtml + "</tbody></table>"
      : '<p class="help">Nothing had been detected by this date.</p>';
  }

  // Selecting a scene does two things at once: it enlarges that scene in
  // place, and filters the detections to what had appeared by its date.
  let selected = null;

  /** Run a layout change without the strip moving under the pointer.
   *  Inserting a tall element above the chips would otherwise push the one
   *  just clicked off-screen. */
  function keepStripStill(mutate) {
    const strip = document.querySelector(".chips");
    const before = strip ? strip.getBoundingClientRect().top : 0;
    mutate();
    if (!strip) return;
    const after = strip.getBoundingClientRect().top;
    // Two-argument form: the options object with behavior:"instant" is
    // rejected by some engines, and an invalid value makes the whole call a
    // no-op rather than scrolling instantly.
    window.scrollBy(0, after - before);
  }

  function collapse() {
    selected = null;
    keepStripStill(() => { $("preview").hidden = true; });
    $("pvImg").removeAttribute("src");
    document.querySelectorAll(".chip").forEach((x) => {
      x.classList.remove("chip--on");
      x.setAttribute("aria-expanded", "false");
    });
    show(null, null);
  }

  function expand(cell) {
    const sensor = cell.dataset.sensor, date = cell.dataset.date;
    selected = cell;
    document.querySelectorAll(".chip").forEach((x) => {
      const on = x === cell;
      x.classList.toggle("chip--on", on);
      x.setAttribute("aria-expanded", on ? "true" : "false");
    });

    const full = "/api/chip?bbox=" + bbox.join(",") + "&scene_id=" +
      encodeURIComponent(cell.dataset.id) + "&sensor=" + sensor.toLowerCase() + "&px=1024";
    $("pvTitle").textContent = (sensor === "S2" ? "Sentinel-2" : "Sentinel-1") + " " + date;
    $("pvMeta").textContent = cell.dataset.detail + " · " + cell.dataset.id;
    $("pvOpen").href = full;

    const img = $("pvImg");
    img.alt = sensor + " scene of the area of interest, " + date;
    // Reserve the box and show progress: a cold full-resolution render is a
    // real wait, not an instant swap, and the page must not jump when it lands.
    img.removeAttribute("src");
    $("pvLoading").hidden = false;
    $("pvLoading").textContent = "Loading full resolution...";
    img.onload = () => { $("pvLoading").hidden = true; };
    img.onerror = () => { $("pvLoading").textContent = "That scene could not be rendered."; };
    img.src = full;

    keepStripStill(() => { $("preview").hidden = false; });
    show(date, sensor + " " + date);
  }

  document.querySelectorAll(".chip[data-id]").forEach((c) => {
    c.setAttribute("aria-expanded", "false");
    c.setAttribute("aria-controls", "preview");
    // Clicking the open scene again closes it.
    c.onclick = () => (selected === c ? collapse() : expand(c));
  });
  $("all").onclick = collapse;
  show(null, null);
})();
</script>
"""

DRONE = """
<main class="container">
  <h1>Drone flights</h1>
  <p style="color:var(--color-muted-foreground);max-width:72ch">
    Compare two flights of the same site at the finer of their two resolutions.
    Flights are read from the server's drone folder (<code>drone_data</code>, set in
    <code>settings.toml</code>) &mdash; pushing multi-gigabyte orthophotos through a
    form would gain nothing. Paths outside that folder are refused.
  </p>

  <section class="card" style="max-width:720px">
    <p class="card__title">Flights</p>
    <label for="before">Earlier flight (path inside the drone folder)</label>
    <input id="before" placeholder="site-a\\2024-03\\ortho.tif" autocomplete="off">
    <label for="after">Later flight (path inside the drone folder)</label>
    <input id="after" placeholder="site-a\\2025-03\\ortho.tif" autocomplete="off">
    <div class="row" style="margin-top:var(--space-3)">
      <button class="btn" id="inspect">Inspect</button>
      <button class="btn btn--primary" id="compare">Compare</button>
    </div>
    <div id="out" aria-live="polite"></div>
    <p class="help">Both flights must be georeferenced. Most consumer drones are RGB-only,
    so NDVI is not computable from them &mdash; those fall back to VARI, a weaker
    visible-band proxy, and the result says so rather than returning something
    NDVI-shaped.</p>
  </section>
</main>
"""

DRONE_JS = r"""<script>
const paths = () => ({ before: $("before").value.trim(), after: $("after").value.trim() });
const bothGiven = (p) => p.before && p.after;

$("inspect").onclick = async () => {
  const p = paths();
  if (!bothGiven(p)) { $("out").innerHTML = '<div class="err">Both paths are needed.</div>'; return; }
  $("out").innerHTML = '<div class="note">Reading headers...</div>';
  try {
    const d = await postJSON("/api/drone/inspect", p);
    $("out").innerHTML =
      '<table><thead><tr><th>Flight</th><th>Bands</th><th class="num">Resolution</th>' +
      "<th>Index</th></tr></thead><tbody>" +
      ["before", "after"].map((k) => "<tr><td>" + k + "</td><td>" + d[k].bands +
        '</td><td class="num">' + d[k].resolution_m + " m</td><td>" +
        esc(d[k].index.toUpperCase()) + "</td></tr>").join("") +
      '</tbody></table><div class="note">' + esc(d.before.index_note) + "</div>";
  } catch (e) { $("out").innerHTML = '<div class="err">' + esc(e.message) + "</div>"; }
};

$("compare").onclick = async () => {
  const p = paths();
  if (!bothGiven(p)) { $("out").innerHTML = '<div class="err">Both paths are needed.</div>'; return; }
  $("compare").disabled = true;
  $("out").innerHTML = '<div class="note">Comparing...</div>';
  try {
    const d = await postJSON("/api/drone/detect", p);
    const done = await pollJob(d.job_id, (s) => {
      $("out").innerHTML = '<div class="note">Comparing flights... ' + s + "s</div>";
    });
    if (done.status === "failed") {
      $("out").innerHTML = '<div class="err">' + esc(done.error) + "</div>";
      return;
    }
    const full = await loadResult(d.job_id);
    const q = full.result.properties;
    $("out").innerHTML = "<table><tbody>" +
      '<tr><td>Index used</td><td class="num">' + esc(q.index.toUpperCase()) + "</td></tr>" +
      '<tr><td>Native resolution</td><td class="num">' + q.resolution_m + " m</td></tr>" +
      '<tr><td>Estimated shift</td><td class="num">' + q.shift_px.join(", ") + " px</td></tr>" +
      '<tr><td>Regions</td><td class="num">' + full.result.features.length + "</td></tr>" +
      '<tr><td>Loss / gain</td><td class="num">' + q.stats.hectares.loss + " / " +
        q.stats.hectares.gain + " ha</td></tr></tbody></table>" +
      (q.stats.warnings || []).map((w) => '<div class="warn">' + esc(w) + "</div>").join("") +
      '<a class="btn btn--auto" style="margin-top:var(--space-3)" href="/api/export/' +
      encodeURIComponent(d.job_id) + '.geojson">Download GeoJSON</a>';
  } catch (e) {
    $("out").innerHTML = '<div class="err">' + esc(e.message) + "</div>";
  } finally { $("compare").disabled = false; }
};
</script>
"""

ABOUT = """
<main class="container">
  <h1>How it works</h1>

  <div class="grid grid--2">
    <section class="card">
      <p class="card__title">Why two sensors</p>
      <p>Optical imagery is unusable under cloud, and scene-level cloud percentages
      badly understate the problem. On one Amazon pair whose scenes were reported at
      4% and 16% cloud, per-pixel masking left only <b>28.2%</b> usable. Radar covered
      <b>100%</b>.</p>
      <p>That gap &mdash; <b>71.8%</b>, about 8,900 ha &mdash; is ground an optical-only
      system reports as <i>no data</i>, which in practice gets read as <i>no change</i>.</p>
    </section>

    <section class="card">
      <p class="card__title">Radar is not a second opinion</p>
      <p>On a cloud-free area, pixel-wise agreement between vegetation-index change and
      radar log-ratio change was only <b>3.6%</b>. That is physics, not a bug: one
      measures greenness, the other measures roughness, structure and moisture.</p>
      <p>So radar is a <b>coverage layer and a corroboration tier, never a gate</b>.
      Requiring both sensors to agree would discard about 96% of real detections.</p>
    </section>
  </div>

  <section class="card">
    <p class="card__title">Evidence tiers</p>
    <div class="scroll-x"><table>
      <thead><tr><th>Tier</th><th>Meaning</th></tr></thead>
      <tbody>
        <tr><td><span class="swatch" style="background:var(--tier-confirmed)"></span>Confirmed</td>
            <td>Both sensors observed this ground and both report change</td></tr>
        <tr><td><span class="swatch" style="background:var(--tier-optical)"></span>Optical only</td>
            <td>Spectral change; radar reports no structural change here</td></tr>
        <tr><td><span class="swatch" style="background:var(--tier-sar-only)"></span>Radar only</td>
            <td>Structural or moisture change with no spectral signature</td></tr>
        <tr><td><span class="swatch" style="background:var(--tier-sar-gap)"></span>Radar under cloud</td>
            <td>Change under cloud, invisible to optical entirely</td></tr>
      </tbody>
    </table></div>
    <p class="help">Ground seen by neither sensor is kept separate from unchanged ground.
    Unobserved and unchanged must never collapse into each other.</p>
  </section>

  <section class="card">
    <p class="card__title">Telling causes apart</p>
    <p>Clearing and construction both strip vegetation, so both collapse the vegetation
    index identically &mdash; optical alone cannot separate them. Radar can, because they
    leave opposite roughness signatures: structures act as corner reflectors and
    backscatter <b>rises</b>, while cleared ground goes smooth and backscatter <b>falls</b>.</p>
    <p>When radar is unavailable or ambiguous, the result says <i>vegetation loss, cause
    unresolved</i> rather than guessing the more likely-sounding label.</p>
  </section>

  <section class="card">
    <p class="card__title">Honest limits</p>
    <ul style="color:var(--color-muted-foreground);padding-left:1.1rem">
      <li>Classification thresholds are physically motivated but not calibrated against
      labelled ground truth. Treat confidences as ordering, not probability.</li>
      <li>Semantic search scores cluster narrowly and are not calibrated either. On an
      area with little variety, results fall back toward the base rate.</li>
      <li>Asking for construction on an area auto-centred on a river works against
      itself &mdash; the floodplain dominates. A town or district name gives a better
      population.</li>
      <li>Drone comparison is flight-against-flight. Drone-versus-satellite is not
      supported: the resolution mismatch would force the comparison down to 10 m.</li>
      <li>CCTV is not implemented.</li>
    </ul>
  </section>

  <section class="card">
    <p class="card__title">Data sources</p>
    <div class="scroll-x"><table>
      <thead><tr><th>Source</th><th>Provider</th><th>Authentication</th></tr></thead>
      <tbody>
        <tr><td>Sentinel-2 L2A</td><td>Element84 Earth Search</td><td>none</td></tr>
        <tr><td>Sentinel-1 RTC</td><td>Microsoft Planetary Computer</td><td>free, unauthenticated signing</td></tr>
        <tr><td>Place names</td><td>OpenStreetMap Nominatim</td><td>none</td></tr>
        <tr><td>Basemap</td><td>Esri World Imagery</td><td>none</td></tr>
      </tbody>
    </table></div>
  </section>
</main>
"""

COMPARE = """
<main class="container container--wide cmp" style="display:flex;flex:1;min-height:0">
  <div class="swipe" id="swipe">
    <img class="swipe__fill" id="afterImg" alt="" hidden>
    <div class="swipe__pane" id="pane" style="width:50%">
      <img class="swipe__fill" id="beforeImg" alt="" hidden>
    </div>
    <p class="swipe__caption swipe__caption--before" id="beforeCap">before</p>
    <p class="swipe__caption swipe__caption--after" id="afterCap">after</p>
    <div class="swipe__handle" id="handle" style="left:calc(50% - 1px)" aria-hidden="true">&#8596;</div>
    <svg class="mapstage__graticule" id="overlay" aria-hidden="true"></svg>
    <p class="swipe__loading" id="cmpLoading">Loading scenes...</p>
    <div class="swipe__control" id="ctl">
      <label for="swipe-pos">
        <span class="label-cap">Swipe between the two observations</span>
        <span class="mono" id="posOut">50% before</span>
      </label>
      <input type="range" id="swipe-pos" min="0" max="100" step="1" value="50">
    </div>
  </div>

  <aside class="cmp__side">
    <p class="card__title">Compare</p>
    <label for="leftSel">Before &mdash; left of the handle</label>
    <select id="leftSel"></select>
    <label for="rightSel">After &mdash; right of the handle</label>
    <select id="rightSel"></select>
    <div class="warn" id="mixWarn" hidden>Optical against radar compares greenness against
      roughness. Their measured pixel agreement was 3.6% &mdash; expect the two sides to
      look different.</div>

    <hr class="rule">
    <p class="card__title">Layers</p>
    <label class="check"><input type="checkbox" id="lyPoly" checked>
      <span>Detection outlines</span><span class="legend__count" id="lyPolyN"></span></label>
    <label class="check"><input type="checkbox" id="lyAoi" checked>
      <span>AOI boundary</span></label>

    <hr class="rule">
    <p class="card__title">Legend &mdash; by type</p>
    <div class="legend" id="legend"></div>
    <p class="help" style="border-top:1px solid var(--color-rule);padding-top:12px">
      Solid outline = loss, dashed = gain.</p>
  </aside>
</main>
"""

COMPARE_JS = r"""<script>
(async () => {
  const main = document.querySelector("main"), id = currentJob();
  const fail = (html) => {
    main.className = "container";
    main.removeAttribute("style");
    main.innerHTML = html;
  };
  if (!id) { fail(""); needRun(main, "A before/after comparison"); return; }

  let d;
  try { d = await loadResult(id); }
  catch (e) { fail('<div class="err">' + esc(e.message) + "</div>"); return; }
  if (d.status !== "done") {
    fail('<div class="card"><p>This run is <b>' + esc(d.status) + "</b>.</p></div>");
    return;
  }

  const r = d.result, p = r.properties, b = p.bbox;
  // A series carries a timeline; a two-date run carries only its scenes.
  const stops = ((p.timeline && p.timeline.length) ? p.timeline : (p.scenes || []))
    .filter((t) => t.id);
  if (stops.length < 2) {
    fail('<div class="card"><p>This run has fewer than two scenes to compare.</p></div>');
    return;
  }

  const chip = (stop, px) => "/api/chip?bbox=" + b.join(",") + "&scene_id=" +
    encodeURIComponent(stop.id) + "&sensor=" + stop.sensor.toLowerCase() + "&px=" + px;
  const label = (stop) => (stop.sensor === "S2" ? "S2 true colour" : "S1 VV backscatter") +
    " \u2014 " + stop.date + (stop.cloud_cover !== undefined
      ? " \u00b7 " + stop.cloud_cover + "% cloud" : " \u00b7 orbit " + stop.relative_orbit);

  const left = $("leftSel"), right = $("rightSel");
  stops.forEach((s, i) => {
    [left, right].forEach((sel) => {
      const o = document.createElement("option");
      o.value = i; o.textContent = label(s); sel.appendChild(o);
    });
  });
  // Default to the first and last scene of one sensor, preferring optical, so
  // the page opens on a like-for-like comparison rather than optical vs radar.
  const indexed = stops.map((s, i) => [s, i]);
  const optical = indexed.filter((x) => x[0].sensor === "S2");
  const pool = optical.length >= 2 ? optical : indexed;
  left.value = pool[0][1];
  right.value = pool[pool.length - 1][1];

  const stage = $("swipe"), svg = $("overlay");
  let pending = 0;

  function paint(which) {
    const sel = which === "before" ? left : right;
    const stop = stops[Number(sel.value)];
    const img = $(which === "before" ? "beforeImg" : "afterImg");
    pending += 1;
    $("cmpLoading").textContent = "Loading scenes...";
    $("cmpLoading").hidden = false;
    img.hidden = true;
    img.onload = () => {
      img.hidden = false;
      pending = Math.max(0, pending - 1);
      if (!pending) $("cmpLoading").hidden = true;
      drawOverlay();
    };
    img.onerror = () => {
      pending = Math.max(0, pending - 1);
      $("cmpLoading").textContent = "A scene could not be rendered.";
    };
    img.alt = which + ": " + label(stop);
    img.src = chip(stop, 1024);
    const cap = $(which === "before" ? "beforeCap" : "afterCap");
    cap.textContent = cap.title = which + " \u00b7 " + label(stop);
    $("mixWarn").hidden = stops[Number(left.value)].sensor === stops[Number(right.value)].sensor;
  }

  /* The same box object-fit: contain gives the imagery, so outlines drawn in
     it stay in register with the scene at any stage size. */
  function fitted() {
    const W = stage.clientWidth, H = stage.clientHeight - $("ctl").offsetHeight;
    const img = $("afterImg");
    const aspect = img.naturalWidth && img.naturalHeight
      ? img.naturalWidth / img.naturalHeight
      : (b[2] - b[0]) / (b[3] - b[1]);
    let w = W, h = W / aspect;
    if (h > H) { h = H; w = H * aspect; }
    return { W: W, H: Math.max(H, 1), x: (W - w) / 2, y: (H - h) / 2, w: w, h: h };
  }

  function drawOverlay() {
    const R = fitted();
    const x = (lon) => (R.x + (lon - b[0]) / (b[2] - b[0]) * R.w).toFixed(1);
    const y = (lat) => (R.y + (b[3] - lat) / (b[3] - b[1]) * R.h).toFixed(1);
    let out = "";
    if ($("lyPoly").checked) {
      out += r.features.map((f) => {
        const rings = f.geometry.type === "Polygon" ? f.geometry.coordinates
          : f.geometry.coordinates.flat();
        const dAttr = rings.map((ring) =>
          "M" + ring.map((c) => x(c[0]) + " " + y(c[1])).join("L") + "Z").join(" ");
        return '<path d="' + dAttr + '" fill="none" stroke="' +
          (CLASS_COLOURS[f.properties.change_type] || CLASS_COLOURS.other) + '" stroke-width="2"' +
          (f.properties.direction === "gain" ? ' stroke-dasharray="5 3"' : "") + "/>";
      }).join("");
    }
    if ($("lyAoi").checked) {
      out += '<rect x="' + R.x.toFixed(1) + '" y="' + R.y.toFixed(1) + '" width="' +
        R.w.toFixed(1) + '" height="' + R.h.toFixed(1) +
        '" fill="none" stroke="#FBFAF7" stroke-width="1.5" stroke-dasharray="6 4"/>';
    }
    svg.setAttribute("viewBox", "0 0 " + R.W + " " + R.H);
    svg.innerHTML = out;
  }

  function layout() {
    stage.style.setProperty("--swipe-ctl", $("ctl").offsetHeight + "px");
    // The clipped pane holds a full-stage-width image, so both panes stay in
    // register while the clip moves.
    stage.style.setProperty("--swipe-stage-w", stage.clientWidth + "px");
    drawOverlay();
  }

  const pos = $("swipe-pos");
  function move() {
    $("pane").style.width = pos.value + "%";
    $("handle").style.left = "calc(" + pos.value + "% - 1px)";
    $("posOut").textContent = pos.value + "% before";
  }
  pos.addEventListener("input", move);
  // Drag the handle, or anywhere on the imagery, to move the swipe. The range
  // input stays as the keyboard-accessible control and follows along.
  function dragTo(e) {
    const rect = stage.getBoundingClientRect();
    pos.value = Math.round(Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)) * 100);
    move();
  }
  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || $("ctl").contains(e.target)) return;
    e.preventDefault();
    stage.setPointerCapture(e.pointerId);
    dragTo(e);
  });
  stage.addEventListener("pointermove", (e) => {
    if (stage.hasPointerCapture(e.pointerId)) dragTo(e);
  });

  const counts = {};
  r.features.forEach((f) => {
    const t = f.properties.change_type || "other";
    counts[t] = counts[t] || { n: 0, label: f.properties.change_label || t };
    counts[t].n += 1;
  });
  $("lyPolyN").textContent = r.features.length;
  $("legend").innerHTML = Object.keys(counts).map((t) =>
    '<div class="legend__row"><span class="legend__key" style="background:' +
    (CLASS_COLOURS[t] || CLASS_COLOURS.other) + '"></span><span>' + esc(counts[t].label) +
    '</span><span class="legend__count">' + counts[t].n + "</span></div>").join("");

  left.onchange = () => paint("before");
  right.onchange = () => paint("after");
  $("lyPoly").onchange = drawOverlay;
  $("lyAoi").onchange = drawOverlay;
  addEventListener("resize", layout);

  move();
  layout();
  paint("before");
  paint("after");
})();
</script>
"""

REPORT = """
<main class="container">
  <section class="sheet card card--flush">
    <div class="sheet__body" id="sheet">
      <div class="skeleton">Loading run...</div>
    </div>
  </section>
</main>
"""

REPORT_JS = r"""<script>
(async () => {
  const host = $("sheet"), id = currentJob();
  if (!id) { needRun(host, "A report"); return; }
  let d;
  try { d = await loadResult(id); }
  catch (e) { host.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; return; }
  if (d.status !== "done") {
    host.innerHTML = '<div class="card"><p>This run is <b>' + esc(d.status) + "</b>.</p></div>";
    return;
  }
  const r = d.result, p = r.properties, s = p.stats, th = p.thresholds || {};
  const types = s.change_types || [];
  const total = types.reduce((a, c) => a + c.area_ha, 0);
  const gap = s.optical_gap_pct == null ? 0 : s.optical_gap_pct;
  const loc = p.located && p.located.place;
  const place = loc && loc.name
    ? loc.name.split(",").slice(0, 3).join(",")
    : bboxKm(p.bbox) + " area";
  const obs = p.observations || {};

  host.innerHTML =
    '<div class="sheet__head">' +
      '<div style="flex:1"><p class="label-cap">Change detection report</p>' +
        "<h1>" + esc(place) + "</h1>" +
        (p.located ? '<p class="help">Asked: &ldquo;' + esc(p.located.query) + "&rdquo;</p>" : "") +
        '<p class="mono help">' + p.bbox.map((v) => v.toFixed(3)).join(", ") + " &middot; " +
        bboxKm(p.bbox) + " &middot; " + (obs.s2 || 0) + " optical and " + (obs.s1 || 0) +
        " radar observations</p></div>" +
      '<div class="sheet__figure"><p class="label-cap">Change detected</p>' +
        '<div class="stat-value stat-value--lg">' + fmtHa(total) + " ha</div>" +
        '<p class="help">across ' + r.features.length + " regions</p></div>" +
    "</div>" +

    '<div class="grid grid--2" style="margin-bottom:var(--space-5)">' +
      '<section><p class="label-cap">What changed</p><table><tbody>' +
        types.map((c) =>
          '<tr><td><span class="swatch" style="background:' +
          (CLASS_COLOURS[c.change_type] || CLASS_COLOURS.other) + '"></span>' + esc(c.label) +
          '<div class="help" style="margin-left:19px">' + c.count + " region" +
          (c.count === 1 ? "" : "s") + " &middot; confidence " + c.confidence +
          '</div></td><td class="num">' + fmtHa(c.area_ha) + " ha</td></tr>").join("") +
        '<tr class="total"><td>Total</td><td class="num">' + fmtHa(total) + " ha</td></tr>" +
      "</tbody></table></section>" +

      // Coverage as one ruled bar. Hatch is reserved for ground no sensor saw:
      // outside the optical footprint is still radar-observed, so it sits in
      // the radar segment, not the hatched one.
      '<section><p class="label-cap">What could be observed</p>' +
        '<div class="coverbar" role="img" aria-label="' + s.s2_usable_pct + "% optical, " + gap +
          "% radar only, " + s.not_observed_pct + '% seen by neither sensor">' +
          '<i style="width:' + s.s2_usable_pct + '%;background:var(--tier-optical)"></i>' +
          '<i style="width:' + gap + '%;background:var(--tier-sar-gap)"></i>' +
          '<i style="width:' + s.not_observed_pct + '%;background:var(--hatch-nodata)"></i>' +
        "</div>" +
        '<div class="legend" style="flex-direction:row;flex-wrap:wrap;gap:6px 18px;margin-top:8px">' +
          '<div class="legend__row"><span class="legend__key" style="background:var(--tier-optical)"></span><span>optical</span></div>' +
          '<div class="legend__row"><span class="legend__key" style="background:var(--tier-sar-gap)"></span><span>radar only</span></div>' +
          '<div class="legend__row"><span class="legend__key swatch--nodata"></span><span>seen by neither</span></div>' +
        "</div>" +
        '<table style="margin-top:10px"><tbody>' +
          '<tr><td>Sentinel-2 usable after per-pixel masking</td><td class="num">' + s.s2_usable_pct + "%</td></tr>" +
          '<tr><td>Sentinel-1 usable</td><td class="num">' + s.s1_usable_pct + "%</td></tr>" +
          '<tr><td>&mdash; cloud, shadow or snow</td><td class="num">' + s.cloud_gap_pct + "%</td></tr>" +
          '<tr><td>&mdash; outside the scene footprint</td><td class="num">' + s.never_imaged_pct + "%</td></tr>" +
          '<tr class="total"><td>Seen by neither sensor</td><td class="num">' + s.not_observed_pct + "%</td></tr>" +
        "</tbody></table>" +
        (gap >= 1 ? '<div class="err"><b>' + gap + "%</b> of this area was unusable in optical " +
          "imagery. An optical-only system reports that ground as <i>no data</i> &mdash; which in " +
          "practice is read as <i>no change</i>. Radar observed it.</div>" : "") +
      "</section>" +
    "</div>" +

    (s.persistence ?
      '<section style="margin-bottom:var(--space-5)"><p class="label-cap">How much of it held up</p>' +
      '<div class="grid--butted">' +
        '<div><p class="stat-label">Confirmed by later dates</p><div class="stat-value stat-value--lg mono">' +
          fmtHa(s.persistence.persistent_px / 100) + " ha</div></div>" +
        '<div><p class="stat-label">Latest date only</p><div class="stat-value stat-value--lg mono">' +
          fmtHa(s.persistence.latest_px / 100) + " ha</div></div>" +
        '<div style="background:var(--color-primary)"><p class="stat-label">Rejected as transient</p>' +
          '<div class="stat-value stat-value--lg mono" style="color:var(--color-destructive)">' +
          fmtHa(s.persistence.transient_ha) + " ha</div></div>" +
      "</div>" +
      '<p class="help">A two-date comparison would have reported that ' +
        fmtHa(s.persistence.transient_ha) + " ha as real change. Across the series it reverted, " +
        "so it is excluded here and reported as the noise figure.</p></section>" : "") +

    '<section><p class="label-cap">Provenance</p><table><tbody>' +
      '<tr><td>Index / optical threshold</td><td class="num">' + esc((p.index || "").toUpperCase()) +
        (th.optical != null ? " " + th.optical : "") + "</td></tr>" +
      '<tr><td>Radar threshold</td><td class="num">' +
        (th.sar_db != null ? th.sar_db + " dB" : "\u2014") + "</td></tr>" +
      '<tr><td>Minimum patch</td><td class="num">' +
        (th.sieve_px != null ? th.sieve_px + " px" : "\u2014") +
        " &middot; areas from the sieved mask</td></tr>" +
      (th.min_persist != null ? '<tr><td>Confirmations required</td><td class="num">' +
        th.min_persist + "</td></tr>" : "") +
      '<tr><td>Run</td><td class="num">' + esc(id) +
        (d.created ? " &middot; " + esc(d.created) : "") + "</td></tr>" +
      '<tr><td>Sources</td><td class="num">Element84 Earth Search &middot; ' +
        "Microsoft Planetary Computer</td></tr>" +
    "</tbody></table>" +
    '<p class="help">Every region carries its scene IDs, thresholds and evidence string in the ' +
      "GeoJSON export, so any figure above can be reproduced from the same public scenes.</p>" +
    '<div class="row" style="margin-top:var(--space-4)">' +
      '<button class="btn btn--primary btn--auto" id="print">Print / PDF</button>' +
      '<a class="btn btn--auto" href="/api/export/' + encodeURIComponent(id) +
      '.geojson">Download GeoJSON</a></div>' +
    "</section>";

  $("print").onclick = () => window.print();
})();
</script>
"""

BODIES = {
    "index.html":    (ASK, ASK_JS),
    "explore.html":  (EXPLORE, EXPLORE_JS),
    "results.html":  (RESULTS, RESULTS_DRAWER_JS + RESULTS_JS),
    "compare.html":  (COMPARE, COMPARE_JS),
    "timeline.html": (TIMELINE, TIMELINE_JS),
    "report.html":   (REPORT, REPORT_JS),
    "drone.html":    (DRONE, DRONE_JS),
    "about.html":    (ABOUT, ""),
}


def main() -> None:
    (WEB / "shared.js").write_text(SHARED_JS, encoding="utf-8")
    written = ["shared.js"]
    for filename, _label, _icon, title in PAGES:
        body, script = BODIES[filename]
        (WEB / filename).write_text(shell(title, body, script), encoding="utf-8")
        written.append(filename)
    print("wrote:", ", ".join(written))
    print("note: shared.css is hand-maintained and not regenerated here")


if __name__ == "__main__":
    main()
