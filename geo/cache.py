"""Disk cache for computed index / backscatter arrays.

A two-date Sentinel-2 comparison measured ~51 s, almost all of it network. Users
re-run detection constantly while tuning the threshold and min-area sliders, and
re-fetching for every slider drag makes the UI feel broken. Caching the derived
arrays makes re-detection effectively free.

Signed URLs are deliberately never cached — Planetary Computer SAS tokens expire
in about a day. The pixels are cached; the credential is not.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent


def _cache_dir() -> Path:
    import bridge

    d = ROOT / bridge.SETTINGS["paths"].get("cache_dir", ".cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_bytes() -> int:
    import bridge

    return int(float(bridge.SETTINGS["paths"].get("cache_max_gb", 5)) * 1e9)


def _evict(d: Path, limit: int) -> int:
    """Delete least recently used arrays until the folder fits `limit` bytes.

    ponytail: scans the folder on every store. A store follows a remote read
    that took seconds, so the scan is noise; index sizes if the cache ever
    holds hundreds of thousands of files.
    """
    entries = []
    for f in d.glob("*.npy"):
        try:
            st = f.stat()
        except OSError:
            continue  # removed by another thread mid-scan
        entries.append((st.st_mtime, st.st_size, f))
    total = sum(size for _, size, _ in entries)
    removed = 0
    for _, size, f in sorted(entries):
        if total <= limit:
            break
        try:
            f.unlink()
        except OSError:
            continue  # open in another thread on Windows; the next store retries
        total -= size
        removed += 1
    return removed


def key(scene_id: str, bbox: tuple[float, ...], layer: str) -> str:
    """Stable cache key. bbox is rounded so float jitter does not miss the cache."""
    payload = json.dumps(
        {"scene": scene_id, "bbox": [round(float(v), 6) for v in bbox], "layer": layer},
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:20]


def load(k: str) -> np.ndarray | None:
    path = _cache_dir() / f"{k}.npy"
    if not path.exists():
        return None
    try:
        array = np.load(path)
    except (ValueError, OSError):
        # A truncated file from an interrupted write is not fatal; refetch.
        path.unlink(missing_ok=True)
        return None
    try:
        os.utime(path)  # recently used: eviction takes arrays nobody asks for first
    except OSError:
        pass
    return array


def store(k: str, array: np.ndarray) -> np.ndarray:
    path = _cache_dir() / f"{k}.npy"
    tmp = path.with_name(path.name + ".tmp")
    # Write through a handle: np.save(path_like) appends its own '.npy' suffix
    # when the name does not already end in one, which would miss the rename.
    with tmp.open("wb") as fh:
        np.save(fh, array)
    tmp.replace(path)  # atomic: a reader never sees a half-written array
    _evict(path.parent, _max_bytes())
    return array


#: One lock per cache key. The UI prefetches chips in the background while the
#: user may click for the same chip, and without this both requests would do
#: the same expensive remote read at once.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(k: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(k, threading.Lock())


def get_or_compute(scene_id: str, bbox, layer: str, compute):
    k = key(scene_id, bbox, layer)
    cached = load(k)
    if cached is not None:
        return cached
    with _lock_for(k):
        # Re-check: whoever held the lock may have just written it.
        cached = load(k)
        if cached is not None:
            return cached
        return store(k, compute())


def clear() -> int:
    n = 0
    for f in _cache_dir().glob("*.npy"):
        f.unlink()
        n += 1
    return n
