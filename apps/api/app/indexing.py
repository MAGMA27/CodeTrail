"""Read-only Python snapshots and deterministic definition extraction."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from .contracts import IndexSnapshot, Symbol


IssueKind = Literal[
    "decode_error",
    "file_too_large",
    "read_error",
    "symlink",
    "syntax_error",
    "outside_root",
    "capture_incomplete",
    "parser_unavailable",
    "parser_error",
    "typescript_syntax_error",
    "manifest_invalid",
]

SUBSYSTEM_MANIFEST_PATH = "codetrail-subsystems.json"
SUBSYSTEM_MANIFEST_MAX_BYTES = 64 * 1024
SUBSYSTEM_MANIFEST_MAX_GROUPS = 80
SUBSYSTEM_MANIFEST_MAX_OWNERS = 400
SUBSYSTEM_MANIFEST_MAX_PATH_LENGTH = 500
_SUBSYSTEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DEFAULT_INDEX_MAX_TOTAL_BYTES = 50 * 1024 * 1024

_EXCLUDED_DIRECTORIES = {
    ".codetrail",
    ".codetrail-runtime",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".next",
    ".turbo",
    ".vercel",
    ".vite",
    ".parcel-cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "out",
    "storybook-static",
    "target",
    "venv",
}
_BLOCKING_ISSUES = {
    "decode_error",
    "file_too_large",
    "outside_root",
    "read_error",
    "symlink",
    "capture_incomplete",
    "parser_unavailable",
    "parser_error",
    "typescript_syntax_error",
}


class SnapshotBoundaryError(ValueError):
    """Raised when a snapshot request leaves its configured repository root."""


class IndexingAborted(RuntimeError):
    """Indexing stopped at its deadline or after its HTTP client disconnected."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class IndexingControl:
    """Thread-safe cancellation signal with a per-build monotonic deadline."""

    cancelled: threading.Event = field(default_factory=threading.Event)
    deadline: float | None = None

    def start(self, timeout_seconds: float) -> None:
        self.deadline = time.monotonic() + timeout_seconds

    def cancel(self) -> None:
        self.cancelled.set()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise IndexingAborted("index_cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise IndexingAborted("index_timeout")


def index_timeout_from_environment() -> float:
    """Read the positive finite server indexing timeout, defaulting to 240s."""

    raw_value = os.getenv("CODETRAIL_INDEX_TIMEOUT")
    if raw_value is None:
        return 240.0
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 240.0
    return value if math.isfinite(value) and value > 0 else 240.0


def index_max_total_bytes_from_environment() -> int:
    """Read the positive source byte limit, defaulting to 50 MiB."""
    raw_value = os.getenv("CODETRAIL_INDEX_MAX_TOTAL_BYTES")
    try:
        value = int(raw_value) if raw_value is not None else DEFAULT_INDEX_MAX_TOTAL_BYTES
    except (TypeError, ValueError):
        return DEFAULT_INDEX_MAX_TOTAL_BYTES
    return value if value > 0 else DEFAULT_INDEX_MAX_TOTAL_BYTES


@dataclass(frozen=True)
class SnapshotIssue:
    """A file or request that could not be safely included in a snapshot."""

    path: str
    kind: IssueKind
    message: str
    size: int | None = None
    count: int | None = None


@dataclass(frozen=True)
class SnapshotFile:
    """One bounded UTF-8 source or metadata file in the immutable snapshot."""

    path: str
    size: int
    content_hash: str
    text: str


@dataclass(frozen=True)
class SnapshotExcerpt:
    """A bounded read from the same text held by a source snapshot."""

    path: str
    start_line: int
    end_line: int
    excerpt: str
    content_hash: str


@dataclass(frozen=True)
class SourceSnapshot:
    """Index metadata, file contents, and non-fatal snapshot issues."""

    index: IndexSnapshot
    files: tuple[SnapshotFile, ...]
    issues: tuple[SnapshotIssue, ...]

    def read_source(
        self,
        relative_path: str,
        start_line: int,
        end_line: int,
        *,
        max_lines: int = 200,
    ) -> SnapshotExcerpt:
        """Read bounded lines without touching the repository after indexing."""

        if start_line < 1 or end_line < start_line:
            raise SnapshotBoundaryError("source line range is invalid")
        if end_line - start_line + 1 > max_lines:
            raise SnapshotBoundaryError("source read exceeds the line limit")

        normalized_path = _normalize_relative_path(relative_path)
        file_by_path = {source_file.path: source_file for source_file in self.files}
        source_file = file_by_path.get(normalized_path)
        if source_file is None:
            raise SnapshotBoundaryError("source path is not present in the snapshot")

        lines = source_file.text.splitlines()
        if not lines:
            lines = [""]
        if end_line > len(lines):
            raise SnapshotBoundaryError("source line range is outside the snapshot")

        return SnapshotExcerpt(
            path=normalized_path,
            start_line=start_line,
            end_line=end_line,
            excerpt="\n".join(lines[start_line - 1 : end_line]),
            content_hash=source_file.content_hash,
        )


@dataclass(frozen=True)
class SourceManifestScan:
    """Bounded source manifest observation used without parsing Python."""

    files: tuple[SnapshotFile, ...]
    issues: tuple[SnapshotIssue, ...]
    scanned_files: int
    scanned_bytes: int
    scan_complete: bool
    scan_truncated: bool
    scan_truncation_reason: str | None
    scan_truncation_size: int | None = None
    scan_truncation_count: int | None = None
    unstable_paths: tuple[str, ...] = ()

    @property
    def snapshot_hash(self) -> str:
        """Return the same content identity used by a source snapshot."""

        return _snapshot_hash(list(self.files), list(self.issues))


@dataclass(frozen=True)
class SubsystemOwner:
    """One exact file or directory-prefix ownership declaration."""

    path: str
    directory_prefix: bool


@dataclass(frozen=True)
class SubsystemSpec:
    """One author-declared runtime responsibility group."""

    subsystem_id: str
    label: str
    owners: tuple[SubsystemOwner, ...]


class SubsystemManifestError(ValueError):
    """A bounded, safe diagnostic code for an invalid subsystem manifest."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_source_code_path(path: str) -> bool:
    """Return whether the path is parsed as Python or TypeScript source."""

    return Path(path).suffix.lower() in {".py", ".ts", ".tsx"}


def parse_subsystem_manifest(text: str) -> tuple[SubsystemSpec, ...]:
    """Parse a strict, bounded runtime-ownership manifest without touching disk."""

    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SubsystemManifestError("invalid_unicode") from exc
    if encoded_size > SUBSYSTEM_MANIFEST_MAX_BYTES:
        raise SubsystemManifestError("file_too_large")
    try:
        payload = json.loads(text, object_pairs_hook=_manifest_object_without_duplicate_keys)
    except _DuplicateManifestKey as exc:
        raise SubsystemManifestError("duplicate_key") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SubsystemManifestError("invalid_json") from exc

    if not isinstance(payload, dict) or set(payload) != {"schema_version", "subsystems"}:
        raise SubsystemManifestError("invalid_shape")
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise SubsystemManifestError("unsupported_schema_version")
    raw_subsystems = payload.get("subsystems")
    if not isinstance(raw_subsystems, list) or not raw_subsystems:
        raise SubsystemManifestError("empty_or_invalid_subsystems")
    if len(raw_subsystems) > SUBSYSTEM_MANIFEST_MAX_GROUPS:
        raise SubsystemManifestError("too_many_subsystems")

    specs: list[SubsystemSpec] = []
    seen_ids: set[str] = set()
    all_owners: list[SubsystemOwner] = []
    for raw_spec in raw_subsystems:
        if not isinstance(raw_spec, dict) or set(raw_spec) != {"id", "label", "owners"}:
            raise SubsystemManifestError("invalid_subsystem_shape")
        subsystem_id = raw_spec.get("id")
        label = raw_spec.get("label")
        raw_owners = raw_spec.get("owners")
        if (
            not isinstance(subsystem_id, str)
            or _SUBSYSTEM_ID_PATTERN.fullmatch(subsystem_id) is None
            or subsystem_id in seen_ids
        ):
            raise SubsystemManifestError("invalid_or_duplicate_id")
        if not isinstance(label, str) or not label.strip() or len(label) > 200 or not _manifest_text_is_safe(label):
            raise SubsystemManifestError("invalid_label")
        if not isinstance(raw_owners, list) or not raw_owners:
            raise SubsystemManifestError("empty_or_invalid_owners")
        seen_ids.add(subsystem_id)
        owners: list[SubsystemOwner] = []
        for raw_owner in raw_owners:
            if not isinstance(raw_owner, str) or len(raw_owner) > SUBSYSTEM_MANIFEST_MAX_PATH_LENGTH:
                raise SubsystemManifestError("invalid_owner_path")
            owner = _parse_subsystem_owner(raw_owner)
            owners.append(owner)
            all_owners.append(owner)
            if len(all_owners) > SUBSYSTEM_MANIFEST_MAX_OWNERS:
                raise SubsystemManifestError("too_many_owners")
        specs.append(SubsystemSpec(subsystem_id=subsystem_id, label=label.strip(), owners=tuple(owners)))

    ordered_owners = sorted(all_owners, key=lambda item: (item.path.rstrip("/"), item.directory_prefix))
    for offset, owner in enumerate(ordered_owners):
        for other in ordered_owners[offset + 1 :]:
            if _subsystem_owners_overlap(owner, other):
                raise SubsystemManifestError("overlapping_owners")
    return tuple(specs)


class _DuplicateManifestKey(ValueError):
    pass


def _manifest_object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateManifestKey(key)
        result[key] = value
    return result


def _manifest_text_is_safe(value: str) -> bool:
    return all(
        not (ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF)
        for character in value
    )


def _parse_subsystem_owner(value: str) -> SubsystemOwner:
    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or (len(value) > 1 and value[1] == ":")
        or ":" in value
        or not _manifest_text_is_safe(value)
    ):
        raise SubsystemManifestError("invalid_owner_path")
    is_directory = value.endswith("/")
    candidate = value[:-1] if is_directory else value
    parts = candidate.split("/")
    if (
        not candidate
        or any(not part or part in {".", ".."} or part.endswith((".", " ")) for part in parts)
        or value != "/".join(parts) + ("/" if is_directory else "")
    ):
        raise SubsystemManifestError("invalid_owner_path")
    return SubsystemOwner(path=value, directory_prefix=is_directory)


def _subsystem_owners_overlap(left: SubsystemOwner, right: SubsystemOwner) -> bool:
    left_path = left.path.rstrip("/")
    right_path = right.path.rstrip("/")
    if left_path == right_path:
        return True
    return (
        left.directory_prefix and right_path.startswith(f"{left_path}/")
    ) or (
        right.directory_prefix and left_path.startswith(f"{right_path}/")
    )


DefinitionKind = Literal["module", "class", "function"]


@dataclass(frozen=True)
class DefinitionDescription:
    """A cache-safe definition description without index-bound identity."""

    qualified_name: str
    kind: DefinitionKind
    start_line: int
    end_line: int


@dataclass(frozen=True)
class TypeScriptImportDescription:
    """One static or dynamic TypeScript module reference and its source range."""

    specifier: str
    kind: Literal["static", "dynamic"]
    start_line: int
    end_line: int


@dataclass(frozen=True)
class TypeScriptCallDescription:
    """One bounded call expression with a caller and a proven local/import binding when available."""

    caller_qualified_name: str
    caller_start_line: int
    target_qualified_name: str | None
    target_start_line: int | None
    import_specifier: str | None
    imported_name: str | None
    expression: str
    reason: str | None
    start_line: int
    end_line: int


@dataclass(frozen=True)
class TypeScriptExportDescription:
    """One directly declared or explicitly re-exported named callable."""

    exported_name: str
    target_qualified_name: str
    target_start_line: int


@dataclass(frozen=True)
class TypeScriptHttpRequestDescription:
    """A method/path pair forwarded by a structurally verified fetch wrapper."""

    method: str | None
    path: str | None
    path_kind: Literal["literal", "parameterized"]
    path_template: str | None
    client_name: str
    unknown_reason: str | None
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DefinitionCacheEntry:
    """Validated per-file definitions and the diagnostic from parsing it."""

    definitions: tuple[DefinitionDescription, ...]
    issue: SnapshotIssue | None = None


@dataclass(frozen=True)
class DefinitionIndex:
    """Definitions extracted from one source snapshot."""

    snapshot: SourceSnapshot
    symbols: tuple[Symbol, ...]
    issues: tuple[SnapshotIssue, ...]
    ast_parse_count: int = field(default=0, compare=False)
    cache_hits: int = field(default=0, compare=False)
    cache_misses: int = field(default=0, compare=False)
    cache_entries: tuple[tuple[str, DefinitionCacheEntry], ...] = field(default=(), compare=False)
    parsed_trees: tuple[tuple[str, ast.Module], ...] = field(
        default=(), compare=False, repr=False
    )
    typescript_imports: tuple[tuple[str, TypeScriptImportDescription], ...] = ()
    typescript_calls: tuple[tuple[str, TypeScriptCallDescription], ...] = ()
    typescript_exports: tuple[tuple[str, TypeScriptExportDescription], ...] = ()
    typescript_calls_truncated: bool = False
    typescript_calls_truncation_reason: str | None = None
    typescript_http_requests: tuple[tuple[str, TypeScriptHttpRequestDescription], ...] = ()
    typescript_parse_file_count: int = field(default=0, compare=False)


@dataclass(frozen=True)
class IndexBuildStats:
    """Internal build evidence separating definition and relation parsing."""

    source_file_count: int
    definition_cache_hits: int
    definition_cache_misses: int
    definition_ast_parse_count: int
    typescript_parse_file_count: int
    relation_ast_parse_count: int
    relation_fact_cache_hits: int
    relation_fact_cache_misses: int
    relation_shared_ast_reuse_count: int
    definition_cache_entries_written: int
    relation_cache_entries_written: int


def build_source_snapshot(
    repository_root: str | Path,
    *,
    repository_id: str,
    revision: str = "working-tree",
    dirty: bool = True,
    index_id: str | None = None,
    identity_version: str = "source-v1",
    max_file_bytes: int = 1_000_000,
    max_files: int = 2_000,
    max_total_bytes: int = DEFAULT_INDEX_MAX_TOTAL_BYTES,
    max_seconds: float = 2.0,
    rules_version: str | None = None,
    configuration_hash: str | None = None,
    control: IndexingControl | None = None,
) -> SourceSnapshot:
    """Read a bounded Python/TypeScript source tree into a reproducible snapshot.

    The caller supplies revision metadata because inspecting a repository must not
    execute arbitrary commands. The default explicitly describes an uncommitted
    working tree. Only regular ``.py``, ``.ts`` and ``.tsx`` files plus the bounded
    root subsystem metadata file are captured; metadata is not parsed as source.
    Excluded directories, secrets, symlinks, undecodable files, and oversized source
    files are not parsed.
    """

    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", identity_version):
        raise ValueError("identity_version must be a short stable identifier")

    scan = scan_source_manifest(
        repository_root,
        max_file_bytes=max_file_bytes,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        max_seconds=max_seconds,
        control=control,
    )
    snapshot_files = list(scan.files)
    issues = list(scan.issues)
    if scan.scan_truncated or not scan.scan_complete:
        detail = scan.scan_truncation_reason or "source changed or could not be fully inspected"
        issues.append(
            SnapshotIssue(
                "__snapshot__",
                "capture_incomplete",
                detail,
                size=scan.scan_truncation_size,
                count=scan.scan_truncation_count,
            )
        )
    issues.sort(key=lambda issue: (issue.path, issue.kind))
    snapshot_hash = _snapshot_hash(snapshot_files, issues)
    generated_index_id = index_id or _make_index_id(repository_id, snapshot_hash, identity_version)
    status = "failed" if any(issue.kind in _BLOCKING_ISSUES for issue in issues) else "ready"

    index = IndexSnapshot(
        index_id=generated_index_id,
        repository_id=repository_id,
        revision=revision,
        dirty=dirty,
        snapshot_hash=snapshot_hash,
        status=status,
        rules_version=rules_version,
        configuration_hash=configuration_hash,
    )
    return SourceSnapshot(index, tuple(snapshot_files), tuple(issues))


SOURCE_CAPTURE_RULES_VERSION = "source-v4"
DEFINITION_EXTRACTION_RULES_VERSION = "python-definitions-v1"
TYPESCRIPT_COMPILER_VERSION = "5.9.3"
TYPESCRIPT_FACT_RULES_VERSION = "typescript-facts-v5"
_TYPESCRIPT_PARSER_TIMEOUT_SECONDS = 30
_TYPESCRIPT_PARSER_MAX_INPUT_BYTES = 20 * 1024 * 1024
_TYPESCRIPT_PARSER_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
PYTHON_AST_VERSION = (
    f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
)
_DEFINITION_CACHE_FORMAT_VERSION = "definition-cache-v1"


def current_source_rules_version(identity_version: str) -> str:
    """Return the parser/source capture version used for new index metadata."""

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", identity_version):
        raise ValueError("identity_version must be a short stable identifier")
    return (
        f"{SOURCE_CAPTURE_RULES_VERSION}+python-{sys.version_info.major}.{sys.version_info.minor}"
        f"+typescript-{TYPESCRIPT_COMPILER_VERSION}+{TYPESCRIPT_FACT_RULES_VERSION}+{identity_version}"
    )


def index_configuration_hash(
    *,
    max_file_bytes: int,
    identity_version: str,
    rules_version: str,
    max_files: int = 2_000,
    max_seconds: float = 2.0,
    relation_budgets: Mapping[str, int | float] | None = None,
) -> str:
    """Fingerprint the source/index settings that can change index contents.

    Relation budgets are included when given because a truncated relation graph
    depends on them; changing a budget therefore marks indexes rebuildable.
    """

    if max_file_bytes < 1 or max_files < 1 or max_seconds <= 0:
        raise ValueError("source capture budgets must be positive")
    payload: dict[str, object] = {
        "identity_version": identity_version,
        "max_file_bytes": max_file_bytes,
        "max_files": max_files,
        "max_seconds": max_seconds,
        "rules_version": rules_version,
    }
    if relation_budgets is not None:
        payload["relation_budgets"] = dict(sorted(relation_budgets.items()))
    return _sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def scan_source_manifest(
    repository_root: str | Path,
    *,
    max_file_bytes: int = 1_000_000,
    max_files: int = 2_000,
    max_total_bytes: int = DEFAULT_INDEX_MAX_TOTAL_BYTES,
    max_seconds: float = 2.0,
    control: IndexingControl | None = None,
) -> SourceManifestScan:
    """Read a bounded, hash-only manifest using the snapshot source rules.

    This function deliberately does not call ``ast.parse`` or any relation
    extractor. It still reads every accepted file's bytes so mtime/size cannot
    be mistaken for content identity.
    """

    if max_file_bytes < 1 or max_files < 1 or max_total_bytes < 1 or max_seconds <= 0:
        raise ValueError("source manifest budgets must be positive")

    root = _resolve_repository_root(repository_root)
    deadline = time.monotonic() + max_seconds
    files: list[SnapshotFile] = []
    issues: list[SnapshotIssue] = []
    unstable_paths: list[str] = []
    scanned_files = 0
    scanned_bytes = 0
    scanned_source_files = 0
    scanned_source_bytes = 0
    scan_complete = True
    scan_truncated = False
    scan_truncation_reason: str | None = None
    scan_truncation_size: int | None = None
    scan_truncation_count: int | None = None

    def truncate(
        reason: str,
        *,
        measured_size: int | None = None,
        measured_count: int | None = None,
    ) -> None:
        nonlocal scan_complete, scan_truncated, scan_truncation_reason
        nonlocal scan_truncation_size, scan_truncation_count
        scan_complete = False
        scan_truncated = True
        scan_truncation_reason = reason
        scan_truncation_size = measured_size
        scan_truncation_count = measured_count

    def check_time() -> bool:
        if control is not None:
            control.check()
        if time.monotonic() > deadline:
            truncate("max_seconds")
            return False
        return True

    def visit(directory: Path) -> None:
        nonlocal scanned_files, scanned_bytes, scanned_source_files, scanned_source_bytes, scan_complete
        if scan_truncated or not check_time():
            return
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda entry: (
                    0
                    if directory == root
                    and entry.name.casefold() == SUBSYSTEM_MANIFEST_PATH.casefold()
                    else 1,
                    entry.name.casefold(),
                ),
            )
        except OSError as exc:
            relative = _relative_path_for_issue(root, directory)
            issues.append(SnapshotIssue(relative, "read_error", f"could not list directory: {exc}"))
            scan_complete = False
            return

        for entry in entries:
            if scan_truncated or not check_time():
                return
            relative = _relative_path_for_issue(root, entry)
            manifest_candidate = (
                "/" not in relative
                and relative.casefold() == SUBSYSTEM_MANIFEST_PATH.casefold()
            )
            if manifest_candidate and relative != SUBSYSTEM_MANIFEST_PATH:
                issues.append(SnapshotIssue(relative, "manifest_invalid", "manifest invalid: path_alias"))
                continue
            is_subsystem_manifest = relative == SUBSYSTEM_MANIFEST_PATH

            def manifest_invalid(code: str, size: int | None = None) -> None:
                issues.append(SnapshotIssue(relative, "manifest_invalid", f"manifest invalid: {code}", size=size))

            try:
                is_dir = entry.is_dir()
            except OSError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("unreadable")
                else:
                    issues.append(SnapshotIssue(relative, "read_error", f"could not inspect source: {exc}"))
                    scan_complete = False
                continue
            if is_dir and entry.name.lower() in _EXCLUDED_DIRECTORIES:
                continue
            if _is_secret_name(entry.name):
                continue
            if is_subsystem_manifest and is_dir:
                manifest_invalid("not_a_file")
                continue
            try:
                if entry.is_symlink():
                    if is_subsystem_manifest:
                        manifest_invalid("symlink")
                    else:
                        issues.append(SnapshotIssue(relative, "symlink", "symbolic links are not indexed"))
                    continue
            except OSError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("unreadable")
                else:
                    issues.append(SnapshotIssue(relative, "read_error", f"could not inspect source: {exc}"))
                    scan_complete = False
                continue
            if is_dir:
                visit(entry)
                continue
            try:
                is_file = entry.is_file()
            except OSError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("unreadable")
                else:
                    issues.append(SnapshotIssue(relative, "read_error", f"could not inspect source: {exc}"))
                    scan_complete = False
                continue
            if not is_file:
                if is_subsystem_manifest:
                    manifest_invalid("not_regular_file")
                continue
            if not is_subsystem_manifest and not is_source_code_path(relative):
                continue
            if not is_subsystem_manifest and scanned_source_files >= max_files:
                truncate("max_files", measured_count=scanned_source_files + 1)
                return
            scanned_files += 1
            if not is_subsystem_manifest:
                scanned_source_files += 1
            try:
                resolved = entry.resolve(strict=False)
                resolved.relative_to(root)
            except (OSError, ValueError):
                if is_subsystem_manifest:
                    manifest_invalid("outside_root")
                else:
                    issues.append(SnapshotIssue(relative, "outside_root", "source resolves outside root"))
                continue
            try:
                before = entry.stat()
            except OSError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("unreadable")
                else:
                    issues.append(SnapshotIssue(relative, "read_error", f"could not stat source: {exc}"))
                    scan_complete = False
                continue
            file_byte_limit = SUBSYSTEM_MANIFEST_MAX_BYTES if is_subsystem_manifest else max_file_bytes
            if before.st_size > file_byte_limit:
                if is_subsystem_manifest:
                    manifest_invalid("file_too_large", before.st_size)
                    continue
                issues.append(
                    SnapshotIssue(
                        relative,
                        "file_too_large",
                        f"source file is {before.st_size} bytes; limit is {max_file_bytes}",
                        size=before.st_size,
                    )
                )
                continue
            if not is_subsystem_manifest and scanned_source_bytes + before.st_size > max_total_bytes:
                truncate(
                    "max_total_bytes",
                    measured_size=scanned_source_bytes + before.st_size,
                )
                return
            try:
                if is_subsystem_manifest:
                    with entry.open("rb") as source:
                        raw = source.read(SUBSYSTEM_MANIFEST_MAX_BYTES + 1)
                else:
                    raw = entry.read_bytes()
            except OSError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("unreadable")
                else:
                    issues.append(SnapshotIssue(relative, "read_error", f"could not read source: {exc}"))
                    scan_complete = False
                continue
            scanned_bytes += len(raw)
            if not is_subsystem_manifest:
                scanned_source_bytes += len(raw)
            if not is_subsystem_manifest and scanned_source_bytes > max_total_bytes:
                truncate(
                    "max_total_bytes_after_read",
                    measured_size=scanned_source_bytes,
                )
                return
            try:
                after = entry.stat()
            except OSError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("unreadable")
                else:
                    issues.append(SnapshotIssue(relative, "read_error", f"could not stat source after read: {exc}"))
                    scan_complete = False
                continue
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                if is_subsystem_manifest:
                    manifest_invalid("changed_during_capture", after.st_size)
                    continue
                unstable_paths.append(relative)
                scan_complete = False
            if len(raw) > file_byte_limit:
                if is_subsystem_manifest:
                    manifest_invalid("file_too_large", after.st_size)
                    continue
                issues.append(
                    SnapshotIssue(
                        relative,
                        "file_too_large",
                        f"source file exceeds the {max_file_bytes}-byte limit",
                        size=len(raw),
                    )
                )
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                if is_subsystem_manifest:
                    manifest_invalid("invalid_utf8", len(raw))
                else:
                    issues.append(SnapshotIssue(relative, "decode_error", f"source is not UTF-8: {exc}"))
                continue
            if is_subsystem_manifest:
                try:
                    parse_subsystem_manifest(text)
                except SubsystemManifestError as exc:
                    manifest_invalid(exc.code, len(raw))
            files.append(
                SnapshotFile(
                    path=relative,
                    size=len(raw),
                    content_hash=_sha256(raw),
                    text=text,
                )
            )

    visit(root)
    files.sort(key=lambda source_file: source_file.path)
    issues.sort(key=lambda issue: (issue.path, issue.kind))
    unstable_paths.sort()
    return SourceManifestScan(
        files=tuple(files),
        issues=tuple(issues),
        scanned_files=scanned_files,
        scanned_bytes=scanned_bytes,
        scan_complete=scan_complete and not scan_truncated,
        scan_truncated=scan_truncated,
        scan_truncation_reason=scan_truncation_reason,
        scan_truncation_size=scan_truncation_size,
        scan_truncation_count=scan_truncation_count,
        unstable_paths=tuple(unstable_paths),
    )


def extract_definitions(
    snapshot: SourceSnapshot,
    *,
    cached: Mapping[str, DefinitionCacheEntry] | None = None,
    control: IndexingControl | None = None,
) -> DefinitionIndex:
    """Extract definitions, reusing only validated entries for unchanged files."""

    symbols: list[Symbol] = []
    issues = list(snapshot.issues)
    cache_entries: list[tuple[str, DefinitionCacheEntry]] = []
    parsed_trees: list[tuple[str, ast.Module]] = []
    ast_parse_count = 0
    cache_hits = 0
    cache_misses = 0
    typescript_files = tuple(
        source_file
        for source_file in snapshot.files
        if source_file.path.lower().endswith((".ts", ".tsx"))
    )
    typescript_imports: list[tuple[str, TypeScriptImportDescription]] = []
    typescript_calls: list[tuple[str, TypeScriptCallDescription]] = []
    typescript_exports: list[tuple[str, TypeScriptExportDescription]] = []
    typescript_calls_truncated = False
    typescript_calls_truncation_reason: str | None = None
    typescript_http_requests: list[tuple[str, TypeScriptHttpRequestDescription]] = []
    typescript_parse_file_count = 0
    for source_file in snapshot.files:
        if control is not None:
            control.check()
        if source_file.path.lower().endswith((".ts", ".tsx")):
            continue
        if not source_file.path.lower().endswith(".py"):
            continue
        entry = cached.get(source_file.path) if cached is not None else None
        if entry is None:
            ast_parse_count += 1
            entry, tree = _extract_file_definitions_with_tree(source_file)
            cache_entries.append((source_file.path, entry))
            cache_misses += 1
            if tree is not None:
                parsed_trees.append((source_file.path, tree))
        else:
            cache_hits += 1
        for definition in entry.definitions:
            symbols.append(
                _build_symbol(
                    index_id=snapshot.index.index_id,
                    path=source_file.path,
                    qualified_name=definition.qualified_name,
                    kind=definition.kind,
                    start_line=definition.start_line,
                    end_line=definition.end_line,
                )
            )
        if entry.issue is not None:
            issues.append(entry.issue)

    if typescript_files:
        if control is not None:
            control.check()
        facts, typescript_issues = _extract_typescript_facts(typescript_files, control=control)
        issues.extend(typescript_issues)
        if not any(issue.kind in {"parser_unavailable", "parser_error"} for issue in typescript_issues):
            typescript_parse_file_count = len(typescript_files)
        for source_file in typescript_files:
            module_name = _module_name(source_file.path)
            end_line = max(1, len(source_file.text.splitlines()))
            symbols.append(
                _build_symbol(
                    index_id=snapshot.index.index_id,
                    path=source_file.path,
                    qualified_name=module_name,
                    kind="module",
                    start_line=1,
                    end_line=end_line,
                )
            )
            file_facts = facts.get(source_file.path)
            if file_facts is None:
                continue
            if file_facts.get("calls_truncated") is True:
                typescript_calls_truncated = True
                typescript_calls_truncation_reason = str(file_facts.get("calls_truncation_reason") or "TypeScript call facts truncated")[:200]
            for definition in file_facts.get("definitions", []):
                symbols.append(
                    _build_symbol(
                        index_id=snapshot.index.index_id,
                        path=source_file.path,
                        qualified_name=str(definition["qualified_name"]),
                        kind=definition["kind"],  # type: ignore[arg-type]
                        start_line=int(definition["start_line"]),
                        end_line=int(definition["end_line"]),
                    )
                )
            for item in file_facts.get("imports", []):
                typescript_imports.append(
                    (
                        source_file.path,
                        TypeScriptImportDescription(
                            specifier=str(item["specifier"]),
                            kind=item["kind"],
                            start_line=int(item["start_line"]),
                            end_line=int(item["end_line"]),
                        ),
                    )
                )
            for item in file_facts.get("calls", []):
                typescript_calls.append((source_file.path, TypeScriptCallDescription(
                    caller_qualified_name=str(item["caller_qualified_name"]),
                    caller_start_line=int(item["caller_start_line"]),
                    target_qualified_name=item.get("target_qualified_name"),
                    target_start_line=item.get("target_start_line"),
                    import_specifier=item.get("import_specifier"),
                    imported_name=item.get("imported_name"),
                    expression=str(item["expression"]),
                    reason=item.get("reason"),
                    start_line=int(item["start_line"]),
                    end_line=int(item["end_line"]),
                )))
            for item in file_facts.get("exports", []):
                typescript_exports.append((source_file.path, TypeScriptExportDescription(
                    exported_name=str(item["exported_name"]),
                    target_qualified_name=str(item["target_qualified_name"]),
                    target_start_line=int(item["target_start_line"]),
                )))
            for item in file_facts.get("http_requests", []):
                path_kind = item.get("path_kind", "literal")
                path = item.get("path")
                path_template = item.get("path_template")
                unknown_reason = item.get("unknown_reason")
                if (
                    path_kind not in {"literal", "parameterized"}
                    or path is not None and (not isinstance(path, str) or len(path) > 2_000)
                    or path_template is not None and (not isinstance(path_template, str) or len(path_template) > 2_000)
                    or unknown_reason is not None and (not isinstance(unknown_reason, str) or len(unknown_reason) > 200)
                    or path_kind == "literal" and path_template is not None
                    or path_kind == "parameterized" and (
                        path is not None or not isinstance(path_template, str)
                        or not path_template.startswith("/api/")
                        or any(marker in path_template for marker in "?#")
                        or "{*}" not in path_template
                        or any(
                            ("{" in segment or "}" in segment) and segment != "{*}"
                            for segment in path_template.split("/")
                        )
                    )
                    or path is not None and (
                        not path.startswith("/api/") or any(marker in path for marker in "?#")
                    )
                    or unknown_reason is None and (
                        path_kind == "literal" and (
                            not isinstance(path, str) or path_template is not None
                            or not path.startswith("/api/") or any(marker in path for marker in "?#")
                        )
                        or path_kind == "parameterized" and path_template is None
                    )
                ):
                    raise ValueError("TypeScript parser returned an invalid HTTP path fact")
                typescript_http_requests.append(
                    (
                        source_file.path,
                        TypeScriptHttpRequestDescription(
                            method=item["method"],
                            path=path,
                            path_kind=path_kind,
                            path_template=path_template,
                            client_name=str(item["client_name"]),
                            unknown_reason=unknown_reason,
                            start_line=int(item["start_line"]),
                            end_line=int(item["end_line"]),
                        ),
                    )
                )

        if typescript_issues:
            snapshot = replace(
                snapshot,
                index=snapshot.index.model_copy(update={"status": "failed"}),
            )

    symbols.sort(key=lambda symbol: (symbol.path, symbol.start_line, symbol.end_line, symbol.kind, symbol.qualified_name))
    issues.sort(key=lambda issue: (issue.path, issue.kind))
    return DefinitionIndex(
        snapshot=snapshot,
        symbols=tuple(symbols),
        issues=tuple(issues),
        ast_parse_count=ast_parse_count,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        cache_entries=tuple(cache_entries),
        parsed_trees=tuple(parsed_trees),
        typescript_imports=tuple(typescript_imports),
        typescript_calls=tuple(typescript_calls),
        typescript_exports=tuple(typescript_exports),
        typescript_calls_truncated=typescript_calls_truncated,
        typescript_calls_truncation_reason=typescript_calls_truncation_reason,
        typescript_http_requests=tuple(typescript_http_requests),
        typescript_parse_file_count=typescript_parse_file_count,
    )


def _extract_typescript_facts(
    source_files: tuple[SnapshotFile, ...],
    *,
    control: IndexingControl | None = None,
) -> tuple[dict[str, dict[str, object]], list[SnapshotIssue]]:
    """Parse captured TS/TSX text with the repository's locked TypeScript compiler."""

    if not source_files:
        return {}, []
    parser = Path(__file__).with_name("typescript_parser.cjs")
    node = shutil.which("node")
    if node is None or not parser.is_file():
        return {}, [
            SnapshotIssue(
                "__snapshot__",
                "parser_unavailable",
                "TypeScript indexing requires Node.js and apps/web's locked TypeScript 5.9.3 package",
            )
        ]

    payload = json.dumps(
        {"files": [{"path": item.path, "text": item.text} for item in source_files]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > _TYPESCRIPT_PARSER_MAX_INPUT_BYTES:
        return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser input exceeded its byte budget")]
    environment = {"PATH": os.environ.get("PATH", "")}
    if compiler_path := os.environ.get("CODETRAIL_TYPESCRIPT_COMPILER"):
        environment["CODETRAIL_TYPESCRIPT_COMPILER"] = compiler_path
    for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
        if value := os.environ.get(key):
            environment[key] = value
    environment["NODE_OPTIONS"] = "--max-old-space-size=128"
    try:
        process = subprocess.Popen(
            [node, str(parser)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=parser.parent,
            env=environment,
        )
        deadline = time.monotonic() + _TYPESCRIPT_PARSER_TIMEOUT_SECONDS
        completed = threading.Event()
        result: list[subprocess.CompletedProcess[bytes]] = []

        def communicate() -> None:
            try:
                stdout, stderr = process.communicate(input=payload.encode("utf-8"))
                result.append(subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr))
            finally:
                completed.set()

        communicator = threading.Thread(target=communicate, daemon=True)
        communicator.start()
        while True:
            if control is not None:
                control.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                communicator.join()
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser exceeded its 30 second budget")]
            if completed.wait(min(0.05, remaining)):
                break
        completed_result = result[0]
        completed_result.stdout = completed_result.stdout.decode("utf-8", "replace")
        completed_result.stderr = completed_result.stderr.decode("utf-8", "replace")
        result = completed_result
    except IndexingAborted:
        process.kill()
        if "communicator" in locals():
            communicator.join()
        else:
            process.communicate()
        raise
    except OSError:
        return {}, [SnapshotIssue("__snapshot__", "parser_unavailable", "TypeScript parser process could not be started")]
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > _TYPESCRIPT_PARSER_MAX_OUTPUT_BYTES:
        detail = (result.stderr or "").strip().splitlines()
        suffix = f": {detail[0][:200]}" if detail else ""
        return {}, [
            SnapshotIssue(
                "__snapshot__",
                "parser_error",
                f"TypeScript parser failed or exceeded its output budget{suffix}",
            )
        ]
    try:
        decoded = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned invalid output")]
    if (
        not isinstance(decoded, dict)
        or decoded.get("compiler_version") != TYPESCRIPT_COMPILER_VERSION
        or not isinstance(decoded.get("files"), list)
        or len(decoded["files"]) != len(source_files)
    ):
        return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser output did not match the locked compiler contract")]

    expected_paths = {item.path for item in source_files}
    facts: dict[str, dict[str, list[dict[str, object]]]] = {}
    issues: list[SnapshotIssue] = []
    for item in decoded["files"]:
        if not isinstance(item, dict) or item.get("path") not in expected_paths:
            return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an unknown source path")]
        path = str(item["path"])
        syntax_error = item.get("syntax_error")
        if syntax_error is not None:
            if not isinstance(syntax_error, dict) or not isinstance(syntax_error.get("line"), int):
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid syntax diagnostic")]
            message = str(syntax_error.get("message", "invalid TypeScript syntax"))[:1_500]
            issues.append(SnapshotIssue(path, "typescript_syntax_error", f"TypeScript syntax error at line {syntax_error['line']}: {message}"))
            continue
        line_count = max(1, len(next(source.text for source in source_files if source.path == path).splitlines()))
        definitions = item.get("definitions")
        imports = item.get("imports")
        requests = item.get("http_requests")
        calls = item.get("calls")
        exports = item.get("exports")
        calls_truncated = item.get("calls_truncated")
        calls_truncation_reason = item.get("calls_truncation_reason")
        if (
            not isinstance(definitions, list)
            or not isinstance(imports, list)
            or not isinstance(requests, list)
            or not isinstance(calls, list)
            or not isinstance(exports, list)
            or not isinstance(calls_truncated, bool)
            or calls_truncation_reason is not None and not isinstance(calls_truncation_reason, str)
            or calls_truncated and (not isinstance(calls_truncation_reason, str) or not calls_truncation_reason)
            or not calls_truncated and calls_truncation_reason is not None
        ):
            return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid fact list")]
        for definition in definitions:
            if (
                not isinstance(definition, dict)
                or not isinstance(definition.get("qualified_name"), str)
                or len(definition["qualified_name"]) > 500
                or definition.get("kind") not in {"class", "function"}
                or not _valid_parser_range(definition, line_count)
            ):
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid definition")]
        for item_fact in imports:
            if (
                not isinstance(item_fact, dict)
                or not isinstance(item_fact.get("specifier"), str)
                or item_fact.get("kind") not in {"static", "dynamic"}
                or not _valid_parser_range(item_fact, line_count)
            ):
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid import fact")]
        for call in calls:
            if (
                not isinstance(call, dict)
                or not isinstance(call.get("caller_qualified_name"), str)
                or not call["caller_qualified_name"]
                or len(call["caller_qualified_name"]) > 500
                or not _valid_parser_range({"start_line": call.get("caller_start_line"), "end_line": call.get("caller_start_line")}, line_count)
                or call.get("target_qualified_name") is not None and (
                    not isinstance(call.get("target_qualified_name"), str)
                    or not call["target_qualified_name"]
                    or len(call["target_qualified_name"]) > 500
                )
                or call.get("target_start_line") is not None and (
                    not isinstance(call.get("target_start_line"), int)
                    or isinstance(call.get("target_start_line"), bool)
                )
                or call.get("import_specifier") is not None and (
                    not isinstance(call.get("import_specifier"), str)
                    or not call["import_specifier"]
                    or len(call["import_specifier"]) > 1_000
                )
                or call.get("imported_name") is not None and (
                    not isinstance(call.get("imported_name"), str)
                    or not call["imported_name"]
                    or len(call["imported_name"]) > 500
                )
                or not isinstance(call.get("expression"), str)
                or not call["expression"]
                or len(call["expression"]) > 500
                or call.get("reason") is not None and (
                    not isinstance(call.get("reason"), str)
                    or not call["reason"]
                    or len(call["reason"]) > 200
                )
                or not _valid_parser_range(call, line_count)
                or (call.get("target_qualified_name") is None) != (call.get("target_start_line") is None)
                or (call.get("import_specifier") is None) != (call.get("imported_name") is None)
                or call.get("target_qualified_name") is not None and (
                    call.get("import_specifier") is not None or call.get("reason") is not None
                )
                or call.get("import_specifier") is not None and (
                    call.get("target_qualified_name") is not None or call.get("reason") is not None
                )
                or call.get("target_qualified_name") is None
                and call.get("import_specifier") is None
                and call.get("reason") is None
            ):
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid call fact")]
        for exported in exports:
            if (
                not isinstance(exported, dict)
                or not isinstance(exported.get("exported_name"), str)
                or not exported["exported_name"]
                or len(exported["exported_name"]) > 500
                or not isinstance(exported.get("target_qualified_name"), str)
                or not exported["target_qualified_name"]
                or len(exported["target_qualified_name"]) > 500
                or not _valid_parser_range(
                    {"start_line": exported.get("target_start_line"), "end_line": exported.get("target_start_line")},
                    line_count,
                )
            ):
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid export fact")]
        for request in requests:
            if (
                not isinstance(request, dict)
                or request.get("method") is not None and not isinstance(request.get("method"), str)
                or request.get("path") is not None and not isinstance(request.get("path"), str)
                or not isinstance(request.get("client_name"), str)
                or request.get("unknown_reason") is not None and not isinstance(request.get("unknown_reason"), str)
                or not _valid_parser_range(request, line_count)
            ):
                return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser returned an invalid HTTP request fact")]
        facts[path] = item
    if len(facts) + len(issues) != len(expected_paths):
        return {}, [SnapshotIssue("__snapshot__", "parser_error", "TypeScript parser omitted one or more captured files")]
    return facts, issues


def _valid_parser_range(value: dict[str, object], line_count: int) -> bool:
    start = value.get("start_line")
    end = value.get("end_line")
    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 1 <= start <= end <= line_count
    )


def _extract_file_definitions_with_tree(
    source_file: SnapshotFile,
) -> tuple[DefinitionCacheEntry, ast.Module | None]:
    """Parse one file and retain its in-process AST for the next index stage."""

    try:
        tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
    except SyntaxError as exc:
        location = f" at line {exc.lineno}" if exc.lineno else ""
        return (
            DefinitionCacheEntry(
                definitions=(),
                issue=SnapshotIssue(
                    source_file.path,
                    "syntax_error",
                    f"could not parse Python source{location}: {exc.msg}",
                ),
            ),
            None,
        )

    module_name = _module_name(source_file.path)
    definitions = [
        DefinitionDescription(
            qualified_name=module_name,
            kind="module",
            start_line=1,
            end_line=max(1, len(source_file.text.splitlines())),
        )
    ]
    visitor = _DefinitionVisitor(module_name)
    visitor.visit(tree)
    definitions.extend(visitor.definitions)
    return DefinitionCacheEntry(definitions=tuple(definitions)), tree


class _DefinitionVisitor(ast.NodeVisitor):
    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self._name_stack: list[str] = []
        self.definitions: list[DefinitionDescription] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node, "function")

    def _visit_definition(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: Literal["class", "function"],
    ) -> None:
        qualified_name = ".".join((*([self.module_name] if self.module_name else []), *self._name_stack, node.name))
        self.definitions.append(
            DefinitionDescription(
                qualified_name=qualified_name,
                kind=kind,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            )
        )
        self._name_stack.append(node.name)
        self.generic_visit(node)
        self._name_stack.pop()


def serialize_definition_cache_entry(entry: DefinitionCacheEntry) -> str:
    """Serialize only validated, index-independent definition facts."""

    definitions: list[dict[str, object]] = []
    for definition in entry.definitions:
        _validate_definition_description(definition)
        definitions.append(
            {
                "qualified_name": definition.qualified_name,
                "kind": definition.kind,
                "start_line": definition.start_line,
                "end_line": definition.end_line,
            }
        )
    issue: dict[str, object] | None = None
    if entry.issue is not None:
        if entry.issue.kind != "syntax_error":
            raise ValueError("definition cache can only store syntax diagnostics")
        if not entry.issue.path or not entry.issue.message or len(entry.issue.message) > 2_000:
            raise ValueError("definition cache issue is invalid")
        if entry.issue.size is not None and (
            not isinstance(entry.issue.size, int)
            or isinstance(entry.issue.size, bool)
            or entry.issue.size < 0
        ):
            raise ValueError("definition cache issue size is invalid")
        issue = {
            "path": entry.issue.path,
            "kind": entry.issue.kind,
            "message": entry.issue.message,
            "size": entry.issue.size,
        }
    return json.dumps(
        {
            "format_version": _DEFINITION_CACHE_FORMAT_VERSION,
            "definitions": definitions,
            "issue": issue,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_definition_cache_entry(
    serialized: str,
    *,
    expected_path: str,
) -> DefinitionCacheEntry:
    """Validate and decode one cache value, rejecting unknown fields or shapes."""

    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("definition cache payload is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"format_version", "definitions", "issue"}:
        raise ValueError("definition cache payload shape is invalid")
    if payload["format_version"] != _DEFINITION_CACHE_FORMAT_VERSION:
        raise ValueError("definition cache format is unsupported")
    raw_definitions = payload["definitions"]
    if not isinstance(raw_definitions, list) or len(raw_definitions) > 20_000:
        raise ValueError("definition cache definitions are invalid")
    definitions: list[DefinitionDescription] = []
    seen: set[tuple[str, str, int, int]] = set()
    for raw_definition in raw_definitions:
        if not isinstance(raw_definition, dict) or set(raw_definition) != {
            "qualified_name",
            "kind",
            "start_line",
            "end_line",
        }:
            raise ValueError("definition cache definition shape is invalid")
        qualified_name = raw_definition["qualified_name"]
        kind = raw_definition["kind"]
        start_line = raw_definition["start_line"]
        end_line = raw_definition["end_line"]
        if not isinstance(qualified_name, str) or not 1 <= len(qualified_name) <= 500:
            raise ValueError("definition cache qualified name is invalid")
        if kind not in {"module", "class", "function"}:
            raise ValueError("definition cache kind is invalid")
        if (
            not isinstance(start_line, int)
            or isinstance(start_line, bool)
            or not isinstance(end_line, int)
            or isinstance(end_line, bool)
            or start_line < 1
            or end_line < start_line
        ):
            raise ValueError("definition cache line range is invalid")
        signature = (qualified_name, kind, start_line, end_line)
        if signature in seen:
            raise ValueError("definition cache contains duplicate definitions")
        seen.add(signature)
        definitions.append(
            DefinitionDescription(
                qualified_name=qualified_name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
            )
        )

    raw_issue = payload["issue"]
    issue: SnapshotIssue | None = None
    if raw_issue is not None:
        if not isinstance(raw_issue, dict) or set(raw_issue) != {"path", "kind", "message", "size"}:
            raise ValueError("definition cache issue shape is invalid")
        path = raw_issue["path"]
        kind = raw_issue["kind"]
        message = raw_issue["message"]
        size = raw_issue["size"]
        if path != expected_path or kind != "syntax_error":
            raise ValueError("definition cache issue scope is invalid")
        if not isinstance(message, str) or not 1 <= len(message) <= 2_000:
            raise ValueError("definition cache issue message is invalid")
        if size is not None and (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise ValueError("definition cache issue size is invalid")
        issue = SnapshotIssue(path, kind, message, size=size)
    entry = DefinitionCacheEntry(definitions=tuple(definitions), issue=issue)
    for definition in entry.definitions:
        _validate_definition_description(definition)
    return entry


def _validate_definition_description(definition: DefinitionDescription) -> None:
    if not 1 <= len(definition.qualified_name) <= 500:
        raise ValueError("definition qualified name is invalid")
    if definition.kind not in {"module", "class", "function"}:
        raise ValueError("definition kind is invalid")
    if (
        not isinstance(definition.start_line, int)
        or isinstance(definition.start_line, bool)
        or not isinstance(definition.end_line, int)
        or isinstance(definition.end_line, bool)
        or definition.start_line < 1
        or definition.end_line < definition.start_line
    ):
        raise ValueError("definition line range is invalid")


def _build_symbol(
    *,
    index_id: str,
    path: str,
    qualified_name: str,
    kind: DefinitionKind,
    start_line: int,
    end_line: int,
) -> Symbol:
    identity = "\0".join(
        [index_id, path, qualified_name, kind, str(start_line), str(end_line)]
    ).encode("utf-8")
    symbol_id = f"sym-{hashlib.sha256(identity).hexdigest()[:32]}"
    return Symbol(
        symbol_id=symbol_id,
        index_id=index_id,
        path=path,
        qualified_name=qualified_name,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
    )


def _resolve_repository_root(repository_root: str | Path) -> Path:
    candidate = Path(repository_root)
    if candidate.is_symlink():
        raise SnapshotBoundaryError("repository root must not be a symlink")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise SnapshotBoundaryError(f"repository root cannot be resolved: {exc}") from exc
    if not root.is_dir():
        raise SnapshotBoundaryError("repository root must be a directory")
    return root


def _normalize_relative_path(relative_path: str) -> str:
    raw = str(relative_path).replace("\\", "/")
    parts = raw.split("/")
    if (
        not raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise SnapshotBoundaryError("path must be a normalized relative path")
    return "/".join(parts)


def _relative_path_for_issue(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return path.name or "."


def _is_secret_name(name: str) -> bool:
    lowered = name.casefold()
    if lowered in {".env", ".env.local", ".env.production", ".env.development"}:
        return True
    if "secret" in lowered or "credential" in lowered:
        return True
    return lowered.endswith((".pem", ".key", ".p12", ".pfx"))


def _module_name(relative_path: str) -> str:
    suffix = Path(relative_path).suffix
    parts = relative_path.removesuffix(suffix).split("/")
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts) or "__init__"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot_hash(files: list[SnapshotFile], issues: list[SnapshotIssue]) -> str:
    entries = [
        {"kind": "file", "path": source_file.path, "size": source_file.size, "sha256": source_file.content_hash}
        for source_file in files
    ]
    entries.extend(
        {
            "kind": "issue",
            "path": issue.path,
            "issue": issue.kind,
            "size": issue.size,
        }
        for issue in issues
    )
    payload = json.dumps(sorted(entries, key=lambda entry: (entry["path"], entry["kind"], entry.get("issue", ""))), separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _sha256(payload)


def _make_index_id(repository_id: str, snapshot_hash: str, identity_version: str = "source-v1") -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", repository_id).strip("-_") or "repository"
    if identity_version == "source-v1":
        return f"index-{slug[:80]}-{snapshot_hash[:16]}"
    return f"index-{slug[:60]}-{identity_version}-{snapshot_hash[:16]}"
