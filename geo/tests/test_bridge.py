"""The satellite-mvp fingerprint notices code changes and nothing else."""
import bridge


def test_fingerprint_tracks_code_not_line_endings_or_caches(tmp_path):
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    module = tmp_path / "pkg" / "mod.py"
    module.write_bytes(b"x = 1\n")
    first = bridge.fingerprint(tmp_path)

    module.write_bytes(b"x = 1\r\n")
    (tmp_path / "pkg" / "__pycache__" / "stale.py").write_bytes(b"junk")
    (tmp_path / "notes.txt").write_bytes(b"not code")
    assert bridge.fingerprint(tmp_path) == first

    module.write_bytes(b"x = 2\n")
    assert bridge.fingerprint(tmp_path) != first
