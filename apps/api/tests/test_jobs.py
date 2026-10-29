from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timedelta, timezone

import pytest

from app.jobs import JobConflict, JobControl, JobService
from app.main import create_app
from app.factory import create_services as build_services
from app.service import WorkspaceService
from app import worker


@pytest.fixture(autouse=True)
def _clear_job_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODETRAIL_REDIS_URL",
        "CODETRAIL_DATA_DIR",
        "CODETRAIL_WORKER_CONCURRENCY",
        "CODETRAIL_JOB_MAX_RETRIES",
        "CODETRAIL_JOB_ATTEMPT_TIMEOUT",
        "CODETRAIL_JOB_TOTAL_TIMEOUT",
        "CODETRAIL_JOB_QUEUE_TIMEOUT",
        "CODETRAIL_JOB_LEASE_SECONDS",
        "CODETRAIL_GITHUB_IMPORT_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def service(tmp_path: Path, executor, **kwargs) -> JobService:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return JobService(
        str(tmp_path / "registry.sqlite3"), executor, start_background=False, **kwargs
    )


def test_job_submit_idempotency_and_active_deduplication(tmp_path: Path) -> None:
    jobs = service(tmp_path, lambda record, control: {"ok": True})
    first = jobs.submit(
        "github_import", None, {"url": "https://github.com/a/b"}, idempotency_key="key"
    )
    assert first.status == "queued"
    with pytest.raises(JobConflict):
        jobs.submit("github_import", None, {"url": "different"}, idempotency_key="key")
    assert (
        jobs.submit(
            "github_import", None, {"url": "https://github.com/a/b"}, idempotency_key="key"
        ).job_id
        == first.job_id
    )
    deduped = jobs.submit("github_import", None, {}, dedupe_key="same-commit")
    assert deduped.job_id != first.job_id
    assert (
        jobs.submit("github_import", None, {"other": True}, dedupe_key="same-commit").job_id
        == deduped.job_id
    )


def test_job_success_records_attempt_and_cancel_queued(tmp_path: Path) -> None:
    jobs = service(tmp_path, lambda record, control: {"index_id": "idx-1"})
    job = jobs.submit("project_index_refresh", "project-1", {"index_id": "idx-1"})
    jobs.run(job.job_id)
    finished = jobs.store.get(job.job_id)
    assert finished.status == "succeeded"
    assert finished.result == {"index_id": "idx-1"}
    assert len(finished.attempts) == 1 and finished.attempts[0].ended_at is not None
    queued = jobs.submit("github_import", None, {})
    assert jobs.store.cancel(queued.job_id).status == "cancelled"


def test_submit_returns_immediately_and_cancel_loses_to_completion(tmp_path: Path) -> None:
    calls = 0
    jobs: JobService

    def executor(record, control):
        nonlocal calls
        calls += 1
        jobs.store.cancel(str(record["job_id"]))
        return {"completed": True}

    jobs = service(tmp_path, executor)
    submitted = jobs.submit("github_import", None, {})
    assert submitted.job_id.startswith("job-")
    assert submitted.status == "queued"
    assert calls == 0
    jobs.run(submitted.job_id)
    completed = jobs.store.get(submitted.job_id)
    assert calls == 1 and completed.status == "succeeded"


def test_retry_is_bounded_and_non_retryable_fails_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODETRAIL_JOB_MAX_RETRIES", "2")
    calls = 0

    def transient(record, control):
        nonlocal calls
        calls += 1
        if calls < 4:
            error = RuntimeError("temporary")
            error.code = "connection_error"
            raise error
        return {"ok": True}

    jobs = service(tmp_path / "retry", transient)
    monkeypatch.setattr("app.jobs.time.sleep", lambda _seconds: None)
    job = jobs.submit("github_import", None, {})
    jobs.run(job.job_id, max_retries=2)
    assert jobs.store.get(job.job_id).status == "failed"
    assert calls == 3

    calls = 0

    def invalid(record, control):
        nonlocal calls
        calls += 1
        error = RuntimeError("bad ref")
        error.code = "invalid_input"
        raise error

    jobs = service(tmp_path / "invalid", invalid)
    job = jobs.submit("github_import", None, {})
    jobs.run(job.job_id, max_retries=2)
    assert jobs.store.get(job.job_id).status == "failed"
    assert calls == 1

    calls = 0

    def exhausted_budget(record, control):
        nonlocal calls
        calls += 1
        error = RuntimeError("rate limit wait budget exhausted")
        error.code = "budget_exceeded"
        raise error

    jobs = service(tmp_path / "budget", exhausted_budget)
    job = jobs.submit("github_import", None, {})
    jobs.run(job.job_id, max_retries=2)
    assert jobs.store.get(job.job_id).status == "failed"
    assert calls == 1


def test_cancel_requested_during_execution_wins_at_checkpoint(tmp_path: Path) -> None:
    jobs: JobService

    def executor(record, control: JobControl):
        jobs.store.cancel(str(record["job_id"]))
        control.check()
        return {"ok": True}

    jobs = service(tmp_path, executor)
    job = jobs.submit("github_import", None, {})
    jobs.run(job.job_id)
    assert jobs.store.get(job.job_id).status == "cancelled"


def test_task_total_timeout_is_distinct_from_queue_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODETRAIL_JOB_TOTAL_TIMEOUT", "1")
    jobs = service(tmp_path, lambda record, control: {"ok": True})
    job = jobs.submit("github_import", None, {})
    jobs.store.claim(
        job.job_id,
        [
            {
                "attempt": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "retry_count": 0,
                "phase": "resolving",
            }
        ],
        30,
        1800,
    )
    with jobs.store._connect() as db:
        db.execute(
            "UPDATE jobs SET execution_deadline_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
            (job.job_id,),
        )
        db.execute("UPDATE jobs SET status='queued' WHERE job_id=?", (job.job_id,))
    jobs.run(job.job_id)
    assert jobs.store.get(job.job_id).status == "timed_out"
    assert jobs.store.get(job.job_id).error_code == "job_total_timeout"


def test_lease_recovery_requeues_or_marks_worker_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODETRAIL_JOB_MAX_RETRIES", "1")
    jobs = service(tmp_path, lambda record, control: {"ok": True})
    retryable = jobs.submit("github_import", None, {})
    jobs.store.claim(
        retryable.job_id,
        [
            {
                "attempt": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "retry_count": 0,
                "phase": "indexing",
            }
        ],
        1,
        1800,
    )
    failed = jobs.submit("github_import", None, {})
    jobs.store.claim(
        failed.job_id,
        [
            {
                "attempt": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "retry_count": 1,
                "phase": "indexing",
            }
        ],
        1,
        1800,
    )
    with jobs.store._connect() as db:
        db.execute(
            "UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id IN (?,?)",
            (retryable.job_id, failed.job_id),
        )
    jobs.recover_expired()
    assert jobs.store.get(retryable.job_id).status == "queued"
    assert jobs.store.get(retryable.job_id).error_code == "worker_lost"
    assert jobs.store.get(failed.job_id).status == "failed"
    assert jobs.store.get(failed.job_id).error_code == "worker_lost"


def test_redis_recovery_only_requeues_missing_dispatches(tmp_path: Path) -> None:
    require_redis = os.getenv("CODETRAIL_REQUIRE_REDIS_TEST") == "1"

    def unavailable(reason: str) -> None:
        if require_redis:
            pytest.fail(reason)
        pytest.skip(reason)

    redis_url = os.getenv("CODETRAIL_TEST_REDIS_URL")
    if not redis_url:
        unavailable("CODETRAIL_TEST_REDIS_URL is not configured")

    try:
        parsed_url = urlsplit(redis_url)
        db_path = parsed_url.path
        if parsed_url.scheme not in {"redis", "rediss"} or not db_path.startswith("/"):
            raise ValueError("URL must explicitly select a Redis database")
        db_number = int(db_path[1:])
        query_db = parse_qs(parsed_url.query).get("db")
        if (
            db_path != f"/{db_number}"
            or db_number <= 0
            or (query_db is not None and query_db != [str(db_number)])
        ):
            raise ValueError("URL must explicitly select a non-zero Redis database")
    except (TypeError, ValueError) as exc:
        unavailable(
            f"CODETRAIL_TEST_REDIS_URL must select a dedicated non-zero Redis database; "
            f"the test flushes it: {exc}"
        )

    from redis import Redis

    try:
        redis = Redis.from_url(redis_url)
    except Exception as exc:
        unavailable(f"CODETRAIL_TEST_REDIS_URL is invalid: {exc}")
    try:
        redis.ping()
    except Exception as exc:
        redis.close()
        unavailable(f"test Redis is unavailable: {exc}")
    try:
        redis.flushdb()
        jobs = JobService(
            str(tmp_path / "registry.sqlite3"),
            lambda record, control: {"ok": True},
            redis_url=redis_url,
            start_background=True,
        )
        try:
            submitted = [jobs.submit("github_import", None, {"n": n}) for n in range(3)]
            job_ids = {job.job_id for job in submitted}
            for _ in range(4):
                jobs.recover_expired()
            queue = redis.zrange("arq:queue", 0, -1)
            assert len(queue) == 3
            assert {member.decode().rsplit(":", 1)[0] for member in queue} == job_ids

            lost = submitted[0].job_id
            raw = jobs.store.raw(lost)
            dispatch_id = f"{lost}:{raw['enqueue_seq']}"
            redis.zrem("arq:queue", dispatch_id)
            redis.delete(f"arq:job:{dispatch_id}")
            for _ in range(4):
                jobs.recover_expired()
            queue = redis.zrange("arq:queue", 0, -1)
            assert len(queue) == 3
            assert sum(member.decode().startswith(f"{lost}:") for member in queue) == 1
            assert sum(
                any(member.decode().startswith(f"{job.job_id}:") for member in queue)
                for job in submitted[1:]
            ) == 2

            # Simulate an API crash after the durable SQLite insert but before
            # the first Redis enqueue. Old rows are recovered; fresh ones get grace.
            lost_first_dispatch = jobs.store.create(
                "github_import",
                None,
                {"crashed_before_enqueue": True},
                idempotency_key=None,
                dedupe_key=None,
                wait_seconds=jobs.config.queue_timeout,
            )
            fresh = jobs.store.create(
                "github_import",
                None,
                {"fresh_before_enqueue": True},
                idempotency_key=None,
                dedupe_key=None,
                wait_seconds=jobs.config.queue_timeout,
            )
            with jobs.store._connect() as db:
                db.execute(
                    "UPDATE jobs SET created_at=? WHERE job_id=?",
                    ((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), lost_first_dispatch.job_id),
                )
            for _ in range(4):
                jobs.recover_expired()
            queue = redis.zrange("arq:queue", 0, -1)
            assert sum(member.decode().startswith(f"{lost_first_dispatch.job_id}:") for member in queue) == 1
            assert jobs.store.raw(lost_first_dispatch.job_id)["enqueue_seq"] == 1
            assert not any(member.decode().startswith(f"{fresh.job_id}:") for member in queue)
            assert jobs.store.raw(fresh.job_id)["enqueue_seq"] == 0

            stale = jobs.store.create(
                "github_import",
                None,
                {"stale_scan": True},
                idempotency_key=None,
                dedupe_key=None,
                wait_seconds=jobs.config.queue_timeout,
            )
            jobs._enqueue(stale.job_id)
            stale_dispatch = f"{stale.job_id}:1"
            redis.zrem("arq:queue", stale_dispatch)
            redis.delete(f"arq:job:{stale_dispatch}")
            dispatch_lock = jobs._redis_dispatch_lock

            class MutatingDispatchLock:
                def __enter__(self):
                    with jobs.store._connect() as db:
                        db.execute(
                            "UPDATE jobs SET status='retry_wait', retry_after_at=?, enqueue_seq=2 WHERE job_id=?",
                            ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), stale.job_id),
                        )

                def __exit__(self, *_args):
                    return False

            jobs._redis_dispatch_lock = MutatingDispatchLock()  # type: ignore[assignment]
            try:
                jobs.recover_expired()
            finally:
                jobs._redis_dispatch_lock = dispatch_lock
            assert jobs.store.raw(stale.job_id)["enqueue_seq"] == 2
            assert not any(
                member.decode().startswith(f"{stale.job_id}:")
                for member in redis.zrange("arq:queue", 0, -1)
            )

            retry = jobs.store.create(
                "github_import",
                None,
                {"retry": True},
                idempotency_key=None,
                dedupe_key=None,
                wait_seconds=jobs.config.queue_timeout,
            )
            jobs._enqueue(retry.job_id, defer_seconds=3600)
            with jobs.store._connect() as db:
                db.execute(
                    "UPDATE jobs SET status='retry_wait', retry_after_at=? WHERE job_id=?",
                    ("2000-01-01T00:00:00+00:00", retry.job_id),
                )
            before = len(redis.zrange("arq:queue", 0, -1))
            for _ in range(4):
                jobs.recover_expired()
            queue = redis.zrange("arq:queue", 0, -1)
            assert len(queue) == before
            assert sum(member.decode().startswith(f"{retry.job_id}:") for member in queue) == 1
        finally:
            jobs.close()
    finally:
        redis.flushdb()
        redis.close()


def test_light_recovery_does_not_submit_duplicate_futures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODETRAIL_WORKER_CONCURRENCY", "1")
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    calls: dict[str, int] = {}

    def executor(record, control):
        job_id = record["job_id"]
        calls[job_id] = calls.get(job_id, 0) + 1
        if record["payload"].get("blocker"):
            entered.set()
            assert release.wait(timeout=2)
        else:
            completed.set()
        return {"ok": True}

    jobs = JobService(
        str(tmp_path / "registry.sqlite3"), executor, start_background=True
    )
    submitted_jobs: list[str] = []
    pool_submit = jobs.pool.submit

    def counting_submit(fn, *args, **kwargs):
        submitted_jobs.append(str(args[0]))
        return pool_submit(fn, *args, **kwargs)

    jobs.pool.submit = counting_submit  # type: ignore[method-assign]
    try:
        blocker = jobs.submit("github_import", None, {"blocker": True})
        assert entered.wait(timeout=1)
        queued = jobs.submit("github_import", None, {"queued": True})
        for _ in range(5):
            jobs.recover_expired()
        assert submitted_jobs.count(queued.job_id) == 1
        assert jobs._scheduled_jobs == {blocker.job_id, queued.job_id}
        release.set()
        assert completed.wait(timeout=2)
        deadline = time.monotonic() + 2
        while jobs.store.get(queued.job_id).status != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert jobs.store.get(blocker.job_id).status == "succeeded"
        assert jobs.store.get(queued.job_id).status == "succeeded"
        assert calls == {blocker.job_id: 1, queued.job_id: 1}
        assert submitted_jobs.count(queued.job_id) == 1
    finally:
        release.set()
        jobs.close()


def test_recovery_loop_retries_after_exception_and_stops_on_close(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    jobs = service(tmp_path, lambda record, control: {"ok": True})
    calls = 0
    recovered_twice = threading.Event()

    def recover_expired() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        recovered_twice.set()
        return 0

    jobs.recover_expired = recover_expired  # type: ignore[method-assign]
    jobs._recovery_thread = threading.Thread(
        target=jobs._recovery_loop,
        kwargs={"interval": 0.001},
        name="codetrail-job-recovery-test",
        daemon=True,
    )
    jobs._recovery_thread.start()

    assert recovered_twice.wait(timeout=1)
    jobs.close()

    assert calls >= 2
    assert "Job lease recovery failed" in caplog.text
    assert jobs._recovery_thread is not None and not jobs._recovery_thread.is_alive()


def test_arq_recovery_cron_retries_after_exception(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    class FlakyJobs:
        def recover_expired(self) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("unable to open database file")
            return 1

    ctx: dict[str, object] = {"jobs": FlakyJobs()}
    asyncio.run(worker.recover_jobs(ctx))
    asyncio.run(worker.recover_jobs(ctx))

    assert calls == 2
    assert "Job lease recovery cron failed" in caplog.text


def test_arq_worker_startup_runs_job_and_recovers_without_redis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODETRAIL_DATA_DIR", str(tmp_path / "worker-data"))
    monkeypatch.setenv("CODETRAIL_REDIS_URL", "redis://unused:6379/0")
    monkeypatch.setenv("CODETRAIL_WORKER_CONCURRENCY", "3")
    monkeypatch.setenv("CODETRAIL_JOB_TOTAL_TIMEOUT", "71")
    monkeypatch.setenv("CODETRAIL_GITHUB_IMPORT_ENABLED", "0")

    def execute(_self, _record, _control):
        return {"index_id": "worker-index"}

    created_services = []

    def create_worker_services():
        service = build_services()
        created_services.append(service)
        return service

    monkeypatch.setattr(WorkspaceService, "_execute_job", execute)
    monkeypatch.setattr(worker, "create_services", create_worker_services)
    ctx: dict[str, object] = {}
    asyncio.run(worker.startup(ctx))

    jobs = ctx["jobs"]
    jobs.start_background = False
    job = jobs.store.create(
        "github_import",
        None,
        {"url": "https://github.com/a/b"},
        idempotency_key=None,
        dedupe_key=None,
        wait_seconds=3600,
    )
    asyncio.run(worker.run_job(ctx, job.job_id))
    finished = jobs.store.get(job.job_id)
    assert finished.status == "succeeded"
    assert finished.result == {"index_id": "worker-index"}

    asyncio.run(worker.recover_jobs(ctx))
    settings = worker.worker_settings()
    assert settings["max_jobs"] == 3
    assert settings["job_timeout"] == 101
    assert str(settings["redis_settings"].host) == "unused"
    assert created_services[0].registry.data_root == tmp_path / "worker-data"
    assert not created_services[0].registry.github_import_enabled
    jobs.close()


def test_http_github_submission_returns_job_without_running_it(tmp_path: Path) -> None:
    app = create_app({}, data_root=tmp_path / "api-data", github_import_enabled=True)
    app.state.workspace_service.jobs.start_background = False
    parsed = urlsplit("/api/imports/github")
    body = json.dumps(
        {"name": "queued", "url": "https://github.com/owner/repo", "ref": "main"}
    ).encode()
    messages = []
    seen = False

    async def receive():
        nonlocal seen
        if seen:
            return {"type": "http.disconnect"}
        seen = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": parsed.path,
                "raw_path": parsed.path.encode(),
                "query_string": b"",
                "headers": [(b"host", b"localhost"), (b"content-type", b"application/json")],
                "client": ("127.0.0.1", 1000),
                "server": ("localhost", 80),
                "root_path": "",
            },
            receive,
            send,
        )
    )
    status = next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )
    response = json.loads(
        b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
    )
    assert status == 200
    assert response["job_id"].startswith("job-") and response["status"] == "queued"
    assert "attempt" not in response
    assert app.state.workspace_service.jobs.store.get(response["job_id"]).status == "queued"


def test_unbound_import_job_can_be_cancelled_over_http(tmp_path: Path) -> None:
    app = create_app({}, data_root=tmp_path / "api-data", github_import_enabled=True)
    app.state.workspace_service.jobs.start_background = False
    job = app.state.workspace_service.submit_job(
        "github_import", None, {"url": "https://github.com/owner/repo"}
    )
    target = f"/api/jobs/{job.job_id}/cancel"
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": target,
                "raw_path": target.encode(),
                "query_string": b"",
                "headers": [(b"host", b"localhost")],
                "client": ("127.0.0.1", 1000),
                "server": ("localhost", 80),
                "root_path": "",
            },
            receive,
            send,
        )
    )
    status = next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )
    payload = json.loads(
        b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
    )
    assert status == 200
    assert payload["job_id"] == job.job_id
    assert payload["status"] == "cancelled"


def test_lease_recovery_uses_injected_clock_and_keeps_live_shared_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODETRAIL_JOB_MAX_RETRIES", "0")
    current_time = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    jobs = JobService(
        str(tmp_path / "clock.sqlite3"),
        lambda _record, _control: {"ok": True},
        start_background=False,
        clock=lambda: current_time[0],
    )
    job = jobs.submit("github_import", None, {})
    assert jobs.store.claim(
        job.job_id,
        [
            {
                "attempt": 1,
                "started_at": current_time[0].isoformat(),
                "retry_count": 0,
                "phase": "indexing",
            }
        ],
        6,
        1800,
    )

    current_time[0] += timedelta(seconds=5)
    jobs.recover_expired()
    assert jobs.store.get(job.job_id).status == "running"

    current_time[0] += timedelta(seconds=2)
    jobs.recover_expired()
    recovered = jobs.store.get(job.job_id)
    assert recovered.status == "failed"
    assert recovered.error_code == "worker_lost"
