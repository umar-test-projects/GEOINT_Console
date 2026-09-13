"""Semantic search: crop geometry, prompt shaping, ranking arithmetic.

Deliberately model-free. Loading RemoteCLIP costs 605 MB and ~13 s, so the
parts that can break silently -- where a crop lands, how a query is shaped,
how scores are ordered -- are tested without it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import semantic  # noqa: E402

BBOX = (-60.0, -3.1, -59.9, -3.0)   # 0.1 deg square


def feature(west, south, east, north):
    return {"geometry": {"type": "Polygon", "coordinates": [[
        [west, south], [east, south], [east, north], [west, north], [west, south]]]}}


def chip(size=1024):
    """A chip whose pixel values encode their own column, so a crop's position
    can be recovered from its contents."""
    rgb = np.zeros((size, size, 3), dtype="uint8")
    rgb[:, :, 0] = np.linspace(0, 255, size).astype("uint8")[None, :]
    return rgb


# ------------------------------------------------------------- cropping


def test_crop_is_centred_on_the_region():
    rgb = chip()
    # A region in the far west should crop from the low-value (dark) side.
    west_crop = semantic.region_crops([feature(-59.995, -3.06, -59.99, -3.05)], rgb, BBOX)[0]
    east_crop = semantic.region_crops([feature(-59.91, -3.06, -59.905, -3.05)], rgb, BBOX)[0]
    assert np.asarray(west_crop)[..., 0].mean() < np.asarray(east_crop)[..., 0].mean()


def test_small_region_still_gets_a_full_context_window():
    """A change can be a handful of pixels; the crop must not be."""
    rgb = chip()
    tiny = feature(-59.9501, -3.0501, -59.95, -3.05)   # sub-pixel
    crop = semantic.region_crops([tiny], rgb, BBOX)[0]
    assert min(crop.size) >= semantic.MIN_CROP_PX - 1


def test_large_region_gets_a_crop_larger_than_the_minimum():
    rgb = chip()
    big = feature(-59.98, -3.08, -59.92, -3.02)        # ~60% of the AOI
    crop = semantic.region_crops([big], rgb, BBOX)[0]
    assert min(crop.size) > semantic.MIN_CROP_PX


def test_crop_at_the_edge_stays_inside_the_chip():
    rgb = chip()
    corner = feature(-60.0, -3.1, -59.999, -3.099)      # exact SW corner
    crop = semantic.region_crops([corner], rgb, BBOX)[0]
    assert crop.size[0] > 0 and crop.size[1] > 0        # clamped, not negative


def test_one_crop_per_feature():
    rgb = chip(256)
    feats = [feature(-59.99, -3.09, -59.98, -3.08), feature(-59.92, -3.02, -59.91, -3.01)]
    assert len(semantic.region_crops(feats, rgb, BBOX)) == 2


def test_no_features_gives_no_crops():
    assert semantic.region_crops([], chip(64), BBOX) == []


# -------------------------------------------------------------- prompts


def test_bare_phrase_is_caption_shaped():
    assert semantic.build_prompt("new buildings") == "a satellite image of new buildings"


def test_already_formed_sentence_is_left_alone():
    for q in ("a river delta", "an urban area", "the coastline"):
        assert semantic.build_prompt(q) == q


def test_empty_query_rejected():
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="empty"):
            semantic.build_prompt(bad)


# -------------------------------------------------------------- ranking


def unit(vec):
    v = np.asarray(vec, dtype="float32")
    return v / np.linalg.norm(v)


def test_ranking_orders_by_cosine_similarity():
    emb = np.stack([unit([1, 0, 0]), unit([0, 1, 0]), unit([0.9, 0.1, 0])])
    out = semantic.rank_by_vector(emb, unit([1, 0, 0]), top_k=3)
    assert [m["feature_index"] for m in out] == [0, 2, 1]
    assert out[0]["score"] == pytest.approx(1.0, abs=1e-4)
    assert [m["rank"] for m in out] == [1, 2, 3]


def test_top_k_limits_results():
    emb = np.stack([unit([1, 0]), unit([0, 1]), unit([1, 1])])
    assert len(semantic.rank_by_vector(emb, unit([1, 0]), top_k=2)) == 2


def test_top_k_zero_still_returns_the_best_match():
    emb = np.stack([unit([1, 0]), unit([0, 1])])
    assert len(semantic.rank_by_vector(emb, unit([1, 0]), top_k=0)) == 1


def test_no_embeddings_gives_no_matches():
    assert semantic.rank_by_vector(np.zeros((0, 512), dtype="float32"),
                                   np.zeros(512, dtype="float32")) == []


def test_scores_are_bounded_like_cosines():
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(20, 512)).astype("float32")
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    q = unit(rng.normal(size=512))
    for m in semantic.rank_by_vector(emb, q, top_k=20):
        assert -1.0001 <= m["score"] <= 1.0001
