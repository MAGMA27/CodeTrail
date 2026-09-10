"""Read-only Python snapshots and deterministic definition extraction."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
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
class DefinitionIndex:
    """Definitions extracted from one source snapshot."""

    snapshot: SourceSnapshot
    symbols: tuple[Symbol, ...]
    issues: tuple[SnapshotIssue, ...]


def build_source_snapshot(
    repository_root: str | Path,
    *,
    repository_id: str,
    revision: str = "working-tree",
    dirty: bool = True,
    index_id: str | None = None,
    max_file_bytes: int = 1_000_000,
) -> SourceSnapshot:
    """Read a bounded Python source tree and build a reproducible snapshot.

    The caller supplies revision metadata because inspecting a repository must not
    execute arbitrary commands. The default explicitly describes an uncommitted
    working tree. Only regular ``.py`` files are read; excluded directories,
    secrets, symlinks, undecodable files, and oversized files are not parsed.
    """

    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")

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
    generated_index_id = index_id or _make_index_id(repository_id, snapshot_hash)
    status = "failed" if any(issue.kind in _BLOCKING_ISSUES for issue in issues) else "ready"

    index = IndexSnapshot(
        index_id=generated_index_id,
        repository_id=repository_id,
        revision=revision,
        dirty=dirty,
        snapshot_hash=snapshot_hash,
        status=status,
    )
    return SourceSnapshot(index, tuple(snapshot_files), tuple(issues))


def extract_definitions(snapshot: SourceSnapshot) -> DefinitionIndex:
    """Extract module, class, and function symbols from snapshot text only."""

    symbols: list[Symbol] = []
    issues = list(snapshot.issues)
    for source_file in snapshot.files:
        try:
            tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError as exc:
            location = f" at line {exc.lineno}" if exc.lineno else ""
            issues.append(
                SnapshotIssue(
                    source_file.path,
                    "syntax_error",
                    f"could not parse Python source{location}: {exc.msg}",
                )
            )
            continue

        module_name = _module_name(source_file.path)
        symbols.append(
            _build_symbol(
                index_id=snapshot.index.index_id,
                path=source_file.path,
                qualified_name=module_name,
                kind="module",
                start_line=1,
                end_line=max(1, len(source_file.text.splitlines())),
            )
        )
        visitor = _DefinitionVisitor(snapshot.index.index_id, source_file.path, module_name)
        visitor.visit(tree)
        symbols.extend(visitor.symbols)

    symbols.sort(key=lambda symbol: (symbol.path, symbol.start_line, symbol.end_line, symbol.kind, symbol.qualified_name))
    issues.sort(key=lambda issue: (issue.path, issue.kind))
    return DefinitionIndex(snapshot=snapshot, symbols=tuple(symbols), issues=tuple(issues))


class _DefinitionVisitor(ast.NodeVisitor):
    def __init__(self, index_id: str, path: str, module_name: str) -> None:
        self.index_id = index_id
        self.path = path
        self.module_name = module_name
        self._name_stack: list[str] = []
        self.symbols: list[Symbol] = []

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
        self.symbols.append(
            _build_symbol(
                index_id=self.index_id,
                path=self.path,
                qualified_name=qualified_name,
                kind=kind,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            )
        )
        self._name_stack.append(node.name)
        self.generic_visit(node)
        self._name_stack.pop()


def _build_symbol(
    *,
    index_id: str,
    path: str,
    qualified_name: str,
    kind: Literal["module", "class", "function"],
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


def _make_index_id(repository_id: str, snapshot_hash: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", repository_id).strip("-_") or "repository"
    return f"index-{slug[:80]}-{snapshot_hash[:16]}"

