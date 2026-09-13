"""Name the kind of change, from the combined optical and radar signature.

The physics that makes this work, and the reason it needs both sensors:

    Clearing and construction both strip vegetation, so both show the same
    NDVI collapse. Optical alone cannot separate them. Radar can, because the
    two leave opposite roughness signatures -- buildings act as corner
    reflectors and backscatter RISES, while cleared ground goes smooth and
    backscatter FALLS.

    Flooding is likewise unmistakable in radar: open water reflects the pulse
    away from the sensor, so backscatter collapses. And floods arrive with
    storms, exactly when optical is blinded.

So when radar is missing, this reports that vegetation was lost and refuses to
guess which kind, rather than quietly picking the more likely-sounding label.

Vocabulary follows satellite-mvp's ChangeType (change_detection/models.py:21)
where the classes overlap, so a label means the same thing in both systems.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- classes ------------------------------------------------------------
CONSTRUCTION = "construction"
CLEARANCE = "clearance"
VEGETATION_LOSS = "vegetation_loss"          # loss confirmed, cause unresolved
VEGETATION_GROWTH = "vegetation_growth"
FLOODING = "flooding"
WATER_RECESSION = "water_recession"
ROAD_DEVELOPMENT = "road_development"
SURFACE_CHANGE = "surface_change"
STRUCTURAL_CHANGE = "structural_change"
OTHER = "other"

LABELS = {
    CONSTRUCTION: "Construction / new built-up",
    CLEARANCE: "Clearing / vegetation removal",
    VEGETATION_LOSS: "Vegetation loss (cause unresolved)",
    VEGETATION_GROWTH: "Vegetation growth / regrowth",
    FLOODING: "Flooding / water expansion",
    WATER_RECESSION: "Water recession",
    ROAD_DEVELOPMENT: "Road / linear infrastructure",
    SURFACE_CHANGE: "Bare surface / material change",
    STRUCTURAL_CHANGE: "Structural change (radar only)",
    OTHER: "Unclassified change",
}

# --- thresholds ---------------------------------------------------------
# Optical deltas are in normalized-index units; SAR is dB of backscatter change.
VEG_STRONG = 0.15
WATER_STRONG = 0.15
BUILT_UP = 0.05
SAR_RISE = 1.5          # corner reflectors appearing
SAR_FALL = -1.5         # surface smoothing
SAR_WATER_FALL = -3.0   # specular collapse over open water
ELONGATION_ROAD = 4.0


@dataclass
class Signature:
    """Mean change within one detected region, plus its shape."""

    ndvi: float = 0.0
    ndwi: float = 0.0
    ndbi: float = 0.0
    sar_db: float = 0.0
    elongation: float = 1.0
    area_ha: float = 0.0
    has_optical: bool = True
    has_sar: bool = True


@dataclass
class Classification:
    change_type: str
    confidence: float
    evidence: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LABELS.get(self.change_type, LABELS[OTHER])

    def as_dict(self) -> dict:
        return {
            "change_type": self.change_type,
            "change_label": self.label,
            "class_confidence": round(self.confidence, 2),
            "evidence": "; ".join(self.evidence),
        }


def _readings(sig: Signature) -> list[str]:
    parts = []
    if sig.has_optical:
        parts.append(f"NDVI {sig.ndvi:+.2f}, NDWI {sig.ndwi:+.2f}, NDBI {sig.ndbi:+.2f}")
    if sig.has_sar:
        parts.append(f"SAR {sig.sar_db:+.1f} dB")
    return parts


def classify(sig: Signature) -> Classification:
    """Assign a change class. Rules run most-distinctive first."""
    ev = _readings(sig)

    # --- water, the most distinctive signature available -----------------
    if sig.has_optical and sig.ndwi >= WATER_STRONG:
        if sig.has_sar and sig.sar_db <= SAR_WATER_FALL:
            return Classification(FLOODING, 0.9, ev + [
                "water index rose and backscatter collapsed, which is open water "
                "reflecting the radar pulse away from the sensor"])
        corroboration = ("radar does not corroborate" if sig.has_sar
                         else "no radar available to corroborate")
        return Classification(FLOODING, 0.6, ev + [f"water index rose; {corroboration}"])

    if sig.has_sar and not sig.has_optical and sig.sar_db <= SAR_WATER_FALL:
        # Under cloud this is the flood signal, and cloud is when floods happen.
        return Classification(FLOODING, 0.7, ev + [
            "backscatter collapsed with no optical view; characteristic of open "
            "water appearing under cloud"])

    if sig.has_optical and sig.ndwi <= -WATER_STRONG:
        return Classification(WATER_RECESSION, 0.7, ev + ["water index fell"])

    # --- vegetation loss: clearing or construction -----------------------
    if sig.has_optical and sig.ndvi <= -VEG_STRONG:
        if not sig.has_sar:
            # The honest outcome: optical alone cannot tell these apart.
            return Classification(VEGETATION_LOSS, 0.5, ev + [
                "vegetation lost, but clearing and construction look identical to "
                "optical alone; radar is needed to separate them"])
        if sig.sar_db >= SAR_RISE and sig.ndbi >= BUILT_UP:
            return Classification(CONSTRUCTION, 0.85, ev + [
                "vegetation lost, built-up index rose, and backscatter rose -- "
                "structures acting as corner reflectors"])
        if sig.sar_db >= SAR_RISE:
            return Classification(CONSTRUCTION, 0.65, ev + [
                "vegetation lost and backscatter rose, indicating hard structures"])
        if sig.sar_db <= SAR_FALL:
            return Classification(CLEARANCE, 0.85, ev + [
                "vegetation lost and backscatter fell -- canopy volume scattering "
                "replaced by smooth bare ground"])
        return Classification(VEGETATION_LOSS, 0.55, ev + [
            "vegetation lost; radar is ambiguous, so clearing and construction "
            "cannot be separated"])

    # --- vegetation gain -------------------------------------------------
    if sig.has_optical and sig.ndvi >= VEG_STRONG:
        return Classification(VEGETATION_GROWTH, 0.75, ev + ["vegetation index rose"])

    # --- linear infrastructure -------------------------------------------
    if sig.elongation >= ELONGATION_ROAD and (sig.ndbi >= 0 or sig.sar_db >= SAR_RISE):
        return Classification(ROAD_DEVELOPMENT, 0.6, ev + [
            f"long, narrow footprint (elongation {sig.elongation:.1f})"])

    # --- surface / material change ---------------------------------------
    if sig.has_optical and sig.ndbi >= 2 * BUILT_UP:
        return Classification(SURFACE_CHANGE, 0.55, ev + [
            "built-up index rose without vegetation loss; bare soil or a material change"])

    # --- radar sees something optical does not ---------------------------
    if sig.has_sar and abs(sig.sar_db) >= 2 * SAR_RISE:
        return Classification(STRUCTURAL_CHANGE, 0.6, ev + [
            "backscatter changed with no matching spectral signature -- roughness, "
            "structure or soil moisture"])

    return Classification(OTHER, 0.3, ev + ["no signature matched confidently"])


def summarize(features: list[dict]) -> list[dict]:
    """Aggregate classified features into a per-class breakdown, largest first."""
    totals: dict[str, dict] = {}
    for f in features:
        p = f["properties"]
        ct = p.get("change_type", OTHER)
        row = totals.setdefault(ct, {
            "change_type": ct,
            "label": LABELS.get(ct, LABELS[OTHER]),
            "area_ha": 0.0,
            "count": 0,
            "confidence": 0.0,
        })
        row["area_ha"] += p.get("area_ha", 0.0)
        row["count"] += 1
        row["confidence"] += p.get("class_confidence", 0.0)

    out = []
    for row in totals.values():
        row["confidence"] = round(row["confidence"] / max(row["count"], 1), 2)
        row["area_ha"] = round(row["area_ha"], 2)
        out.append(row)
    return sorted(out, key=lambda r: r["area_ha"], reverse=True)
