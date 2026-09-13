"""Multi-date change detection: persistence and change dating.

Two dates can only tell you that something looks different. They cannot tell
you whether it *stayed* different, and a single-date difference is exactly what
a cloud edge, a shadow, a wet field or a mis-registered pixel looks like.

With a series, a change that holds across every later observation is evidence;
one that reverts is noise. That distinction is the entire point of this module,
and it is also what lets a change be dated rather than merely detected.

Baseline-anchored on purpose: every date is compared against the first
observation, not against its predecessor. satellite-mvp's scene pairing makes
the same choice for the same reason (change_detection/scene_pairing.py:6-15) --
against a rolling predecessor a persistent change reverts to "no change" as
soon as the new state becomes the reference.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A pixel changed at the final observation, with nothing after it to confirm.
#: Real, but not yet corroborated -- reported separately rather than merged
#: into the confirmed total or silently dropped.
LATEST = "latest"
PERSISTENT = "persistent"
TRANSIENT = "transient"

#: Later observations that must agree before a change counts as persistent.
DEFAULT_MIN_PERSIST = 1


@dataclass
class SeriesResult:
    """Per-pixel outcome of a multi-date comparison."""

    persistent: np.ndarray      # bool  - changed and confirmed by later dates
    latest: np.ndarray          # bool  - changed only at the final observation
    transient: np.ndarray       # bool  - changed, then contradicted later
    change_index: np.ndarray    # int   - index into the comparison dates, -1 if none
    confirmations: np.ndarray   # int   - later observations agreeing
    signed: np.ndarray          # float - the delta at the change date
    min_persist: int = DEFAULT_MIN_PERSIST

    @property
    def changed(self) -> np.ndarray:
        """Everything worth reporting: confirmed plus not-yet-confirmable."""
        return self.persistent | self.latest

    def counts(self) -> dict:
        return {
            "persistent_px": int(self.persistent.sum()),
            "latest_px": int(self.latest.sum()),
            "transient_px": int(self.transient.sum()),
        }


def detect_series(
    diffs: np.ndarray,
    valids: np.ndarray,
    threshold: float,
    min_persist: int = DEFAULT_MIN_PERSIST,
) -> SeriesResult:
    """Find persistent change across a stack of baseline-relative differences.

    `diffs[i]` is observation i+1 minus the baseline; `valids[i]` says where
    that comparison could be made at all. Both are (T, H, W).

    A pixel is persistent at the earliest date where it crosses the threshold
    **and** every later valid observation still shows the same-signed change.
    Requiring the same sign matters: a pixel that drops and then rises has not
    persisted, it has oscillated, which is what seasonal and noise artefacts do.
    """
    diffs = np.asarray(diffs, dtype="float32")
    valids = np.asarray(valids, dtype=bool)
    if diffs.shape != valids.shape:
        raise ValueError(f"diffs {diffs.shape} and valids {valids.shape} must match")
    if diffs.ndim != 3:
        raise ValueError(f"expected a (T, H, W) stack, got shape {diffs.shape}")
    if threshold <= 0:
        raise ValueError(f"threshold must be positive, got {threshold}")

    n, h, w = diffs.shape
    crossed = valids & (np.abs(np.where(valids, diffs, 0.0)) >= threshold)
    sign = np.sign(np.where(valids, diffs, 0.0))

    persistent = np.zeros((h, w), dtype=bool)
    latest = np.zeros((h, w), dtype=bool)
    change_index = np.full((h, w), -1, dtype="int16")
    confirmations = np.zeros((h, w), dtype="int16")
    signed = np.zeros((h, w), dtype="float32")

    for i in range(n):
        unassigned = (change_index < 0) & crossed[i]
        if not unassigned.any():
            continue

        later_valid = valids[i + 1:]
        if later_valid.size:
            agrees = crossed[i + 1:] & (sign[i + 1:] == sign[i][None, :, :])
            n_valid_later = later_valid.sum(axis=0)
            n_agree = (later_valid & agrees).sum(axis=0)
        else:
            n_valid_later = np.zeros((h, w), dtype=int)
            n_agree = np.zeros((h, w), dtype=int)

        # Confirmed: enough later looks, and every one of them agrees.
        confirmed = unassigned & (n_valid_later >= min_persist) & (n_agree == n_valid_later)
        # Nothing later to check against. The honest outcome is "not yet
        # confirmable" -- neither "confirmed" nor discarded.
        unconfirmable = unassigned & (n_valid_later == 0)

        for mask, target in ((confirmed, persistent), (unconfirmable, latest)):
            if mask.any():
                target |= mask
                change_index[mask] = i
                confirmations[mask] = n_agree[mask]
                signed[mask] = diffs[i][mask]

    # Crossed the threshold at some point but was never confirmed: the series
    # contradicted it. This is the noise that two dates would have reported.
    ever = crossed.any(axis=0)
    transient = ever & ~persistent & ~latest

    return SeriesResult(
        persistent=persistent,
        latest=latest,
        transient=transient,
        change_index=change_index,
        confirmations=confirmations,
        signed=signed,
        min_persist=min_persist,
    )


def windows(start: str, end: str, steps: int) -> list[tuple[str, str]]:
    """Split a date range into `steps` contiguous search windows.

    Each window is searched independently for its clearest scene, so one
    unusable period costs a single observation rather than the whole run.
    """
    from datetime import date, timedelta

    if steps < 2:
        raise ValueError(f"need at least 2 timestamps, got {steps}")
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    if b <= a:
        raise ValueError(f"end {end} must be after start {start}")

    span = (b - a).days
    if span < steps:
        raise ValueError(f"{span} days cannot be split into {steps} windows")

    edges = [a + timedelta(days=round(span * i / steps)) for i in range(steps + 1)]
    return [
        (edges[i].isoformat(), (edges[i + 1] - timedelta(days=1)).isoformat())
        for i in range(steps)
    ]
