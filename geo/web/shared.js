/* geo - shared behaviour across pages.
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
