"""The API's safety boundaries: who may connect, how big a run may be, and
which files the drone routes may open. None of these touch the network."""
import time

import pytest
from fastapi.testclient import TestClient

import api
import jobs
import raster

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.9", 50000)


@pytest.fixture
def local(monkeypatch):
    monkeypatch.delenv("GEO_PASSWORD", raising=False)
    return TestClient(api.app, client=LOCAL)


def test_remote_client_refused_without_password(monkeypatch):
    monkeypatch.delenv("GEO_PASSWORD", raising=False)
    assert TestClient(api.app, client=REMOTE).get("/api/health").status_code == 403


def test_local_client_allowed_without_password(local):
    assert local.get("/api/health").status_code == 200


def test_password_required_from_everyone_when_set(monkeypatch):
    monkeypatch.setenv("GEO_PASSWORD", "s3cret")
    for addr in (LOCAL, REMOTE):
        c = TestClient(api.app, client=addr)
        assert c.get("/api/health").status_code == 401
        assert c.get("/api/health", auth=("geo", "wrong")).status_code == 401
        assert c.get("/api/health", headers={"Authorization": "Basic !!!"}).status_code == 401
        assert c.get("/api/health", auth=("anyone", "s3cret")).status_code == 200
        assert c.get("/", auth=("anyone", "s3cret")).status_code == 200


def test_target_grid_refuses_oversized_aoi():
    with pytest.raises(ValueError, match="limit"):
        raster.target_grid((0.0, 0.0, 1.0, 1.0))
    raster.target_grid((-60.05, -3.15, -59.95, -3.05))  # the default AOI still fits


def test_oversized_detect_rejected_before_queueing(local):
    before = len(jobs.STORE.all())
    r = local.post("/api/detect", json={
        "bbox": [0, 0, 5, 5], "start": "2023-01-01", "end": "2024-01-01", "steps": 4})
    assert r.status_code == 400
    assert "limit" in r.json()["detail"]
    assert len(jobs.STORE.all()) == before


def test_out_of_range_bbox_rejected(local):
    r = local.post("/api/detect", json={
        "bbox": [179.9, 0, 181, 0.1], "start": "2023-01-01", "end": "2024-01-01", "steps": 4})
    assert r.status_code == 422


SMALL_RUN = {"bbox": [-60.05, -3.15, -59.95, -3.05],
             "start": "2023-01-01", "end": "2024-01-01", "steps": 4}


def test_detect_poll_export_end_to_end(local, monkeypatch, tmp_path):
    """Submit, poll to done, then fetch the result and the export: the path
    every page depends on, with the satellite read replaced by a fixed result."""
    fc = {"type": "FeatureCollection", "features": [], "properties": {"stats": {"warnings": []}}}
    monkeypatch.setattr(jobs, "STORE", jobs.JobStore(tmp_path))
    monkeypatch.setattr(api.series_run, "run_series", lambda *a, **k: fc)

    r = local.post("/api/detect", json=SMALL_RUN)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    for _ in range(500):
        s = local.get(f"/api/jobs/{job_id}?include_features=false").json()
        if s["status"] not in ("pending", "running"):
            break
        time.sleep(0.01)
    assert s["status"] == "done" and s["feature_count"] == 0
    assert local.get(f"/api/jobs/{job_id}").json()["result"] == fc
    export = local.get(f"/api/export/{job_id}.geojson")
    assert export.status_code == 200 and export.json() == fc


def test_full_queue_answers_429(local, monkeypatch):
    def full(*a, **k):
        raise jobs.QueueFull("8 runs are already waiting")

    monkeypatch.setattr(jobs.STORE, "submit", full)
    assert local.post("/api/detect", json=SMALL_RUN).status_code == 429


def test_deep_health_reports_unreachable_upstream(local, monkeypatch):
    monkeypatch.setattr(api, "_reachable", lambda url: "nominatim" not in url)
    r = local.get("/api/health?deep=true")
    assert r.status_code == 503
    assert r.json()["checks"]["nominatim"] is False
    assert r.json()["checks"]["earth_search"] is True


def test_security_headers_on_every_response(local):
    for path in ("/", "/api/health"):
        headers = local.get(path).headers
        assert "frame-ancestors 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "SAMEORIGIN"


def test_chip_renders_are_capped(local, monkeypatch):
    import threading

    monkeypatch.setattr(api, "_chip_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(api, "CHIP_WAIT_S", 0.01)
    api._chip_slots.acquire()  # another render holds the only slot
    r = local.get("/api/chip", params={"bbox": "-60.05,-3.15,-59.95,-3.05", "scene_id": "S2A_X"})
    assert r.status_code == 429


@pytest.mark.parametrize("scene_id", ["../../../search", "x?collections=other", "x#frag",
                                      "%2e%2e/search", "a/b", ""])
def test_malformed_scene_ids_never_reach_the_upstream(scene_id, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a request was sent")

    monkeypatch.setattr(api.s2src.requests, "get", no_network)
    for src in (api.s2src, api.s1src):
        with pytest.raises(src.SceneSearchError):
            src.get_item(scene_id)


def test_detection_settings_are_bounded(local):
    for bad in ({"optical_threshold": 0}, {"sieve_size": -1}, {"steps": 50},
                {"start": "yesterday"}, {"max_cloud": 150}, {"polarization": "hh"}):
        assert local.post("/api/detect", json={**SMALL_RUN, **bad}).status_code == 422, bad
    semantic = {"job_id": "x", "query": "q", "top_k": 10_000}
    assert local.post("/api/semantic", json=semantic).status_code == 422
    assert local.post("/api/locate", json={"query": "x" * 1000}).status_code == 422


def test_wrong_passwords_lock_out_the_address(monkeypatch):
    monkeypatch.setenv("GEO_PASSWORD", "s3cret")
    monkeypatch.setattr(api, "AUTH_MAX_FAILURES", 3)
    monkeypatch.setattr(api, "_auth_failures", {})
    guesser = TestClient(api.app, client=REMOTE)
    for _ in range(3):
        assert guesser.get("/api/health", auth=("geo", "guess")).status_code == 401
    assert guesser.get("/api/health", auth=("geo", "s3cret")).status_code == 429

    browser = TestClient(api.app, client=("198.51.100.7", 50000))
    for _ in range(5):  # a browser's credential-less first request is not a guess
        assert browser.get("/api/health").status_code == 401
    assert browser.get("/api/health", auth=("geo", "s3cret")).status_code == 200


def test_busy_place_search_answers_429(local, monkeypatch):
    monkeypatch.setattr(api.locate, "MAX_WAITING", 0)
    r = local.post("/api/locate", json={"query": "zz unlikely test place"})
    assert r.status_code == 429


@pytest.mark.parametrize("path", ["../api.py", "..\\..\\x.tif", "C:/Windows/win.ini", "/etc/passwd"])
def test_drone_paths_confined_to_drone_folder(local, path):
    for route in ("/api/drone/inspect", "/api/drone/detect"):
        r = local.post(route, json={"before": path, "after": "ok.tif"})
        assert r.status_code == 400
        assert "drone folder" in r.json()["detail"]
