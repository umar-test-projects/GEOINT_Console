"""A single target grid that every sensor is resampled onto.

Fusion compares Sentinel-1 and Sentinel-2 pixel by pixel, so they must share
one grid exactly. Reading each sensor on its own native window and hoping the
shapes line up would offset them by a fraction of a pixel and manufacture
change along every edge in the scene. Instead we define the grid from the AOI
and warp both sensors onto it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

# GDAL must not list remote directories, and must be allowed to range-read.
# Note: never set CPL_VSIL_CURL_ALLOWED_EXTENSIONS — Sentinel-1 RTC assets are
# '.tiff' and a '.tif' allowlist silently breaks every read.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
# Throughput settings for ranged COG reads over HTTP. Without these every read
# is single-threaded with tiny un-cached ranges, which dominates chip latency.
os.environ.setdefault("GDAL_NUM_THREADS", "ALL_CPUS")
os.environ.setdefault("GDAL_CACHEMAX", "512")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("VSI_CACHE_SIZE", "33554432")
os.environ.setdefault("CPL_VSIL_CURL_CHUNK_SIZE", "1048576")
os.environ.setdefault("GDAL_HTTP_VERSION", "2")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")

BBox = tuple[float, float, float, float]  # west, south, east, north (EPSG:4326)


def utm_epsg(bbox: BBox) -> str:
    """UTM zone covering the centre of the AOI.

    Metric CRS keeps pixels square and areas honest; computing hectares from
    degrees is wrong by a latitude-dependent factor.
    """
    west, south, east, north = bbox
    lon = (west + east) / 2.0
    lat = (south + north) / 2.0
    zone = int((lon + 180.0) / 6.0) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


@dataclass(frozen=True)
class Grid:
    """The common analysis grid: CRS, affine transform and pixel dimensions."""

    crs: str
    transform: object
    width: int
    height: int
    resolution: float

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def pixel_area_m2(self) -> float:
        return self.resolution ** 2


#: Largest analysis grid, in pixels: about 30 x 30 km at 10 m. Every date holds
#: several float planes this size, so past it one run exhausts memory rather
#: than merely running slowly. Every detection path builds its grid here.
MAX_GRID_PX = 9_000_000


def target_grid(bbox: BBox, resolution: float = 10.0) -> Grid:
    """Build the analysis grid for an AOI, snapped to whole pixels."""
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise ValueError(f"bbox must be (west, south, east, north), got {bbox}")
    crs = utm_epsg(bbox)
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox)
    # Snap outward to the resolution so the grid is stable across calls.
    left = np.floor(left / resolution) * resolution
    bottom = np.floor(bottom / resolution) * resolution
    right = np.ceil(right / resolution) * resolution
    top = np.ceil(top / resolution) * resolution
    width = int(round((right - left) / resolution))
    height = int(round((top - bottom) / resolution))
    if width <= 0 or height <= 0:
        raise ValueError(f"AOI {bbox} is smaller than one {resolution} m pixel")
    if width * height > MAX_GRID_PX:
        raise ValueError(
            f"AOI is {width} x {height} px at {resolution:g} m; the limit is "
            f"{MAX_GRID_PX:,} px (about 30 x 30 km at 10 m). Choose a smaller area."
        )
    return Grid(crs, from_origin(left, top, resolution, resolution), width, height, resolution)


def read_to_grid(href: str, grid: Grid, band: int = 1, *, categorical: bool = False) -> np.ndarray:
    """Read one band of a remote or local raster onto the analysis grid.

    WarpedVRT reprojects lazily, so only the blocks overlapping the AOI are
    fetched over HTTP — a windowed read measured at ~1% of a Sentinel-2 scene.

    Categorical bands (SCL, QA) use nearest neighbour; blending class codes
    would invent classes that do not exist.
    """
    resampling = Resampling.nearest if categorical else Resampling.bilinear
    with rasterio.open(href) as src:
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=resampling,
        ) as vrt:
            return vrt.read(band).astype("float32")


def read_to_grid_multi(href: str, grid: Grid, bands=(1, 2, 3)) -> np.ndarray:
    """Read several bands of one raster onto the grid in a single open.

    One file handle and one warp for all bands, rather than reopening the
    remote dataset per band.
    """
    with rasterio.open(href) as src:
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=Resampling.bilinear,
        ) as vrt:
            stack = vrt.read(list(bands))
    return np.transpose(stack, (1, 2, 0))
