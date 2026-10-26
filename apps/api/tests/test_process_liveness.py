"""Safe process-owner probing tests without creating or signalling processes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import service as service_module
from app.service import WorkspaceService


class _FakeWinFunction:
    def __init__(self, result) -> None:
        self.result = result
        self.argtypes = None
        self.restype = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _FakeKernel32:
    def __init__(self, *, handle, wait_result, close_result=True) -> None:
        self.OpenProcess = _FakeWinFunction(handle)
        self.WaitForSingleObject = _FakeWinFunction(wait_result)
        self.CloseHandle = _FakeWinFunction(close_result)


def _operation(*, pid: int | None, operation_id: str = "operation-1") -> SimpleNamespace:
    return SimpleNamespace(owner_pid=pid, operation_id=operation_id, project_id="project-1")


def test_windows_process_liveness_uses_readonly_handle_and_closes_it() -> None:
    kernel32 = _FakeKernel32(
        handle=123,
        wait_result=service_module._WINDOWS_WAIT_TIMEOUT,
    )

    state = service_module._windows_process_liveness(456, kernel32=kernel32)

    assert state == "alive"
    assert kernel32.OpenProcess.calls == [(service_module._WINDOWS_SYNCHRONIZE, False, 456)]
    assert kernel32.WaitForSingleObject.calls == [(123, 0)]
    assert kernel32.CloseHandle.calls == [(123,)]


def test_windows_process_liveness_distinguishes_exit_from_unknown_open_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    exited = _FakeKernel32(
        handle=123,
        wait_result=service_module._WINDOWS_WAIT_OBJECT_0,
    )
    assert service_module._windows_process_liveness(456, kernel32=exited) == "exited"

    inaccessible = _FakeKernel32(handle=None, wait_result=0)
    # ctypes.get_last_error exists only on Windows; create it for cross-platform unit tests.
    monkeypatch.setattr(service_module.ctypes, "get_last_error", lambda: 5, raising=False)
    assert service_module._windows_process_liveness(456, kernel32=inaccessible) == "unknown"

    missing = _FakeKernel32(handle=None, wait_result=0)
    monkeypatch.setattr(
        service_module.ctypes,
        "get_last_error",
        lambda: service_module._WINDOWS_ERROR_INVALID_PARAMETER,
        raising=False,
    )
    assert service_module._windows_process_liveness(456, kernel32=missing) == "exited"

    wait_failed = _FakeKernel32(
        handle=123,
        wait_result=service_module._WINDOWS_WAIT_FAILED,
    )
    assert service_module._windows_process_liveness(456, kernel32=wait_failed) == "unknown"
    assert wait_failed.CloseHandle.calls == [(123,)]


def test_windows_owner_dispatch_never_calls_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.os, "name", "nt")
    monkeypatch.setattr(service_module, "_windows_process_liveness", lambda pid: "alive")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Windows owner probing must not call os.kill")

    monkeypatch.setattr(service_module.os, "kill", fail_if_called)

    assert WorkspaceService._project_update_owner_liveness(_operation(pid=456)) == "alive"


def test_posix_probe_keeps_unknown_errors_from_triggering_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.os, "name", "posix")

    def fail_with_unknown_error(*_args, **_kwargs):
        raise OSError("probe failed")

    monkeypatch.setattr(service_module.os, "kill", fail_with_unknown_error)

    assert service_module._process_liveness(456) == "unknown"


def test_unknown_owner_state_keeps_project_update_and_protects_active_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = _operation(pid=456)

    class _ManagementStore:
        def __init__(self) -> None:
            self.excluded_project_ids: tuple[str, ...] | None = None

        def list_project_updates(self):
            return (operation,)

        def mark_active_attempts_interrupted(self, *, exclude_project_ids):
            self.excluded_project_ids = tuple(exclude_project_ids)

    management_store = _ManagementStore()
    registry = SimpleNamespace(management_store=management_store, repository_ids=())
    workspace = WorkspaceService.__new__(WorkspaceService)
    workspace.registry = registry
    recovered: list[object] = []
    monkeypatch.setattr(workspace, "_ensure_managed_projects", lambda: None)
    monkeypatch.setattr(workspace, "_recover_project_update", recovered.append)
    monkeypatch.setattr(workspace, "_project_update_owner_liveness", lambda _operation: "unknown")

    workspace._recover_interrupted_builds()

    assert recovered == []
    assert management_store.excluded_project_ids == ("project-1",)
