"""Render AOI imagery chips so a detection can be checked against real pixels.

A polygon on a map is a claim. These chips are the evidence: the same ground on
the baseline date and the comparison date, so the change can be eyeballed.

Chips are rendered on a plain lat/lon grid rather than the UTM analysis grid.
Leaflet's imageOverlay pins an image to a lat/lon rectangle, so handing it a
UTM raster would place every pixel slightly off -- exactly the kind of silent
misregistration this project spends its effort avoiding.
"""
from __future__ import annotations

import io

import numpy as np
from rasterio.transform import from_origin

import bridge
import cache
from raster import Grid, read_to_grid, read_to_grid_multi

_CFG = bridge.SETTINGS.get("chips", {})
MAX_PX = _CFG.get("max_px", 768)
JPEG_QUALITY = _CFG.get("jpeg_quality", 88)
#: Sentinel-1 gamma0 in dB. Fixed window rather than per-scene percentiles, so
#: the before and after chips stay directly comparable by eye.
SAR_DB_RANGE = (-25.0, 0.0)


def chip_grid(bbox, max_px: int = MAX_PX) -> Grid:
    """A WGS84 grid over the AOI, capped at max_px on the long side."""
    west, south, east, north = bbox
    span_x, span_y = east - west, north - south
    if span_x <= 0 or span_y <= 0:
        raise ValueError(f"invalid bbox {bbox}")
    scale = max_px / max(span_x, span_y)
    width = max(1, int(round(span_x * scale)))
    height = max(1, int(round(span_y * scale)))
    return Grid("EPSG:4326", from_origin(west, north, span_x / width, span_y / height),
                width, height, span_x / width)


def stretch(band: np.ndarray, lo_pct: float = 2.0, hi_pct: float = 98.0) -> np.ndarray:
    """Percentile stretch to 8-bit, ignoring nodata."""
    finite = band[np.isfinite(band) & (band > 0)]
    if finite.size == 0:
        return np.zeros(band.shape, dtype="uint8")
    lo, hi = np.percentile(finite, [lo_pct, hi_pct])
    if hi <= lo:
        hi = lo + 1.0
    out = (np.clip(band, lo, hi) - lo) / (hi - lo)
    return (np.nan_to_num(out) * 255).astype("uint8")


def s2_truecolor(item: dict, grid: Grid, bbox) -> np.ndarray:
    """RGB composite for the AOI.

    Prefers the pre-rendered 'visual' asset: one 8-bit 3-band COG instead of
    three 16-bit single-band ones. Measured 23.3s -> 7.0s for the same chip,
    because it is a third of the requests and a fraction of the bytes.
    Falls back to the raw reflectance bands if a scene lacks it.
    """
    assets = item["assets"]

    def build() -> np.ndarray:
        if "visual" in assets:
            rgb = read_to_grid_multi(assets["visual"]["href"], grid, bands=(1, 2, 3))
            # Already 8-bit and colour-balanced; only rescale if it is not.
            if rgb.dtype != np.uint8:
                return np.dstack([stretch(rgb[..., i]) for i in range(3)])
            return rgb
        bands = [read_to_grid(assets[b]["href"], grid) for b in ("red", "green", "blue")]
        return np.dstack([stretch(b) for b in bands])

    return cache.get_or_compute(item["id"], bbox, f"chip_rgb_{grid.width}", build)


def s1_backscatter(item: dict, grid: Grid, bbox, polarization: str = "vv") -> np.ndarray:
    """Grayscale gamma0 chip on a fixed dB window.

    This is what the radar actually saw -- including through the cloud that
    blinded the optical sensor.
    """
    pol = polarization.lower()

    def build() -> np.ndarray:
        from landsat_acquisition.stac_client import sign_asset_url

        gamma0 = read_to_grid(sign_asset_url(item["assets"][pol]["href"]), grid)
        with np.errstate(divide="ignore", invalid="ignore"):
            db = 10.0 * np.log10(np.where(np.isfinite(gamma0) & (gamma0 > 0), gamma0, np.nan))
        lo, hi = SAR_DB_RANGE
        norm = (np.clip(db, lo, hi) - lo) / (hi - lo)
        grey = (np.nan_to_num(norm) * 255).astype("uint8")
        return np.dstack([grey, grey, grey])

    return cache.get_or_compute(item["id"], bbox, f"chip_{pol}_{grid.width}", build)


def encode(rgb: np.ndarray) -> bytes:
    """Encode a chip for the wire.

    JPEG, not PNG. Measured on a 1024 px chip: PNG with optimize=True took
    0.52s and 1.58 MB, JPEG q85 took 0.02s and 0.19 MB. These are photographs
    of ground, so lossless buys nothing and costs eight times the bytes.
    """
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb, dtype="uint8"), mode="RGB").save(
        buf, format="JPEG", quality=JPEG_QUALITY, optimize=False
    )
    return buf.getvalue()


#: Kept so existing callers and tests do not break.
to_png = encode
