"""Durable SQLite job state with inline and arq queue adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from .contracts import JobAttempt, JobStatusResponse

JobStatus = Literal[
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "timed_out",
]
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
MAX_SUMMARY = 2_000
MAX_RESULT_BYTES = 32_000
logger = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(timezone.utc)


def setting_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


JobResponse = JobStatusResponse


def run_async_from_sync(factory: Callable[[], Any]) -> Any:
    """Run an async queue operation from sync API/MCP code, even inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: list[Any] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=runner, name="codetrail-job-enqueue")
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


@dataclass(frozen=True)
class JobConfig:
    lease_seconds: int
    max_retries: int
    queue_timeout: int
    attempt_timeout: int
    total_timeout: int
    worker_concurrency: int

    @classmethod
    def from_environment(cls) -> "JobConfig":
        return cls(
            lease_seconds=setting_int("CODETRAIL_JOB_LEASE_SECONDS", 60, 5, 3600),
            max_retries=setting_int("CODETRAIL_JOB_MAX_RETRIES", 2, 0, 10),
            queue_timeout=setting_int("CODETRAIL_JOB_QUEUE_TIMEOUT", 3600, 1, 86400),
            attempt_timeout=setting_int("CODETRAIL_JOB_ATTEMPT_TIMEOUT", 600, 1, 3600),
            total_timeout=setting_int("CODETRAIL_JOB_TOTAL_TIMEOUT", 1800, 1, 10800),
            worker_concurrency=setting_int("CODETRAIL_WORKER_CONCURRENCY", 2, 1, 32),
        )


class JobMerged(Exception):
    def __init__(self, job_id: str):
        super().__init__(f"job merged into {job_id}")
        self.job_id = job_id


class JobStore:
    def __init__(self, path: str, clock: Callable[[], datetime] = now) -> None:
        self.path = path
        self.clock = clock
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, project_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, progress TEXT NOT NULL,
                attempts TEXT NOT NULL, result TEXT, error_code TEXT, summary TEXT,
                idempotency_key TEXT UNIQUE, dedupe_key TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, deadline_at TEXT NOT NULL, execution_deadline_at TEXT, lease_expires_at TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                merged_into TEXT,
                enqueue_seq INTEGER NOT NULL DEFAULT 0,
                retry_after_at TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "execution_deadline_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN execution_deadline_at TEXT")
            if "merged_into" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN merged_into TEXT")
            if "enqueue_seq" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN enqueue_seq INTEGER NOT NULL DEFAULT 0")
            if "retry_after_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN retry_after_at TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_dedupe_active ON jobs(dedupe_key,status)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS job_build_keys (build_key TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def create(
        self,
        kind: str,
        project_id: str | None,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None,
        dedupe_key: str | None,
        wait_seconds: int,
    ) -> JobResponse:
        stamp = self.clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                row = db.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if row:
                    project_matches = row["project_id"] == project_id or (
                        kind == "github_import" and project_id is None
                    )
                    if (
                        row["kind"] != kind
                        or not project_matches
                        or json.loads(row["payload"]) != payload
                    ):
                        db.execute("ROLLBACK")
                        raise JobConflict("idempotency key conflicts with a different job request")
                    db.execute("COMMIT")
                    return self._model(row)
            if dedupe_key:
                row = db.execute(
                    """
                    SELECT * FROM jobs
                    WHERE dedupe_key=?
                      AND status IN ('queued', 'running', 'retry_wait', 'cancel_requested')
                    ORDER BY created_at
                    LIMIT 1
                    """,
                    (dedupe_key,),
                ).fetchone()
                if row:
                    db.execute("COMMIT")
                    return self._model(row)
            job_id = f"job-{uuid4().hex}"
            deadline = stamp + timedelta(seconds=wait_seconds)
            db.execute(
                """
                INSERT INTO jobs(
                    job_id, project_id, kind, payload, status, phase, progress,
                    attempts, idempotency_key, dedupe_key, created_at, updated_at, deadline_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    kind,
                    json.dumps(payload),
                    "queued",
                    "queued",
                    "{}",
                    "[]",
                    idempotency_key,
                    dedupe_key,
                    stamp.isoformat(),
                    stamp.isoformat(),
                    deadline.isoformat(),
                ),
            )
            db.execute("COMMIT")
        logger.info("Job %s status=queued phase=queued", job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> JobResponse:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._model(row)

    def raw(self, job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def transition(
        self,
        job_id: str,
        status: JobStatus,
        *,
        phase: str | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        summary: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
        lease_seconds: int = 0,
        retry_count: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> JobResponse:
        stamp = self.clock()
        bounded = None
        if result is not None:
            encoded = json.dumps(result, separators=(",", ":"))
            if len(encoded.encode("utf-8")) <= MAX_RESULT_BYTES:
                bounded = encoded
            else:
                attempt = result.get("attempt") if isinstance(result.get("attempt"), dict) else {}
                bounded = json.dumps(
                    {
                        "truncated": True,
                        "project_id": (
                            result.get("project_id")
                            or (result.get("project") or {}).get("project_id")
                            if isinstance(result.get("project"), dict)
                            else result.get("project_id")
                        ),
                        "attempt_id": attempt.get("attempt_id"),
                        "index_id": (
                            attempt.get("index_id")
                            or (result.get("index") or {}).get("index", {}).get("index_id")
                            if isinstance(result.get("index"), dict)
                            else attempt.get("index_id")
                        ),
                        "summary": "Result exceeded the 32 KB job result limit",
                    }
                )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                UPDATE jobs SET status=?, phase=COALESCE(?, phase), result=COALESCE(?, result),
                    error_code=?, summary=?, attempts=COALESCE(?, attempts), updated_at=?,
                    lease_expires_at=?, retry_count=COALESCE(?, retry_count), retry_after_at=?
                WHERE job_id=?
                """,
                (
                    status,
                    phase,
                    bounded,
                    error_code,
                    (summary or "")[:MAX_SUMMARY] or None,
                    None if attempts is None else json.dumps(attempts),
                    stamp.isoformat(),
                    (
                        (stamp + timedelta(seconds=lease_seconds)).isoformat()
                        if lease_seconds
                        else None
                    ),
                    retry_count,
                    (
                        (stamp + timedelta(seconds=retry_after_seconds)).isoformat()
                        if retry_after_seconds is not None
                        else None
                    ),
                    job_id,
                ),
            )
            if status in TERMINAL:
                db.execute("DELETE FROM job_build_keys WHERE job_id=?", (job_id,))
                db.execute(
                    """
                    UPDATE jobs SET status=?, phase=?, result=?, error_code=?, summary=?,
                        updated_at=?, lease_expires_at=NULL, retry_after_at=NULL
                    WHERE merged_into=? AND status='retry_wait' AND phase='merged_wait'
                    """,
                    (
                        status,
                        f"merged_{status}",
                        bounded,
                        error_code,
                        (summary or "")[:MAX_SUMMARY] or None,
                        stamp.isoformat(),
                        job_id,
                    ),
                )
            db.execute("COMMIT")
        response = self.get(job_id)
        logger.info(
            "Job %s status=%s phase=%s error_code=%s",
            job_id,
            response.status,
            response.phase,
            response.error_code or "none",
        )
        return response

    def claim_build_key(self, job_id: str, build_key: str) -> str | None:
        """Atomically claim a resolved repository/commit build key."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT job_id FROM job_build_keys WHERE build_key=?", (build_key,)
            ).fetchone()
            if row is not None and row["job_id"] != job_id:
                target = row["job_id"]
                db.execute(
                    """
                    UPDATE jobs SET merged_into=?, status='retry_wait', phase='merged_wait', updated_at=?
                    WHERE job_id=? AND status='running'
                    """,
                    (target, self.clock().isoformat(), job_id),
                )
                db.execute("COMMIT")
                return target
            db.execute(
                "INSERT OR IGNORE INTO job_build_keys(build_key,job_id) VALUES(?,?)",
                (build_key, job_id),
            )
            db.execute("COMMIT")
        return None

    def next_enqueue_sequence(self, job_id: str) -> int:
        with self._connect() as db:
            db.execute("UPDATE jobs SET enqueue_seq=enqueue_seq+1 WHERE job_id=?", (job_id,))
            row = db.execute("SELECT enqueue_seq FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return int(row["enqueue_seq"])

    def update_attempts(self, job_id: str, attempts: list[dict[str, Any]]) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET attempts=?,updated_at=? WHERE job_id=?",
                (json.dumps(attempts), self.clock().isoformat(), job_id),
            )

    def claim(
        self, job_id: str, attempts: list[dict[str, Any]], lease_seconds: int, total_timeout: int
    ) -> bool:
        stamp = self.clock()
        retry_count = int(attempts[-1].get("retry_count", 0)) if attempts else 0
        with self._connect() as db:
            total_deadline = stamp + timedelta(seconds=total_timeout)
            cursor = db.execute(
                """
                UPDATE jobs SET status='running', phase='resolving', attempts=?, retry_count=?,
                    updated_at=?, lease_expires_at=?,
                    execution_deadline_at=COALESCE(execution_deadline_at, ?), retry_after_at=NULL
                WHERE job_id=? AND status IN ('queued', 'retry_wait') AND merged_into IS NULL
                """,
                (
                    json.dumps(attempts),
                    retry_count,
                    stamp.isoformat(),
                    (stamp + timedelta(seconds=lease_seconds)).isoformat(),
                    total_deadline.isoformat(),
                    job_id,
                ),
            )
        claimed = cursor.rowcount == 1
        if claimed:
            logger.info("Job %s status=running phase=resolving", job_id)
        return claimed

    def heartbeat(self, job_id: str, lease_seconds: int) -> None:
        stamp = self.clock()
        with self._connect() as db:
            db.execute(
                """
                UPDATE jobs SET lease_expires_at=?, updated_at=?
                WHERE job_id=? AND status IN ('running', 'cancel_requested')
                """,
                ((stamp + timedelta(seconds=lease_seconds)).isoformat(), stamp.isoformat(), job_id),
            )

    def bind_project(self, job_id: str, project_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET project_id=?,updated_at=? WHERE job_id=? AND project_id IS NULL",
                (project_id, self.clock().isoformat(), job_id),
            )

    def phase(self, job_id: str, phase: str, lease_seconds: int) -> None:
        stamp = self.clock()
        with self._connect() as db:
            row = db.execute(
                "SELECT attempts FROM jobs WHERE job_id=? AND status='running'", (job_id,)
            ).fetchone()
            if row is None:
                return
            attempts = json.loads(row["attempts"])
            if attempts:
                attempts[-1]["phase"] = phase
            db.execute(
                """
                UPDATE jobs SET phase=?, attempts=?, updated_at=?, lease_expires_at=?
                WHERE job_id=? AND status='running'
                """,
                (
                    phase,
                    json.dumps(attempts),
                    stamp.isoformat(),
                    (stamp + timedelta(seconds=lease_seconds)).isoformat(),
                    job_id,
                ),
            )

    def cancel(self, job_id: str) -> JobResponse:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            status = row["status"]
            if status == "queued":
                db.execute(
                    """
                    UPDATE jobs SET status='cancelled', phase='cancelled', updated_at=?,
                        summary='Cancelled before execution'
                    WHERE job_id=?
                    """,
                    (self.clock().isoformat(), job_id),
                )
            elif status == "running":
                db.execute(
                    """
                    UPDATE jobs SET status='cancel_requested', phase='cancel_requested', updated_at=?
                    WHERE job_id=?
                    """,
                    (self.clock().isoformat(), job_id),
                )
            elif status == "retry_wait":
                db.execute(
                    """
                    UPDATE jobs SET status='cancelled', phase='cancelled', updated_at=?,
                        summary='Cancelled while waiting to retry'
                    WHERE job_id=?
                    """,
                    (self.clock().isoformat(), job_id),
                )
            db.execute("COMMIT")
        response = self.get(job_id)
        logger.info("Job %s status=%s phase=%s", job_id, response.status, response.phase)
        return response

    @staticmethod
    def _model(row: sqlite3.Row) -> JobResponse:
        return JobResponse(
            job_id=row["job_id"],
            project_id=row["project_id"],
            kind=row["kind"],
            status=row["status"],
            phase=row["phase"],
            progress=json.loads(row["progress"]),
            attempts=json.loads(row["attempts"]),
            result=None if row["result"] is None else json.loads(row["result"]),
            error_code=row["error_code"],
            summary=row["summary"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deadline_at=row["deadline_at"],
            execution_deadline_at=row["execution_deadline_at"],
            merged_into=row["merged_into"],
        )


class JobControl:
    """Cooperative task control checked by long-running service checkpoints."""

    def __init__(
        self, store: JobStore, job_id: str, config: JobConfig, total_deadline_at: str | None = None
    ):
        self.store, self.job_id = store, job_id
        self.config = config
        self.execution_deadline: float | None = None
        self.attempt_deadline: float | None = None
        self.total_deadline = (
            datetime.fromisoformat(total_deadline_at).timestamp() if total_deadline_at else None
        )

    def check(self) -> None:
        job = self.store.get(self.job_id)
        if job.status == "cancel_requested":
            from .indexing import IndexingAborted

            raise IndexingAborted("index_cancelled")
        current_time = time.monotonic()
        if self.attempt_deadline is not None and current_time > self.attempt_deadline:
            raise JobTimedOut
        if self.execution_deadline is not None and current_time > self.execution_deadline:
            from .indexing import IndexingAborted

            raise IndexingAborted("index_timeout")
        if self.total_deadline is not None and now().timestamp() > self.total_deadline:
            raise JobTimedOut

    def start(self, timeout_seconds: float) -> None:
        if self.attempt_deadline is None:
            self.attempt_deadline = time.monotonic() + self.config.attempt_timeout
        phase_deadline = time.monotonic() + timeout_seconds
        self.execution_deadline = min(self.attempt_deadline, phase_deadline)
        self.check()

    def set_phase(self, phase: str) -> None:
        self.store.phase(self.job_id, phase, self.config.lease_seconds)

    def cancel(self) -> None:
        self.store.cancel(self.job_id)

    def claim_build_key(self, build_key: str) -> None:
        target = self.store.claim_build_key(self.job_id, build_key)
        if target is not None:
            raise JobMerged(target)


class JobTimedOut(Exception):
    pass


class JobConflict(Exception):
    pass


class JobFailure(Exception):
    def __init__(self, code: str, message: str, result: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.result = result


class JobService:
    def __init__(
        self,
        path: str,
        executor: Callable[[dict[str, Any], JobControl], dict[str, Any]],
        *,
        redis_url: str | None = None,
        start_background: bool = True,
        clock: Callable[[], datetime] = now,
    ):
        self.config = JobConfig.from_environment()
        self.store = JobStore(path, clock=clock)
        self.executor = executor
        self.redis_url = redis_url or os.getenv("CODETRAIL_REDIS_URL")
        self.start_background = start_background
        self.pool = ThreadPoolExecutor(max_workers=self.config.worker_concurrency)
        self._recovery_stop = threading.Event()
        self._recovery_thread: threading.Thread | None = None
        if start_background and not self.redis_url:
            self._recovery_thread = threading.Thread(
                target=self._recovery_loop,
                name="codetrail-job-recovery",
                daemon=True,
            )
            self._recovery_thread.start()

    def submit(
        self,
        kind: str,
        project_id: str | None,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        dedupe_key: str | None = None,
    ) -> JobResponse:
        job = self.store.create(
            kind,
            project_id,
            payload,
            idempotency_key=idempotency_key,
            dedupe_key=dedupe_key,
            wait_seconds=self.config.queue_timeout,
        )
        if job.status == "queued" and self.start_background:
            if self.redis_url:
                self.submit_existing(job.job_id)
            else:
                self.pool.submit(self.run, job.job_id, self.config.max_retries)
        return job

    def run(self, job_id: str, max_retries: int | None = None) -> None:
        max_retries = self.config.max_retries if max_retries is None else max_retries
        for attempt_no in range(max_retries + 1):
            job = self.store.get(job_id)
            raw = self.store.raw(job_id)
            if raw["merged_into"] is not None:
                return
            if job.status in TERMINAL or job.status == "cancel_requested":
                if job.status == "cancel_requested":
                    self.store.transition(job_id, "cancelled", phase="cancelled")
                return
            if raw["retry_after_at"] is not None and self.store.clock() < datetime.fromisoformat(
                raw["retry_after_at"]
            ):
                return
            if self.store.clock() > job.deadline_at:
                self.store.transition(
                    job_id,
                    "timed_out",
                    phase="timed_out",
                    error_code="queue_wait_timeout",
                    summary="Job exceeded its queue wait deadline",
                )
                return
            raw = self.store.raw(job_id)
            if raw["execution_deadline_at"] and self.store.clock() > datetime.fromisoformat(
                raw["execution_deadline_at"]
            ):
                self.store.transition(
                    job_id,
                    "timed_out",
                    phase="timed_out",
                    error_code="job_total_timeout",
                    summary="Job exceeded its total execution budget",
                )
                return
            history = [item.model_dump(mode="json") for item in job.attempts]
            started = self.store.clock()
            current = {
                "attempt": len(history) + 1,
                "started_at": started.isoformat(),
                "ended_at": None,
                "error_code": None,
                "retry_count": int(self.store.raw(job_id)["retry_count"]),
                "phase": "resolving",
            }
            history.append(current)
            if not self.store.claim(
                job_id, history, self.config.lease_seconds, self.config.total_timeout
            ):
                return
            try:
                control = JobControl(
                    self.store,
                    job_id,
                    self.config,
                    self.store.raw(job_id)["execution_deadline_at"],
                )
                control.start(float(self.config.attempt_timeout))
                heartbeat_stop = threading.Event()
                lease_seconds = self.config.lease_seconds
                heartbeat_thread = threading.Thread(
                    target=lambda: self._heartbeat_loop(job_id, heartbeat_stop, lease_seconds),
                    name=f"codetrail-job-heartbeat-{job_id}",
                    daemon=True,
                )
                heartbeat_thread.start()
                try:
                    result = self.executor(self.store.raw(job_id), control)
                finally:
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=1)
            except JobMerged:
                current.update(ended_at=self.store.clock().isoformat(), error_code="merged")
                self.store.update_attempts(job_id, history)
                return
            except JobTimedOut:
                current.update(ended_at=self.store.clock().isoformat(), error_code="job_timeout")
                self.store.transition(
                    job_id,
                    "timed_out",
                    phase="timed_out",
                    attempts=history,
                    error_code="job_timeout",
                )
                return
            except Exception as exc:
                code = getattr(exc, "code", "internal_error")
                current.update(ended_at=self.store.clock().isoformat(), error_code=code)
                if code == "index_cancelled":
                    self.store.transition(
                        job_id,
                        "cancelled",
                        phase="cancelled",
                        attempts=history,
                        error_code="cancelled",
                        result=getattr(exc, "result", None),
                    )
                    return
                if code == "index_timeout":
                    self.store.transition(
                        job_id,
                        "timed_out",
                        phase="timed_out",
                        attempts=history,
                        error_code=code,
                        summary=str(exc),
                        result=getattr(exc, "result", None),
                    )
                    return
                if code in {
                    "not_found",
                    "invalid_input",
                    "conflict",
                    "forbidden",
                    "budget_exceeded",
                }:
                    self.store.transition(
                        job_id,
                        "failed",
                        phase="failed",
                        attempts=history,
                        error_code=code,
                        summary=str(exc),
                        result=getattr(exc, "result", None),
                    )
                    return
                retryable = code in {
                    "download_timeout",
                    "connection_error",
                    "rate_limited",
                    "temporarily_unavailable",
                }
                if retryable and int(self.store.raw(job_id)["retry_count"]) < max_retries:
                    current["retry_count"] = int(self.store.raw(job_id)["retry_count"]) + 1
                    delay = min(2**attempt_no, 10)
                    next_retry = int(self.store.raw(job_id)["retry_count"]) + 1
                    self.store.transition(
                        job_id,
                        "retry_wait",
                        phase="retry_wait",
                        attempts=history,
                        error_code=code,
                        summary=str(exc),
                        retry_count=next_retry,
                        retry_after_seconds=delay if self.redis_url else None,
                    )
                    if self.redis_url:
                        self._enqueue(job_id, defer_seconds=delay)
                        return
                    time.sleep(delay)
                    continue
                self.store.transition(
                    job_id,
                    "failed",
                    phase="failed",
                    attempts=history,
                    error_code=code,
                    summary=str(exc),
                    result=getattr(exc, "result", None),
                )
                return
            current.update(ended_at=self.store.clock().isoformat(), phase="committing")
            final = self.store.get(job_id)
            if final.status == "cancel_requested":
                current["error_code"] = "cancel_raced_completion"
                self.store.transition(
                    job_id,
                    "succeeded",
                    phase="succeeded",
                    attempts=history,
                    result=result,
                    summary="Task completed before cancellation was applied",
                )
            else:
                self.store.transition(
                    job_id, "succeeded", phase="succeeded", attempts=history, result=result
                )
            return

    def _heartbeat_loop(self, job_id: str, stop: threading.Event, lease_seconds: int) -> None:
        while not stop.wait(max(1, lease_seconds // 3)):
            self.store.heartbeat(job_id, lease_seconds)

    def recover_expired(self) -> int:
        with self.store._connect() as db:
            rows = db.execute(
                """
                SELECT job_id, status, retry_count, attempts
                FROM jobs
                WHERE status IN ('running', 'cancel_requested') AND lease_expires_at < ?
                """,
                (self.store.clock().isoformat(),),
            ).fetchall()
            queued = db.execute(
                """
                SELECT job_id FROM jobs
                WHERE merged_into IS NULL
                  AND (status='queued' OR (status='retry_wait' AND (retry_after_at IS NULL OR retry_after_at<=?)))
                """,
                (self.store.clock().isoformat(),),
            ).fetchall()
        if self.start_background and self.redis_url:
            for row in queued:
                try:
                    self.submit_existing(row["job_id"])
                except Exception:
                    # Redis availability is retried on the next worker scan.
                    pass
        for row in queued:
            if self.start_background and not self.redis_url:
                self.pool.submit(self.run, row["job_id"], self.config.max_retries)
        for row in rows:
            if row["status"] == "cancel_requested":
                self.store.transition(
                    row["job_id"],
                    "cancelled",
                    phase="cancelled",
                    error_code="cancelled",
                    summary="Worker lease expired after cancellation was requested",
                )
                continue
            max_retries = self.config.max_retries
            attempts = json.loads(row["attempts"])
            if attempts:
                attempts[-1]["ended_at"] = self.store.clock().isoformat()
                attempts[-1]["error_code"] = "worker_lost"
            if row["retry_count"] < max_retries:
                if attempts:
                    attempts[-1]["retry_count"] = row["retry_count"] + 1
                self.store.transition(
                    row["job_id"],
                    "queued",
                    phase="queued",
                    error_code="worker_lost",
                    attempts=attempts,
                    retry_count=row["retry_count"] + 1,
                )
                if not self.start_background:
                    continue
                if self.redis_url:
                    self.submit_existing(row["job_id"])
                else:
                    self.pool.submit(self.run, row["job_id"], max_retries)
            else:
                self.store.transition(
                    row["job_id"],
                    "failed",
                    phase="failed",
                    error_code="worker_lost",
                    summary="Worker lease expired",
                    attempts=attempts,
                )
        return len(rows)

    def submit_existing(self, job_id: str) -> None:
        self._enqueue(job_id)

    def _enqueue(self, job_id: str, *, defer_seconds: int = 0) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        async def enqueue() -> None:
            pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
            sequence = self.store.next_enqueue_sequence(job_id)
            try:
                options: dict[str, Any] = {"_job_id": f"{job_id}:{sequence}"}
                if defer_seconds:
                    options["_defer_by"] = timedelta(seconds=defer_seconds)
                queued = await pool.enqueue_job("run_job", job_id, **options)
                if queued is None:
                    raw = self.store.raw(job_id)
                    if raw["status"] not in {
                        "running",
                        "succeeded",
                        "failed",
                        "cancelled",
                        "timed_out",
                    }:
                        raise RuntimeError(
                            f"arq declined job {job_id} dispatch {sequence}; job remains recoverable"
                        )
            finally:
                await pool.close()

        run_async_from_sync(enqueue)

    def _recovery_loop(self, interval: float | None = None) -> None:
        if interval is None:
            interval = max(1.0, min(30.0, self.config.lease_seconds / 3))
        while not self._recovery_stop.wait(interval):
            try:
                self.recover_expired()
            except Exception:
                logger.warning("Job lease recovery failed; it will be retried", exc_info=True)

    def close(self) -> None:
        self._recovery_stop.set()
        if self._recovery_thread is not None:
            self._recovery_thread.join(timeout=1)
        self.pool.shutdown(wait=False, cancel_futures=True)
