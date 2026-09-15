"""Conservative imports and call relations over one immutable source snapshot."""

from __future__ import annotations

import ast
import hashlib
import time
from dataclasses import dataclass
from typing import Literal

from .contracts import CodeEdge, SourceLocation, Symbol, UnresolvedReference
from .indexing import DefinitionIndex, SnapshotIssue, SourceSnapshot


RelationKind = Literal["imports", "calls"]
BindingKind = Literal["module", "symbol"]
RELATION_INDEX_IDENTITY_VERSION = "relations-v2"
# New service-built snapshots use a separate identity namespace so legacy
# rows without freshness metadata remain immutable and explicitly rebuildable.
INDEX_BUILD_IDENTITY_VERSION = "relations-v3"


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
class RelationIndex:
    """Static relations and unresolved references tied to one definition index."""

    definitions: DefinitionIndex
    edges: tuple[CodeEdge, ...]
    unresolved: tuple[UnresolvedReference, ...]
    truncated: bool
    truncation_reason: str | None
    unresolved_truncated: bool | None = None
    unresolved_truncation_reason: str | None = None

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
class _Binding:
    kind: BindingKind
    symbol: Symbol | None = None
    module_name: str | None = None


@dataclass
class _Scope:
    node: ast.AST
    symbol: Symbol
    parent: _Scope | None
    bindings: dict[str, _Binding]
    shadowed: set[str]
    dynamic_names: set[str]


@dataclass(frozen=True)
class _ImportResolution:
    binding_name: str | None
    binding: _Binding | None
    target: Symbol | None
    expression: str
    reason: str


class _RelationBudget:
    def __init__(self, max_edges: int, max_unresolved: int, max_seconds: float) -> None:
        self.max_edges = max_edges
        self.max_unresolved = max_unresolved
        self.deadline = time.monotonic() + max_seconds
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


def extract_relations(
    definitions: DefinitionIndex,
    *,
    max_edges: int = 5_000,
    max_unresolved: int = 5_000,
    max_seconds: float = 5.0,
) -> RelationIndex:
    """Extract only relations whose target is supported by snapshot evidence."""

    if max_edges < 1 or max_unresolved < 1 or max_seconds <= 0:
        raise ValueError("relation budgets must be positive")

    snapshot = definitions.snapshot
    maps = _DefinitionMaps(definitions.symbols)
    budget = _RelationBudget(max_edges, max_unresolved, max_seconds)
    edges: list[CodeEdge] = []
    unresolved: list[UnresolvedReference] = []

    for source_file in snapshot.files:
        if budget.truncated:
            break
        try:
            tree = ast.parse(source_file.text, filename=source_file.path, type_comments=True)
        except SyntaxError:
            continue

        module_symbol = maps.modules_by_path.get(source_file.path)
        if module_symbol is None:
            continue
        visitor = _RelationVisitor(
            path=source_file.path,
            module_name=module_symbol.qualified_name,
            tree=tree,
            module_symbol=module_symbol,
            maps=maps,
            budget=budget,
            edges=edges,
            unresolved=unresolved,
        )
        visitor.visit_module()

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
    )


def build_relation_index(
    definitions: DefinitionIndex,
    *,
    max_edges: int = 5_000,
    max_unresolved: int = 5_000,
    max_seconds: float = 5.0,
) -> RelationIndex:
    """Named entry point for callers that treat relations as an index stage."""

    return extract_relations(
        definitions,
        max_edges=max_edges,
        max_unresolved=max_unresolved,
        max_seconds=max_seconds,
    )


class _DefinitionMaps:
    def __init__(self, symbols: tuple[Symbol, ...]) -> None:
        self.by_key: dict[tuple[str, str, str, int], Symbol] = {}
        self.by_qualified_name: dict[str, list[Symbol]] = {}
        self.modules_by_path: dict[str, Symbol] = {}
        self.modules_by_name: dict[str, Symbol] = {}
        self.simple_candidates: dict[str, list[Symbol]] = {}
        for symbol in symbols:
            self.by_key[(symbol.path, symbol.qualified_name, symbol.kind, symbol.start_line)] = symbol
            self.by_qualified_name.setdefault(symbol.qualified_name, []).append(symbol)
            if symbol.kind == "module":
                self.modules_by_path[symbol.path] = symbol
                self.modules_by_name[symbol.qualified_name] = symbol
            elif symbol.kind in {"class", "function", "test"}:
                simple_name = symbol.qualified_name.rsplit(".", 1)[-1]
                self.simple_candidates.setdefault(simple_name, []).append(symbol)

    def one_by_name(self, qualified_name: str) -> Symbol | None:
        values = self.by_qualified_name.get(qualified_name, [])
        return values[0] if len(values) == 1 else None

    def candidates(self, name: str) -> list[Symbol]:
        return sorted(self.simple_candidates.get(name, []), key=lambda symbol: symbol.symbol_id)


class _BindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.assigned: set[str] = set()
        self.definition_names: set[str] = set()
        self.global_names: set[str] = set()
        self.dynamic_names: set[str] = set()

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

    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_getattr_call(node.value):
            self.dynamic_names.update(_assigned_names(node.targets))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and _is_getattr_call(node.value):
            self.dynamic_names.update(_assigned_names([node.target]))
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if _is_getattr_call(node.value):
            self.dynamic_names.update(_assigned_names([node.target]))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.assigned.add(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.assigned.add(node.name)
        self.generic_visit(node)


class _RelationVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        module_name: str,
        tree: ast.Module,
        module_symbol: Symbol,
        maps: _DefinitionMaps,
        budget: _RelationBudget,
        edges: list[CodeEdge],
        unresolved: list[UnresolvedReference],
    ) -> None:
        self.path = path
        self.module_name = module_name
        self.tree = tree
        self.maps = maps
        self.budget = budget
        self.edges = edges
        self.unresolved = unresolved
        self.scope = self._make_scope(tree, module_symbol, None)
        self._edge_occurrences: dict[tuple[str, str, str, int, int], int] = {}
        self._unresolved_occurrences: dict[tuple[str, str, int, int], int] = {}

    def visit_module(self) -> None:
        for node in self.tree.body:
            self.visit(node)
            if self.budget.truncated:
                return

    def visit(self, node: ast.AST) -> object:
        if self.budget.truncated:
            return None
        return super().visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            resolution = self._resolve_import(node, alias)
            if resolution.target is not None:
                self._add_edge("imports", resolution.target, "explicit_import", node)
            else:
                self._add_unresolved(
                    node,
                    resolution.expression,
                    resolution.reason,
                    self._candidate_ids(alias.name.rsplit(".", 1)[-1]),
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            resolution = self._resolve_import(node, alias)
            if resolution.target is not None:
                self._add_edge("imports", resolution.target, "explicit_import", node)
            else:
                self._add_unresolved(
                    node,
                    resolution.expression,
                    resolution.reason,
                    self._candidate_ids(alias.name),
                )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_nested_scope(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_nested_scope(node, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_nested_scope(node, "class")

    def visit_Call(self, node: ast.Call) -> None:
        target, reason, candidates = self._resolve_call_target(node.func)
        if target is not None:
            self._add_edge("calls", target, "ast", node)
        else:
            self._add_unresolved(node, _expression(node), reason, candidates)
        self.generic_visit(node)

    def _visit_nested_scope(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        kind: Literal["class", "function"],
    ) -> None:
        parent_scope = self.scope
        for expression in _definition_expressions(node):
            self.visit(expression)
            if self.budget.truncated:
                return

        symbol = self._symbol_for_node(node, kind)
        if symbol is None:
            self.generic_visit(node)
            return

        lexical_parent = (
            parent_scope.parent if kind == "function" and isinstance(parent_scope.node, ast.ClassDef) else parent_scope
        )
        self.scope = self._make_scope(node, symbol, lexical_parent)
        for child in node.body:
            self.visit(child)
            if self.budget.truncated:
                break
        self.scope = parent_scope

    def _make_scope(self, node: ast.AST, symbol: Symbol, parent: _Scope | None) -> _Scope:
        collector = _BindingCollector()
        for child in _scope_body(node):
            collector.visit(child)

        shadowed = collector.assigned - collector.global_names
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            shadowed.update(_parameter_names(node))
        bindings: dict[str, _Binding] = {}
        for import_node in collector.imports:
            aliases = import_node.names
            for alias in aliases:
                resolution = self._resolve_import(import_node, alias)
                if (
                    resolution.binding_name
                    and resolution.binding
                    and resolution.binding_name not in shadowed
                ):
                    bindings[resolution.binding_name] = resolution.binding

        for name in collector.definition_names:
            if name in shadowed:
                continue
            local_symbol = self.maps.one_by_name(f"{symbol.qualified_name}.{name}")
            if local_symbol is not None:
                bindings[name] = _Binding(kind="symbol", symbol=local_symbol)

        return _Scope(
            node=node,
            symbol=symbol,
            parent=parent,
            bindings=bindings,
            shadowed=shadowed,
            dynamic_names=collector.dynamic_names,
        )

    def _symbol_for_node(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        kind: Literal["class", "function"],
    ) -> Symbol | None:
        qualified_name = f"{self.scope.symbol.qualified_name}.{node.name}"
        return self.maps.by_key.get((self.path, qualified_name, kind, node.lineno))

    def _resolve_import(
        self,
        node: ast.Import | ast.ImportFrom,
        alias: ast.alias,
    ) -> _ImportResolution:
        if isinstance(node, ast.Import):
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

        expression = _import_expression(node, alias)
        if alias.name == "*":
            return _ImportResolution(None, None, None, expression, "star_import_not_resolved")
        base = _from_import_base(self.module_name, self.path, node.level, node.module)
        if not base:
            return _ImportResolution(None, None, None, expression, "invalid_relative_import")

        target_name = f"{base}.{alias.name}"
        target = self.maps.one_by_name(target_name) or self.maps.modules_by_name.get(target_name)
        if target is None:
            reason = "external_import" if node.level == 0 else "missing_relative_import"
            return _ImportResolution(None, None, None, expression, reason)

        binding_name = alias.asname or alias.name
        if target.kind == "module":
            binding = _Binding(kind="module", symbol=target, module_name=target.qualified_name)
        else:
            binding = _Binding(kind="symbol", symbol=target)
        return _ImportResolution(binding_name, binding, target, expression, "")

    def _resolve_call_target(
        self,
        function: ast.expr,
    ) -> tuple[Symbol | None, str, list[str]]:
        if isinstance(function, ast.Name):
            binding, lookup_reason = self._lookup(function.id)
            if binding is not None and binding.symbol is not None and binding.kind == "symbol":
                return binding.symbol, "", []
            if lookup_reason:
                return None, lookup_reason, []
            candidates = self._candidate_ids(function.id)
            if len(candidates) > 1:
                return None, "ambiguous_name", candidates
            return None, "call_target_not_in_snapshot", candidates

        parts = _attribute_parts(function)
        if parts:
            binding, lookup_reason = self._lookup(parts[0])
            if binding is not None and binding.kind == "module" and binding.module_name:
                target_name = ".".join((binding.module_name, *parts[1:]))
                target = self.maps.one_by_name(target_name)
                if target is not None:
                    return target, "", []
                return None, "module_attribute_not_in_snapshot", self._candidate_ids(parts[-1])
            return None, lookup_reason or "object_method_not_resolved", []

        return None, "dynamic_call_target", []

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
        node: ast.AST,
    ) -> None:
        if not self.budget.reserve("edges"):
            return
        start_line, end_line = _node_range(node)
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
        node: ast.AST,
        expression: str,
        reason: str,
        candidate_ids: list[str],
    ) -> None:
        if not self.budget.reserve("unresolved"):
            return
        start_line, end_line = _node_range(node)
        expression = expression[:500] or type(node).__name__
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


def _import_expression(node: ast.ImportFrom, alias: ast.alias) -> str:
    prefix = "." * node.level + (node.module or "")
    return f"from {prefix} import {alias.name}"


def _node_range(node: ast.AST) -> tuple[int, int]:
    start_line = getattr(node, "lineno", 1) or 1
    end_line = getattr(node, "end_lineno", start_line) or start_line
    return start_line, end_line
