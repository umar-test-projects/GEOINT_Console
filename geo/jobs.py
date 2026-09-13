"""In-process background jobs, kept on disk.

A cold two-date fused comparison takes ~52 s, which is far past what an HTTP
request should hold open, so detection runs in a worker thread and the client
polls. Cached re-runs come back in ~5 s.

Each run is two files in the jobs folder: `<id>.json` (status, parameters,
headline stats) and `<id>.result.json` (the GeoJSON). Statuses live in memory;
results are read from disk on request, so memory does not grow with the number
of runs, and a restart keeps every finished run and every shared `?job=` link.

ponytail: a dict, a thread pool and JSON files. No Celery, no Redis, no
database for a single-machine tool. Swap in a real queue if this ever needs to
scale past one machine. Run ONE server process: statuses live in this
process's memory, so `uvicorn --workers N` would answer a poll from a process
that never saw the run.
"""
from __future__ import annotations

import json
import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import bridge

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"

#: Runs kept. Past this, the oldest finished runs and their files are deleted.
MAX_JOBS = 100
#: Runs waiting for a worker. Past this, submit refuses rather than stacking up
#: an unbounded backlog of multi-minute remote reads.
MAX_PENDING = 8


class QueueFull(RuntimeError):
    """Too many runs are already waiting for a worker."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_QUERY_STRING = re.compile(r"\?[^\s'\"<>]*")


def public_error(exc: BaseException, limit: int = 400) -> str:
    """An exception as a message safe to show a user.

    Remote read failures quote the URL they failed on, and Planetary Computer
    URLs carry a signed access token in the query string, so query strings are
    cut before the message leaves the server. The traceback goes to the log.
    """
    msg = _QUERY_STRING.sub("?…", f"{type(exc).__name__}: {exc}")
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


@dataclass
class Job:
    id: str
    status: str = PENDING
    error: str | None = None
    params: dict = field(default_factory=dict)
    created: str = ""
    finished: str | None = None
    feature_count: int | None = None
    stats: dict | None = None

    def summary(self) -> dict:
        """Status without the payload, so polling stays cheap."""
        out = {
            "job_id": self.id,
            "status": self.status,
            "created": self.created,
            "finished": self.finished,
            "params": self.params,
        }
        if self.error:
            out["error"] = self.error
        if self.status == DONE:
            out["feature_count"] = self.feature_count
            out["stats"] = self.stats
        return out


class JobStore:
    def __init__(self, root: Path, max_workers: int = 2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._load()

    def _meta_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def _result_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.result.json"

    @staticmethod
    def _write(path: Path, obj: Any) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(path)  # atomic: a reader never sees half a file

    def _save(self, job: Job) -> None:
        self._write(self._meta_path(job.id), asdict(job))

    def _load(self) -> None:
        """Pick up runs from before a restart, oldest first.

        A run still going when the server stopped cannot resume, so it is
        reported failed rather than left spinning in a client that polls it.
        """
        found = []
        for path in self.root.glob("*.json"):
            if path.name.endswith(".result.json"):
                continue
            try:
                found.append(Job(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue  # unreadable metadata: skip it rather than refuse to start
        for job in sorted(found, key=lambda j: j.created):
            if job.status in (PENDING, RUNNING):
                job.status, job.finished = FAILED, _now()
                job.error = "interrupted by a server restart; run it again"
                self._save(job)
            self._jobs[job.id] = job

    def submit(self, fn: Callable[[], Any], params: dict) -> Job:
        with self._lock:
            waiting = sum(j.status == PENDING for j in self._jobs.values())
            if waiting >= MAX_PENDING:
                raise QueueFull(f"{waiting} runs are already waiting; try again when one finishes")
            job = Job(id=uuid.uuid4().hex[:12], params=params, created=_now())
            self._jobs[job.id] = job
            self._save(job)
            self._prune()
        self._pool.submit(self._run, job, fn)
        return job

    def _prune(self) -> None:
        """Drop the oldest finished runs past MAX_JOBS. Caller holds the lock."""
        excess = len(self._jobs) - MAX_JOBS
        if excess <= 0:
            return
        # sorted() is stable, so runs created in the same second keep insertion order.
        finished = sorted((j for j in self._jobs.values() if j.status in (DONE, FAILED)),
                          key=lambda j: j.created)
        for job in finished[:excess]:
            del self._jobs[job.id]
            self._meta_path(job.id).unlink(missing_ok=True)
            self._result_path(job.id).unlink(missing_ok=True)

    def _run(self, job: Job, fn: Callable[[], Any]) -> None:
        with self._lock:
            job.status = RUNNING
            self._save(job)
        try:
            result = fn()
            # Result before the status that points at it: a crash in between
            # leaves the run unfinished (failed on restart), never done-but-empty.
            self._write(self._result_path(job.id), result)
        except Exception as exc:  # noqa: BLE001 - surface the failure, do not crash the worker
            traceback.print_exc()
            with self._lock:
                job.status, job.finished = FAILED, _now()
                job.error = public_error(exc)
                self._save(job)
            return
        with self._lock:
            job.feature_count = len(result.get("features", []))
            job.stats = result.get("properties", {}).get("stats")
            job.status, job.finished = DONE, _now()
            self._save(job)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)

    def result(self, job_id: str) -> dict | None:
        """A finished run's GeoJSON, read from disk; None if it is gone.

        Only ids already in the store are looked up, so a request can never
        steer this at another file.
        """
        with self._lock:
            if job_id not in self._jobs:
                return None
        try:
            return json.loads(self._result_path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


STORE = JobStore(bridge.ROOT / bridge.SETTINGS["paths"].get("jobs_dir", "runs"))
