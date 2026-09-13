"""Put satellite-mvp on sys.path so its proven raster primitives can be imported.

Must be imported before any satellite-mvp module. Verified: 11/11 modules import
cleanly and neither torch nor faiss is pulled in, because we never touch the
retrieval layer.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _settings() -> dict:
    with (ROOT / "settings.toml").open("rb") as fh:
        return tomllib.load(fh)


SETTINGS = _settings()
#: GEO_SATELLITE_MVP overrides settings.toml, so another checkout runs without
#: editing a tracked file. A relative path resolves against this folder.
SATELLITE_MVP = ROOT / os.environ.get("GEO_SATELLITE_MVP", SETTINGS["paths"]["satellite_mvp"])


def install() -> Path:
    """Prepend satellite-mvp to sys.path. Idempotent. Fails loudly, not with a
    bare ImportError three frames deep."""
    if not SATELLITE_MVP.is_dir():
        raise RuntimeError(
            f"satellite-mvp not found at {SATELLITE_MVP}\n"
            f"Fix the 'satellite_mvp' path in {ROOT / 'settings.toml'} or set GEO_SATELLITE_MVP."
        )
    marker = SATELLITE_MVP / "change_detection" / "features.py"
    if not marker.is_file():
        raise RuntimeError(
            f"{SATELLITE_MVP} exists but has no change_detection/features.py — "
            "path points at the wrong directory (note the repo nests a second "
            "satellite-mvp/ inside the outer one)."
        )
    p = str(SATELLITE_MVP)
    if p not in sys.path:
        sys.path.insert(0, p)
    return SATELLITE_MVP


#: What geo was last tested against: a SHA-256 over satellite-mvp's Python files
#: and dependency lists. satellite-mvp is not version controlled and is never
#: modified from here, so this is how a change to it gets noticed:
#: `/api/health?deep=true` reports a mismatch, and once the tests pass against
#: the new code, `python bridge.py --lock` records it as the baseline.
LOCK_FILE = ROOT / "satellite_mvp.lock"


def fingerprint(root: Path | None = None) -> str:
    root = Path(root or SATELLITE_MVP)
    files = [p for p in root.rglob("*.py")
             if not any(part.startswith(".") or part == "__pycache__"
                        for part in p.relative_to(root).parts)]
    files += [p for p in (root / "requirements.txt", root / "pyproject.toml") if p.is_file()]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        # Line endings are normalized so a checkout on another OS matches.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def matches_lock() -> bool:
    try:
        return LOCK_FILE.read_text(encoding="utf-8").split()[0] == fingerprint()
    except (OSError, IndexError):
        return False


install()

if __name__ == "__main__" and sys.argv[1:] == ["--lock"]:
    LOCK_FILE.write_text(f"{fingerprint()}  {SATELLITE_MVP}\n", encoding="utf-8")
    print("recorded", LOCK_FILE.read_text(encoding="utf-8").strip())
