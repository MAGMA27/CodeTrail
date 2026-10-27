"""Bounded access to public GitHub repositories and safe archive extraction."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_GITHUB_API_HOST = "api.github.com"
_GITHUB_REDIRECT_HOSTS = frozenset({"api.github.com", "codeload.github.com", "github.com"})
_GIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_DEFAULT_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_DEFAULT_MAX_FILE_COUNT = 10_000
_DEFAULT_MAX_UNPACKED_BYTES = 200 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_DOWNLOAD_IDLE_TIMEOUT_SECONDS = 30.0
_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 300.0
_MAX_JSON_BYTES = 2 * 1024 * 1024
_DEFAULT_MAX_RATE_LIMIT_RETRIES = 3
_DEFAULT_MAX_RETRY_WAIT_SECONDS = 20.0
_DEFAULT_RATE_LIMIT_WAIT_BUDGET_SECONDS = 20.0
_TOKEN_ENVIRONMENT_VARIABLES = ("CODETRAIL_GITHUB_TOKEN", "GITHUB_TOKEN")
_CHUNK_SIZE = 64 * 1024


class GitHubError(RuntimeError):
    """A safe, user-facing error produced while acquiring a GitHub archive."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class GitHubRepository:
    """The only remote identity accepted by the importer."""

    owner: str
    name: str
    canonical_url: str
    identity: str


@dataclass(frozen=True)
class GitHubResolution:
    repository: GitHubRepository
    requested_ref: str | None
    resolved_commit: str
    resolved_ref: str | None = None


@dataclass(frozen=True)
class GitHubArchive:
    resolution: GitHubResolution
    archive_hash: str
    extracted_file_count: int
    extracted_bytes: int


@dataclass(frozen=True)
class _ResponseData:
    status: int
    headers: Mapping[str, str]
    stream: object


@dataclass
class GitHubRequestBudget:
    """Cumulative rate-limit sleep allowance shared by one GitHub operation."""

    remaining_wait_seconds: float


class _NoRedirectHandler(HTTPRedirectHandler):
    """Expose redirect responses so the target can be checked explicitly."""

    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: Mapping[str, str], newurl: str):  # type: ignore[override]
        return None


class GitHubClient:
    """Resolve a GitHub ref and fetch one bounded tar archive.

    The opener is injectable so all archive, redirect, budget, and interruption
    behavior can be tested offline without contacting GitHub.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        opener: object | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        download_idle_timeout_seconds: float | None = None,
        download_timeout_seconds: float | None = None,
        max_archive_bytes: int = _DEFAULT_MAX_ARCHIVE_BYTES,
        max_file_count: int = _DEFAULT_MAX_FILE_COUNT,
        max_unpacked_bytes: int = _DEFAULT_MAX_UNPACKED_BYTES,
        max_redirects: int = 2,
        max_rate_limit_wait_seconds: float = _DEFAULT_RATE_LIMIT_WAIT_BUDGET_SECONDS,
        max_rate_limit_retries: int = _DEFAULT_MAX_RATE_LIMIT_RETRIES,
        max_retry_wait_seconds: float = _DEFAULT_MAX_RETRY_WAIT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        download_idle_timeout_seconds = (
            _timeout_from_environment("CODETRAIL_GITHUB_DOWNLOAD_IDLE_TIMEOUT", _DEFAULT_DOWNLOAD_IDLE_TIMEOUT_SECONDS)
            if download_idle_timeout_seconds is None
            else download_idle_timeout_seconds
        )
        download_timeout_seconds = (
            _timeout_from_environment("CODETRAIL_GITHUB_DOWNLOAD_TIMEOUT", _DEFAULT_DOWNLOAD_TIMEOUT_SECONDS)
            if download_timeout_seconds is None
            else download_timeout_seconds
        )
        if (
            not all(
                math.isfinite(value) and value > 0
                for value in (
                    timeout_seconds,
                    download_idle_timeout_seconds,
                    download_timeout_seconds,
                    max_rate_limit_wait_seconds,
                )
            )
            or max_archive_bytes <= 0
            or max_file_count <= 0
            or max_unpacked_bytes <= 0
        ):
            raise ValueError("GitHub acquisition budgets must be positive")
        if max_redirects < 0 or max_rate_limit_retries < 0 or max_retry_wait_seconds < 0:
            raise ValueError("GitHub redirect and retry budgets must not be negative")
        if max_rate_limit_retries > _DEFAULT_MAX_RATE_LIMIT_RETRIES:
            raise ValueError("GitHub rate-limit retries cannot exceed three")
        self.timeout_seconds = timeout_seconds
        self.max_rate_limit_wait_seconds = max_rate_limit_wait_seconds
        self.download_idle_timeout_seconds = download_idle_timeout_seconds
        self.download_timeout_seconds = download_timeout_seconds
        self.max_archive_bytes = max_archive_bytes
        self.max_file_count = max_file_count
        self.max_unpacked_bytes = max_unpacked_bytes
        self.max_redirects = max_redirects
        self.max_rate_limit_retries = max_rate_limit_retries
        self.max_retry_wait_seconds = max_retry_wait_seconds
        self._sleep = sleep
        self._clock = clock
        self._monotonic = monotonic
        # The token is only ever sent as an Authorization header; it is never
        # logged, persisted, or included in error messages.
        self._token = (token if token is not None else _token_from_environment()) or None
        self._opener = opener or build_opener(_NoRedirectHandler())

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def new_budget(self) -> GitHubRequestBudget:
        """Start one cumulative rate-limit wait budget for an import operation."""

        return GitHubRequestBudget(self.max_rate_limit_wait_seconds)

    def resolve(
        self,
        url: str,
        requested_ref: str | None,
        *,
        budget: GitHubRequestBudget | None = None,
    ) -> GitHubResolution:
        repository = parse_github_repository_url(url)
        ref = _normalize_ref(requested_ref)
        operation_budget = budget or self.new_budget()
        if ref is None:
            metadata = self._request_json(
                f"https://{_GITHUB_API_HOST}/repos/{quote(repository.owner)}/{quote(repository.name)}",
                budget=operation_budget,
            )
            default_branch = metadata.get("default_branch")
            if not isinstance(default_branch, str) or not default_branch:
                raise GitHubError("internal_error", 503, "GitHub did not return a default branch")
            ref = default_branch
        commit = self._request_json(
            f"https://{_GITHUB_API_HOST}/repos/{quote(repository.owner)}/{quote(repository.name)}/commits/{quote(ref, safe='')}",
            budget=operation_budget,
        )
        sha = commit.get("sha")
        if not isinstance(sha, str) or not _GIT_SHA_PATTERN.fullmatch(sha):
            raise GitHubError("internal_error", 503, "GitHub did not return a valid commit SHA")
        return GitHubResolution(
            repository=repository,
            requested_ref=requested_ref,
            resolved_commit=sha.lower(),
            resolved_ref=ref,
        )

    def download_and_extract(
        self,
        resolution: GitHubResolution,
        destination: Path,
        *,
        budget: GitHubRequestBudget | None = None,
    ) -> GitHubArchive:
        operation_budget = budget or self.new_budget()
        destination = destination.resolve(strict=False)
        destination.mkdir(parents=True, exist_ok=False)
        archive_path = destination.parent / f"{destination.name}.tar.gz"
        try:
            archive_hash = self._download_archive(resolution, archive_path, budget=operation_budget)
            file_count, unpacked_bytes = extract_github_archive(
                archive_path,
                destination,
                max_file_count=self.max_file_count,
                max_unpacked_bytes=self.max_unpacked_bytes,
            )
            return GitHubArchive(
                resolution=resolution,
                archive_hash=archive_hash,
                extracted_file_count=file_count,
                extracted_bytes=unpacked_bytes,
            )
        finally:
            _unlink_owned_file(archive_path)

    def _headers(self, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "CodeTrail/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request_json(self, url: str, *, budget: GitHubRequestBudget) -> dict[str, object]:
        response = self._open(url, allowed_hosts={_GITHUB_API_HOST}, budget=budget)
        try:
            body = self._read_response(
                response,
                max_bytes=_MAX_JSON_BYTES,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status != 200:
                raise _http_error(response.status, response.headers, authenticated=self.authenticated)
        finally:
            _close_response(response)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubError("internal_error", 503, "GitHub returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise GitHubError("internal_error", 503, "GitHub returned an invalid response")
        return payload

    def _download_archive(
        self,
        resolution: GitHubResolution,
        archive_path: Path,
        *,
        budget: GitHubRequestBudget,
    ) -> str:
        deadline = self._monotonic() + self.download_timeout_seconds
        timeout_error = GitHubError("download_timeout", 504, "GitHub archive download timed out")
        url = (
            f"https://{_GITHUB_API_HOST}/repos/{quote(resolution.repository.owner)}/"
            f"{quote(resolution.repository.name)}/tarball/{resolution.resolved_commit}"
        )
        for redirect_count in range(self.max_redirects + 1):
            response = self._open(
                url,
                allowed_hosts=_GITHUB_REDIRECT_HOSTS,
                budget=budget,
                timeout_seconds=self.download_idle_timeout_seconds,
                deadline=deadline,
                timeout_error=timeout_error,
            )
            if response.status in {301, 302, 303, 307, 308}:
                try:
                    location = _header(response.headers, "location")
                    if not location:
                        raise GitHubError("internal_error", 503, "GitHub archive redirect has no target")
                    if redirect_count >= self.max_redirects:
                        raise GitHubError("budget_exceeded", 429, "GitHub archive redirect limit was exceeded")
                    url = _validate_remote_url(location, _GITHUB_REDIRECT_HOSTS)
                    continue
                finally:
                    _close_response(response)
            if response.status != 200:
                try:
                    raise _http_error(response.status, response.headers, authenticated=self.authenticated)
                finally:
                    _close_response(response)
            try:
                return self._stream_to_file(
                    response,
                    archive_path,
                    deadline=deadline,
                    timeout_seconds=self.download_idle_timeout_seconds,
                    timeout_error=timeout_error,
                )
            finally:
                _close_response(response)
        raise GitHubError("budget_exceeded", 429, "GitHub archive redirect limit was exceeded")

    def _open(
        self,
        url: str,
        *,
        allowed_hosts: set[str] | frozenset[str],
        budget: GitHubRequestBudget,
        timeout_seconds: float | None = None,
        deadline: float | None = None,
        timeout_error: GitHubError | None = None,
    ) -> _ResponseData:
        """Open one request, waiting out GitHub rate limits within bounded retries.

        Waits honor ``Retry-After`` first, then ``X-RateLimit-Reset`` when the
        quota is exhausted, and otherwise back off exponentially. A wait longer
        than the per-wait limit, the remaining cumulative wait allowance, or
        the remaining download time is not attempted.
        """

        for attempt in range(self.max_rate_limit_retries + 1):
            response = self._open_once(
                url,
                allowed_hosts=allowed_hosts,
                timeout_seconds=timeout_seconds or self.timeout_seconds,
                deadline=deadline,
                timeout_error=timeout_error,
            )
            if not _is_rate_limited(response.status, response.headers):
                return response
            _close_response(response)
            wait = _rate_limit_wait_seconds(response.headers, attempt, now=self._clock())
            remaining_download = None if deadline is None else deadline - self._monotonic()
            if (
                attempt >= self.max_rate_limit_retries
                or wait > self.max_retry_wait_seconds
                or wait > budget.remaining_wait_seconds
                or (remaining_download is not None and wait >= remaining_download)
            ):
                raise _rate_limit_error(response.headers, wait, authenticated=self.authenticated)
            before_sleep = self._monotonic()
            self._sleep(wait)
            elapsed_sleep = max(wait, self._monotonic() - before_sleep)
            budget.remaining_wait_seconds = max(0.0, budget.remaining_wait_seconds - elapsed_sleep)
        raise AssertionError("unreachable")  # pragma: no cover

    def _open_once(
        self,
        url: str,
        *,
        allowed_hosts: set[str] | frozenset[str],
        timeout_seconds: float,
        deadline: float | None = None,
        timeout_error: GitHubError | None = None,
    ) -> _ResponseData:
        safe_url = _validate_remote_url(url, allowed_hosts)
        request = Request(safe_url, headers=self._headers(), method="GET")
        timeout = timeout_seconds
        if deadline is not None:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise timeout_error or GitHubError("download_timeout", 504, "GitHub archive download timed out")
            timeout = min(timeout, remaining)
        try:
            response = self._opener.open(request, timeout=timeout)  # type: ignore[attr-defined]
        except HTTPError as exc:
            return _ResponseData(exc.code, _headers_from_http(exc.headers), exc)
        except (TimeoutError, URLError, OSError) as exc:
            if timeout_error is not None and _is_timeout_exception(exc):
                raise timeout_error from exc
            raise GitHubError("internal_error", 503, "GitHub request failed or timed out") from exc
        return _ResponseData(
            status=int(getattr(response, "status", getattr(response, "code", 0))),
            headers=_headers_from_http(getattr(response, "headers", {})),
            stream=response,
        )

    def _read_response(
        self,
        response: _ResponseData,
        *,
        max_bytes: int,
        deadline: float | None = None,
        timeout_seconds: float,
    ) -> bytes:
        _check_deadline(deadline, monotonic=self._monotonic)
        stream = response.stream
        body = getattr(stream, "_body", None)
        if isinstance(body, bytes):
            if len(body) > max_bytes:
                raise GitHubError("budget_exceeded", 429, "GitHub response exceeded the configured size limit")
            return body
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                _check_deadline(deadline, monotonic=self._monotonic)
                _cap_stream_timeout(
                    stream,
                    deadline,
                    timeout_seconds=timeout_seconds,
                    monotonic=self._monotonic,
                )
                chunk = stream.read(min(_CHUNK_SIZE, max_bytes - total + 1))  # type: ignore[attr-defined]
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise GitHubError("budget_exceeded", 429, "GitHub response exceeded the configured size limit")
                chunks.append(chunk)
        except GitHubError:
            raise
        except (TimeoutError, URLError, OSError) as exc:
            if deadline is not None and self._monotonic() >= deadline:
                raise GitHubError("budget_exceeded", 429, "GitHub total request time exceeded the configured limit") from exc
            raise GitHubError("internal_error", 503, "GitHub response read failed or timed out") from exc
        return b"".join(chunks)

    def _stream_to_file(
        self,
        response: _ResponseData,
        archive_path: Path,
        *,
        deadline: float | None = None,
        timeout_seconds: float,
        timeout_error: GitHubError,
    ) -> str:
        stream = response.stream
        digest = hashlib.sha256()
        total = 0
        try:
            with archive_path.open("xb") as handle:
                while True:
                    _cap_stream_timeout(
                        stream,
                        deadline,
                        timeout_seconds=timeout_seconds,
                        monotonic=self._monotonic,
                        timeout_error=timeout_error,
                    )
                    read_chunk = getattr(stream, "read1", None)
                    if not callable(read_chunk):
                        read_chunk = getattr(stream, "read")
                    chunk = read_chunk(_CHUNK_SIZE)
                    if deadline is not None and self._monotonic() >= deadline:
                        raise timeout_error
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_archive_bytes:
                        raise GitHubError("budget_exceeded", 429, "GitHub archive exceeded the configured size limit")
                    digest.update(chunk)
                    handle.write(chunk)
        except GitHubError:
            raise
        except (TimeoutError, URLError, OSError) as exc:
            if _is_timeout_exception(exc):
                raise timeout_error from exc
            raise GitHubError("internal_error", 500, "GitHub archive could not be stored") from exc
        if total == 0:
            raise GitHubError("internal_error", 503, "GitHub returned an empty archive")
        return digest.hexdigest()


def parse_github_repository_url(value: str) -> GitHubRepository:
    """Accept only `https://github.com/{owner}/{repo}` repository URLs."""

    raw = value.strip()
    if not raw or len(raw) > 4_000:
        raise GitHubError("invalid_input", 400, "GitHub repository URL is invalid")
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise GitHubError("invalid_input", 400, "GitHub repository URL is invalid") from exc
    if parsed.scheme.casefold() != "https" or hostname is None or hostname.casefold() != "github.com":
        raise GitHubError("invalid_input", 400, "GitHub URL must use the official HTTPS repository host")
    if parsed.username is not None or parsed.password is not None or port not in {None, 443}:
        raise GitHubError("invalid_input", 400, "GitHub URL must not contain credentials or a custom port")
    if parsed.query or parsed.fragment or ";" in parsed.path:
        raise GitHubError("invalid_input", 400, "GitHub URL must not contain a query, fragment, or parameters")
    if "%" in parsed.path:
        raise GitHubError("invalid_input", 400, "GitHub URL must not contain encoded path segments")
    stripped_path = parsed.path.strip("/")
    parts = stripped_path.split("/") if stripped_path else []
    if len(parts) != 2:
        raise GitHubError("invalid_input", 400, "GitHub URL must point to exactly one repository")
    owner, name = (part.strip() for part in parts)
    if not owner or not name or owner in {".", ".."} or name in {".", ".."}:
        raise GitHubError("invalid_input", 400, "GitHub repository identity is invalid")
    if name.casefold().endswith(".git"):
        name = name[:-4]
    if not name or any(char in owner + name for char in "\\\x00"):
        raise GitHubError("invalid_input", 400, "GitHub repository identity is invalid")
    canonical_owner = owner.casefold()
    canonical_name = name.casefold()
    canonical_url = f"https://github.com/{canonical_owner}/{canonical_name}"
    return GitHubRepository(
        owner=owner,
        name=name,
        canonical_url=canonical_url,
        identity=f"github://{canonical_owner}/{canonical_name}",
    )


def extract_github_archive(
    archive_path: Path,
    destination: Path,
    *,
    max_file_count: int = _DEFAULT_MAX_FILE_COUNT,
    max_unpacked_bytes: int = _DEFAULT_MAX_UNPACKED_BYTES,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, int]:
    """Extract a GitHub tar archive without links, traversal, or special files."""

    if max_file_count <= 0 or max_unpacked_bytes <= 0:
        raise ValueError("archive extraction budgets must be positive")
    destination = destination.resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        _check_deadline(deadline, monotonic=monotonic)
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = archive.getmembers()
            _check_deadline(deadline, monotonic=monotonic)
            if len(members) > max_file_count * 2:
                raise GitHubError("budget_exceeded", 429, "GitHub archive contains too many entries")
            normalized_members = [_normalized_member(member.name) for member in members]
            _check_deadline(deadline, monotonic=monotonic)
            prefix = _archive_prefix(normalized_members)
            seen: set[str] = set()
            file_count = 0
            unpacked_bytes = 0
            for member, normalized in zip(members, normalized_members):
                _check_deadline(deadline, monotonic=monotonic)
                relative = _strip_archive_prefix(normalized, prefix)
                if not relative:
                    continue
                if relative in seen:
                    raise GitHubError("invalid_input", 400, "GitHub archive contains duplicate paths")
                seen.add(relative)
                target = _safe_target(destination, relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isreg():
                    raise GitHubError("invalid_input", 400, "GitHub archive contains links or special files")
                file_count += 1
                if file_count > max_file_count:
                    raise GitHubError("budget_exceeded", 429, "GitHub archive contains too many files")
                if member.size < 0 or member.size > max_unpacked_bytes - unpacked_bytes:
                    raise GitHubError("budget_exceeded", 429, "GitHub archive exceeds the unpacked size limit")
                unpacked_bytes += member.size
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise GitHubError("invalid_input", 400, "GitHub archive contains an unreadable file")
                with extracted, target.open("xb") as handle:
                    while True:
                        _check_deadline(deadline, monotonic=monotonic)
                        chunk = extracted.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
            return file_count, unpacked_bytes
    except GitHubError:
        raise
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise GitHubError("invalid_input", 400, "GitHub archive is not a valid safe tar archive") from exc


def _normalize_ref(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 200 or "\x00" in normalized:
        raise GitHubError("invalid_input", 400, "GitHub ref must be a non-empty bounded string")
    return normalized


def _validate_remote_url(value: str, allowed_hosts: set[str] | frozenset[str]) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise GitHubError("forbidden", 403, "GitHub redirect target is invalid") from exc
    host = hostname.casefold() if hostname else None
    if parsed.scheme.casefold() != "https" or host not in allowed_hosts:
        raise GitHubError("forbidden", 403, "GitHub redirect target is not an allowed official HTTPS host")
    if parsed.username is not None or parsed.password is not None or port not in {None, 443}:
        raise GitHubError("forbidden", 403, "GitHub redirect target contains an unsafe authority")
    try:
        if host is not None and ipaddress.ip_address(host).is_private:
            raise GitHubError("forbidden", 403, "GitHub redirect target is private or loopback")
    except ValueError:
        pass
    return value


def _token_from_environment() -> str | None:
    for name in _TOKEN_ENVIRONMENT_VARIABLES:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _is_rate_limited(status: int, headers: Mapping[str, str]) -> bool:
    """429 always; 403 only when GitHub marks it as a primary or secondary rate limit."""

    if status == 429:
        return True
    if status != 403:
        return False
    return _header(headers, "retry-after") is not None or _header(headers, "x-ratelimit-remaining") == "0"


def _rate_limit_wait_seconds(headers: Mapping[str, str], attempt: int, *, now: float) -> float:
    retry_after = _header(headers, "retry-after")
    if retry_after is not None:
        value = retry_after.strip()
        if value.isdigit():
            return float(int(value))
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            retry_at = None
        if retry_at is not None:
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - now)
    reset = _header(headers, "x-ratelimit-reset")
    if _header(headers, "x-ratelimit-remaining") == "0" and reset is not None and reset.strip().isdigit():
        return max(0.0, int(reset.strip()) - now) + 1.0
    return float(2**attempt)


def _rate_limit_error(headers: Mapping[str, str], wait_seconds: float, *, authenticated: bool) -> GitHubError:
    parts = ["GitHub API rate limit reached; the import was not completed."]
    reset = _header(headers, "x-ratelimit-reset")
    if _header(headers, "x-ratelimit-remaining") == "0" and reset is not None and reset.strip().isdigit():
        reset_at = datetime.fromtimestamp(int(reset.strip()), tz=timezone.utc)
        parts.append(f"The limit resets at {reset_at.strftime('%Y-%m-%d %H:%M:%S')} UTC.")
    parts.append(f"Retry after about {max(1, int(-(-wait_seconds // 1)))} seconds.")
    if authenticated:
        parts.append("The configured GitHub token is also rate limited.")
    else:
        parts.append(
            "Unauthenticated requests share a low per-IP limit; set CODETRAIL_GITHUB_TOKEN "
            "(or GITHUB_TOKEN) on the API server to raise it."
        )
    return GitHubError("budget_exceeded", 429, " ".join(parts))


def _http_error(status: int, headers: Mapping[str, str], *, authenticated: bool = False) -> GitHubError:
    if status == 404:
        return GitHubError("not_found", 404, "GitHub repository or ref was not found, or is not public")
    if _is_rate_limited(status, headers):
        return _rate_limit_error(headers, _rate_limit_wait_seconds(headers, 0, now=time.time()), authenticated=authenticated)
    if status == 401:
        message = (
            "GitHub rejected the configured token; check CODETRAIL_GITHUB_TOKEN or GITHUB_TOKEN on the API server"
            if authenticated
            else "GitHub requires authentication for this request"
        )
        return GitHubError("forbidden", 403, message)
    if status == 403:
        return GitHubError("forbidden", 403, "GitHub denied access to this repository or ref")
    if 500 <= status <= 599:
        return GitHubError("internal_error", 503, "GitHub is temporarily unavailable")
    return GitHubError("internal_error", 503, "GitHub returned an unexpected response")


def _check_deadline(
    deadline: float | None,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise GitHubError("budget_exceeded", 429, "GitHub total request time exceeded the configured limit")


def _cap_stream_timeout(
    stream: object,
    deadline: float | None,
    *,
    timeout_seconds: float,
    monotonic: Callable[[], float],
    timeout_error: GitHubError | None = None,
) -> None:
    """Apply an idle timeout, capped by a total download deadline when present."""

    timeout = timeout_seconds
    if deadline is not None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise timeout_error or GitHubError("budget_exceeded", 429, "GitHub total request time exceeded the configured limit")
        timeout = min(timeout, remaining)
    buffered = getattr(stream, "fp", None)
    raw = getattr(buffered, "raw", None)
    sock = getattr(raw, "_sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        settimeout(timeout)


def _timeout_from_environment(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _is_timeout_exception(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, TimeoutError)


def _headers_from_http(headers: object) -> dict[str, str]:
    if hasattr(headers, "items"):
        return {str(key).casefold(): str(value) for key, value in headers.items()}  # type: ignore[union-attr]
    if isinstance(headers, Mapping):
        return {str(key).casefold(): str(value) for key, value in headers.items()}
    return {}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return headers.get(name.casefold())


def _close_response(response: _ResponseData) -> None:
    close = getattr(response.stream, "close", None)
    if callable(close):
        close()


def _normalized_member(name: str) -> str:
    if not name or "\x00" in name:
        raise GitHubError("invalid_input", 400, "GitHub archive contains an invalid path")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise GitHubError("invalid_input", 400, "GitHub archive contains an absolute path")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise GitHubError("invalid_input", 400, "GitHub archive contains a traversal path")
    if ":" in parts[0]:
        raise GitHubError("invalid_input", 400, "GitHub archive contains a drive-qualified path")
    return "/".join(parts)


def _archive_prefix(members: list[str]) -> str | None:
    first_segments = {member.split("/", 1)[0] for member in members if member}
    if len(first_segments) != 1:
        return None
    prefix = next(iter(first_segments))
    if all(member == prefix or member.startswith(f"{prefix}/") for member in members):
        return prefix
    return None


def _strip_archive_prefix(value: str, prefix: str | None) -> str:
    if prefix is None:
        return value
    if value == prefix:
        return ""
    return value[len(prefix) + 1 :]


def _safe_target(root: Path, relative: str) -> Path:
    target = (root / PurePosixPath(relative)).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise GitHubError("invalid_input", 400, "GitHub archive path escaped the managed temp directory") from exc
    return target


def _unlink_owned_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # The archive is inside the per-attempt temp directory and the caller
        # will report a source cleanup failure if the directory remains.
        pass
