"""Read-only Python snapshots and deterministic definition extraction."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
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
]

_EXCLUDED_DIRECTORIES = {
    ".codetrail",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
}
_BLOCKING_ISSUES = {
    "decode_error",
    "file_too_large",
    "outside_root",
    "read_error",
    "symlink",
}


class SnapshotBoundaryError(ValueError):
    """Raised when a snapshot request leaves its configured repository root."""


@dataclass(frozen=True)
class SnapshotIssue:
    """A file or request that could not be safely included in a snapshot."""

    path: str
    kind: IssueKind
    message: str
    size: int | None = None


@dataclass(frozen=True)
class SnapshotFile:
    """One UTF-8 Python file read into the immutable in-memory snapshot."""

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
    unstable_paths: tuple[str, ...] = ()


DefinitionKind = Literal["module", "class", "function"]


@dataclass(frozen=True)
class DefinitionDescription:
    """A cache-safe definition description without index-bound identity."""

    qualified_name: str
    kind: DefinitionKind
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


@dataclass(frozen=True)
class IndexBuildStats:
    """Internal build evidence separating definition and relation parsing."""

    source_file_count: int
    definition_cache_hits: int
    definition_cache_misses: int
    definition_ast_parse_count: int
    relation_ast_parse_count: int
    definition_cache_entries_written: int


def build_source_snapshot(
    repository_root: str | Path,
    *,
    repository_id: str,
    revision: str = "working-tree",
    dirty: bool = True,
    index_id: str | None = None,
    identity_version: str = "source-v1",
    max_file_bytes: int = 1_000_000,
    rules_version: str | None = None,
    configuration_hash: str | None = None,
) -> SourceSnapshot:
    """Read a bounded Python source tree and build a reproducible snapshot.

    The caller supplies revision metadata because inspecting a repository must not
    execute arbitrary commands. The default explicitly describes an uncommitted
    working tree. Only regular ``.py`` files are read; excluded directories,
    secrets, symlinks, undecodable files, and oversized files are not parsed.
    """

    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", identity_version):
        raise ValueError("identity_version must be a short stable identifier")

    root = _resolve_repository_root(repository_root)
    paths, issues = _discover_python_files(root)
    snapshot_files: list[SnapshotFile] = []

    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            issues.append(
                SnapshotIssue(relative_path, "read_error", f"could not stat source: {exc}")
            )
            continue

        if size > max_file_bytes:
            issues.append(
                SnapshotIssue(
                    relative_path,
                    "file_too_large",
                    f"source file is {size} bytes; limit is {max_file_bytes}",
                    size=size,
                )
            )
            continue

        try:
            raw = path.read_bytes()
        except OSError as exc:
            issues.append(
                SnapshotIssue(relative_path, "read_error", f"could not read source: {exc}")
            )
            continue

        if len(raw) > max_file_bytes:
            issues.append(
                SnapshotIssue(
                    relative_path,
                    "file_too_large",
                    f"source file exceeds the {max_file_bytes}-byte limit",
                    size=len(raw),
                )
            )
            continue

        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            issues.append(
                SnapshotIssue(relative_path, "decode_error", f"source is not UTF-8: {exc}")
            )
            continue

        snapshot_files.append(
            SnapshotFile(
                path=relative_path,
                size=len(raw),
                content_hash=_sha256(raw),
                text=text,
            )
        )

    snapshot_files.sort(key=lambda source_file: source_file.path)
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


SOURCE_CAPTURE_RULES_VERSION = "python-source-v1"
DEFINITION_EXTRACTION_RULES_VERSION = "python-definitions-v1"
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
        f"+{identity_version}"
    )


def index_configuration_hash(
    *,
    max_file_bytes: int,
    identity_version: str,
    rules_version: str,
) -> str:
    """Fingerprint the source/index settings that can change index contents."""

    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    payload = {
        "identity_version": identity_version,
        "max_file_bytes": max_file_bytes,
        "rules_version": rules_version,
    }
    return _sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def scan_source_manifest(
    repository_root: str | Path,
    *,
    max_file_bytes: int = 1_000_000,
    max_files: int = 2_000,
    max_total_bytes: int = 20 * 1024 * 1024,
    max_seconds: float = 2.0,
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
    scan_complete = True
    scan_truncated = False
    scan_truncation_reason: str | None = None

    def truncate(reason: str) -> None:
        nonlocal scan_complete, scan_truncated, scan_truncation_reason
        scan_complete = False
        scan_truncated = True
        scan_truncation_reason = reason

    def check_time() -> bool:
        if time.monotonic() > deadline:
            truncate("max_seconds")
            return False
        return True

    def visit(directory: Path) -> None:
        nonlocal scanned_files, scanned_bytes, scan_complete
        if scan_truncated or not check_time():
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            relative = _relative_path_for_issue(root, directory)
            issues.append(SnapshotIssue(relative, "read_error", f"could not list directory: {exc}"))
            scan_complete = False
            return

        for entry in entries:
            if scan_truncated or not check_time():
                return
            relative = _relative_path_for_issue(root, entry)
            try:
                is_dir = entry.is_dir()
            except OSError as exc:
                issues.append(SnapshotIssue(relative, "read_error", f"could not inspect source: {exc}"))
                scan_complete = False
                continue
            if is_dir and entry.name.lower() in _EXCLUDED_DIRECTORIES:
                continue
            if _is_secret_name(entry.name):
                continue
            try:
                if entry.is_symlink():
                    issues.append(SnapshotIssue(relative, "symlink", "symbolic links are not indexed"))
                    continue
            except OSError as exc:
                issues.append(SnapshotIssue(relative, "read_error", f"could not inspect source: {exc}"))
                scan_complete = False
                continue
            if is_dir:
                visit(entry)
                continue
            try:
                is_file = entry.is_file()
            except OSError as exc:
                issues.append(SnapshotIssue(relative, "read_error", f"could not inspect source: {exc}"))
                scan_complete = False
                continue
            if entry.suffix.lower() != ".py" or not is_file:
                continue
            if scanned_files >= max_files:
                truncate("max_files")
                return
            scanned_files += 1
            try:
                resolved = entry.resolve(strict=False)
                resolved.relative_to(root)
            except (OSError, ValueError):
                issues.append(SnapshotIssue(relative, "outside_root", "source resolves outside root"))
                continue
            try:
                before = entry.stat()
            except OSError as exc:
                issues.append(SnapshotIssue(relative, "read_error", f"could not stat source: {exc}"))
                scan_complete = False
                continue
            if before.st_size > max_file_bytes:
                issues.append(
                    SnapshotIssue(
                        relative,
                        "file_too_large",
                        f"source file is {before.st_size} bytes; limit is {max_file_bytes}",
                        size=before.st_size,
                    )
                )
                continue
            if scanned_bytes + before.st_size > max_total_bytes:
                truncate("max_total_bytes")
                return
            try:
                raw = entry.read_bytes()
            except OSError as exc:
                issues.append(SnapshotIssue(relative, "read_error", f"could not read source: {exc}"))
                scan_complete = False
                continue
            scanned_bytes += len(raw)
            try:
                after = entry.stat()
            except OSError as exc:
                issues.append(SnapshotIssue(relative, "read_error", f"could not stat source after read: {exc}"))
                scan_complete = False
                continue
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                unstable_paths.append(relative)
                scan_complete = False
            if len(raw) > max_file_bytes:
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
                issues.append(SnapshotIssue(relative, "decode_error", f"source is not UTF-8: {exc}"))
                continue
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
        unstable_paths=tuple(unstable_paths),
    )


def extract_definitions(
    snapshot: SourceSnapshot,
    *,
    cached: Mapping[str, DefinitionCacheEntry] | None = None,
) -> DefinitionIndex:
    """Extract definitions, reusing only validated entries for unchanged files."""

    symbols: list[Symbol] = []
    issues = list(snapshot.issues)
    cache_entries: list[tuple[str, DefinitionCacheEntry]] = []
    ast_parse_count = 0
    cache_hits = 0
    cache_misses = 0
    for source_file in snapshot.files:
        entry = cached.get(source_file.path) if cached is not None else None
        if entry is None:
            ast_parse_count += 1
            entry = _extract_file_definitions(source_file)
            cache_entries.append((source_file.path, entry))
            cache_misses += 1
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
    )


def _extract_file_definitions(source_file: SnapshotFile) -> DefinitionCacheEntry:
    """Parse one file into index-independent definitions and diagnostics."""

    try:
        tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
    except SyntaxError as exc:
        location = f" at line {exc.lineno}" if exc.lineno else ""
        return DefinitionCacheEntry(
            definitions=(),
            issue=SnapshotIssue(
                source_file.path,
                "syntax_error",
                f"could not parse Python source{location}: {exc.msg}",
            ),
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
    return DefinitionCacheEntry(definitions=tuple(definitions))


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


def _discover_python_files(root: Path) -> tuple[list[Path], list[SnapshotIssue]]:
    paths: list[Path] = []
    issues: list[SnapshotIssue] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            relative = _relative_path_for_issue(root, directory)
            issues.append(SnapshotIssue(relative, "read_error", f"could not list directory: {exc}"))
            return

        for entry in entries:
            relative = _relative_path_for_issue(root, entry)
            if entry.is_dir() and entry.name.lower() in _EXCLUDED_DIRECTORIES:
                continue
            if _is_secret_name(entry.name):
                continue
            if entry.is_symlink():
                issues.append(SnapshotIssue(relative, "symlink", "symbolic links are not indexed"))
                continue
            if entry.is_dir():
                visit(entry)
                continue
            if entry.suffix.lower() != ".py" or not entry.is_file():
                continue
            try:
                resolved = entry.resolve(strict=False)
                resolved.relative_to(root)
            except (OSError, ValueError):
                issues.append(SnapshotIssue(relative, "outside_root", "source resolves outside root"))
                continue
            paths.append(entry)

    visit(root)
    return paths, issues


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
    parts = relative_path.removesuffix(".py").split("/")
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
