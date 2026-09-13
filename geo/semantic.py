"""Natural-language search over detected change regions.

satellite-mvp already does text-to-image retrieval, but against a prebuilt
FAISS index over its own fixed tile grid (retrieval/search.py). That answers
"which tile looks like X". This answers a different question -- "which of the
changes I just detected looks like X" -- over arbitrary AOIs that have no
prebuilt index at all.

The model is the same RemoteCLIP checkpoint satellite-mvp ships, loaded through
its own encoder class, so embeddings mean the same thing in both systems.

No FAISS here. A run produces a few hundred regions, and brute-force cosine
over a few hundred 512-d vectors is a single matrix multiply -- an approximate
index would add a dependency and a failure mode to save nothing.
"""
from __future__ import annotations

import threading

import numpy as np
from PIL import Image

import bridge

#: RemoteCLIP: CLIP fine-tuned on remote-sensing image-text pairs. The
#: general-domain checkpoint markedly underperforms on overhead imagery.
CHECKPOINT = bridge.SATELLITE_MVP / "models" / "selected" / "RemoteCLIP-ViT-B-32.pt"
ARCHITECTURE = "ViT-B-32"

#: Context around each region before encoding. A bare change footprint is often
#: a handful of pixels; what makes it recognisable as a road, a building plot
#: or a flooded field is its surroundings.
CONTEXT_PAD = 2.0
#: CLIP consumes 224x224. Cropping exactly that at Sentinel-2's native 10 m
#: means ~2.2 km of ground per crop with no resampling at all -- which is the
#: scale RemoteCLIP was trained on. Cropping a handful of pixels and upsampling
#: instead pushes the input far outside that distribution, and the ranking
#: degrades accordingly.
MIN_CROP_PX = 224
#: Chip width used for embedding, chosen so chip resolution ~= sensor
#: resolution. Independent of the chip size used for display.
EMBED_CHIP_PX = 1024

#: Prompt template. CLIP was trained on captions, so a bare noun scores worse
#: than the same noun in a caption-shaped sentence.
PROMPT = "a satellite image of {}"

_encoder = None
_lock = threading.Lock()


class SemanticUnavailable(RuntimeError):
    """The encoder could not be loaded; search is unavailable but detection is not."""


def get_encoder():
    """Load RemoteCLIP once and reuse it. Takes ~13 s on first call."""
    global _encoder
    if _encoder is not None:
        return _encoder
    with _lock:
        if _encoder is not None:
            return _encoder
        if not CHECKPOINT.exists():
            raise SemanticUnavailable(f"RemoteCLIP checkpoint not found at {CHECKPOINT}")
        try:
            from retrieval.encoder import OpenCLIPEncoder
        except ImportError as exc:
            raise SemanticUnavailable(
                f"could not import satellite-mvp's encoder: {exc}"
            ) from exc
        try:
            _encoder = OpenCLIPEncoder(
                ARCHITECTURE, checkpoint_path=CHECKPOINT,
                name="RemoteCLIP", version=ARCHITECTURE,
            )
        except Exception as exc:  # noqa: BLE001
            raise SemanticUnavailable(
                f"open_clip is required for semantic search ({exc})"
            ) from exc
        return _encoder


def _feature_bounds(feature: dict) -> tuple[float, float, float, float]:
    """Lon/lat bounds of a polygon feature."""
    ring = feature["geometry"]["coordinates"][0]
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    return min(lons), min(lats), max(lons), max(lats)


def region_crops(features: list[dict], rgb: np.ndarray, bbox) -> list[Image.Image]:
    """Cut a padded context window around each region from the AOI chip.

    The chip is rendered in EPSG:4326 over exactly `bbox`, so lon/lat maps to
    pixels by a straight linear scale with no reprojection.
    """
    west, south, east, north = bbox
    h, w = rgb.shape[:2]
    span_x = east - west
    span_y = north - south
    crops: list[Image.Image] = []

    for f in features:
        x0, y0, x1, y1 = _feature_bounds(f)
        # lon/lat -> pixel; note y is flipped, north is row 0.
        px0 = (x0 - west) / span_x * w
        px1 = (x1 - west) / span_x * w
        py0 = (north - y1) / span_y * h
        py1 = (north - y0) / span_y * h

        cx, cy = (px0 + px1) / 2.0, (py0 + py1) / 2.0
        size = max((px1 - px0) * CONTEXT_PAD, (py1 - py0) * CONTEXT_PAD, MIN_CROP_PX)
        half = size / 2.0

        # Clamp to the chip, keeping the window square where possible.
        left = int(max(0, min(cx - half, w - 1)))
        top = int(max(0, min(cy - half, h - 1)))
        right = int(min(w, max(left + 1, cx + half)))
        bottom = int(min(h, max(top + 1, cy + half)))
        crops.append(Image.fromarray(rgb[top:bottom, left:right]).convert("RGB"))
    return crops


def embed_regions(features: list[dict], rgb: np.ndarray, bbox, batch: int = 32) -> np.ndarray:
    """Embed every region's context crop. Returns L2-normalized (N, 512)."""
    enc = get_encoder()
    crops = region_crops(features, rgb, bbox)
    if not crops:
        return np.zeros((0, enc.embedding_dimension()), dtype="float32")
    out = [enc.encode_image(crops[i:i + batch]) for i in range(0, len(crops), batch)]
    return np.vstack(out)


def embed_query(query: str) -> np.ndarray:
    enc = get_encoder()
    return enc.encode_text([build_prompt(query)])[0]


def rank(embeddings: np.ndarray, query: str, top_k: int = 20) -> list[dict]:
    """Rank regions against the query by cosine similarity.

    Both sides are L2-normalized by the encoder, so the dot product *is* the
    cosine. Scores are comparable within one query and not across queries --
    CLIP similarities are not calibrated probabilities, so they order results
    rather than measure them.
    """
    if embeddings.shape[0] == 0:
        return []
    return rank_by_vector(embeddings, embed_query(query), top_k)


def rank_by_vector(embeddings: np.ndarray, query_vec: np.ndarray, top_k: int = 20) -> list[dict]:
    """The ranking arithmetic, separated from the model so it can be tested
    without loading 605 MB of weights."""
    if embeddings.shape[0] == 0:
        return []
    scores = embeddings @ query_vec
    order = np.argsort(-scores)[:max(1, top_k)]
    return [
        {"feature_index": int(i), "score": round(float(scores[i]), 4), "rank": r + 1}
        for r, i in enumerate(order)
    ]


def build_prompt(query: str) -> str:
    """Caption-shape a bare phrase; leave an already-formed sentence alone."""
    text = query.strip()
    if not text:
        raise ValueError("query is empty")
    return text if text.lower().startswith(("a ", "an ", "the ")) else PROMPT.format(text)
