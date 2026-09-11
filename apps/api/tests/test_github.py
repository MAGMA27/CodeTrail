"""Offline GitHub acquisition, archive safety, and import lifecycle tests."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.github import (
    GitHubArchive,
    GitHubClient,
    GitHubError,
    GitHubResolution,
    extract_github_archive,
    parse_github_repository_url,
)
from app.main import create_app


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

    def open(self, request, timeout: float) -> _FakeResponse:
        url = request.full_url
        self.calls.append(url)
        values = self.responses.get(url)
        if not values:
            raise AssertionError(f"unexpected GitHub request: {url}")
        return values.pop(0)


def test_github_rate_limit_redirect_and_timeout_fail_with_bounded_errors(tmp_path: Path) -> None:
    api_commit = "https://api.github.com/repos/owner/repo/commits/main"
    rate_limited = _FakeOpener({api_commit: [_FakeResponse(429, headers={"Retry-After": "3"})]})
    with pytest.raises(GitHubError) as rate_error:
        GitHubClient(opener=rate_limited).resolve("https://github.com/owner/repo", "main")
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

    def resolve(self, url: str, requested_ref: str | None) -> GitHubResolution:
        repository = parse_github_repository_url(url)
        return GitHubResolution(repository, requested_ref, next(self.commits))

    def download_and_extract(self, resolution: GitHubResolution, destination: Path) -> GitHubArchive:
        destination.mkdir(parents=True, exist_ok=False)
        self.downloads += 1
        (destination / "module.py").write_text(
            f"COMMIT = '{resolution.resolved_commit}'\n\ndef run() -> str:\n    return COMMIT\n",
            encoding="utf-8",
        )
        return GitHubArchive(resolution, "b" * 64, 1, (destination / "module.py").stat().st_size)


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

    status, indexes = asyncio.run(_request(app, "GET", f"/api/projects/{project_id}/indexes"))
    assert status == 200
    assert len(indexes["indexes"]) == 2


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
