"""Job persistence, pruning, the queue limit and error hygiene, against a
temporary folder."""
import threading
import time

import pytest

import jobs

FC = {"type": "FeatureCollection",
      "features": [{"type": "Feature", "geometry": None, "properties": {}}],
      "properties": {"stats": {"warnings": []}}}


def wait(store, job, timeout=5.0):
    end = time.time() + timeout
    while store.get(job.id).status in (jobs.PENDING, jobs.RUNNING):
        assert time.time() < end, "job did not finish"
        time.sleep(0.01)
    return store.get(job.id)


def test_finished_run_survives_restart(tmp_path):
    store = jobs.JobStore(tmp_path)
    job = wait(store, store.submit(lambda: FC, {"q": "x"}))
    assert job.status == jobs.DONE and job.feature_count == 1

    again = jobs.JobStore(tmp_path)
    assert again.get(job.id).summary()["feature_count"] == 1
    assert again.get(job.id).params == {"q": "x"}
    assert again.result(job.id) == FC


def test_unfinished_run_reported_failed_after_restart(tmp_path):
    store = jobs.JobStore(tmp_path)
    gate = threading.Event()
    job = store.submit(lambda: gate.wait(5) and FC, {})
    try:
        # Reload only once the worker has saved "running" and is blocked, so the
        # second store never reads a file the first is replacing at that moment.
        while store.get(job.id).status == jobs.PENDING:
            time.sleep(0.01)
        again = jobs.JobStore(tmp_path)
        assert again.get(job.id).status == jobs.FAILED
        assert "restart" in again.get(job.id).error
    finally:
        gate.set()


def test_result_lookup_only_for_known_ids(tmp_path):
    store = jobs.JobStore(tmp_path)
    (tmp_path / "x.result.json").write_text("{}", encoding="utf-8")
    assert store.result("x") is None
    assert store.result("../jobs") is None


def test_oldest_finished_runs_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_JOBS", 2)
    store = jobs.JobStore(tmp_path)
    ids = [wait(store, store.submit(lambda: FC, {})).id for _ in range(3)]
    assert store.get(ids[0]) is None
    assert not (tmp_path / f"{ids[0]}.json").exists()
    assert not (tmp_path / f"{ids[0]}.result.json").exists()
    assert store.get(ids[1]) and store.get(ids[2])


def test_queue_refuses_past_max_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_PENDING", 1)
    store = jobs.JobStore(tmp_path, max_workers=1)
    gate = threading.Event()
    try:
        first = store.submit(lambda: gate.wait(5) and FC, {})
        while store.get(first.id).status == jobs.PENDING:  # let it take the worker
            time.sleep(0.01)
        store.submit(lambda: FC, {})  # waits for the worker
        with pytest.raises(jobs.QueueFull):
            store.submit(lambda: FC, {})
    finally:
        gate.set()


def test_failure_message_hides_signed_url_tokens(tmp_path):
    store = jobs.JobStore(tmp_path)

    def boom():
        raise OSError("read failed: https://acct.blob.core.windows.net/a.tif?st=2026&sig=SECRET")

    job = wait(store, store.submit(boom, {}))
    assert job.status == jobs.FAILED
    assert "SECRET" not in job.error
    assert "a.tif?…" in job.error
