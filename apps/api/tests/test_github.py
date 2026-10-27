"""Offline GitHub acquisition, archive safety, and import lifecycle tests."""

from __future__ import annotations

import io
import json
import os
import tarfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.github import (
    GitHubArchive,
    GitHubClient,
    GitHubError,
    GitHubRequestBudget,
    GitHubResolution,
    extract_github_archive,
    parse_github_repository_url,
)
from app.contracts import GitHubImportRequest
from app.main import create_app
from app.service import ServiceError
from app.storage import StorageError


def _archive_bytes(*members: tuple[str, bytes | None, str]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, content, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "target.py"
                archive.addfile(info)
            else:
                payload = content or b""
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._offset = 0
        self.socket_timeouts: list[float] = []

        class _Socket:
            def __init__(self, timeouts: list[float]) -> None:
                self.timeouts = timeouts

            def settimeout(self, timeout: float) -> None:
                self.timeouts.append(timeout)

        class _Raw:
            def __init__(self, socket: _Socket) -> None:
                self._sock = socket

        class _Buffered:
            def __init__(self, raw: _Raw) -> None:
                self.raw = raw

        self.fp = _Buffered(_Raw(_Socket(self.socket_timeouts)))

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        start = self._offset
        self._offset += size
        return self._body[start : start + size]

    def close(self) -> None:
        return None


class _FakeOpener:
    def __init__(self, responses: dict[str, list[_FakeResponse]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.requests: list[object] = []
        self.timeouts: list[float] = []

    def open(self, request, timeout: float) -> _FakeResponse:
        url = request.full_url
        self.calls.append(url)
        self.requests.append(request)
        self.timeouts.append(timeout)
        values = self.responses.get(url)
        if not values:
            raise AssertionError(f"unexpected GitHub request: {url}")
        return values.pop(0)


def test_github_rate_limit_redirect_and_timeout_fail_with_bounded_errors(tmp_path: Path) -> None:
    api_commit = "https://api.github.com/repos/owner/repo/commits/main"
    rate_limited = _FakeOpener({api_commit: [_FakeResponse(429, headers={"Retry-After": "3"})]})
    with pytest.raises(GitHubError) as rate_error:
        GitHubClient(opener=rate_limited, max_rate_limit_retries=0).resolve("https://github.com/owner/repo", "main")
    assert rate_error.value.code == "budget_exceeded"
    assert "3" in rate_error.value.message

    archive_url = "https://api.github.com/repos/owner/repo/tarball/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    unsafe_redirect = _FakeOpener({archive_url: [_FakeResponse(302, headers={"Location": "https://127.0.0.1/archive"})]})
    resolution = GitHubResolution(parse_github_repository_url("https://github.com/owner/repo"), "main", "a" * 40)
    with pytest.raises(GitHubError) as redirect_error:
        GitHubClient(opener=unsafe_redirect).download_and_extract(resolution, tmp_path / "redirect-temp")
    assert redirect_error.value.code == "forbidden"

    class _TimeoutOpener:
        def open(self, request, timeout: float):
            raise TimeoutError("read timeout")

    with pytest.raises(GitHubError) as timeout_error:
        GitHubClient(opener=_TimeoutOpener()).resolve("https://github.com/owner/repo", "main")
    assert timeout_error.value.status_code == 503


_COMMIT_URL = "https://api.github.com/repos/owner/repo/commits/main"
_COMMIT_BODY = json.dumps({"sha": "b" * 40}).encode("utf-8")


def _rate_limited_client(responses: list[_FakeResponse], **kwargs) -> tuple[GitHubClient, _FakeOpener, list[float]]:
    opener = _FakeOpener({_COMMIT_URL: responses})
    sleeps: list[float] = []
    kwargs.setdefault("token", "")
    client = GitHubClient(opener=opener, sleep=sleeps.append, clock=lambda: 1_000_000.0, **kwargs)
    return client, opener, sleeps


def test_github_import_shares_one_deadline_across_resolve_and_download(tmp_path: Path) -> None:
    api_archive = "https://api.github.com/repos/owner/repo/tarball/" + "b" * 40
    monotonic_now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        monotonic_now[0] += seconds

    opener = _FakeOpener(
        {
            _COMMIT_URL: [
                _FakeResponse(429, headers={"Retry-After": "15", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000030"}),
                _FakeResponse(200, _COMMIT_BODY),
            ],
            api_archive: [_FakeResponse(429, headers={"Retry-After": "15", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000030"})],
        }
    )
    client = GitHubClient(
        token="",
        opener=opener,
        max_total_seconds=20.0,
        sleep=sleep,
        clock=lambda: 1_000_000.0,
        monotonic=lambda: monotonic_now[0],
    )
    budget = client.new_budget()
    resolution = client.resolve("https://github.com/owner/repo", "main", budget=budget)

    with pytest.raises(GitHubError) as error:
        client.download_and_extract(resolution, tmp_path / "shared-budget", budget=budget)

    assert monotonic_now[0] - 100.0 == 15.0
    assert monotonic_now[0] < budget.deadline
    assert sleeps == [15.0]
    assert opener.calls == [_COMMIT_URL, _COMMIT_URL, api_archive]
    assert opener.timeouts == [20.0, 5.0, 5.0]
    assert error.value.code == "budget_exceeded"
    assert error.value.status_code == 429
    assert "rate limit" in error.value.message
    assert "resets at" in error.value.message
    assert "CODETRAIL_GITHUB_TOKEN" in error.value.message


def test_github_import_http_returns_rate_limit_hint_before_client_timeout(tmp_path: Path) -> None:
    import asyncio

    monotonic_now = [100.0]

    def sleep(seconds: float) -> None:
        monotonic_now[0] += seconds

    opener = _FakeOpener(
        {
            _COMMIT_URL: [
                _FakeResponse(429, headers={"Retry-After": "15", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000030"})
                for _ in range(4)
            ]
        }
    )
    client = GitHubClient(
        token="",
        opener=opener,
        max_total_seconds=20.0,
        sleep=sleep,
        clock=lambda: 1_000_000.0,
        monotonic=lambda: monotonic_now[0],
    )
    application = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,
    )
    status, error = asyncio.run(
        _request(
            application,
            "POST",
            "/api/imports/github",
            {"name": "Rate limited", "url": "https://github.com/owner/repo", "ref": "main"},
        )
    )

    assert status == 429
    assert monotonic_now[0] - 100.0 == 15.0
    assert error["code"] == "budget_exceeded"
    assert "rate limit" in error["message"]
    assert "resets at" in error["message"]
    assert "CODETRAIL_GITHUB_TOKEN" in error["message"]


def test_github_budget_expiry_during_response_read_keeps_rate_limit_hint() -> None:
    monotonic_now = [100.0]
    budget_deadline = monotonic_now[0] + 20.0

    def sleep(seconds: float) -> None:
        monotonic_now[0] += seconds

    class _DeadlineResponse(_FakeResponse):
        def __init__(self) -> None:
            super().__init__(200)
            self._body = None

        def read(self, _size: int = -1) -> bytes:
            monotonic_now[0] = budget_deadline
            raise TimeoutError("response body stalled until the shared deadline")

    slow_response = _DeadlineResponse()
    opener = _FakeOpener(
        {
            _COMMIT_URL: [
                _FakeResponse(429, headers={"Retry-After": "19", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000030"}),
                slow_response,
            ]
        }
    )
    client = GitHubClient(
        token="",
        opener=opener,
        timeout_seconds=10.0,
        max_total_seconds=20.0,
        sleep=sleep,
        clock=lambda: 1_000_000.0,
        monotonic=lambda: monotonic_now[0],
    )
    budget = client.new_budget()

    with pytest.raises(GitHubError) as error:
        client.resolve("https://github.com/owner/repo", "main", budget=budget)

    assert monotonic_now[0] == budget.deadline
    assert opener.timeouts == [10.0, 1.0]
    assert slow_response.socket_timeouts == [1.0]
    assert error.value.code == "budget_exceeded"
    assert error.value.status_code == 429
    assert "rate limit" in error.value.message
    assert "resets at" in error.value.message
    assert "CODETRAIL_GITHUB_TOKEN" in error.value.message


def test_github_import_succeeds_after_one_rate_limited_resolve_and_download(tmp_path: Path) -> None:
    api_archive = "https://api.github.com/repos/owner/repo/tarball/" + "b" * 40
    codeload_archive = "https://codeload.github.com/owner/repo/legacy.tar.gz/" + "b" * 40
    archive = _archive_bytes(("owner-repo-bbbb/main.py", b"def main():\n    return 1\n", "file"))
    archive_response = _FakeResponse(200, archive)
    monotonic_now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        monotonic_now[0] += seconds

    opener = _FakeOpener(
        {
            _COMMIT_URL: [_FakeResponse(429, headers={"Retry-After": "19"}), _FakeResponse(200, _COMMIT_BODY)],
            api_archive: [_FakeResponse(302, headers={"Location": codeload_archive})],
            codeload_archive: [archive_response],
        }
    )
    client = GitHubClient(
        token="",
        opener=opener,
        timeout_seconds=10.0,
        max_total_seconds=20.0,
        sleep=sleep,
        clock=lambda: 1_000_000.0,
        monotonic=lambda: monotonic_now[0],
    )
    budget = client.new_budget()

    resolution = client.resolve("https://github.com/owner/repo", "main", budget=budget)
    result = client.download_and_extract(resolution, tmp_path / "success-after-retry", budget=budget)

    assert result.resolution.resolved_commit == "b" * 40
    assert result.extracted_file_count == 1
    assert (tmp_path / "success-after-retry" / "main.py").is_file()
    assert sleeps == [19.0]
    assert monotonic_now[0] - 100.0 == 19.0
    assert monotonic_now[0] < budget.deadline
    assert opener.timeouts == [10.0, 1.0, 1.0, 1.0]
    assert archive_response.socket_timeouts
    assert all(timeout == 1.0 for timeout in archive_response.socket_timeouts)


def test_github_retries_after_429_using_retry_after() -> None:
    client, opener, sleeps = _rate_limited_client(
        [_FakeResponse(429, headers={"Retry-After": "2"}), _FakeResponse(200, _COMMIT_BODY)]
    )
    resolution = client.resolve("https://github.com/owner/repo", "main")
    assert resolution.resolved_commit == "b" * 40
    assert sleeps == [2.0]
    assert len(opener.calls) == 2


def test_github_waits_for_primary_limit_reset_and_backs_off_exponentially() -> None:
    client, _opener, sleeps = _rate_limited_client(
        [
            _FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000005"}),
            _FakeResponse(429),
            _FakeResponse(429),
            _FakeResponse(200, _COMMIT_BODY),
        ]
    )
    client.resolve("https://github.com/owner/repo", "main")
    assert sleeps == [6.0, 2.0, 4.0]


def test_github_rate_limit_gives_up_with_clear_error_after_bounded_retries() -> None:
    client, opener, sleeps = _rate_limited_client(
        [_FakeResponse(429, headers={"Retry-After": "1"}) for _ in range(4)]
    )
    with pytest.raises(GitHubError) as error:
        client.resolve("https://github.com/owner/repo", "main")
    assert error.value.code == "budget_exceeded"
    assert error.value.status_code == 429
    assert "rate limit" in error.value.message
    assert "CODETRAIL_GITHUB_TOKEN" in error.value.message
    assert sleeps == [1.0, 1.0, 1.0]
    assert len(opener.calls) == 4


def test_github_rate_limit_does_not_wait_past_the_retry_budget() -> None:
    client, opener, sleeps = _rate_limited_client(
        [_FakeResponse(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1003600"})]
    )
    with pytest.raises(GitHubError) as error:
        client.resolve("https://github.com/owner/repo", "main")
    assert error.value.code == "budget_exceeded"
    assert "resets at" in error.value.message
    assert sleeps == []
    assert len(opener.calls) == 1


def test_github_plain_403_and_401_are_access_errors_without_retry() -> None:
    client, opener, sleeps = _rate_limited_client([_FakeResponse(403)])
    with pytest.raises(GitHubError) as denied:
        client.resolve("https://github.com/owner/repo", "main")
    assert denied.value.code == "forbidden"
    assert sleeps == [] and len(opener.calls) == 1

    client, _opener, _sleeps = _rate_limited_client([_FakeResponse(401)], token="secret-token-value")
    with pytest.raises(GitHubError) as unauthorized:
        client.resolve("https://github.com/owner/repo", "main")
    assert unauthorized.value.code == "forbidden"
    assert "token" in unauthorized.value.message
    assert "secret-token-value" not in unauthorized.value.message


def test_github_token_from_environment_is_sent_as_bearer_header_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODETRAIL_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "fallback-token")
    opener = _FakeOpener({_COMMIT_URL: [_FakeResponse(429, headers={"Retry-After": "1"}), _FakeResponse(200, _COMMIT_BODY)]})
    client = GitHubClient(opener=opener, sleep=lambda _seconds: None)
    client.resolve("https://github.com/owner/repo", "main")
    assert client.authenticated
    assert [request.get_header("Authorization") for request in opener.requests] == ["Bearer fallback-token"] * 2
    assert all("fallback-token" not in url for url in opener.calls)

    monkeypatch.setenv("CODETRAIL_GITHUB_TOKEN", "primary-token")
    opener = _FakeOpener({_COMMIT_URL: [_FakeResponse(429) for _ in range(2)]})
    with pytest.raises(GitHubError) as error:
        GitHubClient(opener=opener, sleep=lambda _seconds: None, max_rate_limit_retries=1).resolve(
            "https://github.com/owner/repo", "main"
        )
    assert opener.requests[0].get_header("Authorization") == "Bearer primary-token"
    assert "primary-token" not in error.value.message
    assert "configured GitHub token is also rate limited" in error.value.message

    monkeypatch.setenv("CODETRAIL_GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "  ")
    opener = _FakeOpener({_COMMIT_URL: [_FakeResponse(200, _COMMIT_BODY)]})
    client = GitHubClient(opener=opener)
    client.resolve("https://github.com/owner/repo", "main")
    assert not client.authenticated
    assert opener.requests[0].get_header("Authorization") is None


def test_github_url_normalization_and_rejection() -> None:
    repository = parse_github_repository_url("https://GitHub.com/Owner/Repo.git/")
    assert repository.canonical_url == "https://github.com/owner/repo"
    assert repository.identity == "github://owner/repo"

    for value in (
        "http://github.com/owner/repo",
        "https://github.com/owner/repo/issues",
        "https://github.com/owner/repo?download=1",
        "https://user:secret@github.com/owner/repo",
        "https://example.test/owner/repo",
    ):
        with pytest.raises(GitHubError):
            parse_github_repository_url(value)


def test_github_client_resolves_default_branch_follows_only_allowed_redirect_and_hashes_archive() -> None:
    repository_url = "https://github.com/owner/repo"
    api_repo = "https://api.github.com/repos/owner/repo"
    api_commit = "https://api.github.com/repos/owner/repo/commits/main"
    archive_url = "https://api.github.com/repos/owner/repo/tarball/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    codeload_url = "https://codeload.github.com/owner/repo/legacy.tar.gz/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    archive = _archive_bytes(("owner-repo-aaaa/", None, "dir"), ("owner-repo-aaaa/main.py", b"def main():\n    return 1\n", "file"))
    opener = _FakeOpener(
        {
            api_repo: [_FakeResponse(200, json.dumps({"default_branch": "main"}).encode())],
            api_commit: [_FakeResponse(200, json.dumps({"sha": "A" * 40}).encode())],
            archive_url: [_FakeResponse(302, headers={"Location": codeload_url})],
            codeload_url: [_FakeResponse(200, archive)],
        }
    )
    client = GitHubClient(opener=opener)
    resolution = client.resolve(repository_url, None)
    destination = Path.cwd() / ".pytest-github-client-temp"
    if destination.exists():
        for item in destination.iterdir():
            if item.is_dir():
                import shutil

                shutil.rmtree(item)
            else:
                item.unlink()
        destination.rmdir()
    try:
        result = client.download_and_extract(resolution, destination)
        assert result.resolution.resolved_commit == "a" * 40
        assert result.resolution.resolved_ref == "main"
        assert result.extracted_file_count == 1
        assert (destination / "main.py").read_text(encoding="utf-8").startswith("def main")
        assert result.archive_hash
    finally:
        if destination.exists():
            import shutil

            shutil.rmtree(destination)
    assert opener.calls[-1] == codeload_url


def test_github_archive_rejects_traversal_links_and_unpack_budget(tmp_path: Path) -> None:
    traversal = tmp_path / "traversal.tar.gz"
    traversal.write_bytes(_archive_bytes(("root/../escape.py", b"pass\n", "file")))
    with pytest.raises(GitHubError, match="traversal"):
        extract_github_archive(traversal, tmp_path / "out")

    link = tmp_path / "link.tar.gz"
    link.write_bytes(_archive_bytes(("root/target.py", b"pass\n", "file"), ("root/link.py", None, "symlink")))
    with pytest.raises(GitHubError, match="links"):
        extract_github_archive(link, tmp_path / "link-out")

    oversized = tmp_path / "oversized.tar.gz"
    oversized.write_bytes(_archive_bytes(("root/big.py", b"12345", "file")))
    with pytest.raises(GitHubError, match="unpacked"):
        extract_github_archive(oversized, tmp_path / "oversized-out", max_unpacked_bytes=4)


class _FakeGitHubClient:
    def __init__(self, commits: list[str]) -> None:
        self.commits = iter(commits)
        self.downloads = 0
        self.resolve_budgets: list[GitHubRequestBudget] = []
        self.download_budgets: list[GitHubRequestBudget] = []

    def new_budget(self) -> GitHubRequestBudget:
        return GitHubRequestBudget(float("inf"))

    def resolve(
        self,
        url: str,
        requested_ref: str | None,
        *,
        budget: GitHubRequestBudget | None = None,
    ) -> GitHubResolution:
        budget = budget or self.new_budget()
        self.resolve_budgets.append(budget)
        repository = parse_github_repository_url(url)
        return GitHubResolution(repository, requested_ref, next(self.commits), requested_ref or "main")

    def download_and_extract(
        self,
        resolution: GitHubResolution,
        destination: Path,
        *,
        budget: GitHubRequestBudget | None = None,
    ) -> GitHubArchive:
        budget = budget or self.new_budget()
        self.download_budgets.append(budget)
        destination.mkdir(parents=True, exist_ok=False)
        self.downloads += 1
        (destination / "module.py").write_text(
            f"COMMIT = '{resolution.resolved_commit}'\n\ndef run() -> str:\n    return COMMIT\n",
            encoding="utf-8",
        )
        return GitHubArchive(resolution, "b" * 64, 1, (destination / "module.py").stat().st_size)


class _BlockingGitHubClient(_FakeGitHubClient):
    def __init__(
        self,
        commits: list[str],
        *,
        block_call: int,
        started: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(commits)
        self._resolve_calls = 0
        self._block_call = block_call
        self._started = started
        self._release = release

    def resolve(
        self,
        url: str,
        requested_ref: str | None,
        *,
        budget: GitHubRequestBudget | None = None,
    ) -> GitHubResolution:
        self._resolve_calls += 1
        if self._resolve_calls == self._block_call:
            self._started.set()
            if not self._release.wait(timeout=10):
                raise AssertionError("controlled GitHub resolve did not receive its release")
        return super().resolve(url, requested_ref, budget=budget)


async def _request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
    parsed = urlsplit(target)
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    messages: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    await application(scope, receive, send)
    response = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return response["status"], json.loads(response_body or b"{}")


def test_github_http_import_is_fixed_commit_idempotent_and_cleans_temp(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    second = "b" * 40
    client = _FakeGitHubClient([first, second])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    payload = {
        "name": "Remote Python",
        "url": "https://github.com/Owner/Repo",
        "ref": "main",
        "idempotency_key": "remote-one",
    }
    status, imported = asyncio.run(_request(app, "POST", "/api/imports/github", payload))
    assert status == 200
    assert imported["attempt"]["status"] == "ready"
    assert imported["attempt"]["resolved_commit"] == first
    assert imported["index"]["index"]["dirty"] is False
    assert imported["cleanup_status"] == "succeeded"
    project_id = imported["project"]["project_id"]
    attempt_id = imported["attempt"]["attempt_id"]
    assert client.downloads == 1
    assert client.resolve_budgets[0] is client.download_budgets[0]
    assert list((tmp_path / "service-data" / "github-imports").iterdir()) == []

    status, repeated = asyncio.run(_request(app, "POST", "/api/imports/github", payload))
    assert status == 200
    assert repeated["attempt"]["attempt_id"] == attempt_id
    assert client.downloads == 1

    status, moved = asyncio.run(
        _request(app, "POST", "/api/imports/github", {**payload, "idempotency_key": None})
    )
    assert status == 200
    assert moved["project"]["project_id"] == project_id
    assert moved["attempt"]["resolved_commit"] == second
    assert moved["attempt"]["attempt_id"] != attempt_id
    assert client.downloads == 2
    assert client.resolve_budgets[1] is client.download_budgets[1]

    status, indexes = asyncio.run(_request(app, "GET", f"/api/projects/{project_id}/indexes"))
    assert status == 200
    assert len(indexes["indexes"]) == 2


def test_github_freshness_is_metadata_only_and_explicit_refresh_updates_latest(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    second = "b" * 40
    client = _FakeGitHubClient([first, second, second, second])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    payload = {"name": "Remote Python", "url": "https://github.com/owner/repo", "ref": "main"}
    status, imported = asyncio.run(_request(app, "POST", "/api/imports/github", payload))
    assert status == 200
    project_id = imported["project"]["project_id"]
    baseline_index_id = imported["index"]["index"]["index_id"]
    assert client.downloads == 1

    status, freshness = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/freshness")
    )
    assert status == 200
    assert freshness["status"] == "changed"
    assert freshness["remote_status"] == "changed"
    assert freshness["remote_source_url"] == "https://github.com/owner/repo"
    assert freshness["remote_requested_ref"] == "main"
    assert freshness["remote_resolved_ref"] == "main"
    assert freshness["remote_resolved_commit"] == second
    assert "source_changed" in freshness["reasons"]
    assert client.downloads == 1

    status, refreshed = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh")
    )
    assert status == 200
    assert refreshed["mode"] == "created"
    assert refreshed["freshness"]["baseline_index_id"] == baseline_index_id
    assert refreshed["index"]["index"]["revision"] == second
    assert client.downloads == 2
    assert client.resolve_budgets[2] is client.resolve_budgets[3] is client.download_budgets[1]


def test_github_refresh_reuses_compatible_historical_commit_without_download(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    second = "b" * 40
    client = _FakeGitHubClient([first, second, second, first, first])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    payload = {"name": "Remote Python", "url": "https://github.com/owner/repo", "ref": "main"}
    status, imported = asyncio.run(_request(app, "POST", "/api/imports/github", payload))
    assert status == 200
    project_id = imported["project"]["project_id"]
    first_index_id = imported["index"]["index"]["index_id"]

    status, moved = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{first_index_id}/github-refresh")
    )
    assert status == 200
    second_index_id = moved["index"]["index"]["index_id"]
    assert moved["mode"] == "created"
    assert second_index_id != first_index_id
    assert client.downloads == 2

    status, restored = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{second_index_id}/github-refresh")
    )
    assert status == 200
    assert restored["mode"] == "historical_reused"
    assert restored["index"]["index"]["index_id"] == first_index_id
    assert client.downloads == 2


def test_github_refresh_failure_keeps_latest_and_source_identity(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    second = "b" * 40
    client = _FakeGitHubClient([first, second, second])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    payload = {"name": "Remote Python", "url": "https://github.com/owner/repo", "ref": "main"}
    status, imported = asyncio.run(_request(app, "POST", "/api/imports/github", payload))
    assert status == 200
    project_id = imported["project"]["project_id"]
    baseline_index_id = imported["index"]["index"]["index_id"]
    repository_id = imported["attempt"]["repository_id"]

    def fail_download(resolution: GitHubResolution, destination: Path) -> GitHubArchive:
        client.downloads += 1
        raise GitHubError("internal_error", 503, "archive unavailable")

    client.download_and_extract = fail_download  # type: ignore[method-assign]

    status, failed = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh")
    )
    assert status == 503
    assert failed["code"] == "internal_error"
    assert app.state.registry.management_store.load_source(repository_id).resolved_commit == first
    status, indexes = asyncio.run(_request(app, "GET", f"/api/projects/{project_id}/indexes"))
    assert status == 200
    assert indexes["latest_index_id"] == baseline_index_id


def test_github_freshness_unknown_remote_does_not_update(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    client = _FakeGitHubClient([first])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    status, imported = asyncio.run(
        _request(
            app,
            "POST",
            "/api/imports/github",
            {"name": "Remote Python", "url": "https://github.com/owner/repo"},
        )
    )
    assert status == 200
    project_id = imported["project"]["project_id"]
    baseline_index_id = imported["index"]["index"]["index_id"]

    status, freshness = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/freshness")
    )
    assert status == 200
    assert freshness["status"] == "unknown"
    assert freshness["remote_status"] == "unknown"
    assert freshness["reasons"] == ["source_unavailable"]
    assert client.downloads == 1

    status, failed = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh")
    )
    assert status == 409
    assert failed["code"] == "conflict"
    assert client.downloads == 1
    status, indexes = asyncio.run(_request(app, "GET", f"/api/projects/{project_id}/indexes"))
    assert status == 200
    assert indexes["latest_index_id"] == baseline_index_id


def test_github_default_ref_without_persisted_resolution_stays_unknown(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    client = _FakeGitHubClient([first])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    status, imported = asyncio.run(
        _request(
            app,
            "POST",
            "/api/imports/github",
            {"name": "Remote Python", "url": "https://github.com/owner/repo"},
        )
    )
    assert status == 200
    project_id = imported["project"]["project_id"]
    baseline_index_id = imported["index"]["index"]["index_id"]
    app.state.registry.management_store.update_source(
        imported["attempt"]["repository_id"],
        resolved_ref=None,
    )

    status, freshness = asyncio.run(
        _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/freshness")
    )
    assert status == 200
    assert freshness["status"] == "unknown"
    assert freshness["remote_status"] == "unknown"
    assert freshness["reasons"] == ["remote_metadata_missing"]
    assert client.downloads == 1


def test_github_cleanup_failure_is_recorded_and_retryable(tmp_path: Path) -> None:
    import asyncio

    client = _FakeGitHubClient(["c" * 40])
    app = create_app(
        {},
        data_root=tmp_path / "service-data",
        github_import_enabled=True,
        github_client=client,  # type: ignore[arg-type]
    )
    service = app.state.workspace_service
    original = service._cleanup_github_temp
    service._cleanup_github_temp = lambda _path, _attempt: "injected cleanup failure"
    try:
        status, failed_cleanup = asyncio.run(
            _request(
                app,
                "POST",
                "/api/imports/github",
                {"name": "Cleanup", "url": "https://github.com/owner/repo", "ref": "main"},
            )
        )
    finally:
        service._cleanup_github_temp = original
    assert status == 200
    assert failed_cleanup["attempt"]["status"] == "ready"
    assert failed_cleanup["cleanup_status"] == "failed"

    status, cleaned = asyncio.run(
        _request(app, "POST", f"/api/imports/{failed_cleanup['attempt']['attempt_id']}/cleanup")
    )
    assert status == 200
    assert cleaned["cleanup_status"] == "succeeded"


def test_github_refresh_stale_new_commit_cannot_overwrite_independent_service_update(tmp_path: Path) -> None:
    first = "a" * 40
    second = "b" * 40
    started = threading.Event()
    release = threading.Event()
    client_a = _BlockingGitHubClient(
        [first, second, second],
        block_call=2,
        started=started,
        release=release,
    )
    client_b = _FakeGitHubClient([second, second])
    data_root = tmp_path / "service-data"
    app_a = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client_a)  # type: ignore[arg-type]
    initial = app_a.state.workspace_service.import_github(
        GitHubImportRequest(
            name="Remote Python",
            url="https://github.com/owner/repo",
            ref="main",
        )
    )
    project_id = initial.project.project_id
    baseline_index_id = initial.index.index.index_id  # type: ignore[union-attr]
    app_b = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client_b)  # type: ignore[arg-type]

    stale_error: list[ServiceError] = []

    def stale_refresh() -> None:
        try:
            app_a.state.workspace_service.refresh_github_source(project_id, baseline_index_id)
        except ServiceError as exc:
            stale_error.append(exc)

    worker = threading.Thread(target=stale_refresh)
    worker.start()
    assert started.wait(timeout=10)
    updated = app_b.state.workspace_service.refresh_github_source(project_id, baseline_index_id)
    assert updated.mode == "created"
    latest_after_b = updated.index.index.index_id  # type: ignore[union-attr]
    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert len(stale_error) == 1
    assert stale_error[0].code == "conflict"

    current = app_a.state.workspace_service.get_project(project_id)
    assert current.latest_index_id == latest_after_b
    source = app_a.state.registry.management_store.load_source(initial.attempt.repository_id)
    assert source.resolved_commit == second


def test_github_refresh_stale_historical_reuse_cannot_rewrite_source_identity(tmp_path: Path) -> None:
    first = "a" * 40
    second = "b" * 40
    third = "c" * 40
    started = threading.Event()
    release = threading.Event()
    client_a = _BlockingGitHubClient(
        [first, second, second, first, first],
        block_call=4,
        started=started,
        release=release,
    )
    client_b = _FakeGitHubClient([third, third])
    data_root = tmp_path / "service-data"
    app_a = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client_a)  # type: ignore[arg-type]
    initial = app_a.state.workspace_service.import_github(
        GitHubImportRequest(
            name="Remote Python",
            url="https://github.com/owner/repo",
            ref="main",
        )
    )
    project_id = initial.project.project_id
    first_index_id = initial.index.index.index_id  # type: ignore[union-attr]
    second_result = app_a.state.workspace_service.refresh_github_source(project_id, first_index_id)
    second_index_id = second_result.index.index.index_id  # type: ignore[union-attr]
    app_b = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client_b)  # type: ignore[arg-type]

    stale_error: list[ServiceError] = []

    def stale_restore() -> None:
        try:
            app_a.state.workspace_service.refresh_github_source(project_id, second_index_id)
        except ServiceError as exc:
            stale_error.append(exc)

    worker = threading.Thread(target=stale_restore)
    worker.start()
    assert started.wait(timeout=10)
    updated = app_b.state.workspace_service.refresh_github_source(project_id, second_index_id)
    assert updated.mode == "created"
    latest_after_b = updated.index.index.index_id  # type: ignore[union-attr]
    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert len(stale_error) == 1
    assert stale_error[0].code == "conflict"

    current = app_a.state.workspace_service.get_project(project_id)
    assert current.latest_index_id == latest_after_b
    source = app_a.state.registry.management_store.load_source(initial.attempt.repository_id)
    assert source.resolved_commit == third
    assert source.source_url == "https://github.com/owner/repo"
    assert source.requested_ref == "main"
    assert source.resolved_ref == "main"
    assert source.content_hash == updated.index.index.snapshot_hash  # type: ignore[union-attr]


def test_github_project_update_recovers_source_commit_after_registry_failure(tmp_path: Path) -> None:
    first = "a" * 40
    second = "b" * 40
    client = _FakeGitHubClient([first, second, second])
    data_root = tmp_path / "service-data"
    app = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client)  # type: ignore[arg-type]
    initial = app.state.workspace_service.import_github(
        GitHubImportRequest(
            name="Remote Python",
            url="https://github.com/owner/repo",
            ref="main",
        )
    )
    project_id = initial.project.project_id
    baseline_index_id = initial.index.index.index_id  # type: ignore[union-attr]
    repository_id = initial.attempt.repository_id
    management_store = app.state.registry.management_store
    original_commit = management_store.commit_project_update

    def fail_registry_commit(*_args, **_kwargs) -> None:
        raise StorageError("injected registry commit failure")

    management_store.commit_project_update = fail_registry_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(ServiceError, match="existing index was kept"):
            app.state.workspace_service.refresh_github_source(project_id, baseline_index_id)
    finally:
        management_store.commit_project_update = original_commit  # type: ignore[method-assign]

    pending = management_store.list_project_updates()
    assert len(pending) == 1
    operation = pending[0]
    assert operation.owner_pid is None
    assert operation.candidate_index_id is not None
    assert app.state.workspace_service.get_project(project_id).latest_index_id == operation.candidate_index_id
    assert management_store.load_source(repository_id).resolved_commit == first
    management_store.update_project_update(operation.operation_id, owner_pid=os.getpid())

    reopened = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client)  # type: ignore[arg-type]
    assert reopened.state.registry.management_store.list_project_updates() == ()
    recovered_project = reopened.state.workspace_service.get_project(project_id)
    assert recovered_project.latest_index_id == operation.candidate_index_id
    recovered_source = reopened.state.registry.management_store.load_source(repository_id)
    assert recovered_source.resolved_commit == second
    recovered_attempt = reopened.state.registry.management_store.load_attempt(operation.attempt_id)  # type: ignore[arg-type]
    assert recovered_attempt.status == "ready"
    assert recovered_attempt.index_id == operation.candidate_index_id


def test_github_build_failure_aborts_update_without_changing_latest_or_source(tmp_path: Path) -> None:
    import asyncio

    first = "a" * 40
    second = "b" * 40
    client = _FakeGitHubClient([first, second, second])
    data_root = tmp_path / "service-data"
    app = create_app({}, data_root=data_root, github_import_enabled=True, github_client=client)  # type: ignore[arg-type]
    initial = app.state.workspace_service.import_github(
        GitHubImportRequest(
            name="Remote Python",
            url="https://github.com/owner/repo",
            ref="main",
        )
    )
    project_id = initial.project.project_id
    baseline_index_id = initial.index.index.index_id  # type: ignore[union-attr]
    repository_id = initial.attempt.repository_id
    service = app.state.workspace_service
    original_create = service._create_project_index

    def fail_build(*_args, **_kwargs):
        raise ServiceError("internal_error", 503, "injected GitHub index build failure")

    service._create_project_index = fail_build  # type: ignore[method-assign]
    try:
        status, failed = asyncio.run(
            _request(app, "POST", f"/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh")
        )
    finally:
        service._create_project_index = original_create  # type: ignore[method-assign]

    assert status == 503
    assert failed["code"] == "internal_error"
    assert app.state.workspace_service.get_project(project_id).latest_index_id == baseline_index_id
    assert app.state.registry.management_store.load_source(repository_id).resolved_commit == first
    assert app.state.registry.management_store.list_project_updates() == ()
