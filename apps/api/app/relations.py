"""Conservative imports and call relations over one immutable source snapshot."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import posixpath
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from .contracts import CodeEdge, ContextualCall, SourceLocation, Symbol, UnresolvedReference
from .indexing import (
    DefinitionIndex,
    IndexingControl,
    SnapshotIssue,
    SourceSnapshot,
    SnapshotFile,
    TypeScriptCallDescription,
    TypeScriptImportDescription,
    _module_name,
)


RelationKind = Literal["imports", "calls"]
BindingKind = Literal["module", "symbol"]
RelationScopeKind = Literal["module", "class", "function"]
# The semantic version changes when the graph/unresolved meaning changes.
RELATION_SEMANTICS_VERSION = "relations-v10"
# This version only governs the persisted, index-independent facts payload.
RELATION_FACT_EXTRACTION_RULES_VERSION = "python-relation-facts-v4"
_RELATION_FACTS_FORMAT_VERSION = "relation-facts-v4"
# This namespace is part of generated immutable index IDs. Bump it when source
# boundaries or build rules can change an index with the same content hash.
INDEX_BUILD_IDENTITY_VERSION = "relations-v14"

# Relation budgets. Defaults come from measured full extractions (see
# apps/api/indexing.md); each one can be overridden by its environment variable.
DEFAULT_RELATION_MAX_SECONDS = 60.0
DEFAULT_RELATION_MAX_EDGES = 100_000
DEFAULT_RELATION_MAX_UNRESOLVED = 50_000
# Share of max_seconds available to fact gathering; the rest is kept for
# resolving and storing whatever was gathered.
RELATION_FACT_TIME_SHARE = 0.75


@dataclass(frozen=True)
class RelationBudgets:
    max_seconds: float = DEFAULT_RELATION_MAX_SECONDS
    max_edges: int = DEFAULT_RELATION_MAX_EDGES
    max_unresolved: int = DEFAULT_RELATION_MAX_UNRESOLVED


def _positive_int_from_environment(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float_from_environment(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def relation_budgets_from_environment() -> RelationBudgets:
    """Read relation budgets; missing or invalid values fall back to defaults."""

    return RelationBudgets(
        max_seconds=_positive_float_from_environment(
            "CODETRAIL_RELATION_MAX_SECONDS", DEFAULT_RELATION_MAX_SECONDS
        ),
        max_edges=_positive_int_from_environment(
            "CODETRAIL_RELATION_MAX_EDGES", DEFAULT_RELATION_MAX_EDGES
        ),
        max_unresolved=_positive_int_from_environment(
            "CODETRAIL_RELATION_MAX_UNRESOLVED", DEFAULT_RELATION_MAX_UNRESOLVED
        ),
    )


def relation_coverage_limitations(
    *,
    truncated: bool,
    truncation_reason: str | None,
    unresolved_truncated: bool | None,
    unresolved_truncation_reason: str | None,
) -> list[str]:
    """Describe index coverage without treating missing relations as proof of absence."""

    limitations: list[str] = []
    if truncated:
        reason = truncation_reason or "未知原因"
        limitations.append(f"固定关系索引扫描未完成：{reason}；空邻接不能证明没有关系。")
    if unresolved_truncated is True:
        reason = unresolved_truncation_reason or "未知原因"
        limitations.append(
            f"固定关系索引的 unresolved 详情已截断：{reason}；未返回的 unresolved 不能被当作不存在。"
        )
    elif unresolved_truncated is None:
        limitations.append("固定关系索引的 unresolved 覆盖状态未知（旧索引未记录该字段）。")
    return limitations


@dataclass(frozen=True)
class RelationImportAlias:
    """One import alias retained without an AST object."""

    name: str
    asname: str | None = None


@dataclass(frozen=True)
class RelationImportFact:
    """One ordered import statement and all aliases it contains."""

    kind: Literal["import", "from"]
    aliases: tuple[RelationImportAlias, ...]
    module: str | None
    level: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RelationBindings:
    """Scope-wide binding facts used by conservative target resolution."""

    imports: tuple[RelationImportFact, ...]
    assigned: tuple[str, ...]
    definition_names: tuple[str, ...]
    global_names: tuple[str, ...]
    dynamic_names: tuple[str, ...]
    parameter_names: tuple[str, ...]
    positional_parameter_names: tuple[str, ...]
    has_variadic_parameters: bool
    nonlocal_names: tuple[str, ...]
    attribute_write_names: tuple[str, ...]
    indirect_attribute_write_names: tuple[str, ...]


@dataclass(frozen=True)
class RelationCallTargetFact:
    """A simple name, attribute, or direct constructor expression."""

    target_kind: Literal["name", "attribute", "constructor"]
    target_name: str | None
    target_parts: tuple[str, ...]


@dataclass(frozen=True)
class RelationCallArgumentFact:
    """A call argument's simple source, if its expression is statically shaped."""

    position: int | None
    keyword: str | None
    target: RelationCallTargetFact | None


@dataclass(frozen=True)
class RelationCallFact:
    """A call expression's syntactic target and source range."""

    expression: str
    target_kind: Literal["name", "attribute", "dynamic"]
    target_name: str | None
    target_parts: tuple[str, ...]
    first_argument: RelationCallTargetFact | None
    arguments: tuple[RelationCallArgumentFact, ...]
    has_argument_expansion: bool
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RelationAssignmentFact:
    """An ordered assignment that can bind or invalidate a local object name."""

    target_names: tuple[str, ...]
    target_attributes: tuple[RelationCallTargetFact, ...]
    constructor_name: str | None
    value_name: str | None
    certainty: Literal["definite", "uncertain"]
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RelationImportEvent:
    fact: RelationImportFact


@dataclass(frozen=True)
class RelationCallEvent:
    fact: RelationCallFact


@dataclass(frozen=True)
class RelationAssignmentEvent:
    fact: RelationAssignmentFact


@dataclass(frozen=True)
class RelationReturnEvent:
    target: RelationCallTargetFact | None


@dataclass(frozen=True)
class RelationScopeEvent:
    scope: RelationScopeFacts


RelationEvent = (
    RelationImportEvent
    | RelationCallEvent
    | RelationAssignmentEvent
    | RelationReturnEvent
    | RelationScopeEvent
)


@dataclass(frozen=True)
class RelationScopeFacts:
    """One lexical scope and its ordered relation events."""

    qualified_name: str
    kind: RelationScopeKind
    start_line: int
    bindings: RelationBindings
    events: tuple[RelationEvent, ...]
    has_decorators: bool
    has_bases: bool


@dataclass(frozen=True)
class RelationFileFacts:
    """Versioned, index-independent syntax facts for one source file."""

    path: str
    module_name: str
    syntax_error: bool
    scope: RelationScopeFacts | None


@dataclass(frozen=True)
class RelationIndex:
    """Static relations and unresolved references tied to one definition index."""

    definitions: DefinitionIndex
    edges: tuple[CodeEdge, ...]
    unresolved: tuple[UnresolvedReference, ...]
    truncated: bool
    truncation_reason: str | None
    unresolved_truncated: bool | None = None
    unresolved_truncation_reason: str | None = None
    ast_parse_count: int = field(default=0, compare=False)
    fact_cache_hits: int = field(default=0, compare=False)
    fact_cache_misses: int = field(default=0, compare=False)
    shared_ast_reuse_count: int = field(default=0, compare=False)
    fact_cache_entries: tuple[tuple[str, RelationFileFacts], ...] = field(
        default=(), compare=False, repr=False
    )
    http_client_requests: tuple[HttpClientRequestFact, ...] = ()
    contextual_calls: tuple[ContextualCall, ...] = ()

    @property
    def snapshot(self) -> SourceSnapshot:
        return self.definitions.snapshot

    @property
    def symbols(self) -> tuple[Symbol, ...]:
        return self.definitions.symbols

    @property
    def issues(self) -> tuple[SnapshotIssue, ...]:
        return self.definitions.issues


@dataclass(frozen=True)
class HttpClientRequestFact:
    """A frozen call through a verified HTTP client wrapper, without route certainty."""

    request_id: str
    index_id: str
    source_module_id: str
    source_path: str
    method: str | None
    request_path: str | None
    path_kind: str
    request_path_template: str | None
    unknown_reason: str | None
    client_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class _Binding:
    kind: BindingKind
    symbol: Symbol | None = None
    module_name: str | None = None


@dataclass
class _Scope:
    facts: RelationScopeFacts
    kind: RelationScopeKind
    symbol: Symbol
    parent: _Scope | None
    bindings: dict[str, _Binding]
    shadowed: set[str]
    dynamic_names: set[str]
    attribute_write_names: set[str]
    object_bindings: dict[str, Symbol]
    object_instance_site_ids: dict[str, str]
    object_invalidated: set[str]
    verified_threadpool_wrappers: set[str]
    active_threadpool_wrappers: set[str]
    blocked_threadpool_names: set[str]


@dataclass(frozen=True)
class _ImportResolution:
    binding_name: str | None
    binding: _Binding | None
    target: Symbol | None
    expression: str
    reason: str


@dataclass(frozen=True)
class _ConstructorCallSite:
    site_id: str
    class_symbol: Symbol
    caller_symbol: Symbol
    location: SourceLocation
    positional_arguments: tuple[Symbol | None, ...]
    keyword_arguments: tuple[tuple[str, Symbol | None], ...]
    has_argument_expansion: bool


@dataclass(frozen=True)
class _ActivationCallSite:
    caller_symbol: Symbol
    target_symbol: Symbol
    receiver_site_id: str
    location: SourceLocation


def _constructor_instance_site_id(
    caller_symbol: Symbol,
    path: str,
    class_symbol: Symbol,
    line: int,
) -> str:
    identity = "\0".join(
        [caller_symbol.index_id, caller_symbol.symbol_id, path, class_symbol.symbol_id, str(line)]
    )
    return f"instance-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


class _RelationBudget:
    def __init__(self, max_edges: int, max_unresolved: int, max_seconds: float) -> None:
        self.max_edges = max_edges
        self.max_unresolved = max_unresolved
        started = time.monotonic()
        self.deadline = started + max_seconds
        # Fact gathering may use only part of the time budget so that whatever was
        # gathered can still be resolved and stored before the overall deadline.
        self.fact_deadline = started + max_seconds * RELATION_FACT_TIME_SHARE
        self.edge_count = 0
        self.unresolved_count = 0
        self.truncated = False
        self.reason: str | None = None
        self.unresolved_truncated = False
        self.unresolved_reason: str | None = None

    def reserve(self, category: Literal["edges", "unresolved"]) -> bool:
        if self.truncated:
            return False
        if time.monotonic() >= self.deadline:
            self._stop("max_seconds exceeded")
            return False
        if category == "edges":
            if self.edge_count >= self.max_edges:
                self._stop("max_edges reached")
                return False
            self.edge_count += 1
        else:
            if self.unresolved_count >= self.max_unresolved:
                self.unresolved_truncated = True
                self.unresolved_reason = "max_unresolved reached"
                return False
            self.unresolved_count += 1
        return True

    def _stop(self, reason: str) -> None:
        self.truncated = True
        self.reason = reason


def _resolve_typescript_module_target(
    path: str,
    specifier: str,
    target_files: set[str],
) -> tuple[str | None, str | None, list[str]]:
    if not specifier.startswith("."):
        return None, "external_import_or_path_alias", []
    if any(mark in specifier for mark in ("\\", "?", "#", "%")):
        return None, "unsupported_relative_import_shape", []
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
    if normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
        return None, "relative_import_outside_snapshot", []
    suffix = posixpath.splitext(normalized)[1].lower()
    if suffix in {".ts", ".tsx"}:
        candidates = [normalized]
    elif suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        stem = normalized[: -len(suffix)]
        candidates = [f"{stem}.ts", f"{stem}.tsx", f"{stem}.d.ts"]
    elif not suffix:
        candidates = [
            f"{normalized}.ts",
            f"{normalized}.tsx",
            f"{normalized}.d.ts",
            f"{normalized}/index.ts",
            f"{normalized}/index.tsx",
            f"{normalized}/index.d.ts",
        ]
    else:
        candidates = []
    matches = sorted(set(candidate for candidate in candidates if candidate in target_files))
    if len(matches) == 1:
        return matches[0], None, matches
    if len(matches) > 1:
        return None, "ambiguous_relative_import", matches
    return None, "missing_relative_import_target", []


def extract_relations(
    definitions: DefinitionIndex,
    *,
    cached: Mapping[str, RelationFileFacts] | None = None,
    max_edges: int = DEFAULT_RELATION_MAX_EDGES,
    max_unresolved: int = DEFAULT_RELATION_MAX_UNRESOLVED,
    max_seconds: float = DEFAULT_RELATION_MAX_SECONDS,
    control: IndexingControl | None = None,
) -> RelationIndex:
    """Resolve file syntax facts against the current immutable definition set.

    Budgets never discard finished work. If fact gathering runs past its share
    of ``max_seconds``, the files gathered so far are still resolved (direct
    imports and calls only; constructor-attribute and contextual inference
    need every file) and the result is marked truncated.
    """

    if max_edges < 1 or max_unresolved < 1 or max_seconds <= 0:
        raise ValueError("relation budgets must be positive")

    snapshot = definitions.snapshot
    maps = _DefinitionMaps(
        definitions.symbols,
        package_init_paths=tuple(
            source_file.path
            for source_file in definitions.snapshot.files
            if source_file.path.endswith("/__init__.py") or source_file.path == "__init__.py"
        ),
    )
    budget = _RelationBudget(max_edges, max_unresolved, max_seconds)
    edges: list[CodeEdge] = []
    unresolved: list[UnresolvedReference] = []
    ast_parse_count = 0
    fact_cache_hits = 0
    fact_cache_misses = 0
    shared_ast_reuse_count = 0
    fact_cache_entries: list[tuple[str, RelationFileFacts]] = []
    parsed_trees = dict(definitions.parsed_trees)
    syntax_error_paths = {
        issue.path for issue in definitions.issues if issue.kind == "syntax_error"
    }
    facts_by_path: dict[str, RelationFileFacts] = {}
    facts_complete = True

    for source_file in snapshot.files:
        if control is not None:
            control.check()
        if budget.truncated:
            break
        if not source_file.path.lower().endswith(".py"):
            continue
        facts = cached.get(source_file.path) if cached is not None else None
        if facts is None:
            fact_cache_misses += 1
            tree = parsed_trees.get(source_file.path)
            if tree is not None:
                shared_ast_reuse_count += 1
            if tree is None and source_file.path in syntax_error_paths:
                facts, parsed = (
                    RelationFileFacts(
                        path=source_file.path,
                        module_name=_module_name(source_file.path),
                        syntax_error=True,
                        scope=None,
                    ),
                    0,
                )
            else:
                try:
                    facts, parsed = _extract_relation_facts(
                        source_file,
                        tree=tree,
                        deadline=budget.fact_deadline,
                    )
                except _RelationFactsIncomplete:
                    facts_complete = False
                    break
            ast_parse_count += parsed
            fact_cache_entries.append((source_file.path, facts))
        else:
            fact_cache_hits += 1
        facts_by_path[source_file.path] = facts

    scope_facts_by_key: dict[tuple[str, str, RelationScopeKind, int], RelationScopeFacts] = {}
    for path, facts in facts_by_path.items():
        if facts.scope is not None:
            _collect_scope_facts(path, facts.scope, scope_facts_by_key)

    constructor_sites: list[_ConstructorCallSite] = []
    activation_call_sites: list[_ActivationCallSite] = []
    unsafe_constructor_classes: set[str] = set()
    bypassed_constructor_classes: set[str] = set()
    escaped_instance_site_ids: set[str] = set()
    binding_mutation_after_escape_unverified_site_ids: set[str] = set()
    multisegment_attribute_writes: dict[str, list[Symbol | None]] = {}
    if facts_complete and not budget.truncated:
        for source_file in snapshot.files:
            facts = facts_by_path.get(source_file.path)
            module_symbol = maps.modules_by_path.get(source_file.path)
            if facts is None or facts.syntax_error or facts.scope is None or module_symbol is None:
                continue
            resolver = _RelationFactResolver(
                path=source_file.path,
                module_name=facts.module_name,
                scope_facts=facts.scope,
                module_symbol=module_symbol,
                maps=maps,
                scope_facts_by_key=scope_facts_by_key,
                budget=budget,
                edges=[],
                unresolved=[],
                constructor_sites=constructor_sites,
                activation_call_sites=activation_call_sites,
                unsafe_constructor_classes=unsafe_constructor_classes,
                bypassed_constructor_classes=bypassed_constructor_classes,
                escaped_instance_site_ids=escaped_instance_site_ids,
                binding_mutation_after_escape_unverified_site_ids=(
                    binding_mutation_after_escape_unverified_site_ids
                ),
                multisegment_attribute_writes=multisegment_attribute_writes,
                collect_only=True,
            )
            resolver.visit_scope()

    constructor_attribute_bindings: dict[tuple[str, str], Symbol] = {}
    constructor_writes: dict[str, list[RelationAssignmentFact]] = {}
    indirect_constructor_writes: dict[str, list[RelationAssignmentFact]] = {}
    if facts_complete:
        constructor_attribute_bindings, constructor_writes, indirect_constructor_writes = _resolve_constructor_attribute_bindings(
            facts_by_path,
            scope_facts_by_key,
            maps,
            constructor_sites,
            unsafe_constructor_classes,
            bypassed_constructor_classes,
            multisegment_attribute_writes,
            budget,
        )

    if not budget.truncated:
        for source_file in snapshot.files:
            if budget.truncated:
                break
            facts = facts_by_path.get(source_file.path)
            if facts is None or facts.syntax_error or facts.scope is None:
                continue
            module_symbol = maps.modules_by_path.get(source_file.path)
            if module_symbol is None:
                continue
            resolver = _RelationFactResolver(
                path=source_file.path,
                module_name=facts.module_name,
                scope_facts=facts.scope,
                module_symbol=module_symbol,
                maps=maps,
                scope_facts_by_key=scope_facts_by_key,
                budget=budget,
                edges=edges,
                unresolved=unresolved,
                constructor_attribute_bindings=constructor_attribute_bindings,
            )
            resolver.visit_scope()

    modules_by_path = maps.modules_by_path
    target_files = set(modules_by_path)
    edge_occurrences: dict[tuple[str, str, int, int], int] = {}
    unresolved_occurrences: dict[tuple[str, str, int, int], int] = {}
    for path, fact in definitions.typescript_imports:
        if budget.truncated:
            break
        source_module = modules_by_path.get(path)
        if source_module is None:
            continue
        target_path: str | None = None
        reason: str | None = None
        candidates: list[str] = []
        if fact.kind == "dynamic":
            reason = "dynamic_import"
        else:
            target_path, reason, candidates = _resolve_typescript_module_target(
                path,
                fact.specifier,
                target_files,
            )
        if target_path is not None:
            if not budget.reserve("edges"):
                break
            target_module = modules_by_path[target_path]
            occurrence_key = (path, target_path, fact.start_line, fact.end_line)
            occurrence = edge_occurrences.get(occurrence_key, 0)
            edge_occurrences[occurrence_key] = occurrence + 1
            identity = "\0".join(
                [snapshot.index.index_id, "imports", source_module.symbol_id, target_module.symbol_id,
                 path, str(fact.start_line), str(fact.end_line), str(occurrence), "typescript"]
            )
            edges.append(
                CodeEdge(
                    edge_id=f"edge-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
                    index_id=snapshot.index.index_id,
                    source_id=source_module.symbol_id,
                    target_id=target_module.symbol_id,
                    kind="imports",
                    extraction_method="ast",
                    source_location=SourceLocation(
                        path=path,
                        start_line=fact.start_line,
                        end_line=fact.end_line,
                    ),
                )
            )
        elif reason is not None:
            if not budget.reserve("unresolved"):
                # Losing one diagnostic must not hide later definite imports.
                continue
            expression = fact.specifier[:500] or "<dynamic>"
            unresolved_key = (source_module.symbol_id, expression, reason, fact.start_line)
            occurrence = unresolved_occurrences.get(unresolved_key, 0)
            unresolved_occurrences[unresolved_key] = occurrence + 1
            identity = "\0".join(
                [snapshot.index.index_id, source_module.symbol_id, path, expression, reason,
                 str(fact.start_line), str(fact.end_line), str(occurrence)]
            )
            unresolved.append(
                UnresolvedReference(
                    unresolved_id=f"unresolved-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
                    index_id=snapshot.index.index_id,
                    source_symbol_id=source_module.symbol_id,
                    path=path,
                    expression=expression,
                    reason=reason,
                    possible_target_ids=[modules_by_path[candidate].symbol_id for candidate in candidates],
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                )
            )

    http_client_requests: list[HttpClientRequestFact] = []
    request_occurrences: dict[tuple[str, str, int, int], int] = {}
    for path, fact in definitions.typescript_http_requests:
        if budget.truncated:
            break
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            break
        source_module = modules_by_path.get(path)
        if source_module is None:
            continue
        key = (path, fact.client_name, fact.start_line, fact.end_line)
        occurrence = request_occurrences.get(key, 0)
        request_occurrences[key] = occurrence + 1
        identity = "\0".join(
            [snapshot.index.index_id, source_module.symbol_id, fact.method or "", fact.path or "",
             fact.path_kind, fact.path_template or "",
             fact.client_name, path, str(fact.start_line), str(fact.end_line), str(occurrence)]
        )
        http_client_requests.append(
            HttpClientRequestFact(
                request_id=f"http-request-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
                index_id=snapshot.index.index_id,
                source_module_id=source_module.symbol_id,
                source_path=path,
                method=fact.method,
                request_path=fact.path,
                path_kind=fact.path_kind,
                request_path_template=fact.path_template,
                unknown_reason=fact.unknown_reason,
                client_name=fact.client_name,
                start_line=fact.start_line,
                end_line=fact.end_line,
            )
        )
        if fact.unknown_reason is not None and budget.reserve("unresolved"):
            unresolved_key = (source_module.symbol_id, fact.client_name, fact.unknown_reason, fact.start_line)
            occurrence = unresolved_occurrences.get(unresolved_key, 0)
            unresolved_occurrences[unresolved_key] = occurrence + 1
            reference = "<dynamic>" if fact.path is None else fact.path
            unresolved_id = hashlib.sha256(
                "\0".join([snapshot.index.index_id, source_module.symbol_id, path, reference,
                             fact.unknown_reason, str(fact.start_line), str(fact.end_line), str(occurrence)]).encode("utf-8")
            ).hexdigest()[:32]
            unresolved.append(
                UnresolvedReference(
                    unresolved_id=f"unresolved-{unresolved_id}",
                    index_id=snapshot.index.index_id,
                    source_symbol_id=source_module.symbol_id,
                    path=path,
                    expression=f"{fact.client_name}({reference})"[:500],
                    reason=fact.unknown_reason,
                    possible_target_ids=[],
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                )
            )

    contextual_calls = (
        _resolve_contextual_calls(
            snapshot.index.index_id,
            constructor_sites,
            activation_call_sites,
            unsafe_constructor_classes,
            escaped_instance_site_ids,
            binding_mutation_after_escape_unverified_site_ids,
            scope_facts_by_key,
            maps,
            constructor_writes,
            indirect_constructor_writes,
            multisegment_attribute_writes,
            edges,
            budget,
        )
        if facts_complete
        else []
    )

    export_targets: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for path, fact in definitions.typescript_exports:
        export_targets.setdefault((path, fact.exported_name), []).append(
            (fact.target_qualified_name, fact.target_start_line)
        )
    typescript_call_occurrences: dict[tuple[str, str, int, int], int] = {}
    for path, fact in definitions.typescript_calls:
        if budget.truncated:
            break
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            break
        source_module = modules_by_path.get(path)
        caller = maps.by_key.get((path, fact.caller_qualified_name, "function", fact.caller_start_line))
        if source_module is None:
            continue
        caller_symbol = caller or source_module
        target: Symbol | None = None
        reason = fact.reason
        if reason is None and fact.target_qualified_name is not None and fact.target_start_line is not None:
            target = maps.by_key.get((path, fact.target_qualified_name, "function", fact.target_start_line))
            if target is None:
                reason = "call_target_not_in_snapshot"
        elif reason is None and fact.import_specifier is not None and fact.imported_name is not None:
            target_path, reason, _candidates = _resolve_typescript_module_target(
                path,
                fact.import_specifier,
                target_files,
            )
            if target_path is not None:
                exports = sorted(set(export_targets.get((target_path, fact.imported_name), [])))
                if len(exports) == 1:
                    target_name, target_line = exports[0]
                    target = maps.by_key.get((target_path, target_name, "function", target_line))
                    if target is None:
                        reason = "named_import_target_not_in_snapshot"
                elif len(exports) > 1:
                    reason = "ambiguous_named_import_target"
                else:
                    reason = "missing_named_export"
        if target is not None and caller is not None:
            if not budget.reserve("edges"):
                break
            key = (caller.symbol_id, target.symbol_id, fact.start_line, fact.end_line)
            occurrence = typescript_call_occurrences.get(key, 0)
            typescript_call_occurrences[key] = occurrence + 1
            identity = "\0".join(
                [snapshot.index.index_id, "calls", caller.symbol_id, target.symbol_id,
                 path, str(fact.start_line), str(fact.end_line), str(occurrence), "typescript"]
            )
            edges.append(CodeEdge(
                edge_id=f"edge-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
                index_id=snapshot.index.index_id,
                source_id=caller.symbol_id,
                target_id=target.symbol_id,
                kind="calls",
                extraction_method="ast",
                source_location=SourceLocation(path=path, start_line=fact.start_line, end_line=fact.end_line),
            ))
        else:
            unresolved_reason = reason or ("caller_scope_not_indexed" if caller is None else "call_target_not_in_snapshot")
            if not budget.reserve("unresolved"):
                continue
            expression = fact.expression[:500] or "unknown call"
            key = (caller_symbol.symbol_id, expression, fact.start_line, fact.end_line)
            occurrence = typescript_call_occurrences.get(key, 0)
            typescript_call_occurrences[key] = occurrence + 1
            identity = "\0".join(
                [snapshot.index.index_id, caller_symbol.symbol_id, path, expression, unresolved_reason,
                 str(fact.start_line), str(fact.end_line), str(occurrence)]
            )
            candidates = maps.candidates(expression.rsplit(".", 1)[-1]) if reason in {
                "call_target_not_in_snapshot", "name_shadowed_in_scope", "ambiguous_call_target",
            } else []
            unresolved.append(UnresolvedReference(
                unresolved_id=f"unresolved-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
                index_id=snapshot.index.index_id,
                source_symbol_id=caller_symbol.symbol_id,
                path=path,
                expression=expression,
                reason=unresolved_reason,
                possible_target_ids=[candidate.symbol_id for candidate in candidates],
                start_line=fact.start_line,
                end_line=fact.end_line,
            ))

    if not facts_complete and not budget.truncated:
        budget._stop("max_seconds exceeded")
    if definitions.typescript_calls_truncated and not budget.truncated:
        budget._stop(definitions.typescript_calls_truncation_reason or "TypeScript call facts truncated")

    edges.sort(key=lambda edge: (edge.source_location.path, edge.source_location.start_line, edge.kind, edge.edge_id))
    unresolved.sort(key=lambda item: (item.path, item.start_line, item.expression, item.unresolved_id))
    return RelationIndex(
        definitions=definitions,
        edges=tuple(edges),
        unresolved=tuple(unresolved),
        truncated=budget.truncated,
        truncation_reason=budget.reason,
        unresolved_truncated=budget.unresolved_truncated,
        unresolved_truncation_reason=budget.unresolved_reason,
        ast_parse_count=ast_parse_count,
        fact_cache_hits=fact_cache_hits,
        fact_cache_misses=fact_cache_misses,
        shared_ast_reuse_count=shared_ast_reuse_count,
        fact_cache_entries=tuple(fact_cache_entries),
        http_client_requests=tuple(http_client_requests),
        contextual_calls=tuple(contextual_calls),
    )


def build_relation_index(
    definitions: DefinitionIndex,
    *,
    cached: Mapping[str, RelationFileFacts] | None = None,
    max_edges: int = DEFAULT_RELATION_MAX_EDGES,
    max_unresolved: int = DEFAULT_RELATION_MAX_UNRESOLVED,
    max_seconds: float = DEFAULT_RELATION_MAX_SECONDS,
) -> RelationIndex:
    """Named entry point for callers that treat relations as an index stage."""

    return extract_relations(
        definitions,
        cached=cached,
        max_edges=max_edges,
        max_unresolved=max_unresolved,
        max_seconds=max_seconds,
    )


class _DefinitionMaps:
    def __init__(
        self,
        symbols: tuple[Symbol, ...],
        *,
        package_init_paths: tuple[str, ...] = (),
    ) -> None:
        self.by_key: dict[tuple[str, str, str, int], Symbol] = {}
        self.by_qualified_name: dict[str, list[Symbol]] = {}
        self.modules_by_path: dict[str, Symbol] = {}
        self.modules_by_name: dict[str, Symbol] = {}
        self.simple_candidates: dict[str, list[Symbol]] = {}
        self._sorted_candidates: dict[str, list[Symbol]] = {}
        self.package_aliases: dict[str, str] = {}
        for symbol in symbols:
            self.by_key[(symbol.path, symbol.qualified_name, symbol.kind, symbol.start_line)] = symbol
            self.by_qualified_name.setdefault(symbol.qualified_name, []).append(symbol)
            if symbol.kind == "module":
                self.modules_by_path[symbol.path] = symbol
                self.modules_by_name[symbol.qualified_name] = symbol
            elif symbol.kind in {"class", "function", "test"}:
                simple_name = symbol.qualified_name.rsplit(".", 1)[-1]
                self.simple_candidates.setdefault(simple_name, []).append(symbol)
        init_paths = {
            path.rsplit("/", 1)[0]
            for path in package_init_paths
            if path.endswith("/__init__.py")
        }
        package_roots: dict[str, set[str]] = {}
        for package_path in init_paths:
            if any(
                "/".join(package_path.split("/")[:depth]) in init_paths
                for depth in range(1, len(package_path.split("/")))
            ):
                continue
            package_symbol = self.modules_by_path.get(f"{package_path}/__init__.py")
            if package_symbol is None:
                continue
            short_name = package_path.rsplit("/", 1)[-1]
            package_roots.setdefault(short_name, set()).add(package_symbol.qualified_name)
        for short_name, canonical_roots in package_roots.items():
            if len(canonical_roots) != 1:
                continue
            canonical_root = next(iter(canonical_roots))
            if short_name == canonical_root or short_name in self.modules_by_name:
                continue
            self.package_aliases[short_name] = canonical_root
        for short_name, canonical_root in self.package_aliases.items():
            for module in self.modules_by_path.values():
                if module.qualified_name == canonical_root:
                    alias = short_name
                elif module.qualified_name.startswith(f"{canonical_root}."):
                    alias = f"{short_name}{module.qualified_name[len(canonical_root):]}"
                else:
                    continue
                if alias not in self.modules_by_name:
                    self.modules_by_name[alias] = module

    def one_by_name(self, qualified_name: str) -> Symbol | None:
        values = self.by_qualified_name.get(qualified_name, [])
        if values:
            return values[0] if len(values) == 1 else None
        root, separator, suffix = qualified_name.partition(".")
        canonical_root = self.package_aliases.get(root)
        if canonical_root is None:
            return None
        alias_name = f"{canonical_root}.{suffix}" if separator else canonical_root
        canonical_values = self.by_qualified_name.get(alias_name, [])
        return canonical_values[0] if len(canonical_values) == 1 else None

    def candidates(self, name: str) -> list[Symbol]:
        # Read-only for callers; memoized because large repositories ask for the
        # same simple names once per unresolved call.
        cached = self._sorted_candidates.get(name)
        if cached is None:
            cached = sorted(self.simple_candidates.get(name, []), key=lambda symbol: symbol.symbol_id)
            self._sorted_candidates[name] = cached
        return cached


def _ast_node_types(*bases: type[ast.AST]) -> frozenset[type[ast.AST]]:
    found: set[type[ast.AST]] = set()
    pending = list(bases)
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(current.__subclasses__())
    return frozenset(item for item in found if not item.__subclasses__())


# Nodes whose subtrees can never hold a definition, assignment, import or call.
# Both fact walks short-circuit them; the result is identical to visiting them.
_TOKEN_NODE_TYPES = _ast_node_types(ast.expr_context, ast.operator, ast.unaryop, ast.cmpop, ast.boolop)
_LEAF_BINDING_NODE_TYPES = _TOKEN_NODE_TYPES | {ast.Constant, ast.Pass, ast.Break, ast.Continue}
_LEAF_RELATION_NODE_TYPES = _LEAF_BINDING_NODE_TYPES | {ast.Name, ast.Global, ast.Nonlocal}
# Node kinds with a dedicated branch in _extract_relation_events; every other
# kind only forwards its children.
_RELATION_EVENT_NODE_TYPES = frozenset(
    {
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.NamedExpr,
        ast.Delete,
        ast.For,
        ast.AsyncFor,
        ast.With,
        ast.AsyncWith,
        ast.ExceptHandler,
        ast.comprehension,
        ast.Call,
        ast.Return,
    }
)


class _BindingCollector(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> None:
        if type(node) in _LEAF_BINDING_NODE_TYPES:
            return None
        return super().visit(node)

    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.assigned: set[str] = set()
        self.definition_names: set[str] = set()
        self.global_names: set[str] = set()
        self.dynamic_names: set[str] = set()
        self.nonlocal_names: set[str] = set()
        self.attribute_write_names: set[str] = set()
        self.indirect_attribute_write_names: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definition_names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.definition_names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definition_names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._collect_attribute_write_names(node.targets)
        if _is_getattr_call(node.value):
            self.dynamic_names.update(_assigned_names(node.targets))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._collect_attribute_write_names([node.target])
        if node.value is not None and _is_getattr_call(node.value):
            self.dynamic_names.update(_assigned_names([node.target]))
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if _is_getattr_call(node.value):
            self.dynamic_names.update(_assigned_names([node.target]))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._collect_attribute_write_names([node.target])
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        self._collect_attribute_write_names(node.targets)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"setattr", "delattr"}:
            name = (
                node.args[1].value
                if len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                else None
            )
            attribute_name = name or "*"
            self.attribute_write_names.add(attribute_name)
            self.indirect_attribute_write_names.add(attribute_name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.assigned.add(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.assigned.add(node.name)
        self.generic_visit(node)

    def _collect_attribute_write_names(self, targets: list[ast.expr]) -> None:
        for target in targets:
            if isinstance(target, ast.Attribute):
                self.attribute_write_names.add(target.attr)
                if _attribute_parts(target) is None:
                    self.indirect_attribute_write_names.add(target.attr)


class _RelationFactsIncomplete(RuntimeError):
    """Raised when the bounded fact walk cannot finish one source file."""


_UNCERTAIN_RELATION_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.IfExp,
    ast.BoolOp,
)


def _extract_relation_facts(
    source_file: SnapshotFile,
    *,
    tree: ast.Module | None,
    deadline: float,
) -> tuple[RelationFileFacts, int]:
    """Extract cache-safe relation facts, reusing a definition-stage AST when present."""

    parsed = 0
    if tree is None:
        if time.monotonic() >= deadline:
            raise _RelationFactsIncomplete
        parsed = 1
        try:
            tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError:
            return (
                RelationFileFacts(
                    path=source_file.path,
                    module_name=_module_name(source_file.path),
                    syntax_error=True,
                    scope=None,
                ),
                parsed,
            )
    if time.monotonic() >= deadline:
        raise _RelationFactsIncomplete
    module_name = _module_name(source_file.path)
    scope = _extract_scope_facts(
        tree,
        qualified_name=module_name,
        kind="module",
        start_line=1,
        deadline=deadline,
    )
    return (
        RelationFileFacts(
            path=source_file.path,
            module_name=module_name,
            syntax_error=False,
            scope=scope,
        ),
        parsed,
    )


def _extract_scope_facts(
    node: ast.AST,
    *,
    qualified_name: str,
    kind: RelationScopeKind,
    start_line: int,
    deadline: float,
) -> RelationScopeFacts:
    if time.monotonic() >= deadline:
        raise _RelationFactsIncomplete
    collector = _BindingCollector()
    for child in _scope_body(node):
        if time.monotonic() >= deadline:
            raise _RelationFactsIncomplete
        collector.visit(child)
    bindings = RelationBindings(
        imports=tuple(_import_fact_from_node(import_node) for import_node in collector.imports),
        assigned=tuple(sorted(collector.assigned)),
        definition_names=tuple(sorted(collector.definition_names)),
        global_names=tuple(sorted(collector.global_names)),
        dynamic_names=tuple(sorted(collector.dynamic_names)),
        parameter_names=(
            tuple(sorted(_parameter_names(node)))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else ()
        ),
        positional_parameter_names=(
            _positional_parameter_names(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else ()
        ),
        has_variadic_parameters=(
            bool(node.args.vararg or node.args.kwarg)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else False
        ),
        nonlocal_names=tuple(sorted(collector.nonlocal_names)),
        attribute_write_names=tuple(sorted(collector.attribute_write_names)),
        indirect_attribute_write_names=tuple(
            sorted(collector.indirect_attribute_write_names)
        ),
    )
    events: list[RelationEvent] = []
    for child in _scope_body(node):
        events.extend(
            _extract_relation_events(
                child,
                parent_qualified_name=qualified_name,
                deadline=deadline,
            )
        )
    return RelationScopeFacts(
        qualified_name=qualified_name,
        kind=kind,
        start_line=start_line,
        bindings=bindings,
        events=tuple(events),
        has_decorators=bool(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.decorator_list
        ),
        has_bases=bool(
            isinstance(node, ast.ClassDef) and (node.bases or node.keywords)
        ),
    )


def _extract_relation_events(
    node: ast.AST,
    *,
    parent_qualified_name: str,
    deadline: float,
    certainty: Literal["definite", "uncertain"] = "definite",
) -> list[RelationEvent]:
    node_type = type(node)
    if node_type in _LEAF_RELATION_NODE_TYPES:
        return []
    if time.monotonic() >= deadline:
        raise _RelationFactsIncomplete
    if node_type not in _RELATION_EVENT_NODE_TYPES:
        # Most nodes only forward their children; skip the per-kind checks below.
        return _extract_child_relation_events(
            node,
            parent_qualified_name=parent_qualified_name,
            deadline=deadline,
            certainty=certainty,
        )
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [RelationImportEvent(_import_fact_from_node(node))]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        events: list[RelationEvent] = []
        for expression in _definition_expressions(node):
            events.extend(
                _extract_relation_events(
                    expression,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty=certainty,
                )
            )
        child_kind: RelationScopeKind = "class" if isinstance(node, ast.ClassDef) else "function"
        child_scope = _extract_scope_facts(
            node,
            qualified_name=f"{parent_qualified_name}.{node.name}",
            kind=child_kind,
            start_line=node.lineno,
            deadline=deadline,
        )
        events.append(RelationScopeEvent(child_scope))
        return events
    if isinstance(node, ast.Assign):
        events: list[RelationEvent] = []
        assignment = _assignment_fact_from_node(node, certainty=certainty)
        if assignment is not None:
            events.append(RelationAssignmentEvent(assignment))
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty=certainty,
                )
            )
        return events
    if isinstance(node, ast.AnnAssign):
        events = []
        assignment = _assignment_fact_from_node(
            node,
            certainty=(
                "uncertain" if isinstance(node.target, ast.Attribute) else certainty
            ),
        )
        if assignment is not None:
            events.append(RelationAssignmentEvent(assignment))
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty=certainty,
                )
            )
        return events
    if isinstance(node, (ast.AugAssign, ast.NamedExpr, ast.Delete)):
        events = []
        assignment = _assignment_fact_from_node(node, certainty="uncertain")
        if assignment is not None:
            events.append(RelationAssignmentEvent(assignment))
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty="uncertain",
                )
            )
        return events
    if isinstance(node, (ast.For, ast.AsyncFor)):
        events = []
        assignment = _assignment_fact_from_targets(
            [node.target],
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            certainty="uncertain",
        )
        if assignment is not None:
            events.append(RelationAssignmentEvent(assignment))
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty="uncertain",
                )
            )
        return events
    if isinstance(node, (ast.With, ast.AsyncWith)):
        events = []
        assignment = _assignment_fact_from_targets(
            [item.optional_vars for item in node.items if item.optional_vars is not None],
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            certainty="uncertain",
        )
        if assignment is not None:
            events.append(RelationAssignmentEvent(assignment))
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty="uncertain",
                )
            )
        return events
    if isinstance(node, ast.ExceptHandler):
        events = []
        if node.name:
            events.append(
                RelationAssignmentEvent(
                    RelationAssignmentFact(
                        target_names=(node.name,),
                        target_attributes=(),
                        constructor_name=None,
                        value_name=None,
                        certainty="uncertain",
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    )
                )
            )
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty="uncertain",
                )
            )
        return events
    if isinstance(node, ast.comprehension):
        events = []
        assignment = _assignment_fact_from_targets(
            [node.target],
            start_line=node.target.lineno,
            end_line=getattr(node.target, "end_lineno", node.target.lineno)
            or node.target.lineno,
            certainty="uncertain",
        )
        if assignment is not None:
            events.append(RelationAssignmentEvent(assignment))
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty="uncertain",
                )
            )
        return events
    if isinstance(node, ast.Call):
        events = [RelationCallEvent(_call_fact_from_node(node))]
        for child in ast.iter_child_nodes(node):
            events.extend(
                _extract_relation_events(
                    child,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty=certainty,
                )
            )
        return events
    if isinstance(node, ast.Return):
        events: list[RelationEvent] = [
            RelationReturnEvent(
                _call_value_from_node(node.value) if node.value is not None else None
            )
        ]
        if node.value is not None:
            events.extend(
                _extract_relation_events(
                    node.value,
                    parent_qualified_name=parent_qualified_name,
                    deadline=deadline,
                    certainty=certainty,
                )
            )
        return events
    return _extract_child_relation_events(
        node,
        parent_qualified_name=parent_qualified_name,
        deadline=deadline,
        certainty=certainty,
    )


def _extract_child_relation_events(
    node: ast.AST,
    *,
    parent_qualified_name: str,
    deadline: float,
    certainty: Literal["definite", "uncertain"],
) -> list[RelationEvent]:
    events: list[RelationEvent] = []
    child_certainty: Literal["definite", "uncertain"] = (
        "uncertain" if isinstance(node, _UNCERTAIN_RELATION_NODES) else certainty
    )
    for child in ast.iter_child_nodes(node):
        if type(child) in _LEAF_RELATION_NODE_TYPES:
            continue
        events.extend(
            _extract_relation_events(
                child,
                parent_qualified_name=parent_qualified_name,
                deadline=deadline,
                certainty=child_certainty,
            )
        )
    return events


def _assignment_fact_from_node(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr | ast.Delete,
    *,
    certainty: Literal["definite", "uncertain"],
) -> RelationAssignmentFact | None:
    if isinstance(node, ast.Assign):
        targets = node.targets
        value = node.value
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
        value = node.value
    elif isinstance(node, ast.NamedExpr):
        targets = [node.target]
        value = node.value
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
        value = None
    else:
        targets = node.targets
        value = None

    return _assignment_fact_from_targets(
        targets,
        constructor_name=(
            value.func.id
            if (
                certainty == "definite"
                and len(targets) == 1
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
            )
            else None
        ),
        value_name=(
            value.id
            if certainty == "definite" and len(targets) == 1 and isinstance(value, ast.Name)
            else None
        ),
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
        certainty=certainty,
    )


def _assignment_fact_from_targets(
    targets: list[ast.expr],
    *,
    start_line: int,
    end_line: int,
    certainty: Literal["definite", "uncertain"],
    constructor_name: str | None = None,
    value_name: str | None = None,
) -> RelationAssignmentFact | None:
    target_names = tuple(sorted(_target_names(targets)))
    target_attributes = tuple(
        sorted(_target_attributes(targets), key=lambda target: target.target_parts)
    )
    if not target_names and not target_attributes:
        return None
    return RelationAssignmentFact(
        target_names=target_names,
        target_attributes=target_attributes,
        constructor_name=constructor_name,
        value_name=value_name,
        certainty=certainty,
        start_line=start_line,
        end_line=end_line,
    )


def _target_names(targets: list[ast.expr]) -> set[str]:
    names: set[str] = set()

    def collect(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            if isinstance(target.ctx, (ast.Store, ast.Del)):
                names.add(target.id)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                collect(element)
            return
        if isinstance(target, ast.Starred):
            collect(target.value)

    for target in targets:
        collect(target)
    return names


def _target_attributes(targets: list[ast.expr]) -> set[RelationCallTargetFact]:
    return {
        RelationCallTargetFact("attribute", None, tuple(parts))
        for target in targets
        if isinstance(target, ast.Attribute)
        and (parts := _attribute_parts(target)) is not None
    }


def _collect_scope_facts(
    path: str,
    scope: RelationScopeFacts,
    result: dict[tuple[str, str, RelationScopeKind, int], RelationScopeFacts],
) -> None:
    result[(path, scope.qualified_name, scope.kind, scope.start_line)] = scope
    for event in scope.events:
        if isinstance(event, RelationScopeEvent):
            _collect_scope_facts(path, event.scope, result)


def _import_fact_from_node(node: ast.Import | ast.ImportFrom) -> RelationImportFact:
    return RelationImportFact(
        kind="import" if isinstance(node, ast.Import) else "from",
        aliases=tuple(RelationImportAlias(alias.name, alias.asname) for alias in node.names),
        module=None if isinstance(node, ast.Import) else node.module,
        level=0 if isinstance(node, ast.Import) else node.level,
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
    )


def _call_fact_from_node(node: ast.Call) -> RelationCallFact:
    target_kind: Literal["name", "attribute", "dynamic"]
    target_name: str | None = None
    target_parts: tuple[str, ...] = ()
    if isinstance(node.func, ast.Name):
        target_kind = "name"
        target_name = node.func.id
    else:
        parts = _attribute_parts(node.func)
        if parts:
            target_kind = "attribute"
            target_parts = tuple(parts)
        else:
            target_kind = "dynamic"
    first_argument = _call_target_from_node(node.args[0]) if node.args else None
    arguments: list[RelationCallArgumentFact] = []
    has_argument_expansion = False
    for position, argument in enumerate(node.args):
        if isinstance(argument, ast.Starred):
            arguments.append(RelationCallArgumentFact(position, None, None))
            has_argument_expansion = True
        else:
            arguments.append(
                RelationCallArgumentFact(
                    position,
                    None,
                    _call_value_from_node(argument),
                )
            )
    for keyword in node.keywords:
        if keyword.arg is None:
            has_argument_expansion = True
        arguments.append(
            RelationCallArgumentFact(
                None,
                keyword.arg,
                _call_value_from_node(keyword.value),
            )
        )
    return RelationCallFact(
        expression=_expression(node)[:500],
        target_kind=target_kind,
        target_name=target_name,
        target_parts=target_parts,
        first_argument=first_argument,
        arguments=tuple(arguments),
        has_argument_expansion=has_argument_expansion,
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
    )


def _call_target_from_node(node: ast.expr) -> RelationCallTargetFact | None:
    if isinstance(node, ast.Name):
        return RelationCallTargetFact("name", node.id, ())
    parts = _attribute_parts(node)
    if parts:
        return RelationCallTargetFact("attribute", None, tuple(parts))
    return None


def _call_value_from_node(node: ast.expr) -> RelationCallTargetFact | None:
    target = _call_target_from_node(node)
    if target is not None:
        return target
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return RelationCallTargetFact("constructor", node.func.id, ())
        parts = _attribute_parts(node.func)
        if parts:
            return RelationCallTargetFact("constructor", None, tuple(parts))
    return None


class _RelationFactResolver:
    def __init__(
        self,
        *,
        path: str,
        module_name: str,
        scope_facts: RelationScopeFacts,
        module_symbol: Symbol,
        maps: _DefinitionMaps,
        scope_facts_by_key: Mapping[
            tuple[str, str, RelationScopeKind, int], RelationScopeFacts
        ],
        budget: _RelationBudget,
        edges: list[CodeEdge],
        unresolved: list[UnresolvedReference],
        constructor_sites: list[_ConstructorCallSite] | None = None,
        activation_call_sites: list[_ActivationCallSite] | None = None,
        unsafe_constructor_classes: set[str] | None = None,
        bypassed_constructor_classes: set[str] | None = None,
        escaped_instance_site_ids: set[str] | None = None,
        binding_mutation_after_escape_unverified_site_ids: set[str] | None = None,
        multisegment_attribute_writes: dict[str, list[Symbol | None]] | None = None,
        constructor_attribute_bindings: Mapping[tuple[str, str], Symbol] | None = None,
        collect_only: bool = False,
    ) -> None:
        self.path = path
        self.module_name = module_name
        self.scope_facts = scope_facts
        self.maps = maps
        self.scope_facts_by_key = scope_facts_by_key
        self.budget = budget
        self.edges = edges
        self.unresolved = unresolved
        self.constructor_sites = constructor_sites
        self.activation_call_sites = activation_call_sites
        self.unsafe_constructor_classes = unsafe_constructor_classes
        self.bypassed_constructor_classes = bypassed_constructor_classes
        self.escaped_instance_site_ids = escaped_instance_site_ids
        self.binding_mutation_after_escape_unverified_site_ids = (
            binding_mutation_after_escape_unverified_site_ids
        )
        self.multisegment_attribute_writes = multisegment_attribute_writes
        self.constructor_attribute_bindings = constructor_attribute_bindings or {}
        self.collect_only = collect_only
        self.scope = self._make_scope(scope_facts, module_symbol, None)
        self._edge_occurrences: dict[tuple[str, str, str, int, int], int] = {}
        self._unresolved_occurrences: dict[tuple[str, str, int, int], int] = {}

    def visit_scope(self) -> None:
        self._visit_events(self.scope_facts.events)

    def _visit_events(self, events: tuple[RelationEvent, ...]) -> None:
        for event in events:
            if self.budget.truncated:
                return
            if time.monotonic() >= self.budget.deadline:
                self.budget._stop("max_seconds exceeded")
                return
            if isinstance(event, RelationImportEvent):
                self._visit_import(event.fact)
            elif isinstance(event, RelationCallEvent):
                self._visit_call(event.fact)
            elif isinstance(event, RelationAssignmentEvent):
                self._visit_assignment(event.fact)
            elif isinstance(event, RelationReturnEvent):
                self._visit_return(event.target)
            else:
                self._visit_nested_scope(event.scope)

    def _visit_assignment(self, fact: RelationAssignmentFact) -> None:
        if fact.value_name is not None:
            value_site_id = self._lookup_instance_site_id(fact.value_name)
            if value_site_id is not None:
                if fact.target_attributes:
                    if self.binding_mutation_after_escape_unverified_site_ids is not None:
                        self.binding_mutation_after_escape_unverified_site_ids.add(value_site_id)
                elif self.escaped_instance_site_ids is not None:
                    self.escaped_instance_site_ids.add(value_site_id)
        if self.escaped_instance_site_ids is not None:
            for target in fact.target_attributes:
                if len(target.target_parts) == 2:
                    escaped_site_id = self._lookup_instance_site_id(target.target_parts[0])
                    if escaped_site_id is not None:
                        self.escaped_instance_site_ids.add(escaped_site_id)
        if self.multisegment_attribute_writes is not None:
            source_class = None
            if fact.certainty == "definite":
                if fact.constructor_name is not None:
                    source_class = self._resolve_constructor(fact.constructor_name)
                elif fact.value_name is not None:
                    source_class, _reason = self._lookup_object(fact.value_name)
            for target in fact.target_attributes:
                if len(target.target_parts) >= 3:
                    self.multisegment_attribute_writes.setdefault(
                        target.target_parts[-1], []
                    ).append(source_class)
        if self.unsafe_constructor_classes is not None and fact.value_name is not None:
            binding, _reason = self._lookup(fact.value_name)
            if (
                binding is not None
                and binding.kind == "symbol"
                and binding.symbol is not None
                and binding.symbol.kind == "class"
            ):
                self.unsafe_constructor_classes.add(binding.symbol.symbol_id)
        for name in fact.target_names:
            self.scope.object_bindings.pop(name, None)
            self.scope.object_instance_site_ids.pop(name, None)
            self.scope.object_invalidated.add(name)
        if (
            fact.certainty == "definite"
            and fact.constructor_name is not None
            and len(fact.target_names) == 1
            and self._is_unique_constructor_assignment(fact)
        ):
            target = self._resolve_constructor(fact.constructor_name)
            if target is not None:
                name = fact.target_names[0]
                self.scope.object_bindings[name] = target
                self.scope.object_instance_site_ids[name] = _constructor_instance_site_id(
                    self.scope.symbol,
                    self.path,
                    target,
                    fact.start_line,
                )
                self.scope.object_invalidated.discard(name)

    def _visit_return(self, target: RelationCallTargetFact | None) -> None:
        if target is None:
            return
        if target.target_kind == "name" and target.target_name is not None:
            if self.escaped_instance_site_ids is not None:
                escaped_site_id = self._lookup_instance_site_id(target.target_name)
                if escaped_site_id is not None:
                    self.escaped_instance_site_ids.add(escaped_site_id)
            if self.unsafe_constructor_classes is None:
                return
            binding, _reason = self._lookup(target.target_name)
            if (
                binding is not None
                and binding.kind == "symbol"
                and binding.symbol is not None
                and binding.symbol.kind == "class"
            ):
                self.unsafe_constructor_classes.add(binding.symbol.symbol_id)

    def _is_unique_constructor_assignment(self, fact: RelationAssignmentFact) -> bool:
        name = fact.target_names[0]
        facts = self.scope.facts
        assignments = [
            event.fact
            for event in facts.events
            if isinstance(event, RelationAssignmentEvent) and name in event.fact.target_names
        ]
        return (
            len(assignments) == 1
            and assignments[0] == fact
            and name not in facts.bindings.definition_names
            and not any(name in _import_binding_names(item) for item in facts.bindings.imports)
            and not _nested_scope_reassigns(facts, name)
        )

    def _visit_import(self, fact: RelationImportFact) -> None:
        for alias in fact.aliases:
            resolution = self._resolve_import(fact, alias)
            if self.collect_only:
                pass
            elif resolution.target is not None:
                self._add_edge(
                    "imports",
                    resolution.target,
                    "explicit_import",
                    fact.start_line,
                    fact.end_line,
                )
            else:
                candidate_name = alias.name.rsplit(".", 1)[-1] if fact.kind == "import" else alias.name
                self._add_unresolved(
                    resolution.expression,
                    resolution.reason,
                    self._candidate_ids(candidate_name),
                    fact.start_line,
                    fact.end_line,
                )
            binding_name = _import_binding_name(fact, alias)
            if (
                binding_name is not None
                and binding_name in self.scope.verified_threadpool_wrappers
                and _is_threadpool_wrapper_import(fact, alias)
            ):
                self.scope.active_threadpool_wrappers.add(binding_name)

    def _visit_call(self, fact: RelationCallFact) -> None:
        target, reason, candidates = self._resolve_call_target(fact)
        bypassed_class = self._constructor_new_bypass_class(fact)
        if self.unsafe_constructor_classes is not None or self.escaped_instance_site_ids is not None:
            for argument in fact.arguments:
                value = argument.target
                if value is None or value.target_kind != "name" or value.target_name is None:
                    continue
                if self.escaped_instance_site_ids is not None:
                    escaped_site_id = self._lookup_instance_site_id(value.target_name)
                    if escaped_site_id is not None and (target is None or target.kind != "class"):
                        self.escaped_instance_site_ids.add(escaped_site_id)
                binding, _reason = self._lookup(value.target_name)
                if (
                    self.unsafe_constructor_classes is not None
                    and
                    binding is not None
                    and binding.kind == "symbol"
                    and binding.symbol is not None
                    and binding.symbol.kind == "class"
                ):
                    if bypassed_class is not None and binding.symbol.symbol_id == bypassed_class.symbol_id:
                        if self.bypassed_constructor_classes is not None:
                            self.bypassed_constructor_classes.add(binding.symbol.symbol_id)
                    else:
                        self.unsafe_constructor_classes.add(binding.symbol.symbol_id)
        if (
            self.constructor_sites is not None
            and target is not None
            and target.kind == "class"
        ):
            self.constructor_sites.append(
                _ConstructorCallSite(
                    site_id=_constructor_instance_site_id(
                        self.scope.symbol,
                        self.path,
                        target,
                        fact.start_line,
                    ),
                    class_symbol=target,
                    caller_symbol=self.scope.symbol,
                    location=SourceLocation(
                        path=self.path,
                        start_line=fact.start_line,
                        end_line=fact.end_line,
                    ),
                    positional_arguments=tuple(
                        self._resolve_constructor_argument(argument.target)
                        for argument in fact.arguments
                        if argument.position is not None
                    ),
                    keyword_arguments=tuple(
                        (argument.keyword, self._resolve_constructor_argument(argument.target))
                        for argument in fact.arguments
                        if argument.keyword is not None
                    ),
                    has_argument_expansion=fact.has_argument_expansion,
                )
            )
        forwarded = (
            fact.first_argument
            if fact.target_kind == "name"
            and fact.target_name is not None
            and self._is_verified_threadpool_wrapper(fact.target_name)
            else None
        )
        activation_target = (
            forwarded
            if forwarded is not None
            else RelationCallTargetFact(fact.target_kind, fact.target_name, fact.target_parts)
        )
        if (
            self.activation_call_sites is not None
            and target is not None
            and target.kind == "function"
            and activation_target.target_kind == "attribute"
            and len(activation_target.target_parts) == 2
        ):
            receiver_site_id = self._lookup_instance_site_id(activation_target.target_parts[0])
            if receiver_site_id is not None:
                self.activation_call_sites.append(
                    _ActivationCallSite(
                        caller_symbol=self.scope.symbol,
                        target_symbol=target,
                        receiver_site_id=receiver_site_id,
                        location=SourceLocation(
                            path=self.path,
                            start_line=fact.start_line,
                            end_line=fact.end_line,
                        ),
                    )
                )
        if self.collect_only:
            return
        if target is not None:
            self._add_edge("calls", target, "ast", fact.start_line, fact.end_line)
        else:
            self._add_unresolved(
                fact.expression,
                reason,
                candidates,
                fact.start_line,
                fact.end_line,
            )

    def _resolve_constructor_argument(
        self,
        argument: RelationCallTargetFact | None,
    ) -> Symbol | None:
        if argument is None:
            return None
        if argument.target_kind == "name" and argument.target_name is not None:
            target, _reason = self._lookup_object(argument.target_name)
            return target
        target_kind = (
            "name" if argument.target_kind == "constructor" and argument.target_name else
            "attribute" if argument.target_kind == "constructor" else
            argument.target_kind
        )
        target_name = argument.target_name if target_kind == "name" else None
        target_parts = argument.target_parts
        if argument.target_kind == "constructor" and target_kind == "name":
            target_parts = ()
        target, _reason, _candidates = self._resolve_call_target_parts(
            target_kind,
            target_name,
            target_parts,
        )
        return target if target is not None and target.kind == "class" else None

    def _visit_nested_scope(self, scope_facts: RelationScopeFacts) -> None:
        parent_scope = self.scope
        symbol = self._symbol_for_scope(scope_facts)
        if symbol is None:
            self._visit_events(scope_facts.events)
            return
        lexical_parent = (
            parent_scope.parent
            if scope_facts.kind == "function" and parent_scope.kind == "class"
            else parent_scope
        )
        self.scope = self._make_scope(scope_facts, symbol, lexical_parent)
        self._visit_events(scope_facts.events)
        self.scope = parent_scope

    def _make_scope(
        self,
        scope_facts: RelationScopeFacts,
        symbol: Symbol,
        parent: _Scope | None,
    ) -> _Scope:
        binding_facts = scope_facts.bindings
        shadowed = set(binding_facts.assigned) - set(binding_facts.global_names)
        if scope_facts.kind == "function":
            shadowed.update(binding_facts.parameter_names)
        bindings: dict[str, _Binding] = {}
        for import_fact in binding_facts.imports:
            for alias in import_fact.aliases:
                resolution = self._resolve_import(import_fact, alias)
                if (
                    resolution.binding_name
                    and resolution.binding
                    and resolution.binding_name not in shadowed
                ):
                    bindings[resolution.binding_name] = resolution.binding
        for name in binding_facts.definition_names:
            if name in shadowed:
                continue
            local_symbol = self.maps.one_by_name(f"{symbol.qualified_name}.{name}")
            if local_symbol is not None:
                bindings[name] = _Binding(kind="symbol", symbol=local_symbol)
        verified_threadpool_wrappers: set[str] = set()
        blocked_threadpool_names = {
            *binding_facts.assigned,
            *binding_facts.definition_names,
            *binding_facts.global_names,
            *binding_facts.dynamic_names,
            *binding_facts.parameter_names,
        }
        imports_by_name: dict[str, list[tuple[RelationImportFact, RelationImportAlias]]] = {}
        for import_fact in binding_facts.imports:
            for alias in import_fact.aliases:
                name = _import_binding_name(import_fact, alias)
                if name is not None:
                    imports_by_name.setdefault(name, []).append((import_fact, alias))
        for name, imports in imports_by_name.items():
            if name in blocked_threadpool_names:
                continue
            if (
                len(imports) == 1
                and _is_threadpool_wrapper_import(*imports[0])
                and imports[0][0].module not in self.maps.modules_by_name
                and not _nested_scope_reassigns(scope_facts, name)
            ):
                verified_threadpool_wrappers.add(name)
            else:
                blocked_threadpool_names.add(name)
        return _Scope(
            facts=scope_facts,
            kind=scope_facts.kind,
            symbol=symbol,
            parent=parent,
            bindings=bindings,
            shadowed=shadowed,
            dynamic_names=set(binding_facts.dynamic_names),
            attribute_write_names=set(binding_facts.attribute_write_names),
            object_bindings={},
            object_instance_site_ids={},
            object_invalidated={
                *shadowed,
                *binding_facts.parameter_names,
                *binding_facts.definition_names,
                *bindings,
            },
            verified_threadpool_wrappers=verified_threadpool_wrappers,
            active_threadpool_wrappers=set(),
            blocked_threadpool_names=blocked_threadpool_names,
        )

    def _symbol_for_scope(self, scope_facts: RelationScopeFacts) -> Symbol | None:
        return self.maps.by_key.get(
            (self.path, scope_facts.qualified_name, scope_facts.kind, scope_facts.start_line)
        )

    def _resolve_import(
        self,
        fact: RelationImportFact,
        alias: RelationImportAlias,
    ) -> _ImportResolution:
        if fact.kind == "import":
            expression = alias.name
            target = self.maps.modules_by_name.get(alias.name)
            if target is None:
                return _ImportResolution(None, None, None, expression, "external_import")
            binding_name = alias.asname or alias.name.split(".", 1)[0]
            bound_module = alias.name if alias.asname else alias.name.split(".", 1)[0]
            if bound_module not in self.maps.modules_by_name:
                bound_module = alias.name
            return _ImportResolution(
                binding_name,
                _Binding(kind="module", symbol=target, module_name=bound_module),
                target,
                expression,
                "",
            )

        expression = _import_expression_fact(fact, alias)
        if alias.name == "*":
            return _ImportResolution(None, None, None, expression, "star_import_not_resolved")
        base = _from_import_base(self.module_name, self.path, fact.level, fact.module)
        if not base:
            return _ImportResolution(None, None, None, expression, "invalid_relative_import")
        target_name = f"{base}.{alias.name}"
        target = self.maps.one_by_name(target_name) or self.maps.modules_by_name.get(target_name)
        if target is None:
            reason = "external_import" if fact.level == 0 else "missing_relative_import"
            return _ImportResolution(None, None, None, expression, reason)
        binding_name = alias.asname or alias.name
        if target.kind == "module":
            binding = _Binding(kind="module", symbol=target, module_name=target.qualified_name)
        else:
            binding = _Binding(kind="symbol", symbol=target)
        return _ImportResolution(binding_name, binding, target, expression, "")

    def _resolve_call_target(
        self,
        fact: RelationCallFact,
    ) -> tuple[Symbol | None, str, list[str]]:
        if (
            fact.target_kind == "name"
            and fact.target_name is not None
            and self._is_verified_threadpool_wrapper(fact.target_name)
        ):
            forwarded = fact.first_argument
            if forwarded is None or forwarded.target_kind == "constructor":
                return None, "dynamic_call_target", []
            target, reason, candidates = self._resolve_call_target_parts(
                forwarded.target_kind,
                forwarded.target_name,
                forwarded.target_parts,
            )
            if target is None:
                return None, reason, candidates
            if target.kind not in {"function", "test"}:
                return None, "unsupported_wrapper_callable_target", [target.symbol_id]
            return target, "", []
        return self._resolve_call_target_parts(
            fact.target_kind,
            fact.target_name,
            fact.target_parts,
        )

    def _resolve_call_target_parts(
        self,
        target_kind: Literal["name", "attribute", "dynamic"],
        target_name: str | None,
        target_parts: tuple[str, ...],
    ) -> tuple[Symbol | None, str, list[str]]:
        target, reason, candidates = self._resolve_call_target_parts_statically(
            target_kind,
            target_name,
            target_parts,
        )
        if (
            target is None
            and target_kind == "attribute"
            and len(target_parts) >= 3
            and target_parts[-2] == "hook"
        ):
            # pluggy dispatches ``pm.hook.<name>(...)`` to every registered
            # implementation at runtime. Keep it unresolved and expose
            # same-name definitions only as possible targets.
            return None, "pluggy_hook_dispatch", self._candidate_ids(target_parts[-1])
        return target, reason, candidates

    def _resolve_call_target_parts_statically(
        self,
        target_kind: Literal["name", "attribute", "dynamic"],
        target_name: str | None,
        target_parts: tuple[str, ...],
    ) -> tuple[Symbol | None, str, list[str]]:
        if target_kind == "name" and target_name is not None:
            binding, lookup_reason = self._lookup(target_name)
            if binding is not None and binding.symbol is not None and binding.kind == "symbol":
                return binding.symbol, "", []
            if lookup_reason:
                return None, lookup_reason, []
            candidates = self._candidate_ids(target_name)
            if len(candidates) > 1:
                return None, "ambiguous_name", candidates
            return None, "call_target_not_in_snapshot", candidates
        if target_kind == "attribute" and target_parts:
            parts = target_parts
            if len(parts) == 3:
                owner = self._instance_receiver_class(parts[0])
                if owner is None:
                    return None, "object_method_not_resolved", self._candidate_ids(parts[-1])
                attribute_class = self.constructor_attribute_bindings.get(
                    (owner.symbol_id, parts[1])
                )
                if attribute_class is None:
                    return None, "object_method_not_resolved", self._candidate_ids(parts[-1])
                target = self._direct_method(attribute_class, parts[2])
                if target is not None:
                    return target, "", []
                return None, "object_method_not_resolved", self._candidate_ids(parts[-1])
            if len(parts) == 2:
                object_target, object_reason = self._lookup_object(parts[0])
                if object_target is not None:
                    target = self._direct_method(object_target, parts[1])
                    if target is not None:
                        return target, "", []
                    return None, "object_method_not_resolved", self._candidate_ids(parts[-1])
                if object_reason:
                    return None, object_reason, []
            binding, lookup_reason = self._lookup(parts[0])
            if binding is not None and binding.kind == "module" and binding.module_name:
                target_name = ".".join((binding.module_name, *parts[1:]))
                target = self.maps.one_by_name(target_name)
                if target is not None:
                    return target, "", []
                return None, "module_attribute_not_in_snapshot", self._candidate_ids(parts[-1])
            return None, lookup_reason or "object_method_not_resolved", []
        return None, "dynamic_call_target", []

    def _instance_receiver_class(self, receiver_name: str) -> Symbol | None:
        scope = self.scope
        if (
            scope.kind != "function"
            or not scope.facts.bindings.positional_parameter_names
            or scope.facts.bindings.positional_parameter_names[0] != receiver_name
            or receiver_name in scope.facts.bindings.assigned
            or receiver_name in scope.facts.bindings.dynamic_names
            or receiver_name in scope.facts.bindings.global_names
            or receiver_name in scope.facts.bindings.nonlocal_names
            or _nested_scope_reassigns(scope.facts, receiver_name)
        ):
            return None
        owner_name = scope.symbol.qualified_name.rsplit(".", 1)[0]
        owners = [
            symbol
            for symbol in self.maps.by_qualified_name.get(owner_name, [])
            if symbol.path == self.path and symbol.kind == "class"
        ]
        if len(owners) != 1:
            return None
        owner = owners[0]
        class_scope = self.scope_facts_by_key.get(
            (owner.path, owner.qualified_name, "class", owner.start_line)
        )
        if (
            class_scope is None
            or class_scope.has_bases
            or class_scope.has_decorators
            or scope.facts.has_decorators
            or "__new__" in class_scope.bindings.definition_names
            or "__new__" in class_scope.bindings.assigned
        ):
            return None
        return owner

    def _is_verified_threadpool_wrapper(self, name: str) -> bool:
        scope: _Scope | None = self.scope
        while scope is not None:
            if name in scope.blocked_threadpool_names:
                return False
            if name in scope.active_threadpool_wrappers:
                return True
            scope = scope.parent
        return False

    def _resolve_constructor(self, name: str) -> Symbol | None:
        binding, _reason = self._lookup(name)
        if (
            binding is not None
            and binding.kind == "symbol"
            and binding.symbol is not None
            and binding.symbol.kind == "class"
        ):
            return binding.symbol
        return None

    def _direct_method(self, class_symbol: Symbol, method_name: str) -> Symbol | None:
        candidates = [
            symbol
            for symbol in self.maps.by_qualified_name.get(
                f"{class_symbol.qualified_name}.{method_name}", []
            )
            if symbol.path == class_symbol.path and symbol.kind == "function"
        ]
        if len(candidates) != 1:
            return None
        method = candidates[0]
        class_scope = self.scope_facts_by_key.get(
            (class_symbol.path, class_symbol.qualified_name, "class", class_symbol.start_line)
        )
        method_scope = self.scope_facts_by_key.get(
            (method.path, method.qualified_name, "function", method.start_line)
        )
        if class_scope is None or method_scope is None:
            return None
        if class_scope.has_bases or class_scope.has_decorators or method_scope.has_decorators:
            return None
        if method_name in class_scope.bindings.assigned:
            return None
        if self._scope_chain_has_attribute_write(method_name):
            return None
        if _scope_has_attribute_write(class_scope, method_name):
            return None
        return method

    def _scope_chain_has_attribute_write(self, attribute_name: str) -> bool:
        scope: _Scope | None = self.scope
        while scope is not None:
            if (
                attribute_name in scope.attribute_write_names
                or attribute_name in scope.facts.bindings.indirect_attribute_write_names
                or "*" in scope.facts.bindings.indirect_attribute_write_names
            ):
                return True
            scope = scope.parent
        return False

    def _lookup_object(self, name: str) -> tuple[Symbol | None, str | None]:
        scope: _Scope | None = self.scope
        while scope is not None:
            if name in scope.object_invalidated:
                if name in scope.dynamic_names:
                    return None, "dynamic_call_target"
                return None, "name_rebound_in_scope"
            target = scope.object_bindings.get(name)
            if target is not None:
                return target, None
            scope = scope.parent
        return None, None

    def _lookup_instance_site_id(self, name: str) -> str | None:
        scope: _Scope | None = self.scope
        while scope is not None:
            if name in scope.object_invalidated:
                return None
            site_id = scope.object_instance_site_ids.get(name)
            if site_id is not None:
                return site_id
            scope = scope.parent
        return None

    def _constructor_new_bypass_class(self, fact: RelationCallFact) -> Symbol | None:
        if (
            fact.target_kind != "attribute"
            or len(fact.target_parts) != 2
            or fact.target_parts[1] != "__new__"
        ):
            return None
        owner = self._resolve_constructor(fact.target_parts[0])
        first_argument = next(
            (argument.target for argument in fact.arguments if argument.position == 0),
            None,
        )
        if owner is None or first_argument is None or first_argument.target_kind != "name":
            return None
        argument_class = self._resolve_constructor(first_argument.target_name or "")
        return owner if argument_class is not None and argument_class.symbol_id == owner.symbol_id else None

    def _lookup(self, name: str) -> tuple[_Binding | None, str | None]:
        scope: _Scope | None = self.scope
        while scope is not None:
            if name in scope.shadowed:
                if name in scope.dynamic_names:
                    return None, "dynamic_call_target"
                return None, "name_rebound_in_scope"
            binding = scope.bindings.get(name)
            if binding is not None:
                return binding, None
            scope = scope.parent
        return None, None

    def _candidate_ids(self, name: str) -> list[str]:
        return [symbol.symbol_id for symbol in self.maps.candidates(name)]

    def _add_edge(
        self,
        kind: RelationKind,
        target: Symbol,
        extraction_method: Literal["ast", "explicit_import", "fixture"],
        start_line: int,
        end_line: int,
    ) -> None:
        if not self.budget.reserve("edges"):
            return
        key = (kind, self.scope.symbol.symbol_id, target.symbol_id, start_line, end_line)
        occurrence = self._edge_occurrences.get(key, 0)
        self._edge_occurrences[key] = occurrence + 1
        identity = "\0".join(
            [
                self.scope.symbol.index_id,
                kind,
                self.scope.symbol.symbol_id,
                target.symbol_id,
                self.path,
                str(start_line),
                str(end_line),
                str(occurrence),
            ]
        )
        edge_id = f"edge-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
        self.edges.append(
            CodeEdge(
                edge_id=edge_id,
                index_id=self.scope.symbol.index_id,
                source_id=self.scope.symbol.symbol_id,
                target_id=target.symbol_id,
                kind=kind,
                extraction_method=extraction_method,
                source_location=SourceLocation(
                    path=self.path,
                    start_line=start_line,
                    end_line=end_line,
                ),
            )
        )

    def _add_unresolved(
        self,
        expression: str,
        reason: str,
        candidate_ids: list[str],
        start_line: int,
        end_line: int,
    ) -> None:
        if not self.budget.reserve("unresolved"):
            return
        expression = expression[:500] or "unknown"
        key = (expression, reason, start_line, end_line)
        occurrence = self._unresolved_occurrences.get(key, 0)
        self._unresolved_occurrences[key] = occurrence + 1
        identity = "\0".join(
            [
                self.scope.symbol.index_id,
                self.scope.symbol.symbol_id,
                self.path,
                expression,
                reason,
                str(start_line),
                str(end_line),
                str(occurrence),
            ]
        )
        unresolved_id = f"unresolved-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
        self.unresolved.append(
            UnresolvedReference(
                unresolved_id=unresolved_id,
                index_id=self.scope.symbol.index_id,
                source_symbol_id=self.scope.symbol.symbol_id,
                path=self.path,
                expression=expression,
                reason=reason[:1_000],
                possible_target_ids=sorted(set(candidate_ids)),
                start_line=start_line,
                end_line=end_line,
            )
        )


def _import_binding_name(
    fact: RelationImportFact,
    alias: RelationImportAlias,
) -> str | None:
    if fact.kind == "import":
        return alias.asname or alias.name.split(".", 1)[0]
    if alias.name == "*":
        return None
    return alias.asname or alias.name


def _import_binding_names(fact: RelationImportFact) -> set[str]:
    return {
        name
        for alias in fact.aliases
        if (name := _import_binding_name(fact, alias)) is not None
    }


def _is_threadpool_wrapper_import(
    fact: RelationImportFact,
    alias: RelationImportAlias,
) -> bool:
    return (
        fact.kind == "from"
        and fact.level == 0
        and fact.module in {"fastapi.concurrency", "starlette.concurrency"}
        and alias.name == "run_in_threadpool"
    )


def _nested_scope_reassigns(scope: RelationScopeFacts, name: str) -> bool:
    for event in scope.events:
        if not isinstance(event, RelationScopeEvent):
            continue
        nested = event.scope
        written_names = {
            *nested.bindings.assigned,
            *nested.bindings.definition_names,
            *(
                imported
                for import_fact in nested.bindings.imports
                for imported in _import_binding_names(import_fact)
            ),
        }
        if (
            name in written_names
            and name in (*nested.bindings.global_names, *nested.bindings.nonlocal_names)
        ) or _nested_scope_reassigns(nested, name):
            return True
    return False


def _scope_has_attribute_write(scope: RelationScopeFacts, attribute_name: str) -> bool:
    if (
        attribute_name in scope.bindings.attribute_write_names
        or attribute_name in scope.bindings.indirect_attribute_write_names
        or "*" in scope.bindings.indirect_attribute_write_names
    ):
        return True
    return any(
        isinstance(event, RelationScopeEvent)
        and _scope_has_attribute_write(event.scope, attribute_name)
        for event in scope.events
    )


def _resolve_constructor_attribute_bindings(
    facts_by_path: Mapping[str, RelationFileFacts],
    scope_facts_by_key: Mapping[
        tuple[str, str, RelationScopeKind, int], RelationScopeFacts
    ],
    maps: _DefinitionMaps,
    constructor_sites: list[_ConstructorCallSite],
    unsafe_constructor_classes: set[str],
    bypassed_constructor_classes: set[str],
    multisegment_attribute_writes: Mapping[str, list[Symbol | None]],
    budget: _RelationBudget,
) -> tuple[
    dict[tuple[str, str], Symbol],
    dict[str, list[RelationAssignmentFact]],
    set[str],
]:
    # ponytail: field-name-wide write guards can reject unrelated objects;
    # add receiver identity analysis only when those false negatives matter.
    writes: dict[str, list[RelationAssignmentFact]] = {}
    indirect_writes: set[str] = set()
    attribute_protocol_methods = {
        "__getattribute__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
    }

    def collect(scope: RelationScopeFacts) -> None:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            return
        indirect_writes.update(scope.bindings.indirect_attribute_write_names)
        for event in scope.events:
            if time.monotonic() >= budget.deadline:
                budget._stop("max_seconds exceeded")
                return
            if isinstance(event, RelationAssignmentEvent):
                for target in event.fact.target_attributes:
                    if len(target.target_parts) == 2:
                        writes.setdefault(target.target_parts[-1], []).append(event.fact)
            elif isinstance(event, RelationScopeEvent):
                collect(event.scope)
                if budget.truncated:
                    return

    for facts in facts_by_path.values():
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            return {}, {}, set()
        if facts.scope is not None:
            collect(facts.scope)
        if budget.truncated:
            return {}, {}, set()

    sites_by_class: dict[str, list[_ConstructorCallSite]] = {}
    for site in constructor_sites:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            return {}, {}, set()
        sites_by_class.setdefault(site.class_symbol.symbol_id, []).append(site)

    result: dict[tuple[str, str], Symbol] = {}
    class_symbols: list[Symbol] = []
    for symbols in maps.by_qualified_name.values():
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            return {}, {}, set()
        class_symbols.extend(symbol for symbol in symbols if symbol.kind == "class")
    for class_symbol in class_symbols:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            return {}, {}, set()
        if (
            class_symbol.symbol_id in unsafe_constructor_classes
            or class_symbol.symbol_id in bypassed_constructor_classes
        ):
            continue
        class_scope = scope_facts_by_key.get(
            (class_symbol.path, class_symbol.qualified_name, "class", class_symbol.start_line)
        )
        init_symbols = [
            symbol
            for symbol in maps.by_qualified_name.get(
                f"{class_symbol.qualified_name}.__init__", []
            )
            if symbol.path == class_symbol.path and symbol.kind == "function"
        ]
        if (
            class_scope is None
            or class_scope.has_bases
            or class_scope.has_decorators
            or "__new__" in class_scope.bindings.definition_names
            or "__new__" in class_scope.bindings.assigned
            or attribute_protocol_methods.intersection(
                (*class_scope.bindings.definition_names, *class_scope.bindings.assigned)
            )
            or len(init_symbols) != 1
        ):
            continue
        init_symbol = init_symbols[0]
        init_scope = scope_facts_by_key.get(
            (init_symbol.path, init_symbol.qualified_name, "function", init_symbol.start_line)
        )
        if init_scope is None or init_scope.has_decorators:
            continue
        positional = init_scope.bindings.positional_parameter_names
        if not positional or init_scope.bindings.has_variadic_parameters:
            continue
        receiver = positional[0]
        if receiver in init_scope.bindings.assigned:
            continue
        field_assignments = [
            event.fact
            for event in init_scope.events
            if isinstance(event, RelationAssignmentEvent)
            and event.fact.certainty == "definite"
            and not event.fact.target_names
            and len(event.fact.target_attributes) == 1
            and event.fact.target_attributes[0].target_parts
            == (receiver, event.fact.target_attributes[0].target_parts[-1])
            and event.fact.value_name is not None
            and event.fact.value_name in init_scope.bindings.parameter_names
            and event.fact.value_name != receiver
        ]
        for assignment in field_assignments:
            if time.monotonic() >= budget.deadline:
                budget._stop("max_seconds exceeded")
                return {}, {}, set()
            field = assignment.target_attributes[0].target_parts[-1]
            source_parameter = assignment.value_name
            if source_parameter is None:
                continue
            if (
                field in class_scope.bindings.assigned
                or field in class_scope.bindings.definition_names
                or field in indirect_writes
                or "*" in indirect_writes
                or source_parameter in init_scope.bindings.assigned
                or _nested_scope_reassigns(init_scope, source_parameter)
            ):
                continue
            matching_writes = writes.get(field, [])
            if len(matching_writes) != 1 or matching_writes[0] is not assignment:
                continue
            sites = sites_by_class.get(class_symbol.symbol_id, [])
            if not sites:
                continue
            parameter_position = (
                positional.index(source_parameter) - 1
                if source_parameter in positional
                else None
            )
            actual_classes: list[Symbol] = []
            for site in sites:
                if time.monotonic() >= budget.deadline:
                    budget._stop("max_seconds exceeded")
                    return {}, {}, set()
                if site.has_argument_expansion:
                    actual_classes = []
                    break
                positional_present = (
                    parameter_position is not None
                    and parameter_position < len(site.positional_arguments)
                )
                positional_value = (
                    site.positional_arguments[parameter_position]
                    if positional_present and parameter_position is not None
                    else None
                )
                keyword_values = [
                    value
                    for name, value in site.keyword_arguments
                    if name == source_parameter
                ]
                if len(keyword_values) > 1 or (positional_present and keyword_values):
                    actual_classes = []
                    break
                value = keyword_values[0] if keyword_values else positional_value
                if value is None or value.kind != "class":
                    actual_classes = []
                    break
                value_scope = scope_facts_by_key.get(
                    (value.path, value.qualified_name, "class", value.start_line)
                )
                if (
                    value_scope is None
                    or value_scope.has_bases
                    or value_scope.has_decorators
                    or "__new__" in value_scope.bindings.definition_names
                    or "__new__" in value_scope.bindings.assigned
                    or attribute_protocol_methods.intersection(
                        (*value_scope.bindings.definition_names, *value_scope.bindings.assigned)
                    )
                ):
                    actual_classes = []
                    break
                actual_classes.append(value)
            if actual_classes and len({symbol.symbol_id for symbol in actual_classes}) == 1:
                actual_class = actual_classes[0]
                external_writes = multisegment_attribute_writes.get(field, [])
                if any(
                    written_class is None
                    or written_class.symbol_id != actual_class.symbol_id
                    for written_class in external_writes
                ):
                    continue
                result[(class_symbol.symbol_id, field)] = actual_class
    return result, writes, indirect_writes


def _resolve_contextual_calls(
    index_id: str,
    constructor_sites: list[_ConstructorCallSite],
    activation_sites: list[_ActivationCallSite],
    unsafe_constructor_classes: set[str],
    escaped_instance_site_ids: set[str],
    binding_mutation_after_escape_unverified_site_ids: set[str],
    scope_facts_by_key: Mapping[
        tuple[str, str, RelationScopeKind, int], RelationScopeFacts
    ],
    maps: _DefinitionMaps,
    writes: Mapping[str, list[RelationAssignmentFact]],
    indirect_writes: set[str],
    multisegment_attribute_writes: Mapping[str, list[Symbol | None]],
    edges: list[CodeEdge],
    budget: _RelationBudget,
) -> list[ContextualCall]:
    sites_by_id: dict[str, list[_ConstructorCallSite]] = {}
    for site in constructor_sites:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            return []
        sites_by_id.setdefault(site.site_id, []).append(site)

    activation_counts: dict[tuple[str, str, str, int, int], int] = {}
    for activation in activation_sites:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded while resolving contextual calls")
            return []
        key = (
            activation.caller_symbol.symbol_id,
            activation.target_symbol.symbol_id,
            activation.location.path,
            activation.location.start_line,
            activation.location.end_line,
        )
        activation_counts[key] = activation_counts.get(key, 0) + 1
    edges_by_activation: dict[tuple[str, str, str, int, int], list[CodeEdge]] = {}
    for edge in edges:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded while resolving contextual calls")
            return []
        if edge.kind != "calls":
            continue
        key = (
            edge.source_id,
            edge.target_id,
            edge.source_location.path,
            edge.source_location.start_line,
            edge.source_location.end_line,
        )
        edges_by_activation.setdefault(key, []).append(edge)

    protocols = {"__getattribute__", "__getattr__", "__setattr__", "__delattr__"}
    contextual: list[ContextualCall] = []
    emitted: set[tuple[str, str, str, str, int, int]] = set()

    def method_transfers_instance(scope: RelationScopeFacts, receiver: str, field: str) -> bool | None:
        possible_aliases: set[str] = set()
        for item in scope.events:
            if time.monotonic() >= budget.deadline:
                return None
            if isinstance(item, RelationAssignmentEvent):
                possible_aliases.update(item.fact.target_names)
        object_names = possible_aliases | {receiver}
        for item in scope.events:
            if time.monotonic() >= budget.deadline:
                return None
            if isinstance(item, RelationAssignmentEvent):
                if any(
                    target.target_parts
                    and target.target_parts[0] in object_names
                    for target in item.fact.target_attributes
                ):
                    return True
            elif isinstance(item, RelationScopeEvent):
                # A nested function may close over either object and run later;
                # this one-hop proof does not follow closure mutation.
                return True
            elif isinstance(item, RelationReturnEvent):
                target = item.target
                if target is not None and (
                    (target.target_kind == "name" and target.target_name in object_names)
                    or (
                        target.target_kind == "attribute"
                        and target.target_parts[:2] == (receiver, field)
                    )
                ):
                    return True
            elif isinstance(item, RelationCallEvent):
                call = item.fact
                if any(
                    argument.target is not None
                    and (
                        (argument.target.target_kind == "name" and argument.target.target_name in object_names)
                        or (
                            argument.target.target_kind == "attribute"
                            and argument.target.target_parts[:2] == (receiver, field)
                        )
                    )
                    for argument in call.arguments
                ):
                    return True
                if (
                    call.target_kind == "attribute"
                    and len(call.target_parts) >= 2
                    and call.target_parts[0] in possible_aliases
                ):
                    return True
        return False

    for activation in activation_sites:
        if time.monotonic() >= budget.deadline:
            budget._stop("max_seconds exceeded")
            break
        site_candidates = sites_by_id.get(activation.receiver_site_id, [])
        if (
            len(site_candidates) != 1
            or activation.receiver_site_id in escaped_instance_site_ids
        ):
            continue
        constructor = site_candidates[0]
        owner_name = activation.target_symbol.qualified_name.rsplit(".", 1)[0]
        owner = maps.one_by_name(owner_name)
        if (
            owner is None
            or owner.kind != "class"
            or owner.path != activation.target_symbol.path
            or owner.symbol_id != constructor.class_symbol.symbol_id
            or owner.symbol_id in unsafe_constructor_classes
        ):
            continue
        activation_key = (
            activation.caller_symbol.symbol_id,
            activation.target_symbol.symbol_id,
            activation.location.path,
            activation.location.start_line,
            activation.location.end_line,
        )
        activation_edges = edges_by_activation.get(activation_key, [])
        if activation_counts.get(activation_key) != 1 or len(activation_edges) != 1:
            continue
        class_scope = scope_facts_by_key.get(
            (owner.path, owner.qualified_name, "class", owner.start_line)
        )
        init_symbols: list[Symbol] = []
        for symbol in maps.by_qualified_name.get(f"{owner.qualified_name}.__init__", []):
            if time.monotonic() >= budget.deadline:
                budget._stop("max_seconds exceeded while resolving contextual calls")
                return contextual
            if symbol.path == owner.path and symbol.kind == "function":
                init_symbols.append(symbol)
        method_scope = scope_facts_by_key.get(
            (
                activation.target_symbol.path,
                activation.target_symbol.qualified_name,
                "function",
                activation.target_symbol.start_line,
            )
        )
        if (
            class_scope is None
            or class_scope.has_bases
            or class_scope.has_decorators
            or "__new__" in class_scope.bindings.definition_names
            or "__new__" in class_scope.bindings.assigned
            or protocols.intersection(
                (*class_scope.bindings.definition_names, *class_scope.bindings.assigned)
            )
            or len(init_symbols) != 1
            or method_scope is None
            or method_scope.has_decorators
        ):
            continue
        init_symbol = init_symbols[0]
        method_parameters = method_scope.bindings.positional_parameter_names
        if (
            method_scope.kind != "function"
            or not method_parameters
            or method_scope.bindings.has_variadic_parameters
        ):
            continue
        receiver = method_parameters[0]
        if (
            receiver in method_scope.bindings.assigned
            or receiver in method_scope.bindings.dynamic_names
            or receiver in method_scope.bindings.global_names
            or receiver in method_scope.bindings.nonlocal_names
            or _nested_scope_reassigns(method_scope, receiver)
            or method_scope.bindings.indirect_attribute_write_names
        ):
            continue
        init_scope = scope_facts_by_key.get(
            (init_symbols[0].path, init_symbols[0].qualified_name, "function", init_symbols[0].start_line)
        )
        if (
            init_scope is None
            or init_scope.has_decorators
            or not init_scope.bindings.positional_parameter_names
            or init_scope.bindings.has_variadic_parameters
        ):
            continue
        init_receiver = init_scope.bindings.positional_parameter_names[0]
        if init_receiver in init_scope.bindings.assigned:
            continue
        field_assignments: list[RelationAssignmentFact] = []
        for event in init_scope.events:
            if time.monotonic() >= budget.deadline:
                budget._stop("max_seconds exceeded while resolving contextual calls")
                return contextual
            if not isinstance(event, RelationAssignmentEvent):
                continue
            assignment = event.fact
            if (
                assignment.certainty == "definite"
                and not assignment.target_names
                and len(assignment.target_attributes) == 1
                and len(assignment.target_attributes[0].target_parts) == 2
                and assignment.target_attributes[0].target_parts[0] == init_receiver
                and assignment.value_name in init_scope.bindings.parameter_names
                and assignment.value_name != init_receiver
            ):
                field_assignments.append(assignment)
        for assignment in field_assignments:
            if time.monotonic() >= budget.deadline:
                budget._stop("max_seconds exceeded")
                return contextual
            field_name = assignment.target_attributes[0].target_parts[-1]
            source_parameter = assignment.value_name
            assert source_parameter is not None
            if (
                field_name in class_scope.bindings.assigned
                or field_name in class_scope.bindings.definition_names
                or field_name in indirect_writes
                or "*" in indirect_writes
                or source_parameter in init_scope.bindings.assigned
                or _nested_scope_reassigns(init_scope, source_parameter)
                or writes.get(field_name) != [assignment]
            ):
                continue
            parameter_position = (
                init_scope.bindings.positional_parameter_names.index(source_parameter) - 1
                if source_parameter in init_scope.bindings.positional_parameter_names
                else None
            )
            positional_present = (
                parameter_position is not None
                and parameter_position < len(constructor.positional_arguments)
            )
            positional_value = (
                constructor.positional_arguments[parameter_position]
                if positional_present and parameter_position is not None
                else None
            )
            keyword_values = [
                value
                for name, value in constructor.keyword_arguments
                if name == source_parameter
            ]
            if (
                constructor.has_argument_expansion
                or len(keyword_values) > 1
                or (positional_present and keyword_values)
            ):
                continue
            dependency = keyword_values[0] if keyword_values else positional_value
            if dependency is None or dependency.kind != "class":
                continue
            dependency_scope = scope_facts_by_key.get(
                (
                    dependency.path,
                    dependency.qualified_name,
                    "class",
                    dependency.start_line,
                )
            )
            if (
                dependency_scope is None
                or dependency_scope.has_bases
                or dependency_scope.has_decorators
                or "__new__" in dependency_scope.bindings.definition_names
                or "__new__" in dependency_scope.bindings.assigned
                or protocols.intersection(
                    (*dependency_scope.bindings.definition_names, *dependency_scope.bindings.assigned)
                )
            ):
                continue
            dependency_has_escaped_instance = False
            for dependency_site in constructor_sites:
                if time.monotonic() >= budget.deadline:
                    budget._stop("max_seconds exceeded while resolving contextual calls")
                    return contextual
                if (
                    dependency_site.class_symbol.symbol_id == dependency.symbol_id
                    and dependency_site.site_id in escaped_instance_site_ids
                ):
                    dependency_has_escaped_instance = True
                    break
            external_writes = multisegment_attribute_writes.get(field_name, [])
            if dependency_has_escaped_instance or any(
                written_class is None or written_class.symbol_id != dependency.symbol_id
                for written_class in external_writes
            ):
                continue
            transfers = method_transfers_instance(method_scope, receiver, field_name)
            if transfers is None:
                budget._stop("max_seconds exceeded while resolving contextual calls")
                return contextual
            if transfers:
                continue
            for event in method_scope.events:
                if time.monotonic() >= budget.deadline:
                    budget._stop("max_seconds exceeded")
                    return contextual
                if not isinstance(event, RelationCallEvent):
                    continue
                call = event.fact
                if (
                    call.target_kind != "attribute"
                    or len(call.target_parts) != 3
                    or call.target_parts[:2] != (receiver, field_name)
                ):
                    continue
                method_name = call.target_parts[-1]
                target_candidates: list[Symbol] = []
                for symbol in maps.by_qualified_name.get(
                    f"{dependency.qualified_name}.{method_name}", []
                ):
                    if time.monotonic() >= budget.deadline:
                        budget._stop("max_seconds exceeded while resolving contextual calls")
                        return contextual
                    if symbol.path == dependency.path and symbol.kind == "function":
                        target_candidates.append(symbol)
                target_scope = (
                    scope_facts_by_key.get(
                        (
                            target_candidates[0].path,
                            target_candidates[0].qualified_name,
                            "function",
                            target_candidates[0].start_line,
                        )
                    )
                    if len(target_candidates) == 1
                    else None
                )
                if (
                    len(target_candidates) != 1
                    or target_scope is None
                    or dependency_scope.has_decorators
                    or method_name in dependency_scope.bindings.assigned
                    or _scope_has_attribute_write(dependency_scope, method_name)
                    or _scope_has_attribute_write(method_scope, method_name)
                    or bool(writes.get(method_name))
                    or bool(multisegment_attribute_writes.get(method_name))
                    or target_scope.has_decorators
                ):
                    continue
                key = (
                    activation_edges[0].edge_id,
                    activation.target_symbol.symbol_id,
                    target_candidates[0].symbol_id,
                    field_name,
                    call.start_line,
                    call.end_line,
                )
                if key in emitted:
                    continue
                if budget.edge_count >= budget.max_edges:
                    budget._stop("max_edges reached while collecting contextual calls")
                    return contextual
                if not budget.reserve("edges"):
                    return contextual
                emitted.add(key)
                identity = "\0".join(
                    [index_id, *key[:4], constructor.site_id, str(call.start_line), str(call.end_line)]
                )
                contextual.append(
                    ContextualCall(
                        context_id=f"context-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
                        index_id=index_id,
                        activation_edge_id=activation_edges[0].edge_id,
                        activation_source=activation.caller_symbol,
                        source=activation.target_symbol,
                        target=target_candidates[0],
                        activation_location=activation.location,
                        source_location=SourceLocation(
                            path=activation.target_symbol.path,
                            start_line=call.start_line,
                            end_line=call.end_line,
                        ),
                        receiver_constructor_location=constructor.location,
                        field_assignment_location=SourceLocation(
                            path=init_symbol.path,
                            start_line=assignment.start_line,
                            end_line=assignment.end_line,
                        ),
                        field_name=field_name,
                        binding_mutation_after_escape_unverified=(
                            constructor.site_id in binding_mutation_after_escape_unverified_site_ids
                        ),
                    )
                )
    return contextual


def serialize_relation_file_facts(facts: RelationFileFacts) -> str:
    """Serialize only validated, index-independent relation syntax facts."""

    _validate_relation_file_facts(facts)
    return json.dumps(
        {
            "format_version": _RELATION_FACTS_FORMAT_VERSION,
            "path": facts.path,
            "module_name": facts.module_name,
            "syntax_error": facts.syntax_error,
            "scope": _relation_scope_to_json(facts.scope) if facts.scope is not None else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_relation_file_facts(
    serialized: str,
    *,
    expected_path: str,
) -> RelationFileFacts:
    """Validate and decode one relation facts cache value."""

    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("relation facts payload is not valid JSON") from exc
    _require_dict(
        payload,
        {"format_version", "path", "module_name", "syntax_error", "scope"},
        "relation facts payload",
    )
    if payload["format_version"] != _RELATION_FACTS_FORMAT_VERSION:
        raise ValueError("relation facts format is unsupported")
    path = _require_text(payload["path"], "relation facts path", 1_000)
    if path != expected_path:
        raise ValueError("relation facts path does not match source")
    module_name = _require_text(payload["module_name"], "relation facts module name", 500)
    if module_name != _module_name(expected_path):
        raise ValueError("relation facts module name does not match path")
    syntax_error = payload["syntax_error"]
    if not isinstance(syntax_error, bool):
        raise ValueError("relation facts syntax flag is invalid")
    raw_scope = payload["scope"]
    if raw_scope is None:
        scope = None
    else:
        scope = _relation_scope_from_json(raw_scope)
    facts = RelationFileFacts(path, module_name, syntax_error, scope)
    _validate_relation_file_facts(facts)
    return facts


def _relation_scope_to_json(scope: RelationScopeFacts) -> dict[str, object]:
    return {
        "qualified_name": scope.qualified_name,
        "kind": scope.kind,
        "start_line": scope.start_line,
        "bindings": {
            "imports": [_relation_import_to_json(item) for item in scope.bindings.imports],
            "assigned": list(scope.bindings.assigned),
            "definition_names": list(scope.bindings.definition_names),
            "global_names": list(scope.bindings.global_names),
            "dynamic_names": list(scope.bindings.dynamic_names),
            "parameter_names": list(scope.bindings.parameter_names),
            "positional_parameter_names": list(scope.bindings.positional_parameter_names),
            "has_variadic_parameters": scope.bindings.has_variadic_parameters,
            "nonlocal_names": list(scope.bindings.nonlocal_names),
            "attribute_write_names": list(scope.bindings.attribute_write_names),
            "indirect_attribute_write_names": list(
                scope.bindings.indirect_attribute_write_names
            ),
        },
        "events": [_relation_event_to_json(event) for event in scope.events],
        "has_decorators": scope.has_decorators,
        "has_bases": scope.has_bases,
    }


def _relation_event_to_json(event: RelationEvent) -> dict[str, object]:
    if isinstance(event, RelationImportEvent):
        return {"type": "import", "fact": _relation_import_to_json(event.fact)}
    if isinstance(event, RelationCallEvent):
        return {"type": "call", "fact": _relation_call_to_json(event.fact)}
    if isinstance(event, RelationAssignmentEvent):
        return {"type": "assignment", "fact": _relation_assignment_to_json(event.fact)}
    if isinstance(event, RelationReturnEvent):
        return {
            "type": "return",
            "target": (
                _relation_call_target_to_json(event.target)
                if event.target is not None
                else None
            ),
        }
    return {"type": "scope", "scope": _relation_scope_to_json(event.scope)}


def _relation_import_to_json(fact: RelationImportFact) -> dict[str, object]:
    return {
        "kind": fact.kind,
        "aliases": [{"name": alias.name, "asname": alias.asname} for alias in fact.aliases],
        "module": fact.module,
        "level": fact.level,
        "start_line": fact.start_line,
        "end_line": fact.end_line,
    }


def _relation_call_to_json(fact: RelationCallFact) -> dict[str, object]:
    return {
        "expression": fact.expression,
        "target_kind": fact.target_kind,
        "target_name": fact.target_name,
        "target_parts": list(fact.target_parts),
        "first_argument": (
            _relation_call_target_to_json(fact.first_argument)
            if fact.first_argument is not None
            else None
        ),
        "arguments": [
            {
                "position": argument.position,
                "keyword": argument.keyword,
                "target": (
                    _relation_call_target_to_json(argument.target)
                    if argument.target is not None
                    else None
                ),
            }
            for argument in fact.arguments
        ],
        "has_argument_expansion": fact.has_argument_expansion,
        "start_line": fact.start_line,
        "end_line": fact.end_line,
    }


def _relation_assignment_to_json(fact: RelationAssignmentFact) -> dict[str, object]:
    return {
        "target_names": list(fact.target_names),
        "target_attributes": [
            _relation_call_target_to_json(target) for target in fact.target_attributes
        ],
        "constructor_name": fact.constructor_name,
        "value_name": fact.value_name,
        "certainty": fact.certainty,
        "start_line": fact.start_line,
        "end_line": fact.end_line,
    }


def _relation_call_target_to_json(target: RelationCallTargetFact) -> dict[str, object]:
    return {
        "target_kind": target.target_kind,
        "target_name": target.target_name,
        "target_parts": list(target.target_parts),
    }


def _relation_scope_from_json(raw_scope: object) -> RelationScopeFacts:
    _require_dict(
        raw_scope,
        {
            "qualified_name",
            "kind",
            "start_line",
            "bindings",
            "events",
            "has_decorators",
            "has_bases",
        },
        "relation scope",
    )
    qualified_name = _require_text(raw_scope["qualified_name"], "relation scope name", 500)
    kind = raw_scope["kind"]
    if kind not in {"module", "class", "function"}:
        raise ValueError("relation scope kind is invalid")
    start_line = _require_positive_int(raw_scope["start_line"], "relation scope line")
    raw_bindings = raw_scope["bindings"]
    _require_dict(
        raw_bindings,
        {
            "imports",
            "assigned",
            "definition_names",
            "global_names",
            "dynamic_names",
            "parameter_names",
            "positional_parameter_names",
            "has_variadic_parameters",
            "nonlocal_names",
            "attribute_write_names",
            "indirect_attribute_write_names",
        },
        "relation bindings",
    )
    raw_imports = raw_bindings["imports"]
    if not isinstance(raw_imports, list) or len(raw_imports) > 20_000:
        raise ValueError("relation binding imports are invalid")
    bindings = RelationBindings(
        imports=tuple(_relation_import_from_json(item) for item in raw_imports),
        assigned=_relation_names_from_json(raw_bindings["assigned"], "assigned names"),
        definition_names=_relation_names_from_json(
            raw_bindings["definition_names"], "definition names"
        ),
        global_names=_relation_names_from_json(raw_bindings["global_names"], "global names"),
        dynamic_names=_relation_names_from_json(
            raw_bindings["dynamic_names"], "dynamic names"
        ),
        parameter_names=_relation_names_from_json(
            raw_bindings["parameter_names"], "parameter names"
        ),
        positional_parameter_names=_relation_parameter_names_from_json(
            raw_bindings["positional_parameter_names"]
        ),
        has_variadic_parameters=_require_bool(
            raw_bindings["has_variadic_parameters"], "relation variadic parameter flag"
        ),
        nonlocal_names=_relation_names_from_json(
            raw_bindings["nonlocal_names"], "nonlocal names"
        ),
        attribute_write_names=_relation_names_from_json(
            raw_bindings["attribute_write_names"], "attribute write names"
        ),
        indirect_attribute_write_names=_relation_names_from_json(
            raw_bindings["indirect_attribute_write_names"], "indirect attribute write names"
        ),
    )
    raw_events = raw_scope["events"]
    if not isinstance(raw_events, list) or len(raw_events) > 100_000:
        raise ValueError("relation scope events are invalid")
    return RelationScopeFacts(
        qualified_name=qualified_name,
        kind=kind,
        start_line=start_line,
        bindings=bindings,
        events=tuple(_relation_event_from_json(item) for item in raw_events),
        has_decorators=_require_bool(raw_scope["has_decorators"], "relation scope decorators"),
        has_bases=_require_bool(raw_scope["has_bases"], "relation scope bases"),
    )


def _relation_event_from_json(raw_event: object) -> RelationEvent:
    if not isinstance(raw_event, dict) or "type" not in raw_event:
        raise ValueError("relation event shape is invalid")
    event_type = raw_event["type"]
    if event_type == "import":
        _require_dict(raw_event, {"type", "fact"}, "relation import event")
        return RelationImportEvent(_relation_import_from_json(raw_event["fact"]))
    if event_type == "call":
        _require_dict(raw_event, {"type", "fact"}, "relation call event")
        return RelationCallEvent(_relation_call_from_json(raw_event["fact"]))
    if event_type == "assignment":
        _require_dict(raw_event, {"type", "fact"}, "relation assignment event")
        return RelationAssignmentEvent(_relation_assignment_from_json(raw_event["fact"]))
    if event_type == "return":
        _require_dict(raw_event, {"type", "target"}, "relation return event")
        return RelationReturnEvent(
            None
            if raw_event["target"] is None
            else _relation_call_target_from_json(raw_event["target"])
        )
    if event_type == "scope":
        _require_dict(raw_event, {"type", "scope"}, "relation scope event")
        return RelationScopeEvent(_relation_scope_from_json(raw_event["scope"]))
    raise ValueError("relation event type is invalid")


def _relation_import_from_json(raw_fact: object) -> RelationImportFact:
    _require_dict(
        raw_fact,
        {"kind", "aliases", "module", "level", "start_line", "end_line"},
        "relation import fact",
    )
    kind = raw_fact["kind"]
    if kind not in {"import", "from"}:
        raise ValueError("relation import kind is invalid")
    raw_aliases = raw_fact["aliases"]
    if not isinstance(raw_aliases, list) or not 1 <= len(raw_aliases) <= 1_000:
        raise ValueError("relation import aliases are invalid")
    aliases: list[RelationImportAlias] = []
    for raw_alias in raw_aliases:
        _require_dict(raw_alias, {"name", "asname"}, "relation import alias")
        name = _require_text(raw_alias["name"], "relation import name", 500)
        asname = raw_alias["asname"]
        if asname is not None:
            asname = _require_text(asname, "relation import alias name", 500)
        aliases.append(RelationImportAlias(name, asname))
    module = raw_fact["module"]
    if module is not None:
        module = _require_text(module, "relation import module", 500)
    level = _require_nonnegative_int(raw_fact["level"], "relation import level")
    start_line = _require_positive_int(raw_fact["start_line"], "relation import start line")
    end_line = _require_positive_int(raw_fact["end_line"], "relation import end line")
    return RelationImportFact(kind, tuple(aliases), module, level, start_line, end_line)


def _relation_call_from_json(raw_fact: object) -> RelationCallFact:
    _require_dict(
        raw_fact,
        {
            "expression",
            "target_kind",
            "target_name",
            "target_parts",
            "first_argument",
            "arguments",
            "has_argument_expansion",
            "start_line",
            "end_line",
        },
        "relation call fact",
    )
    expression = _require_text(raw_fact["expression"], "relation call expression", 500)
    target_kind = raw_fact["target_kind"]
    if target_kind not in {"name", "attribute", "dynamic"}:
        raise ValueError("relation call target kind is invalid")
    target_name = raw_fact["target_name"]
    if target_name is not None:
        target_name = _require_text(target_name, "relation call target name", 500)
    raw_parts = raw_fact["target_parts"]
    if not isinstance(raw_parts, list) or len(raw_parts) > 100:
        raise ValueError("relation call target parts are invalid")
    target_parts = tuple(
        _require_text(part, "relation call target part", 500) for part in raw_parts
    )
    first_argument = (
        None
        if raw_fact["first_argument"] is None
        else _relation_call_target_from_json(raw_fact["first_argument"])
    )
    raw_arguments = raw_fact["arguments"]
    if not isinstance(raw_arguments, list) or len(raw_arguments) > 10_000:
        raise ValueError("relation call arguments are invalid")
    arguments = tuple(_relation_call_argument_from_json(item) for item in raw_arguments)
    start_line = _require_positive_int(raw_fact["start_line"], "relation call start line")
    end_line = _require_positive_int(raw_fact["end_line"], "relation call end line")
    return RelationCallFact(
        expression,
        target_kind,
        target_name,
        target_parts,
        first_argument,
        arguments,
        _require_bool(raw_fact["has_argument_expansion"], "relation call expansion flag"),
        start_line,
        end_line,
    )


def _relation_call_target_from_json(raw_target: object) -> RelationCallTargetFact:
    _require_dict(
        raw_target,
        {"target_kind", "target_name", "target_parts"},
        "relation call first argument",
    )
    target_kind = raw_target["target_kind"]
    if target_kind not in {"name", "attribute", "constructor"}:
        raise ValueError("relation call first argument kind is invalid")
    target_name = raw_target["target_name"]
    if target_name is not None:
        target_name = _require_text(target_name, "relation first argument name", 500)
    raw_parts = raw_target["target_parts"]
    if not isinstance(raw_parts, list) or len(raw_parts) > 100:
        raise ValueError("relation call first argument parts are invalid")
    target_parts = tuple(
        _require_text(part, "relation first argument part", 500) for part in raw_parts
    )
    target = RelationCallTargetFact(target_kind, target_name, target_parts)
    _validate_relation_call_target(target)
    return target


def _relation_call_argument_from_json(raw_argument: object) -> RelationCallArgumentFact:
    _require_dict(raw_argument, {"position", "keyword", "target"}, "relation call argument")
    position = raw_argument["position"]
    if position is not None:
        position = _require_nonnegative_int(position, "relation call argument position")
    keyword = raw_argument["keyword"]
    if keyword is not None:
        keyword = _require_text(keyword, "relation call argument keyword", 500)
    target = (
        None
        if raw_argument["target"] is None
        else _relation_call_target_from_json(raw_argument["target"])
    )
    if (position is None) == (keyword is None):
        raise ValueError("relation call argument location is invalid")
    return RelationCallArgumentFact(position, keyword, target)


def _relation_assignment_from_json(raw_fact: object) -> RelationAssignmentFact:
    _require_dict(
        raw_fact,
        {
            "target_names",
            "target_attributes",
            "constructor_name",
            "value_name",
            "certainty",
            "start_line",
            "end_line",
        },
        "relation assignment fact",
    )
    raw_names = raw_fact["target_names"]
    if not isinstance(raw_names, list) or len(raw_names) > 20_000:
        raise ValueError("relation assignment target names are invalid")
    target_names = tuple(
        _require_text(name, "relation assignment target name", 500) for name in raw_names
    )
    raw_attributes = raw_fact["target_attributes"]
    if not isinstance(raw_attributes, list) or len(raw_attributes) > 20_000:
        raise ValueError("relation assignment target attributes are invalid")
    target_attributes = tuple(
        _relation_call_target_from_json(item) for item in raw_attributes
    )
    constructor_name = raw_fact["constructor_name"]
    if constructor_name is not None:
        constructor_name = _require_text(
            constructor_name, "relation assignment constructor name", 500
        )
    value_name = raw_fact["value_name"]
    if value_name is not None:
        value_name = _require_text(value_name, "relation assignment value name", 500)
    certainty = raw_fact["certainty"]
    if certainty not in {"definite", "uncertain"}:
        raise ValueError("relation assignment certainty is invalid")
    start_line = _require_positive_int(raw_fact["start_line"], "relation assignment start line")
    end_line = _require_positive_int(raw_fact["end_line"], "relation assignment end line")
    return RelationAssignmentFact(
        target_names,
        target_attributes,
        constructor_name,
        value_name,
        certainty,
        start_line,
        end_line,
    )


def _relation_names_from_json(raw_names: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw_names, list) or len(raw_names) > 20_000:
        raise ValueError(f"relation {label} are invalid")
    names = tuple(_require_text(name, f"relation {label[:-1]} name", 500) for name in raw_names)
    if names != tuple(sorted(set(names))):
        raise ValueError(f"relation {label} are not canonical")
    return names


def _relation_parameter_names_from_json(raw_names: object) -> tuple[str, ...]:
    if not isinstance(raw_names, list) or len(raw_names) > 20_000:
        raise ValueError("relation positional parameter names are invalid")
    names = tuple(
        _require_text(name, "relation positional parameter name", 500) for name in raw_names
    )
    if len(names) != len(set(names)):
        raise ValueError("relation positional parameter names are not unique")
    return names


def _require_dict(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} shape is invalid")
    return value


def _require_text(value: object, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise ValueError(f"{label} is invalid")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} is invalid")
    return value


def _require_positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} is invalid")
    return value


def _require_nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _validate_relation_file_facts(facts: RelationFileFacts) -> None:
    _require_text(facts.path, "relation facts path", 1_000)
    _require_text(facts.module_name, "relation facts module name", 500)
    if not isinstance(facts.syntax_error, bool):
        raise ValueError("relation facts syntax flag is invalid")
    if facts.syntax_error:
        if facts.scope is not None:
            raise ValueError("syntax-error relation facts cannot contain a scope")
        return
    if facts.scope is None:
        raise ValueError("valid relation facts require a scope")
    if facts.scope.kind != "module" or facts.scope.qualified_name != facts.module_name:
        raise ValueError("relation facts module scope is invalid")
    if facts.scope.start_line != 1:
        raise ValueError("relation facts module line is invalid")
    counters = [0]
    _validate_relation_scope(facts.scope, depth=0, counters=counters)


def _validate_relation_scope(
    scope: RelationScopeFacts,
    *,
    depth: int,
    counters: list[int],
) -> None:
    if depth > 1_000:
        raise ValueError("relation facts scope nesting is too deep")
    _require_text(scope.qualified_name, "relation scope name", 500)
    if scope.kind not in {"module", "class", "function"}:
        raise ValueError("relation scope kind is invalid")
    if not isinstance(scope.has_decorators, bool) or not isinstance(scope.has_bases, bool):
        raise ValueError("relation scope safety flags are invalid")
    _require_positive_int(scope.start_line, "relation scope line")
    bindings = scope.bindings
    if not isinstance(bindings.has_variadic_parameters, bool):
        raise ValueError("relation variadic parameter flag is invalid")
    if not isinstance(bindings.imports, tuple) or len(bindings.imports) > 20_000:
        raise ValueError("relation binding imports are invalid")
    for import_fact in bindings.imports:
        _validate_relation_import(import_fact)
    for label, names in (
        ("assigned names", bindings.assigned),
        ("definition names", bindings.definition_names),
        ("global names", bindings.global_names),
        ("dynamic names", bindings.dynamic_names),
        ("parameter names", bindings.parameter_names),
        ("nonlocal names", bindings.nonlocal_names),
        ("attribute write names", bindings.attribute_write_names),
        ("indirect attribute write names", bindings.indirect_attribute_write_names),
    ):
        if not isinstance(names, tuple) or len(names) > 20_000:
            raise ValueError(f"relation {label} are invalid")
        for name in names:
            _require_text(name, f"relation {label} name", 500)
        if names != tuple(sorted(set(names))):
            raise ValueError(f"relation {label} are not canonical")
    if not isinstance(bindings.positional_parameter_names, tuple) or len(
        bindings.positional_parameter_names
    ) > 20_000:
        raise ValueError("relation positional parameter names are invalid")
    if len(bindings.positional_parameter_names) != len(set(bindings.positional_parameter_names)):
        raise ValueError("relation positional parameter names are not unique")
    if not set(bindings.positional_parameter_names) <= set(bindings.parameter_names):
        raise ValueError("relation positional parameters do not match scope parameters")
    if not isinstance(scope.events, tuple) or len(scope.events) > 100_000:
        raise ValueError("relation scope events are invalid")
    for event in scope.events:
        counters[0] += 1
        if counters[0] > 200_000:
            raise ValueError("relation facts contain too many events")
        if isinstance(event, RelationImportEvent):
            _validate_relation_import(event.fact)
        elif isinstance(event, RelationCallEvent):
            _validate_relation_call(event.fact)
        elif isinstance(event, RelationAssignmentEvent):
            _validate_relation_assignment(event.fact)
        elif isinstance(event, RelationReturnEvent):
            if event.target is not None:
                _validate_relation_call_target(event.target)
        elif isinstance(event, RelationScopeEvent):
            if event.scope.kind == "module":
                raise ValueError("nested relation module scope is invalid")
            _validate_relation_scope(event.scope, depth=depth + 1, counters=counters)
        else:
            raise ValueError("relation event type is invalid")


def _validate_relation_import(fact: RelationImportFact) -> None:
    if fact.kind not in {"import", "from"}:
        raise ValueError("relation import kind is invalid")
    if not isinstance(fact.aliases, tuple) or not 1 <= len(fact.aliases) <= 1_000:
        raise ValueError("relation import aliases are invalid")
    for alias in fact.aliases:
        _require_text(alias.name, "relation import name", 500)
        if alias.asname is not None:
            _require_text(alias.asname, "relation import alias name", 500)
    if fact.module is not None:
        _require_text(fact.module, "relation import module", 500)
    _require_nonnegative_int(fact.level, "relation import level")
    if fact.kind == "import" and (fact.module is not None or fact.level != 0):
        raise ValueError("plain relation import metadata is invalid")
    _validate_line_range(fact.start_line, fact.end_line, "relation import")


def _validate_relation_call(fact: RelationCallFact) -> None:
    _require_text(fact.expression, "relation call expression", 500)
    if fact.target_kind not in {"name", "attribute", "dynamic"}:
        raise ValueError("relation call target kind is invalid")
    if fact.target_name is not None:
        _require_text(fact.target_name, "relation call target name", 500)
    if not isinstance(fact.target_parts, tuple) or len(fact.target_parts) > 100:
        raise ValueError("relation call target parts are invalid")
    for part in fact.target_parts:
        _require_text(part, "relation call target part", 500)
    if fact.target_kind == "name" and (
        fact.target_name is None or fact.target_parts
    ):
        raise ValueError("name call target metadata is invalid")
    if fact.target_kind == "attribute" and (
        fact.target_name is not None or len(fact.target_parts) < 2
    ):
        raise ValueError("attribute call target metadata is invalid")
    if fact.target_kind == "dynamic" and (fact.target_name is not None or fact.target_parts):
        raise ValueError("dynamic call target metadata is invalid")
    if fact.first_argument is not None:
        _validate_relation_call_target(fact.first_argument)
        if fact.first_argument.target_kind == "constructor":
            raise ValueError("relation wrapper argument cannot be a constructor expression")
    if not isinstance(fact.arguments, tuple) or len(fact.arguments) > 10_000:
        raise ValueError("relation call arguments are invalid")
    if not isinstance(fact.has_argument_expansion, bool):
        raise ValueError("relation call expansion flag is invalid")
    positions: set[int] = set()
    keywords: set[str] = set()
    for argument in fact.arguments:
        if not isinstance(argument, RelationCallArgumentFact):
            raise ValueError("relation call argument is invalid")
        if (argument.position is None) == (argument.keyword is None):
            raise ValueError("relation call argument location is invalid")
        if argument.position is not None:
            positions.add(_require_nonnegative_int(argument.position, "call argument position"))
        else:
            keyword = _require_text(argument.keyword, "call argument keyword", 500)
            if keyword in keywords:
                raise ValueError("relation call argument keyword is duplicated")
            keywords.add(keyword)
        if argument.target is not None:
            _validate_relation_call_target(argument.target)
    if len(positions) != sum(1 for argument in fact.arguments if argument.position is not None):
        raise ValueError("relation call argument position is duplicated")
    _validate_line_range(fact.start_line, fact.end_line, "relation call")


def _validate_relation_call_target(target: RelationCallTargetFact) -> None:
    if target.target_kind not in {"name", "attribute", "constructor"}:
        raise ValueError("relation call first argument kind is invalid")
    if target.target_name is not None:
        _require_text(target.target_name, "relation first argument name", 500)
    if not isinstance(target.target_parts, tuple) or len(target.target_parts) > 100:
        raise ValueError("relation call first argument parts are invalid")
    for part in target.target_parts:
        _require_text(part, "relation first argument part", 500)
    if target.target_kind == "name" and (target.target_name is None or target.target_parts):
        raise ValueError("relation call first argument name metadata is invalid")
    if target.target_kind == "attribute" and (
        target.target_name is not None or len(target.target_parts) < 2
    ):
        raise ValueError("relation call first argument attribute metadata is invalid")
    if target.target_kind == "constructor" and not (
        (target.target_name is not None and not target.target_parts)
        or (target.target_name is None and len(target.target_parts) >= 2)
    ):
        raise ValueError("relation call constructor metadata is invalid")


def _validate_relation_assignment(fact: RelationAssignmentFact) -> None:
    if not isinstance(fact.target_names, tuple) or not isinstance(
        fact.target_attributes, tuple
    ) or not (fact.target_names or fact.target_attributes):
        raise ValueError("relation assignment target names are invalid")
    for name in fact.target_names:
        _require_text(name, "relation assignment target name", 500)
    if fact.target_names != tuple(sorted(set(fact.target_names))):
        raise ValueError("relation assignment target names are not canonical")
    for target in fact.target_attributes:
        _validate_relation_call_target(target)
        if target.target_kind != "attribute":
            raise ValueError("relation assignment attribute target is invalid")
    if fact.target_attributes != tuple(
        sorted(set(fact.target_attributes), key=lambda target: target.target_parts)
    ):
        raise ValueError("relation assignment attribute targets are not canonical")
    if fact.constructor_name is not None:
        _require_text(fact.constructor_name, "relation assignment constructor name", 500)
        if len(fact.target_names) + len(fact.target_attributes) != 1:
            raise ValueError("relation assignment constructor target is invalid")
    if fact.value_name is not None:
        _require_text(fact.value_name, "relation assignment value name", 500)
    if fact.certainty not in {"definite", "uncertain"}:
        raise ValueError("relation assignment certainty is invalid")
    _validate_line_range(fact.start_line, fact.end_line, "relation assignment")


def _validate_line_range(start_line: object, end_line: object, label: str) -> None:
    start = _require_positive_int(start_line, f"{label} start line")
    end = _require_positive_int(end_line, f"{label} end line")
    if end < start:
        raise ValueError(f"{label} line range is invalid")


def _scope_body(node: ast.AST) -> list[ast.stmt]:
    if isinstance(node, ast.Module):
        return node.body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.body
    return []


def _parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    arguments = node.args
    names = {argument.arg for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
    if arguments.vararg:
        names.add(arguments.vararg.arg)
    if arguments.kwarg:
        names.add(arguments.kwarg.arg)
    return names


def _positional_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    return tuple(argument.arg for argument in (*node.args.posonlyargs, *node.args.args))


def _definition_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> list[ast.expr]:
    expressions: list[ast.expr] = [*node.decorator_list]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        expressions.extend(node.args.defaults)
        expressions.extend(default for default in node.args.kw_defaults if default is not None)
        expressions.extend(
            argument.annotation
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if argument.annotation is not None
        )
        if node.args.vararg and node.args.vararg.annotation:
            expressions.append(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            expressions.append(node.args.kwarg.annotation)
        if node.returns:
            expressions.append(node.returns)
    else:
        expressions.extend(node.bases)
        expressions.extend(keyword.value for keyword in node.keywords)
    return expressions


def _from_import_base(
    module_name: str,
    path: str,
    level: int,
    requested_module: str | None,
) -> str | None:
    if level == 0:
        return requested_module
    is_package_module = path.endswith("/__init__.py")
    package_parts = module_name.split(".") if is_package_module else module_name.split(".")[:-1]
    climb = level - 1
    if climb > len(package_parts):
        return None
    base_parts = package_parts[: len(package_parts) - climb]
    if requested_module:
        base_parts.extend(requested_module.split("."))
    return ".".join(base_parts) or None


def _assigned_names(targets: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for target in targets:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                names.add(node.id)
    return names


def _is_getattr_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"


def _attribute_parts(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return list(reversed(parts))


def _expression(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _import_expression_fact(fact: RelationImportFact, alias: RelationImportAlias) -> str:
    prefix = "." * fact.level + (fact.module or "")
    return f"from {prefix} import {alias.name}"
