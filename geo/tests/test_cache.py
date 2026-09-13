"""The array cache stays inside its disk budget, dropping least recently used first."""
import os

import numpy as np

import cache


def test_evicts_least_recently_used_first(tmp_path):
    for i, name in enumerate(["old", "mid", "new"]):
        path = tmp_path / f"{name}.npy"
        np.save(path, np.zeros(1000))
        os.utime(path, (1000 + i, 1000 + i))
    size = (tmp_path / "old.npy").stat().st_size

    assert cache._evict(tmp_path, limit=2 * size) == 1
    assert sorted(p.stem for p in tmp_path.glob("*.npy")) == ["mid", "new"]
    assert cache._evict(tmp_path, limit=2 * size) == 0
