"""arq worker entry point for durable CodeTrail jobs."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from .jobs import JobConfig
from .factory import create_services

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    service = create_services()
    ctx["jobs"] = service.jobs


async def run_job(ctx: dict[str, Any], job_id: str) -> None:
    logger.info("Starting CodeTrail job %s", job_id)
    try:
        await asyncio.to_thread(ctx["jobs"].run, job_id, JobConfig.from_environment().max_retries)
    finally:
        logger.info("Finished CodeTrail job %s", job_id)


async def recover_jobs(ctx: dict[str, Any]) -> None:
    try:
        recovered = ctx["jobs"].recover_expired()
    except Exception:
        logger.warning("Job lease recovery cron failed; it will be retried", exc_info=True)
        return
    if recovered:
        logger.info("Recovered %s expired CodeTrail job lease(s)", recovered)


def worker_settings() -> dict[str, Any]:
    """Construct arq settings at launch, after CLI settings reach the environment."""

    config = JobConfig.from_environment()
    return {
        "functions": [run_job],
        "cron_jobs": [cron(recover_jobs, second={0, 10, 20, 30, 40, 50})],
        "on_startup": startup,
        "max_jobs": config.worker_concurrency,
        "job_timeout": config.total_timeout + 30,
        "keep_result": 60,
        "redis_settings": RedisSettings.from_dsn(os.environ["CODETRAIL_REDIS_URL"]),
    }


class WorkerSettings:
    """Stable arq import target; environment-backed settings are built at launch."""

    functions = [run_job]
    cron_jobs = [cron(recover_jobs, second={0, 10, 20, 30, 40, 50})]
    on_startup = startup
    keep_result = 60
