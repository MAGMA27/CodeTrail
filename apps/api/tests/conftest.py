"""Isolate import-time API construction from developer service data."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


_TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="codetrail-pytest-data-"))
os.environ.update(
    {
        "CODETRAIL_DATA_DIR": str(_TEST_DATA_ROOT),
        "CODETRAIL_REPOSITORIES": "{}",
        "CODETRAIL_ALLOWED_IMPORT_ROOTS": "[]",
        "CODETRAIL_REDIS_URL": "",
        "CODETRAIL_GITHUB_IMPORT_ENABLED": "0",
        "CODETRAIL_MCP_GITHUB_IMPORT_ENABLED": "0",
        "CODETRAIL_REAL_RUN_ENABLED": "0",
        "CODETRAIL_MCP_REAL_RUN_ENABLED": "0",
        "CODETRAIL_GITHUB_TOKEN": "",
        "GITHUB_TOKEN": "",
        "CODETRAIL_SYNTHETIC": "1",
    }
)


def pytest_unconfigure() -> None:
    """Stop import-time background workers before removing their isolated data."""

    try:
        main_module = sys.modules.get("app.main")
        application = None if main_module is None else getattr(main_module, "app", None)
        if application is not None:
            application.state.workspace_service.jobs.close()
    finally:
        shutil.rmtree(_TEST_DATA_ROOT, ignore_errors=True)
